"""Publish a film and its time-aligned director captions from confirmed editorial evidence."""
import argparse
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile

from film_script import validate_script
from film_source_windows import require_archived_window

SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,74}')
FIELDS = ('AI制作', '剪辑', '运镜', '美术', '调度', '调色', '风格', '美学方法', '声音')


def atomic_text(path, content):
    with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    temporary.replace(path)


def media_digest(media):
    with media.open('rb') as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def caption_text(t):
    ms = round(t * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'


def confirm_edition(directory, plan):
    if not SLUG.fullmatch(plan['slug']) or plan['slug'] != directory.name:
        raise ValueError('成片地址必须与专用目录对应。')
    film = directory / 'film.mp4'
    duration = float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration',
                                              '-of','csv=p=0',str(film)],text=True,timeout=30))
    if media_digest(film) != plan['sha256']:
        raise ValueError('成片与导演记录的文件版本不一致。')
    previous = 0.0
    for scene in plan['scenes']:
        start, end = scene['start'], scene['end']
        if type(start) not in (int,float) or type(end) not in (int,float) or not previous <= start < end <= duration+.02 or start-previous>.025:
            raise ValueError('导演字幕必须采用成片内的连续时序。')
        previous = end
        if not scene['title'].strip() or not scene['aim'].strip() or not scene['craft']:
            raise ValueError('每条字幕至少需要实际手法与创作目标。')
        if any(key not in FIELDS or not isinstance(value,str) or not value.strip()
               for key,value in scene['craft'].items()):
            raise ValueError('手法只能使用已核实的内容。')
    if not plan['scenes'] or abs(previous-duration)>.025:
        raise ValueError('导演时间线须覆盖整条成片。')
    return duration


def readable_caption(line):
    return (isinstance(line, str) and 1 <= len(line.strip()) <= 90
            and all(char.isprintable() and char != '\ufffd' for char in line))


def generate_captions(scenes, generate):
    lines = {}
    pending = []
    for index, scene in enumerate(scenes):
        if 'reviewed_caption' in scene:
            line = scene['reviewed_caption']
            if not readable_caption(line):
                raise ValueError('已审导演字幕含有缺字、控制符或长度不合适。')
            lines[index] = line.strip()
        else:
            pending.append(dict(index=index, title=scene['title'], aim=scene['aim'], craft=scene['craft']))
    instruction = (
        '为影片下方写随场切换的导演字幕。以下是已经剪进这一版画面的核对记录。'
        '每项用一句简明的中文说明这段为何这样拍，优先指出观众能看见或听见的东西。'
        '保留原义，不发明设备、模型、人物动作、配色来源、画面和数字。只有输入明确写到才提。'
        '按输入索引和次序完整返回 {"captions":[{"index":0,"line":"一句中文"}]}。每句至多68个字。\n')
    for start in range(0, len(pending), 8):
        batch = pending[start:start+8]
        for attempt in range(2):
            prompt = instruction + json.dumps(batch, ensure_ascii=False)
            if attempt:
                prompt += '\n上次这些条目有损坏字元或长度不当。请重新写成完整可读的短句。'
            answer = generate(prompt, 3200)['captions']
            if [item['index'] for item in answer] != [row['index'] for row in batch]:
                raise ValueError('模型未按时间线返回完整导演字幕。')
            retry = []
            for row, item in zip(batch, answer, strict=True):
                if readable_caption(item['line']):
                    lines[row['index']] = item['line'].strip()
                else:
                    retry.append(row)
            if not retry:
                break
            batch = retry
        else:
            raise ValueError('指定场次的导演字幕仍有损坏字元或长度不合适。')
    return [lines[index] for index in range(len(scenes))]


def publish(directory, plan, generate):
    duration = confirm_edition(directory, plan)
    script = validate_script(plan['script'], plan['sha256'], duration) if 'script' in plan else None
    lines = generate_captions(plan['scenes'], generate)
    cues = [{**scene, 'line':line} for scene,line in zip(plan['scenes'],lines,strict=True)]
    result = {key:plan[key] for key in ('slug','title','book','edition','summary','sha256')}
    for field in ('music_credits', 'sound_revision'):
        if field in plan:
            result[field] = plan[field]
    result.update(duration=duration,cues=cues)
    vtt = 'WEBVTT\n\n' + ''.join(
        f'{caption_text(cue["start"])} --> {caption_text(cue["end"])}\n{cue["line"]}\n\n' for cue in cues)
    if script is not None:
        atomic_text(directory / 'script.json', json.dumps(script,ensure_ascii=False,indent=2)+'\n')
    atomic_text(directory / 'director.vtt',vtt)
    atomic_text(directory / 'published.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def current_editions(directory):
    index = directory / 'current-editions.json'
    if not index.exists():
        return []
    state = json.loads(index.read_text(encoding='utf-8'))
    if state.get('version') != 1 or not isinstance(state.get('editions'), list):
        raise ValueError('放映厅选片记录格式不完整。')
    entries = state['editions']
    keys = set()
    slugs = set()
    for item in entries:
        if (any(not SLUG.fullmatch(item[key]) for key in ('work', 'cut_type', 'slug'))
                or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])):
            raise ValueError('放映厅选片记录无效。')
        key = item['work'], item['cut_type']
        if key in keys or item['slug'] in slugs:
            raise ValueError('同一个作品和剪辑类型只允许一个当前版本。')
        keys.add(key)
        slugs.add(item['slug'])
    return entries


