"""Render a versioned novel film with H3, check the footage, and cut accepted takes."""
import base64
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from flask import g, has_app_context
from redis.exceptions import RedisError
from rq import get_current_job

from generation_queue import get_generation_queue, get_redis_connection
import requests

from model_providers import CATALOG
import video_providers as providers
from video_prompts import AESTHETIC_DIRECTION, MATERIAL_CONTINUITY
from continuity_prompt_policy import LOCAL_RENDER_CONTINUITY
from shot_visual_style import NIGHT_JADE_GUIDE, generation_direction
import video_workflow as workflow
from film_speech import NATURAL_SPEECH, mandarin_word_match
import rented_h3
import openfilm_export
from film_cut_methods import require_distinct_source_windows
from film_edges import CutRejected, check_exported_cuts, validate_distinct_cut_plans
import film_experience
from film_score import mix_score, music_timeline, sound_timeline, voice_windows
from media_segments import fingerprint
from remote_media import generate_sound, synthesize_speech, transcribe_speech

H3 = {'provider': 'minimax', 'model': 'MiniMax-H3'}
POLL_SECONDS = 12
MAX_TAKES = 2
JUDGE_TIMEOUT = 90
MAX_POLL_ERRORS = 3
MAX_MODEL_REVIEW_SHOTS = 3
PENDING_TAKE_STATES = {'submitting', 'submission_unknown', 'queued', 'running'}
BOUNDARY_DEPENDENCIES = {'action-match', 'foreground-handoff', 'registered-restyle', 'graphic-match', 'occlusion'}


class InvalidTake(ValueError):
    pass


class ReviewExhausted(ValueError):
    pass


class TakeFailed(ValueError):
    pass


class ReceiptTransportError(RuntimeError):
    pass


def enqueue(job, key, judge_key, *, resume=False, sound_workspace=''):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('制作短片需要 FFmpeg 和 FFprobe。')
    if job['config']['provider'] == 'rented_h3':
        base = rented_h3.ensure_tunnel()
        if not rented_h3.ready(base, motion=bool(job.get('motion_references')),
                               voice_count=int(bool(job.get('voice_references')))):
            raise ValueError('DSW 还未加载制作所需的 H3 权重。')
    openfilm_export.check_ready()
    if not key:
        use = '参考图' if job['config']['provider'] == 'rented_h3' else '视频和参考图'
        raise ValueError(f'请填写用于生成{use}的 MiniMax 密钥。')
    if not judge_key:
        raise ValueError('请填写用于配音和审片的百炼密钥。')
    package = job['film_snapshot']
    validate_distinct_cut_plans([shot for scene in package['scenes'] for shot in scene['shots']])
    if any(scene.get('sound_effects') for scene in package['scenes']) and not sound_workspace:
        raise ValueError('请在模型设置填写百炼 Workspace ID，用于独立生成空间音效。')
    assets = {asset['id']: asset for asset in package['bible']['assets']}
    for scene in package['scenes']:
        for index, shot in enumerate(scene['shots'], 1):
            if len(take_prompt(shot, assets)) > 4400:
                raise ValueError(f'{scene["title"]}第 {index} 镜的提示词过长，请合计压缩至 4400 字以内。')
    if resume:
        workflow.claim_film_phase(job)
    else:
        workflow.put(job, insert=True)
    secret_id = 'morpho:film:' + job['id']
    try:
        redis = get_redis_connection()
        redis.setex(secret_id, 7200, json.dumps({'video': key, 'judge': judge_key, 'sound_workspace': sound_workspace}))
        user_id, project_id = workflow.identity()
        get_generation_queue().enqueue('film_render.run', user_id, project_id, job['id'],
                                       job_id=str(uuid.uuid4()), job_timeout=43200, failure_ttl=86400,
                                       on_failure='film_render.mark_worker_failure')
    except (RedisError, ValueError) as exc:
        job.update(status='failed', error='短片无法加入制作队列。')
        workflow.put(job)
        raise RuntimeError(job['error']) from exc


