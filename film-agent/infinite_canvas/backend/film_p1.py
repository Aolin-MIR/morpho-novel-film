"""Director cards, archived assets and evidence for the finished edit."""
import json
from pathlib import Path
import re
import shutil

from media_segments import fingerprint
from shot_visual_style import style_axes

FEATURES = {
    'source': '全书原文与情节核对',
    'camera': '摄影和镜头运动规格',
    'style': '生成端多维风格',
    'assets': '已审核参考素材与版本',
    'match': '前镜出点约束',
    'previs': '代码三维动作预演',
    'rig_audit': '手部接触与步态检查',
    'code': '纯代码镜头与过门',
    'overlay': '同镜代码特效',
    'revision': '定点返修选片',
    'trim': '连续性剪辑',
    'reference_edit': '对标片节奏与画面分析',
}


def shot_card(shot, assets):
    refs = shot['reference_ids']
    if not 1 <= len(refs) <= 5 or len(set(refs)) != len(refs):
        raise ValueError(f'{shot["id"]} 的参考图必须是 1–5 个不同素材。')
    linked = [{'id': key, 'kind': assets[key]['kind'], 'name': assets[key]['name']} for key in refs]
    if all(asset['kind'] != 'location' for asset in linked):
        raise ValueError(f'{shot["id"]} 缺少场景参考。')
    camera = {key: shot[key] for key in ('scale', 'camera', 'composition', 'first_frame', 'last_frame')}
    return {'shot_id': shot['id'], 'story_event': shot['event'], 'source_excerpt': shot['source_excerpt'],
            'screen_action': shot['screen_action'], 'timing': shot['timing'],
            'continuity': shot['continuity'], 'handoff': shot['handoff'], 'camera': camera,
            'style': style_axes(shot), 'assets': linked}


def normalized(text):
    return re.sub(r'\s+', '', text)


def audit_director(package, source):
    text = normalized(source)
    assets = {item['id']: item for item in package['bible']['assets']}
    acts = []
    seen_actions = {}
    for scene in package['scenes']:
        cards = [shot_card(shot, assets) for shot in scene['shots']]
        events = [card['story_event'] for card in cards]
        if (not events or any(type(event) is not int or event < 1 for event in events)
                or events != sorted(events) or set(events) != set(range(1, events[-1] + 1))):
            raise ValueError(f'{scene["title"]} 的镜头有缺失的情节或打乱了情节顺序。')
        last_event = None
        positions = []
        for card in cards:
            quote = normalized(card['source_excerpt'])
            if not quote or (offset := text.find(quote)) < 0:
                raise ValueError(f'{card["shot_id"]} 的原文依据未在原著中找到。')
            action = normalized(card['screen_action'])
            if action in seen_actions:
                raise ValueError(f'{card["shot_id"]} 与 {seen_actions[action]} 重复使用同一个动作。')
            seen_actions[action] = card['shot_id']
            card['source_offset'] = offset
            if card['story_event'] != last_event:
                positions.append(offset)
            last_event = card['story_event']
        acts.append({'title': scene['title'], 'scales': sorted({c['camera']['scale'] for c in cards}),
                     'source_positions': positions, 'shots': cards})
    return acts