STORY_REVIEWS = {
    'full-book': ('开场冲突', '原著主线覆盖', '关键转折', '收束或悬念'),
    'story': ('人物目标', '阻碍和转折', '人物选择', '可见后果'),
    'standalone-story': ('人物目标', '阻碍和转折', '人物选择', '可见后果'),
}


def confirm_story_delivery(plan, cut_type):
    required = STORY_REVIEWS.get(cut_type)
    if required is None:
        return
    review = plan.get('story_review')
    if (not isinstance(review, dict) or review.get('sha256') != plan['sha256']
            or review.get('decision') != 'approved' or review.get('blocking_issues') != []):
        raise ValueError('完整影片须提供对应成片、无阻断问题的叙事验收。')
    evidence = review.get('evidence')
    if not isinstance(evidence, dict):
        raise ValueError('叙事验收须给出实际场次与银幕上的证据。')
    for role in required:
        item = evidence.get(role)
        if (not isinstance(item, dict) or type(item.get('scene')) is not int
                or not 0 <= item['scene'] < len(plan['scenes'])
                or not isinstance(item.get('visible_or_audible'), str)
                or not item['visible_or_audible'].strip()):
            raise ValueError(f'缺少实剪叙事证据：{role}。')


def promote(edition_path, work, cut_type, expected_sha256):
    directory = edition_path.parent
    if (edition_path.name != 'edition.json' or not SLUG.fullmatch(work) or not SLUG.fullmatch(cut_type)
            or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)):
        raise ValueError('晋级需要作品、剪辑类型和核准成片的完整指纹。')
    hub = directory.parent
    lock = hub / '.current-editions.lock'
    with lock.open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        plan = json.loads(edition_path.read_text(encoding='utf-8'))
        record = json.loads((directory / 'published.json').read_text(encoding='utf-8'))
        if plan['sha256'] != expected_sha256 or record['sha256'] != expected_sha256:
            raise ValueError('提交的版本与审核的成片指纹不符。')
        duration = confirm_edition(directory, plan)
        film = directory / 'film.mp4'
        frames = int(subprocess.check_output(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
             'stream=nb_frames', '-of', 'csv=p=0', str(film)], text=True, timeout=30))
        require_archived_window(film, expected_sha256, 0, frames)
        if film.is_symlink():
            require_archived_window(film.resolve(), expected_sha256, 0, frames)
        confirm_story_delivery(plan, cut_type)
        if (record['slug'] != directory.name or abs(record['duration']-duration) > .025
                or len(record['cues']) != len(plan['scenes']) or not (directory/'poster.jpg').is_file()):
            raise ValueError('完整成片、海报和已发布导演字幕须属于同一版。')
        for scene, cue in zip(plan['scenes'], record['cues'], strict=True):
            if (any(scene[key] != cue[key] for key in ('start','end','title','aim','craft'))
                    or not readable_caption(cue['line'])):
                raise ValueError('已核准的场次与字幕不一致。')
        vtt = 'WEBVTT\n\n' + ''.join(
            f'{caption_text(c["start"])} --> {caption_text(c["end"])}\n{c["line"]}\n\n' for c in record['cues'])
        if vtt != (directory/'director.vtt').read_text(encoding='utf-8'):
            raise ValueError('可下载的字幕和播放页不同步。')
        item = dict(work=work, cut_type=cut_type, slug=directory.name, sha256=expected_sha256)
        entries = current_editions(hub)
        for entry in entries:
            if entry['slug']==item['slug'] and (entry['work'], entry['cut_type']) != (work, cut_type):
                raise ValueError('同一放映画面已属于其他作品或剪辑类型。')
        if item not in entries:
            entries = [x for x in entries if (x['work'],x['cut_type']) != (work,cut_type)]
            entries.append(item)
            atomic_text(hub/'current-editions.json',json.dumps({'version':1,'editions':entries},ensure_ascii=False,indent=2)+'\n')
        return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('edition', type=Path)
    parser.add_argument('--promote', action='store_true', help='将已经验收的版本选入放映厅')
    parser.add_argument('--work')
    parser.add_argument('--cut-type')
    parser.add_argument('--sha256')
    args = parser.parse_args()
    values = (args.work, args.cut_type, args.sha256)
    if args.promote:
        if not all(values):
            parser.error('晋级需要 --work、--cut-type 和 --sha256')
        print(json.dumps(promote(args.edition, *values), ensure_ascii=False))
        return
    if any(values):
        parser.error('审核参数须配合 --promote 使用')
    from dsw_film_agent import configure_text, runtime
    from local_story_gateway import generate_json
    runtime('text')
    configure_text()
    data = json.loads(args.edition.read_text(encoding='utf-8'))
    result = publish(args.edition.parent, data, generate_json)
    print(result['slug'], len(result['cues']), result['duration'], flush=True)


if __name__ == '__main__':
    main()