def art(asset, style, directory, key, library, correction=''):
    identity = json.dumps(['MiniMax-H3-Image', style, {k: v for k, v in asset.items() if k != 'id'}],
                          sort_keys=True, ensure_ascii=False).encode()
    filename = hashlib.sha256(identity).hexdigest() + '.jpg'
    revision = hashlib.sha256(correction[:500].encode()).hexdigest()[:12] + '-' if correction else ''
    path = directory / (revision + filename)
    if not path.exists():
        stored = library / filename
        if not correction and stored.is_file():
            shutil.copyfile(stored, path)
            return path
        prompt = style + '\n' + asset['image_prompt']
        if 'signature' in asset and 'material_logic' in asset:
            prompt += ('\n固定辨识点：' + asset['signature'] + '\n材质和受光：' + asset['material_logic']
                       + '\n单主体，保持所选媒介的比例、尺度和光影逻辑。无拼贴，无文字。' + AESTHETIC_DIRECTION)
        if correction:
            prompt += '\n只修正以下问题，保留既定设计：' + correction[:500]
        result = providers.request_json(
            'POST', 'https://api.minimax.io/v1/images/generations',
            {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            {'model': 'MiniMax-H3-Image', 'prompt': prompt,
             'aspect_ratio': '1:1', 'area': '1K', 'n': 1, 'output_format': 'jpeg',
             'response_format': 'b64_json', 'disable_prompt_enhanced': True}, read_timeout=240)
        try:
            raw = base64.b64decode(result['data'][0]['b64_json'], validate=True)
            workflow.frame_value('data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii'))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"参考图未生成：{asset['name']}。") from exc
        temporary = path.with_suffix('.part')
        try:
            temporary.write_bytes(raw)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def image_data(path):
    return 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def take_prompt(shot, assets):
    treatment = shot.get('visual_treatment')
    roles = {
        'character': '只固定角色轮廓、脸、发式和服装状态，人物站位与场景按本镜',
        'location': '只固定地标、布局和光源方向，人物与手持物按本镜',
        'prop': '只固定道具外形、材质和尺度，持有者与位置按本镜',
    }
    if treatment:
        roles = {
            'character': '只固定身份、发式和服装裁剪，场景、站位与重照服从本镜',
            'location': '只固定地标、空间布局和光源动机，采用本镜曝光与人物调度',
            'prop': '只固定外形和尺度，归属、位置与材质受光服从本镜',
        }
    references = []
    for number, ref in enumerate(shot['reference_ids'], 1):
        asset = assets[ref]
        references.append(f'<Picture {number}> {asset["name"]}，{roles[asset["kind"]]}。')
    def shot_local(value):
        # Archived plans can also carry the whole-film progression in the image prompt.
        sections = value.splitlines()
        return '\n'.join(
            NIGHT_JADE_GUIDE if line.startswith('全片采用“夜玉金箔”造型') else line
            for line in sections if not line.startswith(('全片色彩规则：', '全片配色：'))
        ).strip()

    prompt = shot_local(shot['video_prompt'])
    opening = shot_local(shot.get('image_prompt', ''))
    if opening and opening not in prompt:
        prompt += '\n以这一版开场构图为准：' + opening
    if treatment:
        lines = prompt.splitlines()
        for index, line in enumerate(lines):
            if line.startswith('本镜造型：'):
                lines[index] = '本镜造型：' + generation_direction(treatment['id'])
                break
        else:
            lines.append('本镜造型：' + generation_direction(treatment['id']))
        prompt = '\n'.join(lines)
    local_materials = (LOCAL_RENDER_CONTINUITY if (shot.get('screenplay') or {}).get('anchor')
                       else MATERIAL_CONTINUITY)
    if local_materials not in prompt:
        prompt += '\n' + local_materials
    prompt += '\n信件、碑刻、卷轴若需要清晰汉字，给后期留空并维持纸面或石面的透视。'
    prompt += '画面上不要生成可读字、伪字、印刷标题、水印和字幕。'
    if not shot.get('dialogue', '').strip():
        prompt += '\n本镜只做无言动作和反应，保留自然呼吸。画面可见的人没有说话动作。'
    prompt += '\n同期只保留镜内已指定声源。旁白、主题配乐及指定后期音效另轨制作。'
    return prompt + '\n参考图用途（按上传顺序）：\n' + '\n'.join(references)


def judge(content, key):
    # A subprocess deadline also bounds a response that keeps sending partial data.
    try:
        worker = subprocess.run(
            [sys.executable, '-c', 'from film_render import judge_worker; judge_worker()'],
            input=json.dumps({'content': content, 'key': key}, ensure_ascii=False), encoding='utf-8',
            cwd=Path(__file__).resolve().parent, capture_output=True, check=True, timeout=JUDGE_TIMEOUT)
        result = json.loads(worker.stdout)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('审片超过 90 秒，已停止等待。已下载素材保留，可恢复审片。') from exc
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError('审片进程异常结束，已停止制作。') from exc
    if 'error' in result:
        raise ValueError(result['error'])
    return result['verdict']


def judge_worker():
    request = json.load(sys.stdin)
    try:
        result = {'verdict': request_judgment(request['content'], request['key'])}
    except ValueError as exc:
        result = {'error': str(exc)}
    print(json.dumps(result, ensure_ascii=True))


def request_judgment(content, key):
    payload = {'model': 'qwen3.8-omni-flash', 'messages': [{'role': 'user', 'content': content}],
               'modalities': ['text'], 'stream': True, 'reasoning_effort': 'none', 'max_tokens': 1400}
    parts = []
    stop = None
    try:
        with requests.post(CATALOG['qwen']['endpoint'], headers={'Authorization': 'Bearer ' + key},
                           json=payload, timeout=(10, 65), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError('百炼无法审片，请核对密钥及 Qwen Omni 调用权限。')
            for line in response.iter_lines():
                if not line.startswith(b'data: '):
                    continue
                data = line[6:]
                if data == b'[DONE]':
                    break
                for choice in json.loads(data).get('choices', []):
                    part = choice.get('delta', {}).get('content')
                    if isinstance(part, str):
                        parts.append(part)
                    if choice.get('finish_reason'):
                        stop = choice['finish_reason']
        if stop != 'stop':
            raise ValueError('审片服务未返回完整判断。')
        return json.loads(''.join(parts).strip().removeprefix('```json').removesuffix('```').strip())
    except (requests.RequestException, json.JSONDecodeError) as exc:
        raise ValueError('审片服务连接中断或返回格式错误。') from exc


def check_media(path, shot, package):
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                               capture_output=True, check=True, text=True, timeout=15)
        media = json.loads(probe.stdout)
        duration = float(media['format']['duration'])
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        raise InvalidTake('无法读取视频或时长。') from exc
    video = next((stream for stream in media['streams'] if stream['codec_type'] == 'video'), None)
    width, height = map(int, package['options']['aspect_ratio'].split(':'))
    if (not video or not video.get('height') or not video.get('width')
            or abs((video['width'] / video['height']) / (width / height) - 1) > .035
            or not math.isfinite(duration) or not shot['clip_seconds'] <= duration <= 15
            or not any(stream['codec_type'] == 'audio' for stream in media['streams'])):
        raise InvalidTake('H3 片段的画幅、音轨或时长不符合要求。')
    return duration