def read_optional(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def timecode(frame):
    seconds, subframe = divmod(frame, 24)
    minutes, sec = divmod(seconds, 60)
    return f'{minutes:02d}:{sec:02d}:{subframe:02d}'


def freeze_reference(archive, path):
    digest = fingerprint(path)
    snapshots = archive / 'creative-records' / 'reference-snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    copy = snapshots / (digest + path.suffix.lower())
    if not copy.is_file():
        temporary = copy.with_suffix('.part')
        shutil.copyfile(path, temporary)
        temporary.replace(copy)
    return digest


def write_audio_tracks(project, cuts, narration, score, effects):
    bank = project / 'assets' / 'audio'
    bank.mkdir(parents=True, exist_ok=True)

    def archive_source(path):
        name = fingerprint(path) + path.suffix.lower()
        target = bank / name
        if not target.is_file():
            shutil.copyfile(path, target)
        return f'assets/audio/{name}'

    tracks = {'同期声': [], '画外音': [], '配乐': [], '空间音效': []}
    elapsed = 0
    for index, ((start, end), voice) in enumerate(zip(cuts, narration, strict=True), 1):
        period = end - start
        tracks['同期声'].append({'shot': index, 'file': f'assets/source-{index:03d}.mp4',
                                'at': round(elapsed, 6), 'source_in': start, 'duration': round(period, 6),
                                'duck_under_voice': bool(voice)})
        if voice:
            path, delay, pace = voice
            tracks['画外音'].append({'shot': index, 'file': archive_source(path), 'at': round(elapsed + delay / 1000, 6),
                                  'delay_ms': delay, 'tempo': pace})
        elapsed += period
    for path, start, duration, offset in score:
        tracks['配乐'].append({'file': archive_source(path), 'at': start, 'duration': duration,
                             'source_in': offset, 'gain': .16})
    for path, start, duration, cue in effects:
        tracks['空间音效'].append({'file': archive_source(path), 'at': start, 'duration': duration,
                                'cue': cue})
    return tracks


def write_usage(project, archive, film, shots, takes, cuts, choices, audio_tracks):
    records = archive / 'creative-records'
    audit = json.loads((records / 'director.json').read_text(encoding='utf-8'))
    cards = {card['shot_id']: card for section in audit['acts'] for card in section['shots']}
    references = json.loads((records / 'assets.json').read_text(encoding='utf-8'))
    motion = read_optional(records / 'previs.json')
    receipts = read_optional(records / 'archived-generations.json')
    reference_study = read_optional(records / 'reference-study.json')
    bridge_choices = read_optional(records / 'bridge-choices.json')
    edit = json.loads((project / 'edit.json').read_text(encoding='utf-8'))
    frame = 0
    items = []
    for index, (shot, take, (start, end)) in enumerate(zip(shots, takes, cuts, strict=True)):
        shot_id = shot['id']
        choice = choices.get(shot_id, {})
        uses = {}
        card = cards.get(shot_id)
        plate = shot.get('picture_overlay', {}).get('plate', take)
        take_hash = fingerprint(plate)
        job = read_optional(plate.with_suffix('.job.json'))
        evidence = job.get('production') or receipts.get(job.get('id'), {})
        if evidence.get('take_sha256', take_hash) != take_hash:
            raise ValueError(f'H3 生成记录与选镜版本不一致：{shot_id}')
        if card:
            uses['source'] = {'excerpt': card['source_excerpt'], 'event': card['story_event'],
                              'offset': card['source_offset']}
        if evidence.get('card'):
            uses['camera'] = evidence['card']['camera']
        if evidence.get('style_axes'):
            uses['style'] = evidence['style_axes']
        if evidence.get('references'):
            uses['assets'] = evidence['references']
        if evidence.get('previous_take'):
            uses['match'] = evidence['previous_take']
        if evidence.get('motion_sha256'):
            uses['previs'] = {'motion_sha256': evidence['motion_sha256']}
            check = motion.get(shot_id)
            if check and check['reference_sha256'] == evidence['motion_sha256']:
                uses['rig_audit'] = check
        if shot.get('transition_source'):
            uses['code'] = shot['transition']
            if (study := bridge_choices.get(shot_id)) and study['source_sha256'] == fingerprint(shot['transition_source']):
                uses['reference_edit'] = study
        if shot.get('picture_overlay'):
            uses['overlay'] = {'source': shot['picture_overlay']['spec'].name}
        if choice.get('repaired') and choice.get('approved_sha256') == take_hash:
            original = archive / 'shots' / (shot_id + '.mp4')
            alternate = project / 'assets' / 'alternate-takes' / original.name
            alternate.parent.mkdir(exist_ok=True)
            shutil.copyfile(original, alternate)
            relative = alternate.relative_to(project).as_posix()
            uses['revision'] = {'selected': plate.name, 'original': relative, 'note': choice.get('note', '')}
            edit['shots'][index]['alternate_takes'] = [{'file': relative, 'sha256': fingerprint(original)}]
        if choice.get('frames'):
            uses['trim'] = {'source_frames': choice['frames'], 'note': choice.get('note', '')}
        frames = round((end - start) * 24)
        items.append({'shot_id': shot_id, 'from': timecode(frame), 'to': timecode(frame + frames),
                      'take_sha256': take_hash, 'uses': uses})
        frame += frames
    edit['audio_tracks'] = audio_tracks
    edit['director_records'] = '制作说明.json'
    (project / 'edit.json').write_text(json.dumps(edit, ensure_ascii=False, indent=2), encoding='utf-8')
    stored = project / 'assets' / 'reference-library'
    stored.mkdir(parents=True, exist_ok=True)
    used_assets = {(item['id'], item['sha256'])
                   for row in items for item in row['uses'].get('assets', [])}
    verified_library = {}
    for asset_id, digest in sorted(used_assets):
        item = references[asset_id]
        source = records / 'reference-snapshots' / (digest + '.jpg')
        if fingerprint(source) != digest:
            raise ValueError(f'参考图版本已经变动：{asset_id}')
        filename = f'{asset_id}-{digest[:12]}.jpg'
        shutil.copyfile(source, stored / filename)
        verified_library[f'{asset_id}:{digest[:12]}'] = {
            **item, 'file': f'assets/reference-library/{filename}', 'sha256': digest}
    report = {'title': film['source_title'], 'fps': 24, 'duration': timecode(frame),
              'edition': audit['edition'], 'chapters': [x['title'] for x in audit['acts']],
              'shots': items, 'reference_library': verified_library,
              'audio_tracks': audio_tracks, 'reference_study': reference_study,
              'trial_results': read_optional(records / 'trials.json')}
    grouped = {feature: [row['from'] for row in items if feature in row['uses']]
               for feature in FEATURES}
    lines = [f'# {film["source_title"]} · 功能使用清单', '', f'片长 {timecode(frame)}（分:秒:帧）', '',
             '## 用在交付版中的环节', '']
    for feature, label in FEATURES.items():
        if locations := grouped[feature]:
            preview = '、'.join(locations[:8])
            lines.append(f'- **{label}** · {len(locations)} 镜 · {preview}')
    if audio_tracks:
        lines.append('- **独立音频素材和时间表** · 位于工程的 edit.json 和 assets/audio。')
    lines += ['', '## 试拍与研究记录', '']
    lines.extend(f'- **{name}** · {result["result"]}' for name, result in report['trial_results'].items())
    lines += ['', '每个镜头的完整时间码、所选版本、使用证据和原文出处见制作说明.json。']
    (project / '制作说明.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (project / 'P1-使用清单.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
