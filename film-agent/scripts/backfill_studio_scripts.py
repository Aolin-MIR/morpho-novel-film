"""Import dialogue and picture notes from the archived cuts currently in the studio."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
from urllib.request import ProxyHandler, build_opener

from film_script import validate_script

AUTHORS = {'镜花缘': '李汝珍', '猫城记': '老舍', '三国演义': '罗贯中',
           '三体': '刘慈欣', '五香街': '残雪'}
MIRROR_BASIS = {'empress': '武则天', 'tang': '唐小山', 'duo': '多九公',
                'lin': '林之洋', 'father': '唐敖', 'ruo': '阴若花'}
COLLECTIONS = ('review-films', 'screenings')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def clock(value):
    hour, minute, second = value.replace(',', '.').split(':')
    return int(hour) * 3600 + int(minute) * 60 + float(second)


def line(start, end, text, kind='对白', evidence='剪辑工程音轨', speaker=None):
    row = dict(start=start, end=end, text=text, kind=kind, evidence=evidence)
    if speaker:
        row['speaker'] = speaker
    return row


def transcript(row, speaker=None, evidence='已转写核对'):
    if not row.get('exact') and row.get('word_match') != 'pronoun_homophone':
        raise ValueError('转写结果尚未核对。')
    return line(row['start_seconds'], row['end_seconds'], row['expected'],
                evidence=evidence, speaker=speaker)


def srt(path, kind, evidence, speaker=None):
    result = []
    for block in re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig').strip()):
        _, times, *text = block.splitlines()
        start, end = map(clock, times.split(' --> '))
        result.append(line(start, end, '\n'.join(text), kind, evidence, speaker))
    return result


def ass(path, cast, narrator=(), initial_speakers=None):
    result = []
    initial_speakers = initial_speakers or {}
    for row in path.read_text(encoding='utf-8-sig').splitlines():
        if not row.startswith('Dialogue: '):
            continue
        _, start, end, style, name, _, _, _, _, text = row.split(',', 9)
        text = re.sub(r'\{[^}]*\}', '', text).replace(r'\N', '\n').replace(r'\h', ' ')
        if not text.strip():
            continue
        begin = clock(start)
        speaker = cast.get(text) or initial_speakers.get(begin) or name or None
        kind = ('画面文字' if 'title' in style.lower() else '旁白' if text in narrator
                else '对白' if speaker else '字幕记录')
        result.append(line(begin, clock(end), text, kind, '随片字幕工程', speaker))
    return result


def remap(rows, in_frame, out_frame, at_frame, fps=24):
    source_start, source_end, dest = (value / fps for value in (in_frame, out_frame, at_frame))
    for row in rows:
        start, end = max(source_start, row['start']), min(source_end, row['end'])
        if end - start < .12:
            continue
        if start > row['start'] + .03 or end < row['end'] - .03:
            if row['kind'] not in ('画面文字', '字幕记录') and not row['evidence'].startswith('随片字幕'):
                continue
        yield {**row, 'start': dest + start - source_start, 'end': dest + end - source_start}


def red(path):
    plan = read(path)
    result = []
    for row in plan['dialogue']:
        who = row.get('role') or row['who'].removesuffix('原生同期').split('，')[0]
        evidence = '原生同期转写' if '同期' in row.get('who', '') else '剪辑工程对白轨'
        result.append(line(row['at'], row['at'] + row['duration'], row['text'], evidence=evidence, speaker=who))
    result.extend(line(row['at'], row['end'], row['text'], '画面文字', '成片字卡轨') for row in plan['titles'])
    return result


def cats(revised, slug):
    cat = revised / '猫城记'
    forest = srt(cat / '迷林的价钱-剪辑工程/迷林的价钱-对白.srt', '对白', '随片字幕工程')
    for row in forest:
        row['speaker'] = ('大蝎' if 23 <= row['start'] < 28 or 36 <= row['start'] < 42
                          or row['start'] > 60 else '旅人')
    if slug == 'cat-country-price-of-the-grove':
        return forest
    cut = cat / '当前剪辑工程' / slug.rsplit('-', 1)[1]
    plan = read(cut / 'timeline.json')
    result = srt(cut / '画外音.srt', '旁白', '随片画外音字幕', '旅人')
    arrival = transcript(read(cat / '当前剪辑工程/动作与节奏复用/C07-02-实际剪入原声.json'),
                         evidence='剪入原生同期转写')
    for shot in plan['shots']:
        start, end = shot['source_frames']
        at, stop = shot['timeline_frames']
        if shot['shot'].startswith('forest-'):
            result.extend(remap(forest, start, end, at))
        elif shot['shot'] == 'C07-02':
            result.extend(remap([arrival], start, end, at))
        if shot['title']:
            result.append(line(at / 24, stop / 24, shot['title'], '画面文字', '剪辑工程字卡'))
    return result


def three_body(archive, revised, page):
    slug = page['slug']
    if slug == 'three-body-third-shadow':
        return ass(archive / '对白与叙事修订/三体/编辑工程/对白与旁白.ass', {},
                   narrator=('汪淼退出游戏。那里的白昼，无法按时到来。',),
                   initial_speakers={.23: '汪淼', 4.35: '汪淼', 16.15: '汪淼'})
    three = revised / '三体'
    source = ('三体-第一部宣传短剪-红岸与观测.json' if slug.endswith('-promo')
              else '三体-第三道影子-单故事短剪.json')
    plan = read(three / source)
    if plan['sha256'] != page['sha256']:
        raise ValueError(f'{slug} 的剪辑工程与播放版本不符。')
    receipt = read(three / '三日证据段-成片音轨回执.json')
    result = []
    for shot in plan['shots']:
        if shot['speech']:
            if shot['sha256'] != receipt['sha256']:
                raise ValueError(f'{slug} 的同期转写与实际剪入镜头不符。')
            result.extend(remap([transcript(receipt, '汪淼', '剪入原生同期转写')],
                                *shot['frames'], shot['at_frames']))
        if shot['title']:
            title, begin, end = shot['title']
            offset = shot['at_frames'] / 24
            result.append(line(offset + begin, offset + end, title, '画面文字', '成片字卡轨'))
    return result


def mirror(archive, revised, page):
    mirror = revised / '镜花缘'
    old = archive / 'jing-hua-yuan-feature/production/review/revision-04'

    def old_captions(film, original_plan):
        log = read(old / original_plan / '应用记录.json')
        cast = {row['text']: MIRROR_BASIS.get(row['role']) for row in log['actor_dialogue']}
        narration = log['narration']
        if isinstance(narration, dict):
            narration = narration.values()
        narrators = [row['start'] for row in narration if isinstance(row, dict) and 'start' in row]
        rows = ass(old / film / '字幕与题字.ass', cast)
        for row in rows:
            if row['kind'] == '字幕记录' and any(abs(start - row['start']) < .015 for start in narrators):
                row['kind'] = '旁白'
        return rows

    slug = page['slug']
    if slug == 'mirror-flowers-fathers-bundle':
        return [transcript(read(mirror / '认包问父-混音后问话证据.json'), '唐小山', '原生同期转写'),
                transcript(read(mirror / '认包问父-混音后答话证据.json'), evidence='两人问答同期转写')]
    if slug == 'review-mirror-story':
        folder = mirror / '寻父-连续审看工程'
        result = [transcript(read(folder / '认包到练攀-混音后问话证据.json'), '唐小山', '原生同期转写'),
                  transcript(read(folder / '认包到练攀-混音后两轮证据.json'), evidence='两人问答同期转写')]
        original = old_captions('寻父叙事对白重剪', '寻父叙事对白重剪') if (
            old / '寻父叙事对白重剪/应用记录.json').is_file() else ass(old / '寻父叙事对白重剪/字幕与题字.ass', {})
        plan = read(folder / '唐小山寻父-连续审看时间线.json')
        for clip in plan['clips']:
            if clip['source'].endswith('/寻父叙事对白重剪/审看.mp4'):
                result.extend(remap(original, clip['first_frame'], clip['last_frame'], clip['destination']))
        return result
    original = old_captions('全书去花瓣与对白', '全书对白修订')
    if slug == 'mirror-flowers-book-revision-four':
        return original
    opener = read(mirror / '全书-新版开场-最终混音同期复核.json')
    if opener['sha256'] != page['sha256']:
        raise ValueError('《镜花缘》全书修订的同期复核与播放版本不符。')
    return [transcript(opener, '武则天', '该版混音转写'),
            *remap(original, 212, 6360, 194), *remap(original, 6732, 14778, 6411)]


def five_spice(archive, page):
    folder = archive / '美学试拍/五香街'
    speech = read(folder / '审看/双证词剪辑-母版对白转写.json')
    verified = {page['sha256'], page.get('sound_revision', {}).get('source')}
    names = ('左侧证人', '右侧证人', 'X 女士', 'X 女士')
    result = []
    for role, row in zip(names, speech, strict=True):
        if row['sha256'] not in verified:
            raise ValueError('《五香街》的转写与播放版本不符。')
        result.append(transcript(row, role, '同期转写，沿用此对白剪段'))
    effect = read(folder / '审看/五香街-取字随动作.json')
    cue = next(cue for cue in page['cues'] if cue['title'] == '她拿走了哪个字')
    contact = cue['start'] + effect['contact_seconds']
    for start, end, key in ((cue['start'], contact, 'caption_before'),
                            (contact, cue['end'], 'caption_after')):
        result.append(line(start, end, effect[key], '画面文字', '取字合成规格'))
    return result


def screenplay_lines(archive, page):
    revised = archive / '八部影片重制'
    match page['book']:
        case '三国演义':
            slug = page['slug']
            if slug == 'red-cliff-deception':
                return red(archive / '对白与叙事修订/赤壁一夜/timeline.json')
            folder = revised / '三国演义/本轮拍摄/两版合剪'
            folder /= '三国演义-以汉之名-全书覆盖试剪' if slug.endswith('-promo') else '赤壁一夜-当日剧情粗剪'
            plan_file = folder / 'timeline.json'
            if abs(read(plan_file)['seconds'] - page['duration']) > .03:
                raise ValueError(f'{slug} 的剪辑工程与播放时长不符。')
            return red(plan_file)
        case '猫城记':
            return cats(revised, page['slug'])
        case '镜花缘':
            return mirror(archive, revised, page)
        case '三体':
            return three_body(archive, revised, page)
        case '五香街':
            return five_spice(archive, page)
    raise ValueError(f'尚未登记 {page["slug"]} 的档案来源。')


def source(source_id, kind, name, use, role, url=None):
    entry = dict(id=source_id, kind=kind, name=name, use=use, role=role)
    if url:
        entry['url'] = url
    return entry


def origins(page):
    book, slug = page['book'], page['slug']
    text = source('novel', '原著', f'{AUTHORS[book]}《{book}》', '实际使用',
                  '改编的人物和情节来源。台词按这版成片整理。')
    if book == '五香街':
        text['role'] = '借用原作的 X 女士、众说纷纭和街坊议论。两份证词、出庭段落和取字为本片的改编设计。'
    methods = [
        text,
        source('harness', 'Harness', 'Codex Harness', '实际使用',
               '编排或重编本版的场次、对白位置、画面文字和实剪顺序。'),
        source('archive', '制作记录', '本版剪辑与声音档案', '实际使用',
               '提供时码和实际剪入内容。每句分别标注同期转写、剪辑音轨或随片字幕。'),
        source('sceneweave', 'SceneWeave', 'SceneWeave', '待核实',
               '项目中用于原著结构整理。本版档案尚无可把这些台词归到 SceneWeave 输出的记录。'),
    ]
    if slug in ('review-cat-promo', 'review-mirror-book', 'mirror-flowers-book-revision-four',
                'review-red-cliff-promo', 'review-three-body-promo'):
        methods.append(source('lotus', '第三方框架', 'Lotus', '借鉴方法',
            '项目的宣传片流程借鉴它的选段、视听分镜、独立审稿和音乐重音方法。本版台本由制作记录整理。',
            'https://github.com/DanielWLiu07/Lotus'))
    if book == '五香街':
        methods.append(source('art', 'Skill', 'film-art-direction', '借鉴方法',
            '用叙事越界设计人物取字。两份证词的冲突先成立，取字后接人物发问和记录者放下笔。'))
    return methods


def build(archive, page):
    materials = origins(page)
    scenes = []
    last = 0
    duration = page['duration']
    for cue in page['cues']:
        start, end = round(max(last, cue['start']), 6), round(min(duration, cue['end']), 6)
        if end <= start:
            continue
        tags = ['harness', 'archive']
        if page['book'] == '五香街' and re.search(r'取字|不字|越界', cue['title'] + cue['line']):
            tags.append('art')
        scenes.append(dict(start=start, end=end, title=cue['title'], action=cue['line'],
                           sources=tags, lines=[]))
        last = end
    seen = set()
    for row in sorted(screenplay_lines(archive, page), key=lambda item: item['start']):
        start, stop = round(row['start'], 6), round(min(row['end'], duration), 6)
        if not 0 <= start < stop:
            continue
        key = (start, stop, row['kind'], row['text'])
        if key in seen:
            continue
        seen.add(key)
        eligible = [scene for scene in scenes if scene['start'] <= start]
        if eligible and start < eligible[-1]['end']:
            eligible[-1]['lines'].append({**row, 'start': start, 'end': stop})
        else:
            following = next((scene for scene in scenes if scene['start'] > start), None)
            end = following['start'] if following else duration
            if end > start:
                supplement = dict(start=start, end=end, title='补充声音与画面文字',
                                  action='这段有独立的声音或字卡记录。', sources=['archive'], lines=[])
                if eligible and eligible[-1]['title'] == supplement['title'] and eligible[-1]['end'] == start:
                    eligible[-1]['end'] = end
                    supplement = eligible[-1]
                else:
                    scenes.insert(scenes.index(following) if following else len(scenes), supplement)
                supplement['lines'].append({**row, 'start': start, 'end': stop})
    note = ('按当前播放版的剪辑、声音和字幕档案整理。段落以镜头动作和剪辑线索为主，'
            '旧段仅有字幕记录的地方按原标签显示。')
    result = dict(sha256=page['sha256'], edition='实剪台本', note=note, sources=materials, scenes=scenes)
    return validate_script(result, page['sha256'], duration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-origin', required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))

    def api(route):
        with opener.open(f"{args.review_origin.rstrip('/')}/api/{route}", timeout=15) as response:
            return json.load(response)

    def export(route):
        page = api(route)
        result = build(args.archive, page)
        directory = args.output / route
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'script.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                               encoding='utf-8')
        return route, len(result['scenes']), sum(len(x['lines']) for x in result['scenes'])

    routes = [f"{collection}/{page['slug']}" for collection in COLLECTIONS for page in api(collection)]
    with ThreadPoolExecutor(max_workers=6) as workers:
        for route, scenes, utterances in workers.map(export, routes):
            print(f'{route}: {scenes} scenes, {utterances} caption or dialogue entries')


if __name__ == '__main__':
    main()
