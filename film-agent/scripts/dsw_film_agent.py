"""Run Morpho film work and persist its receipts inside the personal DSW workspace."""
import argparse
from base64 import b64encode
from contextlib import closing
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
from dotenv import dotenv_values

from film_cut_methods import editorial_candidates, protect_native_dialogue
from film_edges import extract_cut, write_receipt
import film_experience
import film_review_sampling as sampling
from film_source_windows import require_clean_source_window, require_time_window
from h3_reference_plate import derive_plate
from media_segments import concat_segments, fingerprint, prepare_segment

WORKSPACE = Path('/mnt/AIRSSD/user_workspace/liaolin2')
ROOT = WORKSPACE / 'morpho-personal' / 'film-agent'
LANES = {
    'research': 'research', 'repair-plan': 'text', 'story-plan': 'text',
    'record-case': 'cpu', 'assemble': 'cpu', 'cut-evidence': 'cpu', 'openfilm': 'cpu',
    'dialogue-evidence': 'cpu', 'publish-screening': 'text',
    'render': 'video', 'review-shot': 'picture', 'expert-shot-review': 'picture', 'repair-edge': 'picture',
}
H3_SLOTS = {0: 28188, 1: 28191}
VIDEO_LANE = 'video'
COMFY_HTTP = build_opener(ProxyHandler({}))
DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL, lane TEXT NOT NULL,
    inputs TEXT NOT NULL, dependencies TEXT NOT NULL,
    state TEXT NOT NULL, result TEXT, error TEXT, updated_at TEXT NOT NULL, assigned_gpu INTEGER
);
CREATE TABLE IF NOT EXISTS cases (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), project TEXT NOT NULL,
    observed TEXT NOT NULL, decision TEXT NOT NULL, evidence TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_failure_modes (
    job_id TEXT NOT NULL REFERENCES cases(job_id), mode TEXT NOT NULL,
    PRIMARY KEY(job_id, mode)
);
CREATE INDEX IF NOT EXISTS cases_by_failure_mode ON case_failure_modes(mode, job_id);
"""


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def connection():
    db = sqlite3.connect(ROOT / 'queue.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(DB_SCHEMA)
    with db:
        db.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        if 'assigned_gpu' not in columns:
            db.execute('ALTER TABLE jobs ADD COLUMN assigned_gpu INTEGER')
            db.execute("UPDATE jobs SET lane='video' WHERE kind='render' AND lane='video-alt' AND state='queued'")
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS active_job_per_gpu ON jobs(assigned_gpu)
                      WHERE lane='video' AND state='running' AND assigned_gpu IS NOT NULL''')
    return db


def state_fields(row):
    return {key: json.loads(row[key]) if row[key] and key in {'dependencies', 'result'} else row[key]
            for key in ('id', 'project', 'kind', 'lane', 'state', 'dependencies', 'result', 'error', 'updated_at', 'assigned_gpu')}


def submit(db, spec):
    project, kind, inputs = spec['project'], spec['kind'], spec['inputs']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', project):
        raise ValueError('项目标识格式无效。')
    if kind not in LANES or not isinstance(inputs, dict):
        raise ValueError('任务类型或输入无效。')
    lane = spec.get('lane', LANES[kind])
    if kind == 'render' and lane == 'video-alt':
        lane = VIDEO_LANE
    if lane != LANES[kind]:
        raise ValueError('任务须使用其对应队列。H3 镜头统一提交至视频队列。')
    dependencies = spec.get('dependencies', [])
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValueError('依赖须提供队列任务 ID。')
    dependencies = sorted(set(dependencies))
    if kind == 'review-shot':
        if set(inputs) != {'render_id', 'question'} or not isinstance(inputs['question'], str) or not 0 < len(inputs['question']) <= 300:
            raise ValueError('画面审查须提供一条可观察问题和一个渲染任务。')
        render_row = db.execute("SELECT project,kind FROM jobs WHERE id=?", (inputs['render_id'],)).fetchone()
        if render_row is None or tuple(render_row) != (project, 'render'):
            raise ValueError('只能审同一个项目的已登记渲染任务。')
        dependencies = sorted(set(dependencies) | {inputs['render_id']})
    if kind == 'repair-edge':
        required = {'right_render', 'reference_render', 'requirements'}
        if set(inputs) not in (required, required | {'autonomous'}, required | {'pickup_for'}):
            raise ValueError('接点返修须绑定右镜、已入剪参考镜和叙事约束。')
        if (not isinstance(inputs['requirements'], str) or not 0 < len(inputs['requirements'].strip()) <= 600
                or inputs['right_render'] == inputs['reference_render']):
            raise ValueError('接点返修缺少可核对的前史或叙事约束。')
        current = edit_state_path(project)
        if not current.is_file():
            raise ValueError('当前工作剪尚未登记。')
        inputs = {**inputs, 'left_edit_sha256': json.loads(current.read_text())['sha256'],
                  'review_policy': 'edge-repair/v2'}
        for identifier in (inputs['right_render'], inputs['reference_render']):
            render_row = db.execute('SELECT project,kind FROM jobs WHERE id=?', (identifier,)).fetchone()
            if render_row is None or tuple(render_row) != (project, 'render'):
                raise ValueError('接点证据须来自同项目已登记的实际渲染。')
        dependencies = sorted(set(dependencies) | {inputs['right_render'], inputs['reference_render']})
    if kind == 'expert-shot-review':
        inputs = expert_inputs(db, project, inputs)
        dependencies = [inputs['render_id'], inputs['luna_review_id']]
    if kind == 'render':
        from film_identity_lock import bind as bind_identity
        inputs = bind_identity(sys.modules[__name__], project, inputs)
    if kind in {'render', 'story-plan', 'publish-screening'}:
        versions = source_versions(kind, inputs)
        if kind == 'render':
            validate_render_sources(inputs, versions)
        inputs = {**inputs, 'source_versions': versions}
    payload = json_text([project, kind, inputs, dependencies])
    identifier = sha256(payload.encode()).hexdigest()[:24]
    with db:
        if kind == 'expert-shot-review':
            db.execute('BEGIN IMMEDIATE')
        for dep in dependencies:
            row = db.execute('SELECT project FROM jobs WHERE id=?', (dep,)).fetchone()
            if row is None or row['project'] != project:
                raise ValueError('任务依赖必须已经以同一项目提交。')
        if kind == 'expert-shot-review':
            previous = db.execute('''SELECT id FROM jobs WHERE project=? AND kind=? AND
                                      json_extract(inputs,'$.source_sha256')=? AND json_extract(inputs,'$.question')=?''',
                                  (project, kind, inputs['source_sha256'], inputs['question'])).fetchone()
            if previous:
                return previous['id']
        db.execute("""INSERT OR IGNORE INTO jobs
                      (id,project,kind,lane,inputs,dependencies,state,updated_at)
                      VALUES (?,?,?,?,?,?,'queued',?)""",
                   (identifier, project, kind, lane, json_text(inputs), json_text(dependencies), timestamp()))
    return identifier


def claim(db, lane, gpu=None, *, accept_queued=True):
    """Take a job atomically and resume an interrupted render on its original GPU."""
    if (lane == VIDEO_LANE and gpu not in H3_SLOTS) or (lane != VIDEO_LANE and gpu is not None):
        raise ValueError('视频 worker 须绑定已登记的 GPU 0 或 1。')
    with db:
        db.execute('BEGIN IMMEDIATE')
        if lane == VIDEO_LANE:
            rows = db.execute("""SELECT * FROM jobs WHERE lane=? AND
                                 ((state='running' AND assigned_gpu=?) OR
                                  (state='queued' AND assigned_gpu IS NULL AND ?))
                                 ORDER BY state='running' DESC, rowid""",
                              (lane, gpu, accept_queued)).fetchall()
            if rows and rows[0]['state'] == 'running':
                rows = rows[:1]
        else:
            rows = db.execute("""SELECT * FROM jobs WHERE lane=? AND state IN ('running','queued')
                                 ORDER BY state='running' DESC, rowid""", (lane,)).fetchall()
        for row in rows:
            dependencies = json.loads(row['dependencies'])
            states = {dep: db.execute('SELECT state FROM jobs WHERE id=?', (dep,)).fetchone()[0]
                      for dep in dependencies}
            rejected = [dep for dep, state in states.items() if state in {'failed', 'blocked', 'withdrawn'}]
            if rejected:
                db.execute("UPDATE jobs SET state='blocked',error=?,updated_at=? WHERE id=?",
                           ('依赖未通过 ' + ', '.join(rejected), timestamp(), row['id']))
            elif all(state == 'completed' for state in states.values()):
                if row['state'] != 'running':
                    db.execute("UPDATE jobs SET state='running',assigned_gpu=?,updated_at=? WHERE id=?",
                               (gpu, timestamp(), row['id']))
                return db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
    return None


def h3_queue_state(gpu):
    port = H3_SLOTS[gpu]
    try:
        with COMFY_HTTP.open(f'http://127.0.0.1:{port}/queue', timeout=3) as response:
            data = json.load(response)
    except (OSError, URLError, ValueError):
        return 'offline'
    keys = ('queue_running', 'queue_pending')
    if not isinstance(data, dict) or not all(isinstance(data.get(key), list) for key in keys):
        return 'offline'
    return 'busy' if any(data[key] for key in keys) else 'idle'


def personal_file(path):
    source = (WORKSPACE / path).resolve(strict=True)
    if not source.is_relative_to(WORKSPACE) or not source.is_file():
        raise ValueError('输入须位于 DSW 个人工作区。')
    return source


def prompt_argument(inputs):
    present = [key for key in ('prompt', 'shot_contract') if key in inputs]
    if len(present) != 1:
        raise ValueError('H3 任务须明确提供一份渲染提示或结构化拍摄规格。')
    return present[0]


def reference_source(reference):
    if isinstance(reference, str):
        return reference
    if isinstance(reference, dict) and isinstance(reference.get('source'), str):
        return reference['source']
    raise ValueError('参考素材须提供工作区路径或派生参考规格。')


def source_versions(kind, inputs):
    if kind == 'story-plan':
        paths = [inputs['source']]
    elif kind == 'publish-screening':
        paths = [inputs['edition']]
    else:
        paths = [inputs[prompt_argument(inputs)], *(reference_source(ref) for ref in inputs['references']),
                 *inputs.get('voices', [])]
        paths.extend(inputs[key] for key in ('motion', 'direction') if key in inputs)
    return {path: fingerprint(personal_file(path)) for path in paths}


def validate_render_sources(inputs, versions):
    references = inputs['references']
    for item in references:
        if not isinstance(item, dict):
            continue
        digest = versions[reference_source(item)]
        if item.get('sha256') != digest:
            raise ValueError('派生参考的原片指纹与当前文件不一致。')
        if 'frame' in item:
            frame = item['frame']
            if type(frame) is not int or frame < 0:
                raise ValueError('派生参考需要有效的视频源帧号。')
            require_clean_source_window(ROOT, digest, frame, frame + 1)
    seen = set()
    for entry in inputs.get('reference_lineage', []):
        if not isinstance(entry, dict):
            raise ValueError('参考图来源记录无效。')
        index = entry.get('picture')
        digest = entry.get('source_sha256')
        if (type(index) is not int or not 1 <= index <= len(references) or index in seen
                or not isinstance(references[index - 1], str)
                or versions[references[index - 1]] != entry.get('plate_sha256')
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('参考图来源记录与实际上传文件不一致。')
        seen.add(index)
        if 'frame' in entry:
            frame = entry['frame']
            if type(frame) is not int or frame < 0:
                raise ValueError('参考图记录需要有效的视频源帧号。')
            require_clean_source_window(ROOT, digest, frame, frame + 1)
    if motion := inputs.get('motion'):
        require_time_window(ROOT, versions[motion], None, lambda: probe(personal_file(motion))['streams'])


def render_reference(item, out):
    if not isinstance(item, dict):
        return personal_file(item)
    contents, receipt = derive_plate({**item, 'source': str(personal_file(reference_source(item)))},
                                     review_archive=ROOT)
    target = out / (receipt['plate_sha256'] + '.png')
    if not target.is_file():
        temporary = target.with_suffix('.part')
        temporary.write_bytes(contents)
        temporary.replace(target)
    if fingerprint(target) != receipt['plate_sha256']:
        raise ValueError('派生参考成品与确认的指纹不一致。')
    return target


def runtime(lane='video', gpu=None):
    os.environ['PATH'] = str(WORKSPACE / 'morpho-h3' / 'cpu-edit' / 'bin') + ':' + os.environ['PATH']
    os.environ['MORPHO_LOCAL_STORY_GATEWAY'] = '1'
    os.environ['MORPHO_STORY_RUNTIME'] = 'dsw'
    os.environ['ARSENAL_URL'] = 'http://38.98.112.91:8888/comprehensive/search'
    if gpu is not None:
        os.environ['MORPHO_H3_SSH_PORT'] = str(H3_SLOTS[gpu])
        os.environ.pop('MORPHO_H3_SSH_HOST', None)


def configure_text():
    secrets = Path.home() / '.config' / 'nest-labyrinth' / 'litellm.env'
    metadata = secrets.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise PermissionError('DSW 的文本凭据须归个人账号所有且仅个人可读。')
    key = dotenv_values(secrets).get('LITELLM_API_KEY')
    if not key:
        raise ValueError('DSW 缺少 Luna 离线网关凭据。')
    os.environ['LITELLM_API_KEY'] = key


def research(inputs, out):
    from web_research import run_live_web_research

    queries = inputs['queries']
    if (not isinstance(queries, list) or not 1 <= len(queries) <= 4
            or not all(isinstance(q, str) and q.strip() for q in queries)):
        raise ValueError('需显式提供一至四条公开检索词。')
    result = run_live_web_research([{'id': str(i), 'query': q} for i, q in enumerate(queries)],
                                   max_results_per_query=6)
    if result['provider'] != 'company_arsenal':
        raise RuntimeError('公司检索失败 ' + result.get('error', '没有收到有效响应'))
    destination = out / 'research.json'
    write_receipt(destination, result)
    return {'provider': result['provider'], 'records': sum(len(q['results']) for q in result['results']),
            'artifact': str(destination)}


# A case can inform another work only when its observed failure is explicitly shared.
FAILURE_MODES = frozenset({
    'prop-custody', 'lost-action-phase', 'repeated-action-phase', 'body-scale',
    'spatial-axis', 'identity-drift', 'unmotivated-transformation', 'contact-timing',
    'unclear-speaker', 'speech-pacing', 'effect-obscures-action',
    'undersized-face-reference', 'ambient-effect-leakage', 'unmotivated-costume-change',
    'speech-window-truncation', 'duplicated-sound-source',
})


def failure_modes(inputs):
    modes = inputs.get('failure_modes', [])
    if not isinstance(modes, list) or any(not isinstance(mode, str) or mode not in FAILURE_MODES for mode in modes):
        raise ValueError('案例须使用已登记的具体失误类别。')
    return sorted(set(modes))


def relevant_cases(db, project, modes):
    fields = 'c.job_id,c.project,c.observed,c.decision'
    local = db.execute(f'SELECT {fields} FROM cases c WHERE c.project=? ORDER BY c.rowid DESC LIMIT 6',
                       (project,)).fetchall()
    if not modes:
        return local
    placeholders = ','.join('?' for mode in modes)
    transferable = db.execute(f"""SELECT {fields} FROM cases c JOIN case_failure_modes f ON f.job_id=c.job_id
        WHERE c.project<>? AND f.mode IN ({placeholders}) GROUP BY c.job_id
        ORDER BY count(*) DESC,c.rowid DESC LIMIT 3""", (project, *modes)).fetchall()
    return [*local, *transferable]


def record_case(db, row, inputs):
    observed, decision = (inputs[field].strip() for field in ('observed', 'decision'))
    media = [personal_file(item) for item in inputs['evidence']]
    if not observed or not decision or not media:
        raise ValueError('经验记录需有实际观察、编辑决定和素材证据。')
    modes = failure_modes(inputs)
    evidence = [{'path': str(path), 'sha256': fingerprint(path)} for path in media]
    with db:
        db.execute('INSERT OR IGNORE INTO cases VALUES (?,?,?,?,?,?)',
                   (row['id'], row['project'], observed, decision, json_text(evidence), timestamp()))
        db.executemany('INSERT OR IGNORE INTO case_failure_modes VALUES (?,?)',
                       [(row['id'], mode) for mode in modes])
    return {'evidence': evidence, 'case_id': row['id'], 'failure_modes': modes}


def repair_plan(db, row, inputs, out):
    from local_story_gateway import MODEL, generate_json

    configure_text()
    observed = inputs['observed'].strip()
    if not observed:
        raise ValueError('须提供实际观察到的问题。')
    modes = failure_modes(inputs)
    history = relevant_cases(db, row['project'], modes)
    request = json_text({'当前影片': row['project'], '实际问题': observed, '必须保留': inputs['keep'],
                         '失误类别': modes, '编辑记录': [dict(item) for item in history]})
    with film_experience.applying("review", needs=inputs.get("experience_needs", []),
                                  conditions=inputs.get("experience_conditions", [])) as experience:
        result = generate_json(
            '据以下观察给出一次最低影响的定点返修建议。只能提出供编辑审核的方案。'
            '同类案例用于提示该检查什么。外片案例仅迁移判断方式，人物、道具、场地、镜长取自当前影片。'
            '先比对真实切出切入的状态；可选连续区间或改接点时先评估，缺少关键表演才建议局部返拍。'
            '保留对白来源、人物身份及道具状态。不要声称你已经看过或审核视频。'
            '返回三个非空字符串字段 action、keep、verification。'
            + '\n' + request, 4000)
    if not all(isinstance(result.get(field), str) and result[field].strip()
               for field in ('action', 'keep', 'verification')):
        raise ValueError('Luna 没有返回可审核的完整返修方案。')
    destination = out / 'repair-plan.json'
    write_receipt(destination, result)
    return {'model': MODEL, 'artifact': str(destination), 'consulted_cases': [item['job_id'] for item in history], 'experience_sha256': experience['sha256']}


def story_plan(inputs, out):
    from novel_adaptation import read_novel
    from novel_film import generate

    configure_text()
    source = personal_file(inputs['source'])
    body, _ = read_novel(source.read_bytes(), source.name)
    def progress(message, completed, total):
        write_receipt(out / 'progress.json', {'message': message, 'completed': completed, 'total': total})
    source_sha256 = fingerprint(source)

    checkpoint_path = out / 'scene-planning.json'
    cached = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else None
    if cached and cached['source_sha256'] != source_sha256:
        raise ValueError('续跑分场与当前原著版本不一致。')

    def save_scene_planning(checkpoint):
        write_receipt(checkpoint_path, {**checkpoint, 'source_sha256': source_sha256})

    with film_experience.applying('planning', needs=inputs.get('experience_needs', []),
                                  conditions=inputs.get('experience_conditions', [])) as experience:
        package = generate({}, body, inputs['title'], inputs['format'], inputs['options'], progress,
                           feature_editorial=inputs.get('feature_editorial'), story_selection=inputs.get('story_selection'),
                           on_scene_planning=save_scene_planning, known_output_limit=inputs.get('known_output_limit'),
                           cached_scene_planning=cached)
    package['production_experience'] = experience
    destination = out / 'story-plan.json'
    write_receipt(destination, package)
    return {'artifact': str(destination), 'source_sha256': source_sha256,
            'experience_sha256': experience['sha256']}


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, check=True, timeout=30)
    return json.loads(result.stdout)


def picture_crop(clip):
    if 'crop' not in clip:
        return ''
    width, height, x, y = clip['crop']
    if (any(type(value) is not int or value % 2 for value in (width, height, x, y))
            or not (0 <= x < x + width <= 1536 and 0 <= y < y + height <= 864)
            or width * 9 != height * 16):
        raise ValueError('剪辑裁切须为输出画幅内的偶数像素坐标及 16:9 窗口。')
    return f'crop={width}:{height}:{x}:{y},scale=1536:864:flags=lanczos,setsar=1'


def mix_assets(mix):
    music = [(personal_file(item['source']), item['start'], item['duration'], item['source_in'])
             for item in mix['music']]
    effects = [(personal_file(item['source']), item['start'], item['duration'], item['cue'])
               for item in mix['effects']]
    return music, effects


def assemble(inputs, out):
    from film_score import mix_score, voice_windows

    segments = inputs['segments']
    mix_plan = inputs.get('mix')
    if len(segments) < 2:
        raise ValueError('剪接至少需要两个经指定的镜头。')
    paths = [personal_file(clip['source']) for clip in segments]
    hashes = [fingerprint(path) for path in paths]
    if len(set(hashes)) != len(hashes):
        raise ValueError('同一素材重复使用。')
    if hashes != [clip['sha256'] for clip in segments]:
        raise ValueError('剪接素材与指定版本不一致。')
    voice = []
    for clip, digest, path in zip(segments, hashes, paths, strict=True):
        require_time_window(ROOT, digest, clip['cut'], lambda: probe(path)['streams'])
        if mix_plan is not None and type(clip.get('native_dialogue')) is not bool:
            raise ValueError('新版剪辑每镜须声明是否含画内实录对白。')
        cue = clip.get('voiceover')
        if mix_plan is not None and clip['native_dialogue'] != ('dialogue_receipt' in clip):
            raise ValueError('新版剪辑的同期对白须与该镜的原音证据对应。')
        if cue and (mix_plan is None or clip['native_dialogue']):
            raise ValueError('画外音需明确混音计划且不得与同期对白叠放。')
        voice.append((personal_file(cue['source']), cue['delay_ms'], 1) if cue else None)
        if 'dialogue_receipt' in clip:
            receipt = personal_file(clip['dialogue_receipt'])
            lines = json.loads(receipt.read_text())['lines']
            duration = float(probe(path)['format']['duration']) if any(line.get('end_seconds') is None
                                                                      for line in lines) else None
            protect_native_dialogue(clip['cut'], digest, lines, source_duration=duration)
    destination = out / 'draft.mp4'
    durations = []
    with TemporaryDirectory(dir=out) as temporary:
        encoded = []
        for index, (source, clip) in enumerate(zip(paths, segments)):
            target = Path(temporary) / f'{index:04d}.mp4'
            prepare_segment(source, target, 1536, 864, clip['cut'], narration=voice[index],
                            picture_filter=picture_crop(clip), threads=2, normalize_clip=mix_plan is None)
            durations.append(float(probe(target)['format']['duration']))
            encoded.append(target)
        unmixed = Path(temporary) / 'original.mp4' if mix_plan is not None else destination
        concat_segments(encoded, unmixed, durations=durations)
        if mix_plan is not None:
            protected = voice_windows(
                [{'id': index, 'dialogue': '同期声' if clip['native_dialogue'] else ''}
                 for index, clip in enumerate(segments)],
                [(0., length) for length in durations], voice)
            music, effects = mix_assets(mix_plan)
            pending = Path(temporary) / 'mixed.mp4'
            mix_score(unmixed, pending, music, effects, speech_windows=protected)
            pending.replace(destination)
    at = 0.0
    segments_receipt = []
    for clip, duration in zip(segments, durations):
        segments_receipt.append({**clip, 'timeline_in': at, 'timeline_out': at + duration})
        at += duration
    timeline = out / 'timeline.json'
    record = {'media': str(destination), 'segments': segments_receipt}
    if mix_plan is not None:
        record.update(mix=mix_plan, speech_windows=protected)
    write_receipt(timeline, record)
    return {'artifact': str(destination), 'timeline': str(timeline), 'duration': at}


def openfilm(inputs, out):
    from openfilm_export import make_project

    timeline = personal_file(inputs['timeline'])
    data = json.loads(timeline.read_text(encoding='utf-8'))
    media = personal_file(data['media'])
    segments = data['segments']
    choices = [(personal_file(clip['source']), tuple(clip['cut'])) for clip in segments]
    if [fingerprint(path) for path, _ in choices] != [clip['sha256'] for clip in segments]:
        raise ValueError('剪辑工程的原片与审看版本不一致。')
    for (path, cut), clip in zip(choices, segments, strict=True):
        require_time_window(ROOT, clip['sha256'], cut, lambda: probe(path)['streams'])
    shots = [{'id': f'shot-{index:03d}'} for index in range(1, len(choices) + 1)]
    package = {'film': {'source_title': inputs['title']}, 'bible': {}}
    archive = out / 'openfilm.zip'
    with TemporaryDirectory(dir=out) as temporary:
        work = Path(temporary)
        project, duration = make_project(
            package, shots, choices, media, work, picture_filters=[picture_crop(clip) for clip in segments],
            title_card=False, audio_tracks=(
                ([(personal_file(v['source']), v['delay_ms'], 1) if (v := clip.get('voiceover')) else None
                  for clip in segments], *mix_assets(data['mix']))
                if 'mix' in data else ([None] * len(choices), [], [])))
        write_receipt(project / 'editorial-choices.json', {
            'shots': [{'shot_id': shot['id'], 'intent': clip['intent'], 'source_sha256': clip['sha256'],
                       'crop': clip.get('crop')} for shot, clip in zip(shots, segments, strict=True)],
            'review_sha256': fingerprint(media),
        })
        built = Path(shutil.make_archive(str(work / 'edit'), 'zip', project))
        built.replace(archive)
    return {'artifact': str(archive), 'sha256': fingerprint(archive), 'duration': duration}


def cut_evidence(inputs, out):
    source = personal_file(inputs['source'])
    duration = float(probe(source)['format']['duration'])
    cuts = inputs['cuts']
    points = [float(item['at']) for item in cuts]
    if not points or sorted(set(points)) != points or not 0 < points[0] <= points[-1] < duration:
        raise ValueError('实剪切点须在片内且按时间排序。')
    limits = [0.0, *points, duration]
    items = []
    for index, (cut, at) in enumerate(zip(cuts, points), 1):
        path = out / f'cut-{index:03d}.mp4'
        before = extract_cut(source, at, at - limits[index - 1], limits[index + 1] - at, path)
        item = {**cut, 'file': str(path), 'cut_in_sample': before}
        if selection := cut.get('selection'):
            item['eligible_methods'] = editorial_candidates(selection['purpose'], selection['visible_evidence'])
        items.append(item)
    receipt = out / 'cut-evidence.json'
    write_receipt(receipt, {'source': str(source), 'sha256': fingerprint(source),
                           'state': 'pending_review', 'cuts': items})
    return {'artifact': str(receipt), 'clips': len(items), 'review': 'pending_review'}


def dialogue_evidence(inputs, out):
    from verify_dsw_film_dialogue import verify

    source = personal_file(inputs['source'])
    results = [verify(source, cue['text'], cue['in'], cue['out'], None, on_dsw=True)
               for cue in inputs['lines']]
    if not results:
        raise ValueError('须提供存在的原声对白窗口。')
    receipt = out / 'dialogue-evidence.json'
    summary = {'transcripts_exact': all(line['exact'] for line in results),
               'spoken_words_match': all(line['word_match'] != 'mismatch' for line in results),
               'picture_review': 'pending_review'}
    write_receipt(receipt, {'lines': results, **summary})
    return {'artifact': str(receipt), 'lines': len(results), **summary}


def render(inputs, out):
    from film_identity_lock import verify as verify_identity
    from render_h3_pilot import main as render_pilot

    verify_identity(sys.modules[__name__], inputs)

    destination = out / 'shot.mp4'
    receipt = destination.with_suffix('.job.json')
    if not destination.exists():
        validate_render_sources(inputs, source_versions('render', inputs))
        kind = prompt_argument(inputs)
        flag = '--shot-contract' if kind == 'shot_contract' else '--prompt-file'
        arguments = [flag, str(personal_file(inputs[kind])),
                     '--duration', str(inputs['duration']), '--size', inputs['size']]
        for item in inputs['references']:
            arguments += ['--reference', str(render_reference(item, out))]
        for item in inputs.get('voices', []):
            arguments += ['--voice-reference', str(personal_file(item))]
        if 'motion' in inputs:
            arguments += ['--motion', str(personal_file(inputs['motion']))]
        if 'direction' in inputs:
            arguments += ['--direction-plan', str(personal_file(inputs['direction'])),
                          '--direction-shot', str(inputs['direction_shot'])]
        if inputs['quality'] == 'final':
            arguments += ['--final-quality']
        elif inputs['quality'] != 'preview':
            raise ValueError('H3 档位须指定 final 或 preview。')
        render_pilot([*arguments, str(destination)])
    streams = probe(destination)['streams']
    video = next(stream for stream in streams if stream['codec_type'] == 'video')
    if not any(stream['codec_type'] == 'audio' for stream in streams):
        raise ValueError('H3 产物未包含同期声轨道。')
    return {'artifact': str(destination), 'sha256': fingerprint(destination),
            'width': video['width'], 'height': video['height'],
            'comfy_id': json.loads(receipt.read_text())['id'], 'review': 'pending_review',
            **({'identity_lock': inputs['identity_lock']} if 'identity_lock' in inputs else {})}


def expert_inputs(db, project, inputs):
    if set(inputs) != {'luna_review_id', 'focus', 'impact'}:
        raise ValueError('Sol 复查须明确提供 Luna 回执、关键时间范围和交付影响。')
    if inputs['impact'] not in {'identity', 'plot_turn', 'critical_continuity'}:
        raise ValueError('仅影响身份、剧情转折或关键连续性的困难问题可升级。')
    prior = db.execute("SELECT result FROM jobs WHERE id=? AND project=? AND kind='review-shot' AND state='completed'",
                       (inputs['luna_review_id'], project)).fetchone()
    if prior is None:
        raise ValueError('缺少已完成的同项目 Luna 审查。')
    result = json.loads(prior['result'])
    if result['finding'] != 'uncertain':
        raise ValueError('只有证据仍不明确的 Luna 结论才可提交 Sol。')
    current = edit_state_path(project)
    if not current.is_file() or not any(
            item['source_sha256'] == result['sha256'] for item in json.loads(current.read_text())['inserts']):
        raise ValueError('Sol 复查只用于当前工作剪的关键镜头。')
    focus = inputs['focus']
    if (not isinstance(focus, list) or len(focus) != 2 or not all(type(t) in {int, float} for t in focus)
            or not 0 <= focus[0] < focus[1] <= 60 or not 0.25 <= focus[1] - focus[0] <= 2):
        raise ValueError('Sol 须明确指向包含额外可见证据的短时间范围。')
    receipt = json.loads(personal_file(result['artifact']).read_text())
    return {**inputs, 'render_id': result['render_id'], 'question': receipt['question'],
            'source_sha256': result['sha256']}


def review_shot(db, inputs, out):
    from local_story_gateway import MODEL, SOL_MODEL, generate_json

    row = db.execute('SELECT result FROM jobs WHERE id=?', (inputs['render_id'],)).fetchone()
    rendered = json.loads(row['result'])
    source = personal_file(rendered['artifact'])
    digest = fingerprint(source)
    if digest != rendered['sha256']:
        raise ValueError('回片与队列登记的版本不一致。')
    metadata = probe(source)
    duration = float(metadata['format']['duration'])
    if not 0.5 <= duration <= 60:
        raise ValueError('此审查工具仅接受单镜回片。')
    method = sampling.active_method(review_tool_profile(), metadata)
    expert = 'luna_review_id' in inputs
    model = SOL_MODEL if expert else MODEL
    if expert and (out / 'sol-dispatch.json').is_file():
        previous_report = json.loads((out / 'shot-review.json').read_text())
        if previous_report['state'] == 'sampled_review' and previous_report['sha256'] == digest:
            return {'artifact': str(out / 'shot-review.json'), 'sha256': digest, 'render_id': inputs['render_id'],
                    'review': 'sampled_review', 'finding': previous_report['finding']}
        raise RuntimeError('已有一次 Sol 请求登记。结果待核对，未再次请求。')
    if expert:
        start, end = inputs['focus']
        current = edit_state_path(db.execute('SELECT project FROM jobs WHERE id=?', (inputs['render_id'],)).fetchone()[0])
        if (digest != inputs['source_sha256'] or end >= duration or not current.is_file()
                or not any(item['source_sha256'] == digest for item in json.loads(current.read_text())['inserts'])):
            raise ValueError('Sol 复查所需的关键源片或剪辑版本已变化。')
        previous = db.execute('SELECT result FROM jobs WHERE id=?', (inputs['luna_review_id'],)).fetchone()
        earlier = json.loads(personal_file(json.loads(previous['result'])['artifact']).read_text())['samples']
        previous_times = [sample['seconds'] for sample in earlier]
        times = [round(start + (end - start) * step / 5, 3) for step in range(6)]
        if not any(all(abs(timecode - existing) > 0.045 for existing in previous_times) for timecode in times):
            raise ValueError('Sol 复查区间没有新增可见证据。')
        captures = []
        for number, timecode in enumerate(times, 1):
            target = out / f'frame-{number:02d}.jpg'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-ss',str(timecode),'-i',str(source),
                            '-map','0:v:0','-vf',sampling.FILTER,'-frames:v','1','-threads','1','-q:v','3','-y',str(target)],
                           capture_output=True, check=True, timeout=12)
            captures.append((timecode, target.read_bytes()))
        method = 'focused_seek'
    else:
        captures = sampling.samples(source, duration, out, method)
    sample_times = [timecode for timecode, _ in captures]
    samples = [{'frame': number, 'seconds': timecode, 'sha256': sha256(payload).hexdigest()}
               for number, (timecode, payload) in enumerate(captures, 1)]
    encoded = [b64encode(payload).decode('ascii') for _, payload in captures]
    receipt = out / 'shot-review.json'
    evidence = {'render_id': inputs['render_id'], 'sha256': digest, 'model': model,
                'samples': samples, 'question': inputs['question'], 'state': 'samples_ready', 'sampling_method': method}
    write_receipt(receipt, evidence)
    configure_text()
    if expert:
        dispatch_log = out / 'sol-dispatch.json'
        with dispatch_log.open('x', encoding='utf-8') as handle:
            json.dump({'source_sha256': digest, 'requested_at': timestamp()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
    with film_experience.applying('review'):
        result = generate_json(
            '按编号检查实际回片，不预设出现了任何结果。图片按时间顺序，编号从1开始。'
            '先写至少两个可见事实，写清每个镜头中的手、道具和位置如何变化。再回答问题。'
            '结论只选 supported、contradicted、uncertain。仅在画面明确时作出判断。'
            '这是静帧抽验，不评口型、对白或帧间连续运动。给出最小可验证下一步。'
            '输出 {"observations":[{"frame":1,"visible":"实际画面"}],'
            '"finding":"supported|contradicted|uncertain","next_check":"动作与需验证的现象"}。'
            + json_text({'question': inputs['question'], 'timestamps_seconds': sample_times}),
            3600, images=encoded, model=model)
    seen = result.get('observations')
    if (not isinstance(seen, list) or not 2 <= len(seen) <= len(samples)
            or any(not isinstance(item, dict) or type(item.get('frame')) is not int
                   or not 1 <= item['frame'] <= len(samples) or not isinstance(item.get('visible'), str)
                   or not item['visible'].strip() or len(item['visible']) > 400 for item in seen)
            or len({item['frame'] for item in seen}) != len(seen)
            or result.get('finding') not in {'supported', 'contradicted', 'uncertain'}
            or not isinstance(result.get('next_check'), str) or not 0 < len(result['next_check'].strip()) <= 500):
        raise ValueError('画面审查没有返回可核对的取样证据。')
    observations = [{**item, 'seconds': sample_times[item['frame'] - 1]} for item in seen]
    write_receipt(receipt, {**evidence, 'state': 'sampled_review', 'observations': observations,
                           'finding': result['finding'], 'next_check': result['next_check']})
    return {'artifact': str(receipt), 'sha256': digest, 'render_id': inputs['render_id'],
            'review': 'sampled_review', 'finding': result['finding']}


def repair_edge(db, row, inputs, out):
    from film_edge_repair import assess
    from h3_prompt import compile_reference_prompt
    from local_story_gateway import StoryOutputLimit, generate_json

    edit = json.loads(edit_state_path(row['project']).read_text())
    left_path = personal_file(edit['media'])
    if edit['sha256'] != inputs['left_edit_sha256'] or fingerprint(left_path) != edit['sha256']:
        raise ValueError('工作剪已变化，须对实际最新版核查接点。')

    def rendered(identifier):
        data = db.execute('SELECT result FROM jobs WHERE id=?', (identifier,)).fetchone()
        result = json.loads(data['result'])
        path = personal_file(result['artifact'])
        if fingerprint(path) != result['sha256']:
            raise ValueError('渲染素材的实际指纹不一致。')
        return {**result, 'path': path}

    def inspect(path):
        stream = next(item for item in probe(path)['streams'] if item['codec_type'] == 'video')
        if stream['avg_frame_rate'] != '24/1' or (stream['width'], stream['height']) != (1536, 864):
            raise ValueError('本版实剪操作需要固定24帧且原生864p的视频。')
        return {'frames': int(stream['nb_frames'])}

    reference = rendered(inputs['reference_render'])
    matches = [item for item in edit['inserts'] if item['source_sha256'] == reference['sha256']]
    if len(matches) != 1:
        raise ValueError('参考镜须在当前工作剪实际使用一次。')
    reference_cut, = matches
    first, last = reference_cut['source_frames']
    at = reference_cut['timeline_frame']
    earlier = {'sha256': reference['sha256'], 'timeline_frames': [at, at + last - first]}
    right = rendered(inputs['right_render'])
    if right['sha256'] in {item['source_sha256'] for item in edit['inserts']}:
        raise ValueError('同一源片已在当前工作剪使用。')
    right.update(inspect(right['path']), requirements=inputs['requirements'])
    left = {'path': left_path, 'frames': edit['frames'], 'sha256': edit['sha256']}
    if inspect(left_path)['frames'] != left['frames']:
        raise ValueError('登记工作剪帧数与实际文件不一致。')
    configure_text()
    def decision(prompt, pictures):
        try:
            return generate_json(prompt + '\\n观察与理由字段合计不超过150字。', 5600, images=pictures)
        except StoryOutputLimit:
            return generate_json(prompt + '\\n仅返回所需的最小 JSON；证据只写一个句子。', 7200,
                                 images=pictures)

    with film_experience.applying('review'):
        receipt = out / 'edge-repair.json'
        pickup = out / 'pickup-contract.json'
        saved = json.loads(receipt.read_text()) if receipt.is_file() and pickup.is_file() else None
        if saved is not None and saved['left_sha256'] == left['sha256'] and saved['right_sha256'] == right['sha256']:
            verdict = saved
        else:
            verdict = assess(left, earlier, right, out, decision, inspect,
                             try_alternative=inputs.get('autonomous') is True)
        if verdict['state'] == 'held' and inputs.get('autonomous') is True:
            parent = db.execute("SELECT inputs FROM jobs WHERE id=?", (inputs['right_render'],)).fetchone()
            render_inputs = json.loads(parent['inputs'])
            if 'shot_contract' not in render_inputs:
                return {'state':'held','artifact': str(receipt)}
            original = personal_file(render_inputs['shot_contract'])
            contract = json.loads((pickup if pickup.is_file() else original).read_text())
            if not contract.get('speech'):
                if not pickup.is_file():
                    revised = generate_json(
                        '为不可用的接点提出一条非对白补镜。同一次表演，保留人物、动作目的、光线、时段及场地。'
                        '只决定拍摄角度和构图范围。开拍前说明已知物件实际在画内还是画外。'
                        '一镜一个角度，保留可见动作阶段和稳定落点。输出 JSON，只有英语短句字段 opening 和 camera。'
                        + json_text({'failure': verdict['proposal']['observed'], 'existing': {
                            key: contract[key] for key in ('opening','action','camera','outcome')},
                            'verification': verdict.get('verification'), 'requirements': inputs['requirements']}), 3500)
                    if (set(revised) != {'opening','camera'} or any(
                        not isinstance(value, str) or not 0 < len(value.strip()) <= 500 or '\ufffd' in value
                        for value in revised.values())):
                        raise ValueError('补拍只接受短小的开拍状态和摄影机规格。')
                    contract.update(revised)
                    compile_reference_prompt(contract, render_inputs['duration'], len(render_inputs['references']),
                                             len(render_inputs.get('voices', [])), 'motion' in render_inputs)
                    write_receipt(pickup, contract)
                render_inputs['shot_contract'] = str(pickup)
                next_render = submit(db, {'project':row['project'], 'kind':'render', 'inputs':render_inputs})
                next_review = submit(db, {'project':row['project'], 'kind':'repair-edge',
                    'inputs':{'right_render':next_render, 'reference_render':inputs['reference_render'],
                              'requirements':inputs['requirements'], 'pickup_for':row['id']}})
                verdict.update(state='pickup_queued', pickup_render=next_render, pickup_review=next_review)
                write_receipt(out / 'edge-repair.json', verdict)
    return {key: verdict[key] for key in ('state', 'candidate', 'candidate_sha256',
            'pickup_render', 'pickup_review') if key in verdict} | {'artifact': str(out / 'edge-repair.json')}


def review_tool_profile():
    return ROOT / 'tool-reviews' / 'active-review-sampling.json'


def tune_review_tool(db, identifiers):
    from local_story_gateway import MODEL, generate_json

    if not 3 <= len(set(identifiers)) == len(identifiers) <= 6:
        raise ValueError('工具改进需要三至六个不同的实片任务。')
    examples = []
    for identifier in identifiers:
        row = db.execute("SELECT project,result FROM jobs WHERE id=? AND kind='render' AND state='completed'",
                         (identifier,)).fetchone()
        if row is None:
            raise ValueError('工具回归须使用已回传的真实镜头。')
        result = json.loads(row['result'])
        source = personal_file(result['artifact'])
        metadata = probe(source)
        if fingerprint(source) != result['sha256'] or not sampling.scope(metadata):
            raise ValueError('样例版本或媒体范围不匹配。')
        examples.append((row['project'], source, result['sha256'], float(metadata['format']['duration'])))
    if len({project for project, *_ in examples}) < 2:
        raise ValueError('工具改进须用不同项目检验。')
    active = review_tool_profile()
    active.parent.mkdir(parents=True, exist_ok=True)
    with active.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configure_text()
        with film_experience.applying('review'):
            decision = generate_json(
                '你可以提出版本化的实片抽帧工具修改。当前 individual_seek 会为每个时点启动一个 FFmpeg。'
                '候选 select_in_one_decode 读取画面时间戳、按原时点选帧，再用单次解码输出。'
                '选择是否实测候选。必须保留12帧、同一时点、同一分辨率和同一编码。'
                '发布前需跨作品CPU计时并逐张比较字节。输出 JSON 字段 method 与 reason。'
                + json_text({'allowed_methods':[sampling.BASELINE,sampling.CANDIDATE],
                             'examples':[{'project':p,'duration_seconds':d} for p,_,_,d in examples]}), 1500)
        proposed = decision.get('method')
        reason = decision.get('reason')
        if (proposed not in {sampling.BASELINE, sampling.CANDIDATE}
                or not isinstance(reason, str) or not 0 < len(reason.strip()) <= 600):
            raise ValueError('没有得到可执行的抽帧工具修改。')
        results = []
        if proposed == sampling.CANDIDATE:
            for project, source, digest, duration in examples:
                results.append({'project':project, 'source_sha256':digest,
                                **sampling.compare(source, duration, active.parent)})
        promoted = sampling.qualified(results)
        receipt = {'model':MODEL, 'method':proposed, 'signature':sampling.profile_signature(),
                   'reason':reason, 'state':'promoted' if promoted else 'retained',
                   'cases':results, 'updated_at':timestamp()}
        write_receipt(active.with_name('last-review-sampling-trial.json'), receipt)
        if promoted:
            write_receipt(active, receipt)
        return receipt


def edit_state_path(project):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', project):
        raise ValueError('项目标识格式无效。')
    return ROOT / 'active-edits' / project / 'working-edit.json'


def register_edit(db, project, media, manifest):
    destination = edit_state_path(project)
    if db.execute('SELECT 1 FROM jobs WHERE project=? LIMIT 1', (project,)).fetchone() is None:
        raise ValueError('项目未在制作队列登记。')
    video = personal_file(media)
    manifest = personal_file(manifest)
    edit = json.loads(manifest.read_text(encoding='utf-8'))
    if fingerprint(video) != edit['sha256']:
        raise ValueError('剪辑说明与成片文件版本不一致。')
    streams = probe(video)['streams']
    picture = next(stream for stream in streams if stream['codec_type'] == 'video')
    frames = int(picture['nb_frames'])
    if edit['frames'] != frames:
        raise ValueError('剪辑说明与实际成片帧数不一致。')
    cuts = []
    for cut in edit['cuts']:
        start, end = cut['source_frames']
        at = cut['timeline_frame']
        if (not all(type(n) is int for n in (start, end, at)) or not 0 <= start < end
                or not 0 <= at < at + end - start <= frames
                or not re.fullmatch(r'[a-f0-9]{64}', cut['source_sha256'])
                or (cuts and cuts[-1]['timeline_frame'] + cuts[-1]['source_frames'][1]
                    - cuts[-1]['source_frames'][0] > at)):
            raise ValueError('剪辑说明有重叠、越界或未确认的源片段。')
        cuts.append({key: cut[key] for key in
                     ('source_sha256', 'timeline_frame', 'source_frames', 'reason')})
    record = {'state': 'working_cut', 'media': str(video), 'sha256': edit['sha256'],
              'frames': frames, 'fps': picture['avg_frame_rate'],
              'base_sha256': edit['base_sha256'], 'inserts': cuts,
              'updated_at': timestamp()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        write_receipt(destination, record)
    return record


def project_state(db, project):
    current_path = edit_state_path(project)
    active = json.loads(current_path.read_text(encoding='utf-8')) if current_path.is_file() else None
    used = {cut['source_sha256']: cut for cut in active['inserts']} if active else {}
    rows = db.execute('SELECT * FROM jobs WHERE project=? ORDER BY rowid DESC', (project,)).fetchall()
    counts = {}
    shot_reviews = {}
    for row in rows:
        counts[row['state']] = counts.get(row['state'], 0) + 1
        if row['kind'] in {'review-shot', 'expert-shot-review'}:
            target = json.loads(row['inputs'])['render_id']
            shot_reviews.setdefault(target, []).append(state_fields(row))
    shots = []
    for row in rows:
        if row['kind'] == 'render':
            item = state_fields(row)
            result = item['result'] or {}
            item['shot_reviews'] = [review for review in shot_reviews.get(row['id'], [])
                                    if review['state'] == 'completed' and result.get('sha256')
                                    and (review['result'] or {}).get('sha256') == result['sha256']]
            item['used_in_current_edit'] = used.get(result.get('sha256'))
            shots.append(item)
    return {'project': project, 'job_counts': counts, 'current_edit': active, 'shots': shots,
            'recent_tasks': [state_fields(row) for row in rows[:20]]}


def publish_screening(inputs, out):
    from local_story_gateway import generate_json
    from publish_film_screening import publish

    configure_text()
    edition = personal_file(inputs['edition'])
    folder = ROOT / 'studio-data' / 'screenings'
    if edition.name != 'edition.json' or edition.parent.parent != folder:
        raise ValueError('成片发布只接受制作台的核定成片记录。')
    data = publish(edition.parent, json.loads(edition.read_text(encoding='utf-8')), generate_json)
    receipt = out / 'screening.json'
    write_receipt(receipt, data)
    return {'artifact': str(receipt), 'slug': data['slug'], 'duration': data['duration']}


def execute(db, row, inputs, out):
    if 'source_versions' in inputs and inputs['source_versions'] != source_versions(row['kind'], inputs):
        raise ValueError('任务输入在排队后改变。请按最新素材提交明确的新版本。')
    if row['kind'] == 'record-case':
        return record_case(db, row, inputs)
    if row['kind'] == 'repair-plan':
        return repair_plan(db, row, inputs, out)
    if row['kind'] == 'repair-edge':
        return repair_edge(db, row, inputs, out)
    if row['kind'] in {'review-shot', 'expert-shot-review'}:
        return review_shot(db, inputs, out)
    actions = {'research': research, 'story-plan': story_plan, 'assemble': assemble,
               'cut-evidence': cut_evidence, 'dialogue-evidence': dialogue_evidence,
               'openfilm': openfilm, 'render': render, 'publish-screening': publish_screening}
    return actions[row['kind']](inputs, out)


def work(db, lane, once, *, gpu=None):
    from local_story_gateway import StoryPolicyBlocked

    runtime(lane, gpu)
    lock_name = f'{lane}-gpu{gpu}.lock' if gpu is not None else f'{lane}.lock'
    with (ROOT / lock_name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            service = h3_queue_state(gpu) if gpu is not None else 'idle'
            row = claim(db, lane, gpu, accept_queued=service == 'idle') if service != 'offline' else None
            if row is not None:
                out = ROOT / 'artifacts' / row['project'] / row['id']
                try:
                    out.mkdir(parents=True, exist_ok=True)
                    result = execute(db, row, json.loads(row['inputs']), out)
                    if gpu is not None:
                        result['gpu'] = gpu
                    with db:
                        db.execute("UPDATE jobs SET state='completed',result=?,error=NULL,updated_at=? WHERE id=?",
                                   (json_text(result), timestamp(), row['id']))
                except StoryPolicyBlocked as exc:
                    checkpoint = out / 'scene-planning.json'
                    result = {'trace_id': exc.trace_id, 'signal': exc.signal, 'planning': exc.checkpoint}
                    if checkpoint.is_file():
                        result['artifact'] = str(checkpoint)
                    with db:
                        db.execute("UPDATE jobs SET state='blocked',result=?,error=?,updated_at=? WHERE id=?",
                                   (json_text(result), str(exc), timestamp(), row['id']))
                except (Exception, SystemExit) as exc:
                    detail = f'{type(exc).__name__}: {exc}'[:600]
                    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                        log = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr
                        detail = log.strip()[-600:]
                    with db:
                        db.execute("UPDATE jobs SET state='failed',error=?,updated_at=? WHERE id=?",
                                   (detail, timestamp(), row['id']))
            if once:
                return
            if row is None:
                time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('submit', help='Read a task JSON object from stdin.')
    recover = commands.add_parser('recover-story', help='Resume a story plan interrupted by output length.')
    recover.add_argument('id')
    tune = commands.add_parser('tune-review-tool')
    tune.add_argument('render_ids', nargs='+')
    ingest = commands.add_parser('register-edit')
    ingest.add_argument('project')
    ingest.add_argument('media')
    ingest.add_argument('manifest')
    project = commands.add_parser('project-state')
    project.add_argument('project')
    status = commands.add_parser('status')
    status.add_argument('id', nargs='?')
    commands.add_parser('gpu-status', help='Show the two H3 queue slots.')
    memory = commands.add_parser('experience', help='Read active experience and select reviewed methods.')
    memory.add_argument('stage', choices=sorted(film_experience.STAGES))
    memory.add_argument('--need', action='append', default=[])
    memory.add_argument('--condition', action='append', default=[])
    memory.add_argument('--project')
    memory.add_argument('--failure-mode', action='append', default=[])
    worker = commands.add_parser('work')
    worker.add_argument('lane', choices=sorted(set(LANES.values())))
    worker.add_argument('--gpu', type=int, choices=sorted(H3_SLOTS))
    worker.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.command == 'work' and (args.lane == VIDEO_LANE) != (args.gpu is not None):
        parser.error('视频 worker 需要 --gpu 0 或 --gpu 1，其他 worker 不使用显卡。')
    if ROOT.resolve(strict=True) != ROOT or ROOT.stat().st_uid != os.getuid():
        raise PermissionError('Agent 须由 DSW 个人账号在其实际工作区启动。')
    os.umask(0o077)
    with closing(connection()) as db:
        if args.command == 'submit':
            runtime()
            print(submit(db, json.load(sys.stdin)))
        elif args.command == 'recover-story':
            row = db.execute('SELECT * FROM jobs WHERE id=?', (args.id,)).fetchone()
            if not row or row['kind'] != 'story-plan' or row['state'] != 'failed' or '（length）' not in (row['error'] or ''):
                parser.error('只接受已确认因文本输出过长而失败的台本任务。')
            inputs = json.loads(row['inputs'])
            if inputs.get('recovery_of'):
                parser.error('台本已用过整单恢复，应查看分场回执。')
            if source_versions('story-plan', inputs) != inputs.get('source_versions'):
                parser.error('原著版本已变化。')
            checkpoint = json.loads((ROOT / 'artifacts' / row['project'] / row['id'] / 'progress.json').read_text())
            if checkpoint.get('message') != '正在梳理全书情节' or type(checkpoint.get('completed')) is not int:
                parser.error('旧任务没有可核对的原文提取断点。')
            start = {'stage': f"source_events:{checkpoint['completed'] + 1}",
                     'trace_id': 'historical-job:' + row['id'], 'output_budget': 5500}
            source = dict(inputs, recovery_of=row['id'], known_output_limit=start)
            print(submit(db, {'project': row['project'], 'kind': 'story-plan', 'inputs': source,
                              'dependencies': json.loads(row['dependencies'])}))
        elif args.command == 'tune-review-tool':
            runtime('picture')
            print(json.dumps(tune_review_tool(db, args.render_ids), ensure_ascii=False, indent=2))
        elif args.command == 'register-edit':
            print(json.dumps(register_edit(db, args.project, args.media, args.manifest),
                             ensure_ascii=False, indent=2))
        elif args.command == 'project-state':
            print(json.dumps(project_state(db, args.project), ensure_ascii=False, indent=2))
        elif args.command == 'status':
            rows = (db.execute('SELECT * FROM jobs WHERE id=?', (args.id,)) if args.id else
                    db.execute('SELECT * FROM jobs ORDER BY rowid'))
            print(json.dumps([state_fields(row) for row in rows], ensure_ascii=False, indent=2))
        elif args.command == 'experience':
            with film_experience.applying(args.stage, needs=args.need, conditions=args.condition) as data:
                result = {**data, **film_experience.inventory(),
                          'production_cases': db.execute('SELECT count(*) FROM cases').fetchone()[0]}
                if args.project:
                    modes = failure_modes({'failure_modes':args.failure_mode})
                    result['related_cases'] = [dict(row) for row in relevant_cases(db,args.project,modes)]
                print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.command == 'gpu-status':
            rows = db.execute("SELECT id, assigned_gpu FROM jobs WHERE lane='video' AND state='running'").fetchall()
            active = {row['assigned_gpu']: row['id'] for row in rows}
            queued = db.execute("SELECT count(*) FROM jobs WHERE lane='video' AND state='queued'").fetchone()[0]
            slots = [{'gpu': gpu, 'port': port, 'comfy': h3_queue_state(gpu), 'job': active.get(gpu)}
                     for gpu, port in H3_SLOTS.items()]
            print(json.dumps({'queued': queued, 'slots': slots}, indent=2))
        else:
            work(db, args.lane, args.once, gpu=args.gpu)


if __name__ == '__main__':
    main()