def heard_dialogue(path, start, end, expected, key):
    result = subprocess.run(
        ['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(path), '-ss', str(start),
         '-t', str(end-start), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', '-'],
        check=True, capture_output=True, timeout=25)
    try:
        transcript = transcribe_speech(result.stdout, key=key)
    except RuntimeError as exc:
        if str(exc) == '转写服务返回空内容':
            return {'audio_ok': False, 'transcript': '', 'expected': expected, 'word_match': 'mismatch'}
        raise

    match = mandarin_word_match(expected, transcript)
    return {'audio_ok': match != 'mismatch', 'transcript': transcript, 'expected': expected, 'word_match': match}


def review(path, shot, package, references, previous, judge_key):
    duration = check_media(path, shot, package)
    dialogue = shot.get('dialogue', '').strip()
    if not needs_model_review(shot, package):
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(path),
                            '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                           capture_output=True, check=True, timeout=30)
        except subprocess.CalledProcessError as exc:
            raise InvalidTake('视频或音轨无法完整解码。') from exc
        return {'accepted': True, 'score': None, 'method': 'local', 'in_seconds': 0,
                'out_seconds': shot['clip_seconds'], 'note': '本地文件检查通过。'}

    preview = path.with_suffix('.review.mp4')
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-y', '-i', str(path),
                        '-map', '0:v:0', '-map', '0:a:0', '-vf',
                        'scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2',
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '24', '-c:a', 'aac',
                        '-b:a', '96k', str(preview)], capture_output=True, check=True, timeout=90)
        content = [{'type': 'text', 'text':
                    '你在审查真实镜头。依次查看设计参考图、可选的上一镜出点、最后一个含音轨的视频。'
                    '只拦截能明确定位的严重缺陷，例如主动作缺失、身份明显漂移、关键特效没有发生或明显音画错误。'
                    '检查动作、人物身份和道具状态、运镜、伪影、对白及声画匹配。'
                    '有指定对白时，核对说的是指定句子和指定说话人。必须看出词句起止、停顿与此人的口部节奏是否相合。'
                    '结合角色互动判断语速。拖尾、条条一样慢、断句过碎或快到听不清时，把时间和具体问题写入 note。'
                    '可见的干张嘴、他人代说、指代混乱、第二个人的口型或完整台词跑到剪辑区间外，audio_ok 必须为 false。'
                    '计划为画外对白时，须能从场内视线、前后镜头、声源方向或动作反应唯一定位说话人；'
                    '画面不能出现无对应声音的假对白。缺少声源证据或分辨不清时判不通过。'
                    '对对白镜额外返回 speaker_attributed 和 lip_sync_ok 两个布尔值，画外声的 lip_sync_ok 表示无可见的错配张嘴。'
                    '动作以 screen_action 为准。handoff 是后期交接，留给剪辑或旁白的信息不要求出现在原始镜头里。'
                    '有未经安排的背景配乐、画外旁白或 post_sound_sources 中留给后期的明显重复声源时 audio_ok 必须为 false。'
                    '按分镜核对主体位置和前后景、光源方向与明暗层次、色彩状态。运镜须揭示指定信息。'
                    '有 camera_plan 时只核对可辨的空间关系、摄影机路径和阶段顺序。若选连续长镜，拒绝可见的跳切、'
                    '机位重置或无动机的画面加速。有 light_plan 与 framing_plan 时核对主光方向、可读半调、视轴和注意点。'
                    '核对同场人物的服装裁剪、底布本色、发式和道具归属，区分环境光改变和实体突然被替换。'
                    '跟踪道具遮挡前、遮挡后与交接的实际动作。出现无剧情依据的变装、道具消失或换物，continuity_ok 必须为 false。'
                    '无来源的额外花纹、大面积浮在背景或人物上的图样、纹样越出承载物边界时，artifacts_ok 必须为 false。'
                    '检查漂浮物来源及落点，未经本镜设计的花瓣或粒子交叉背景、人物或道具时 artifacts_ok 必须为 false。'
                    '按时间核对动作起因、先后和最后可见状态。用 quality_check 定位本镜最重要的验收条件，'
                    '需要时指出哪一段滑步、穿插、遮挡或特效时序破坏了这个条件。'
                    '按表演正常速度判断身体转移。无剧情动机的整体拖慢、动作突跳、脚底滑移或中途停在未完成姿态要说明时间。'
                    '涉及主动触碰图层或道具，核对视线、肩肘腕的力量传递、同景深比例与遮挡。'
                    '防止孤立漂浮或突变尺度的肢体冒充角色动作；指出真实接触的时点和下一拍留下的结果。'
                    '光、水、粒子按镜头真实遮挡与相对运动检查，不得掩掉重要信息。'
                    '区分有意改变的规则与仍须延续的身份、尺度、材质或声源。'
                    '若效果成功却始终看不出是谁做了什么、为什么发生，或误认成别的剧情与气质，按可见证据定位哪条关系被破坏。'
                    '发现造型变成真人脸、材质变成强镜面，或大面积霓虹、溢出的高光破坏既定媒介和配色时，须报告可见证据。'
                    '如果镜头承担奇观揭示，逐项核对 spectacle 的尺度参照、变化、可见结果和保持稳定的对象，'
                    '任一项失效则 cinematography_ok 必须为 false。'
                    '连续动作和换媒介过门，按 editorial_join 的出入状态、接触点、动作方向和视线核对上一镜出点。'
                    '若接点有跳位、前后主物消失或姿势照抄，标明可见位置，continuity_ok 为 false。'
                    '跳时空按分镜中的视觉提示判断。'
                    '选一段完整保留必要动作与台词的连续区间，区间长度接近计划时长（误差最多0.5秒）。'
                    '只有关键剧情信息因严重缺陷无法辨认时才判不通过，轻微瑕疵可记入 note。只返回 JSON，字段 identity_ok, action_ok, camera_ok,'
                    ' continuity_ok, artifacts_ok, audio_ok, cinematography_ok 为布尔值，score 为 0–100，'
                    'in_seconds、out_seconds 为秒。note 写具体可见或可听证据，尽量定位到时间或画面位置；'
                    '不通过时写出要改的对象和动作。只有确实看见角色参考中的背景被带进不该出现的场景，'
                    '才额外返回 failure_kind=reference_background，并在 note 写出该实物及出现位置。\n' +
                    json.dumps({'shot': shot, 'visual_style': package['bible']['visual_style'],
                                'visual_medium': package['bible'].get('visual_medium'),
                                'color_script': package['bible'].get('color_script'),
                                'continuity_rules': package['bible']['continuity_rules'],
                                'reference_count': len(references), 'has_previous': bool(previous),
                                'duration': duration}, ensure_ascii=False)}]
        content.extend({'type': 'image_url', 'image_url': {'url': image}} for image in references)
        if previous:
            content.append({'type': 'image_url', 'image_url': {'url': previous}})
        encoded = base64.b64encode(preview.read_bytes()).decode('ascii')
        content.append({'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' + encoded,
                                                          'fps': 12.0 if dialogue else 4.0}})
        result = judge(content, judge_key)
    finally:
        preview.unlink(missing_ok=True)

    flags = ('identity_ok', 'action_ok', 'camera_ok', 'continuity_ok', 'artifacts_ok', 'audio_ok', 'cinematography_ok')
    if (not isinstance(result, dict) or any(type(result.get(field)) is not bool for field in flags)
            or type(result.get('score')) is not int or not 0 <= result['score'] <= 100
            or not isinstance(result.get('note'), str) or not result['note'].strip()):
        raise ValueError('审片模型未返回完整判断。')
    start, end = result.get('in_seconds'), result.get('out_seconds')
    valid_cut = (type(start) in (int, float) and type(end) in (int, float)
                 and 0 <= start < end <= duration
                 and abs(end - start - shot['clip_seconds']) <= 0.5)
    accepted = all(result[field] for field in flags)
    if accepted and not valid_cut:
        raise ValueError('审片返回的剪辑时间无效，已停止，原始镜头保留。')
    speech = {}
    note = result['note'][:600]
    if dialogue:
        evidence_fields = ('speaker_attributed', 'lip_sync_ok')
        if any(type(result.get(field)) is not bool for field in evidence_fields):
            raise ValueError('对白审片没有返回说话人和口型的判断。')
        speech.update({key: result[key] for key in evidence_fields})
        accepted = accepted and all(speech.values())
        if accepted:
            speech['asr'] = heard_dialogue(path, start, end, dialogue, judge_key)
            accepted = speech['asr']['audio_ok']
            if not accepted:
                note = f'同期对白与台词不一致。计划「{dialogue}」，实听「{speech["asr"]["transcript"]}」。'
    failure_kind = (film_experience.FAILURE if result.get('failure_kind') == film_experience.FAILURE
                    and (not result['artifacts_ok'] or not result['continuity_ok']) else None)
    return {**{field: result[field] for field in (*flags, 'score', 'in_seconds', 'out_seconds')},
            'method': 'omni', 'note': note, 'accepted': accepted, 'failure_kind': failure_kind,
            **({'speech': speech} if speech else {})}


def needs_model_review(shot, package):
    if shot.get('dialogue', '').strip() or shot.get('_review_reference_policy'):
        return True
    direction = shot.get('direction') or {}
    if ((direction.get('camera_plan') or {}).get('long_take')
            or (direction.get('editorial_join') or {}).get('method')
            in {'action-match', 'registered-restyle', 'foreground-handoff'}):
        return True
    pilot = package['film'].get('pilot_shot_id')
    if shot['id'] == pilot:
        return True
    if not shot.get('spectacle'):
        return False
    spectacles = [candidate['id'] for scene in package['scenes'] for candidate in scene['shots']
                  if candidate.get('spectacle') and candidate['id'] != pilot]
    # Sample the beginning and end of the remaining spectacle footage when the budget is full.
    available = MAX_MODEL_REVIEW_SHOTS - bool(pilot)
    selected = spectacles if len(spectacles) <= available else spectacles[:1] + spectacles[-(available - 1):]
    return shot['id'] in selected


def review_take(job, take, shot, package, references, previous, previous_file, judge_key, attempt):
    history = job['reviews']
    for verdict in reversed(history):
        if (verdict.get('file') == take.name and verdict.get('previous_file') == previous_file
                and (not shot.get('dialogue', '').strip() or 'speech' in verdict)):
            return verdict
    try:
        verdict = review(take, shot, package, references, previous, judge_key)
    except InvalidTake as exc:
        verdict = {'accepted': False, 'score': None, 'method': 'local', 'note': str(exc)}
    record = {'shot_id': shot['id'], 'take': attempt, 'file': take.name,
              'previous_file': previous_file, **verdict}
    history.append(record)
    workflow.put(job)
    return record


def render_take(job, shot, path, refs, prompt, key, rental):
    voice_name = job.get('voice_references', {}).get(shot.get('dialogue_speaker'))
    voice = workflow.ROOT / 'voice-references' / voice_name if voice_name else None
    if voice is not None:
        if not rental or not voice.is_file():
            raise ValueError('当前镜头的角色声音参考不可用。')
        prompt = (f'<Audio 1> 只作为 {shot["dialogue_speaker"]} (S1) 的音色、口音和年龄感参考。'
                  '样本内容不进入音轨，画面中的该角色只按对白标签说话。\n' + prompt)
    motion_name = job.get('motion_references', {}).get(shot['id'])
    motion = workflow.ROOT / 'motion-references' / motion_name if motion_name else None
    if motion:
        if not motion.is_file():
            raise ValueError('此镜头的动作参考文件缺失。')
        prompt += ('\n<Video 1> 只用于动作时序、重心转移和身体与道具的接触。'
                   '面孔、服装、场景和光线按参考图及本镜要求保持。')
    if len(prompt) > 5000:
        raise ValueError('镜头提示词超过 H3 提交上限，请精简后重试。')
    active = job.get('active_take') or {}
    if active.get('shot_id') == shot['id'] and active.get('file') == path.name and active.get('provider_task_id'):
        remote = {'provider_task_id': active['provider_task_id']}
        if not rental:
            remote['poll_url'] = providers.build_request(H3, prompt, shot['duration_seconds'], job['ratio'])[3] + active['provider_task_id']
    else:
        job['active_take'] = {'shot_id': shot['id'], 'file': path.name, 'status': 'submitting'}
        if rental:
            job['active_take']['provider_task_id'] = str(uuid.uuid4())
        workflow.put(job)
        try:
            if rental:
                remote = {'provider_task_id': rented_h3.submit(
                    rental, prompt, shot['duration_seconds'], job['ratio'], refs, motion_reference=motion,
                    voice_references=(voice,) if voice is not None else (),
                    size=(1536, 864) if job['ratio'] == '16:9' else (864, 1536),
                    identifier=job['active_take']['provider_task_id'])}
            else:
                remote = providers.submit(H3, prompt, shot['duration_seconds'], job['ratio'], None, key, references=refs)
        except providers.RejectedRequest:
            job['active_take']['status'] = 'rejected'
            job['active_take'].pop('provider_task_id', None)
            raise
        except (RuntimeError, ValueError, requests.RequestException):
            job['active_take']['status'] = 'submission_unknown'
            raise
        job['active_take'].update(provider_task_id=remote['provider_task_id'], status='queued')
        workflow.put(job)
    remote.update(config=H3)
    deadline = time.monotonic() + 2100
    failures = 0
    while time.monotonic() < deadline:
        try:
            if rental:
                status = rented_h3.poll(rental, remote['provider_task_id'])
                if status == 'completed':
                    rented_h3.download(rental, remote['provider_task_id'], path)
            else:
                result = providers.poll(remote, key)
                status = result['status']
                if status == 'completed':
                    downloaded = workflow.download_job({**result, 'id': str(uuid.uuid4())})
                    downloaded.replace(path)
        except (requests.RequestException, RuntimeError) as exc:
            failures += 1
            if failures >= MAX_POLL_ERRORS:
                raise ReceiptTransportError('连续三次无法查询或取回云端镜头，已保留原任务编号。') from exc
            time.sleep(max(0, min(POLL_SECONDS, deadline - time.monotonic())))
            continue
        failures = 0
        job['active_take']['status'] = status
        if status == 'completed':
            workflow.put(job)
            return
        if status not in {'queued', 'running'}:
            if status == 'missing':
                job['active_take']['status'] = 'submission_unknown'
                raise ValueError('DSW 暂时查不到该镜头，已保留任务编号。')
            job.setdefault('failed_render_files', []).append(path.name)
            workflow.put(job)
            raise TakeFailed('H3 已确认本次镜头生成失败。')
        time.sleep(max(0, min(POLL_SECONDS, deadline - time.monotonic())))
    raise TimeoutError('H3 镜头仍未完成，请通过制作记录核对。')


def outgoing_frame(path, end):
    target = path.with_suffix('.out.jpg')
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(max(0, end - .12)),
                    '-i', str(path), '-frames:v', '1',
                    '-vf', 'scale=480:480:force_original_aspect_ratio=decrease', str(target)],
                   capture_output=True, check=True, timeout=30)
    return image_data(target)


def narration_audio(narration, shots, cuts, work, key):
    cues = {cue['shot_id']: cue for cue in narration['cues']}
    audio = []
    for shot, (start, end) in zip(shots, cuts):
        cue = cues.get(shot['id'])
        if cue is None:
            audio.append(None)
            continue
        slot = min(cue['end'], end - start) - cue['start']
        if slot <= 0:
            raise ValueError('旁白入点超出验收后的镜头长度。')
        instructions = narration['voice'] + ' ' + cue['delivery'] + ' ' + NATURAL_SPEECH
        identity = hashlib.sha256(json.dumps(['Ethan', instructions, cue['text']], ensure_ascii=False).encode()).hexdigest()
        path = work / f'narration-{identity}.wav'
        if not path.is_file():
            temporary = path.with_suffix('.part')
            try:
                temporary.write_bytes(synthesize_speech(cue['text'], voice='Ethan', instructions=instructions, key=key))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=duration',
                                '-of', 'default=nokey=1:noprint_wrappers=1', str(path)],
                               capture_output=True, check=True, text=True, timeout=20)
        duration = float(probe.stdout.strip())
        if not 0 < duration <= slot:
            raise ValueError(f'旁白无法自然读完，镜头 {shot["id"]} 需要缩短旁白或增加停顿空间。')
        audio.append((path, round(cue['start'] * 1000), 1))
    return audio


def reference_library():
    user, project = workflow.identity()
    scope = hashlib.sha256(json.dumps([user, project]).encode()).hexdigest()
    path = workflow.ROOT / 'reference-library' / 'v1' / scope
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_sound_assets(scenes, library, keys):
    effects = {}
    for scene in scenes:
        for cue in scene.get('sound_effects', []):
            prompt = (f'生成一条约 {cue["duration"]} 秒的干净、独立拟音：{cue["source"]}。'
                      '声音一开始就出现。近距离干声，无对白、无音乐、无混响、无其他环境声。')
            asset_id = hashlib.sha256(('qwen-audio-3.1-tts-next\n' + prompt).encode()).hexdigest()
            path = library / f'sound-{asset_id}.wav'
            if not path.is_file():
                partial = path.with_suffix('.part')
                try:
                    partial.write_bytes(generate_sound(prompt, key=keys['judge'], workspace=keys['sound_workspace']))
                    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(partial), '-map', '0:a:0',
                                    '-t', '7', '-f', 'null', '-'], check=True, capture_output=True, timeout=25)
                    partial.replace(path)
                finally:
                    partial.unlink(missing_ok=True)
            effects[(cue['source'], cue['duration'])] = path
    return effects


def produce(job, keys):
    rental = rented_h3.ensure_tunnel() if job['config']['provider'] == 'rented_h3' else None
    if rental and not rented_h3.ready(rental, motion=bool(job.get('motion_references')),
                                       voice_count=int(bool(job.get('voice_references')))):
        raise ValueError('DSW 还未加载制作所需的 H3 权重。')
    package = job['film_snapshot']
    shots = [shot for scene in package['scenes'] for shot in scene['shots']]
    work = workflow.ROOT / job['id']
    work.mkdir(exist_ok=True)
    job.update(status='running', stage='正在确定代码画面的造型与配色')
    workflow.put(job)
    edit_project = work / 'openfilm'
    edit_project.mkdir(exist_ok=True)
    openfilm_export.generate_plan(package, shots, edit_project)
    library = reference_library()
    narration = package['film'].get('narration')
    if job['phase'] == 'pilot' and narration:
        job.update(status='running', stage='正在录制旁白并检查时长')
        workflow.put(job)
        narration_audio(narration, shots, [(0, shot['clip_seconds']) for shot in shots], work, keys['judge'])
    if any(scene.get('sound_effects') for scene in package['scenes']):
        job.update(status='running', stage='正在准备独立空间音效')
        workflow.put(job)
    sound_assets = prepare_sound_assets(package['scenes'], library, keys)
    job.update(status='running', stage='正在生成人物与场景参考图')
    workflow.put(job)
    assets = {asset['id']: asset for asset in package['bible']['assets']}
    style = package['bible']['visual_style']
    if package['options'].get('style_guide'):
        style += '\n' + package['options']['style_guide']
    if package['bible'].get('visual_medium'):
        style += '\n造型媒介：' + package['bible']['visual_medium'] + '\n全片配色：' + package['bible']['color_script']
    art_files = {asset_id: art(assets[asset_id], style, work, keys['video'], library)
                 for asset_id in dict.fromkeys(ref for shot in shots for ref in shot['reference_ids'])}
    references = {asset_id: image_data(path) for asset_id, path in art_files.items()}
    job['reference_assets'] = [{'name': assets[asset_id]['name'], 'file': path.name}
                               for asset_id, path in art_files.items()]
    workflow.put(job)
    for asset_id, image in references.items():
        if asset_id in job.setdefault('accepted_art', []):
            continue
        asset = assets[asset_id]
        stored = library / art_files[asset_id].name
        if stored.is_file():
            shutil.copyfile(stored, art_files[asset_id])
            references[asset_id] = image_data(art_files[asset_id])
        else:
            instruction = {'type': 'text', 'text':
                           '检查参考图的可辨识轮廓、比例、材质和固定特征。场景检查布局、视角、光源和标志物。'
                           '核对 signature 是否一眼可辨，材质纹理、接触阴影与光源是否与 material_logic 相符。'
                           '人物检查五官和关节，道具检查尺度与使用痕迹，场景检查透视和可读层次。'
                           '有拼贴、文字、塑料质感、重复肢体或未经设计的装饰堆叠，须指出可见问题并拒绝。'
                           '如果本片是非真人媒介，拒绝真人脸贴兽耳、磨皮或高光塑胶效果。'
                           '检查少量主色和明确强调色，避免无动机的通幅霓虹与过曝。'
                           '拒绝多余人物、不可辨认的设计、无来源的游离装饰或没有证据的通过。'
                           '背景与素面物体不出现额外花纹，已有纹样应固定在参考指定的实体表面。'
                           '只返回 JSON {"accepted":true,"note":"原因"}。'
                           + json.dumps({'asset': asset, 'visual_style': style}, ensure_ascii=False)}
            candidate = art_files[asset_id]
            result = judge([instruction, {'type': 'image_url', 'image_url': {'url': image}}], keys['judge'])
            if isinstance(result, dict) and result.get('accepted') is False and isinstance(result.get('note'), str) and result['note'].strip():
                candidate = art(asset, style, work, keys['video'], library, correction=result['note'])
                image = image_data(candidate)
                result = judge([instruction, {'type': 'image_url', 'image_url': {'url': image}}], keys['judge'])
            if not isinstance(result, dict) or result.get('accepted') is not True:
                raise ValueError(f"参考图未通过检查：{asset['name']}。")
            temporary = stored.with_suffix('.part')
            try:
                shutil.copyfile(candidate, temporary)
                temporary.replace(stored)
            finally:
                temporary.unlink(missing_ok=True)
            if candidate != art_files[asset_id]:
                candidate.replace(art_files[asset_id])
                references[asset_id] = image
        job['accepted_art'].append(asset_id)
        workflow.put(job)
    selections = job.setdefault('selections', {})
    work_shots = list(enumerate(shots, 1))
    if job['phase'] == 'pilot':
        pilot_id = package['film']['pilot_shot_id']
        work_shots = [(index, shot) for index, shot in work_shots if shot['id'] == pilot_id]
    scene_by_shot = {shot['id']: index for index, scene in enumerate(package['scenes']) for shot in scene['shots']}
    blocked = job.setdefault('blocked_shots', {})
    waiting = job['waiting_on_shots'] = {}
    blocked_scene = None
    missing_previous = None
    previous = None
    previous_file = None
    for index, shot in work_shots:
        shot_id = shot['id']
        scene_index = scene_by_shot[shot_id]
        cut_method = ((shot.get('direction') or {}).get('editorial_join') or {}).get('method')
        if shot_id in blocked:
            blocked_scene = scene_index
            missing_previous = shot_id
            previous = previous_file = None
            continue
        if scene_index == blocked_scene or (missing_previous is not None and cut_method in BOUNDARY_DEPENDENCIES):
            blocked_scene = scene_index
            waiting[shot_id] = missing_previous
            continue
        missing_previous = None
        choice = selections.get(shot_id)
        last_rejection = ''
        if choice and job['phase'] != 'pilot':
            take = work / choice['file']
            if not take.is_file():
                raise ValueError(f'第 {index} 镜的本地素材缺失，请恢复后继续。')
            needs_speech_recheck = bool(shot.get('dialogue', '').strip() and not choice.get('speech_checked'))
            if (not needs_speech_recheck and (shot['id'] == package['film'].get('pilot_shot_id')
                    or ('previous_file' in choice and choice['previous_file'] == previous_file))):
                previous = outgoing_frame(take, choice['out_seconds'])
                previous_file = take.name
                continue
            verdict = review_take(job, take, shot, package, [references[ref] for ref in shot['reference_ids']],
                                  previous, previous_file, keys['judge'], choice.get('take'))
            if verdict['accepted']:
                choice.update(in_seconds=verdict['in_seconds'], out_seconds=verdict['out_seconds'],
                              previous_file=previous_file, speech_checked='speech' in verdict)
                previous = outgoing_frame(take, choice['out_seconds'])
                previous_file = take.name
                workflow.put(job)
                continue
            active = job.get('active_take') or {}
            if active.get('shot_id') not in {None, shot['id']}:
                raise ValueError('前一镜头衔接未通过，仍保留待取回的云端镜头。')
            selections.pop(shot['id'], None)
            last_rejection = verdict['note']
        job.update(stage=f'正在生成和审看片段 {index}/{len(shots)}')
        workflow.put(job)
        refs = [references[ref] for ref in shot['reference_ids']]
        base_prompt = take_prompt(shot, assets)
        kinds = {assets[ref]['kind'] for ref in shot['reference_ids']}
        owner = workflow.identity() if {'character', 'location'} <= kinds and has_app_context() else None
        adopted = False
        if owner:
            with workflow.connection() as db:
                adopted = film_experience.approved(db, *owner)
        if adopted:
            base_prompt += '\n' + film_experience.GUIDANCE
        prompt = (base_prompt + '\n仅修正以下问题，不改变动作、人物和情节：' + last_rejection[:400]
                  if last_rejection else base_prompt)
        trial_baseline = None
        trials = job.setdefault('experience_trials', {})
        accepted = []
        legacy_pilot = job['phase'] == 'pilot' and (
            (job.get('active_take') or {}).get('file', '').startswith('pilot-')
            or any(work.glob(f'pilot-{index:02d}-*.mp4')))
        prefix = 'pilot' if legacy_pilot else 'film'
        for attempt in range(1, MAX_TAKES + 1):
            take = work / f'{prefix}-{index:02d}-{attempt}.mp4'
            if choice and choice['file'] == take.name:
                continue
            if take.name in job.get('failed_render_files', []):
                last_rejection = last_rejection or '云端生成失败。'
                continue
            active = job.get('active_take') or {}
            if active.get('shot_id') not in {None, shot['id']}:
                raise ValueError('有另一镜头尚待取回，已保留云端任务编号。')
            if not take.exists() and active.get('shot_id') == shot['id'] and active.get('file') != take.name:
                continue
            if not take.exists():
                if not active.get('provider_task_id') and owner:
                    if adopted:
                        trials[take.name] = {'mode': 'adopted'}
                    elif trial_baseline:
                        trials[take.name] = {'mode': 'paired', 'baseline_sha256': trial_baseline}
                try:
                    render_take(job, shot, take, refs, prompt, keys['video'], rental)
                except TakeFailed as exc:
                    last_rejection = str(exc)
                    job.pop('active_take', None)
                    workflow.put(job)
                    continue
            trial = trials.get(take.name)
            reviewed_shot = {**shot, '_review_reference_policy': True} if trial else shot
            verdict = review_take(job, take, reviewed_shot, package, refs, previous, previous_file, keys['judge'], attempt)
            if owner and trial:
                with workflow.connection() as db:
                    film_experience.record(db, *owner, f'{job["id"]}/{take.name}',
                                           trial, verdict, fingerprint(take))
            if not active or active.get('file') == take.name or not active.get('provider_task_id'):
                job.pop('active_take', None)
            workflow.put(job)
            if verdict['accepted']:
                accepted.append((verdict, take))
                if not job.get('active_take'):
                    break
            else:
                last_rejection = verdict['note']
                trial_baseline = (fingerprint(take) if owner and not adopted
                                  and verdict.get('failure_kind') == film_experience.FAILURE else None)
                guidance = '\n' + film_experience.GUIDANCE if trial_baseline else ''
                prompt = (base_prompt + '\n按问题重做对应时间段。保留原定剧情结果、角色身份与无关构图。'
                          + guidance + '\n本次修正：' + last_rejection[:400])
        if not accepted:
            if not last_rejection:
                raise ValueError(f'第 {index} 镜没有可核对的本地片段，云端记录已保留。')
            blocked[shot_id] = {'number': index, 'reason': last_rejection[:220]}
            workflow.put(job)
            if job['phase'] == 'pilot':
                raise ReviewExhausted(f'试拍第 {index} 镜用完返修机会。{last_rejection[:220]}')
            blocked_scene = scene_index
            missing_previous = shot_id
            previous = previous_file = None
            continue
        verdict, take = max(accepted, key=lambda item: item[0]['score'] or 0)
        cut = verdict['in_seconds'], verdict['out_seconds']
        previous = outgoing_frame(take, cut[1])
        selections[shot['id']] = {'file': take.name, 'in_seconds': cut[0], 'out_seconds': cut[1],
                                  'take': verdict['take'], 'speech_checked': 'speech' in verdict}
        if job['phase'] == 'film' or index == 1:
            selections[shot['id']]['previous_file'] = previous_file
        previous_file = take.name
        job['completed_shots'] = len(selections)
        workflow.put(job)

    if blocked:
        workflow.put(job)
        reasons = '、'.join(str(row['number']) for row in blocked.values())
        raise ReviewExhausted(f'第 {reasons} 镜用完返修机会。已保留通过的 {len(selections)} 镜，另有 {len(waiting)} 镜等待衔接。')

    if job['phase'] == 'pilot':
        job.update(phase='film', stage='试拍通过，继续制作后续镜头')
        workflow.put(job)
        return produce(job, keys)
    choices = [(work / selections[shot['id']]['file'],
                (selections[shot['id']]['in_seconds'], selections[shot['id']]['out_seconds'])) for shot in shots]
    source_windows = [(fingerprint(path), start, end, shot['id'])
                      for (path, (start, end)), shot in zip(choices, shots, strict=True)]
    require_distinct_source_windows(source_windows)
    duration = sum(end - start for _, (start, end) in choices)
    if package['film']['kind'] == 'feature' and duration <= 600:
        raise ValueError('剪辑后不足十分钟，暂未导出长片。')
    if package['film']['kind'] == 'story' and duration < 300:
        raise ValueError('剪辑后的独立故事未达到五分钟，请先补齐故事结果和必要画面。')
    if package['film'].get('narration') and package['film']['kind'] in {'trailer', 'scene'} and not 45 <= duration <= 60:
        raise ValueError('验收后的剪辑区间未达到 45–60 秒，请调整镜头切点。')
    job.update(stage='正在按分镜顺序剪辑并整理音量')
    workflow.put(job)
    output = workflow.ROOT / (job['id'] + '.mp4')
    temporary = work / 'film.mp4'
    cuts = [cut for _, cut in choices]
    audio = None
    if narration:
        job['stage'] = '正在将旁白和音乐混入成片'
        workflow.put(job)
        audio = narration_audio(narration, shots, cuts, work, keys['judge'])
    entries = music_timeline(package['scenes'], shots, cuts, workflow.ROOT / 'music',
                             trailer=bool(package['film'].get('trailer_moments')))
    sound_entries = sound_timeline(package['scenes'], shots, cuts, sound_assets)
    protected = voice_windows(shots, cuts, audio if audio is not None else [None] * len(shots))
    grades = [shot['grade'] for shot in shots] if package['bible'].get('color_script') else None
    workflow.assemble_paths([path for path, _ in choices], temporary, job['ratio'],
                            cuts=cuts, high_resolution=not rental, narration=audio, grades=grades,
                            native_864=bool(rental), normalize_clips=False)
    source = temporary
    if entries or sound_entries:
        source = work / 'mixed.mp4'
        mix_score(temporary, source, entries, sound_entries, speech_windows=protected)
    job.update(stage='正在制作可编辑画面并导出成片')
    workflow.put(job)
    def report_edit_progress(message):
        job['stage'] = message
        workflow.put(job)
    project, project_duration = openfilm_export.make_project(
        package, shots, choices, source, work, on_progress=report_edit_progress,
        audio_tracks=(audio if audio is not None else [None] * len(shots), entries, sound_entries))
    review_index = {(item['file'], item.get('previous_file')): item for item in job['reviews']}
    proof = []
    position = 0.0
    for digest, start, end, shot_id in source_windows:
        selected = selections[shot_id]
        verdict = review_index.get((selected['file'], selected.get('previous_file')))
        proof.append({'shot_id': shot_id, 'source_sha256': digest, 'take': selected.get('take'),
                      'source_in_seconds': start, 'source_out_seconds': end,
                      'timeline_in_seconds': round(position, 3), 'review': verdict})
        position += end - start
    if project.is_dir():
        (project / '混音时间窗.json').write_text(
            json.dumps({'seconds': protected, 'music_gain_under_voice': .26,
                        'effects_gain_under_voice': .38}, ensure_ascii=False, indent=2), encoding='utf-8')
    (project / '逐镜制作回执.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    pending_video = work / 'final-export.part.mp4'
    archive = work / 'openfilm-project.zip'
    pending_archive = archive.with_suffix('.part')
    try:
        openfilm_export.render(project, pending_video, project_duration, on_progress=report_edit_progress)
        verified_cuts = check_exported_cuts(pending_video, shots, choices, project,
                                           lambda content: judge(content, keys['judge']), report_edit_progress)
        job['verified_cuts'] = len(verified_cuts)
        import zipfile
        with zipfile.ZipFile(pending_archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
            for path in sorted(project.rglob('*')):
                if path.is_file() and '.film' not in path.relative_to(project).parts:
                    bundle.write(path, path.relative_to(project))
        pending_archive.replace(archive)
        pending_video.replace(output)
    finally:
        pending_archive.unlink(missing_ok=True)
        pending_video.unlink(missing_ok=True)
    job.update(status='completed', archived=True, stage='短片已完成',
               duration_seconds=round(duration, 2), music_credits=package['film'].get('music_credits', []),
               edit_project='openfilm-project.zip')


def run(user_id, project_id, job_id):
    # A dedicated worker job has no browser request or Flask session.
    from flask import Flask
    with Flask(__name__).app_context():
        g.current_user = {'id': user_id}
        g.current_project = {'id': project_id}
        job = workflow.get(job_id)
        if job['status'] != 'queued':
            return
        try:
            redis = get_redis_connection()
            with redis.pipeline() as transaction:
                transaction.get('morpho:film:' + job_id)
                transaction.delete('morpho:film:' + job_id)
                raw, _ = transaction.execute()
            if not raw:
                raise ValueError('制作密钥已过期，请重新开始制作。')
            keys = json.loads(raw)
            try:
                produce(job, keys)
            except ReceiptTransportError:
                if not (job.get('active_take') or {}).get('provider_task_id'):
                    raise
                job.update(stage='连接恢复后核对原镜头任务')
                workflow.put(job)
                time.sleep(POLL_SECONDS)
                produce(job, keys)
        except Exception as exc:
            active = job.get('active_take') or {}
            pending = active.get('status') in PENDING_TAKE_STATES
            job.update(status='submission_unknown' if pending else 'failed',
                       stage='镜头状态待核对' if pending else '制作暂停',
                       error=(str(exc)[:350] if isinstance(exc, (ValueError, RuntimeError, TimeoutError))
                              else '短片处理失败，请查看失败的制作步骤。'))
            if isinstance(exc, (ReviewExhausted, CutRejected)):
                job['review_exhausted'] = True
            current = get_current_job()
            if current:
                current.meta['film_error'] = type(exc).__name__
                current.save_meta()
        finally:
            workflow.put(job)



def mark_worker_failure(rq_job, connection, exc_type, exc_value, traceback):
    from flask import Flask
    user_id, project_id, film_id = rq_job.args
    with Flask(__name__).app_context():
        g.current_user = {'id': user_id}
        g.current_project = {'id': project_id}
        job = workflow.get(film_id)
        if job['status'] in {'queued', 'running'}:
            pending = (job.get('active_take') or {}).get('status') in PENDING_TAKE_STATES
            job.update(status='submission_unknown' if pending else 'failed',
                       stage='制作暂停', error='后台任务中断，已下载的镜头已保留。')
            workflow.put(job)
