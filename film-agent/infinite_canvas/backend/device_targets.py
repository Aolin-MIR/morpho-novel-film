"""Typed device targets and validated edits shared by every story view."""
import copy
import re

SPECS = {
    'character': ('characters', '人物', ('goal', 'motivation', 'obstacle', 'arc', 'background', 'dialogue_style')),
    'scene': ('scenes', '场景', ('purpose', 'conflict', 'setting', 'mood')),
    'event': ('events', '事件 / 节拍', ('description', 'consequences', 'foreshadowing')),
    'connection': ('connections', '关系', ('description', 'strength')),
    'worldview': ('worlds', '世界观 / 规则', ('rules', 'culture', 'location', 'political_system', 'social_hierarchy')),
    'theme': ('themes', '主题', ('description', 'examples')),
    'dialogue': ('dialogues', '对白', ('content', 'subtext', 'purpose')),
    'plot_device': ('plot_devices', '其他装置', ('description',)),
    'arc_milestone': ('arc_milestones', '人物弧线节点', ('note',)),
}


def records(value):
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def catalog(payload):
    result = []
    payload = dict(payload or {})
    if not records(payload.get('worlds')) and isinstance(payload.get('worldview'),dict) and payload['worldview']:
        payload['worlds'] = [{**payload['worldview'], 'id':payload['worldview'].get('id') or 'worldview_primary'}]
    names = {str(x['id']): str(x.get('title') or x.get('name') or x['id'])
             for key, _, _ in SPECS.values() for x in records(payload.get(key)) if isinstance(x, dict) and x.get('id')}
    for kind, (key, label, fields) in SPECS.items():
        for item in records(payload.get(key)):
            if not isinstance(item, dict) or not item.get('id'):
                continue
            title = names[str(item['id'])]
            if kind == 'connection':
                title = f"{names.get(item.get('source_id'), item.get('source_id'))} → {names.get(item.get('target_id'), item.get('target_id'))} · {item.get('relationship_type', '')}"
            elif kind == 'dialogue':
                title = str(item.get('content') or title)[:70]
            result.append({'type': kind, 'id': str(item['id']), 'title': title, 'label': label,
                           'scene_id': item.get('scene_id'), 'editable_fields': list(fields),
                           'values': {field: copy.deepcopy(item.get(field)) for field in fields}})
    return result


def episode_number(value):
    match = re.fullmatch(r'(?:第)?\s*([1-9]\d*)\s*集?', str(value or '').strip())
    return int(match.group(1)) if match else None


def episode_scope(devices, payload):
    """Union explicit device episode scopes; one unscoped device means unrestricted."""
    if not devices:
        return None
    scope = set()
    for device in devices:
        requested = (device.get('links') or {}).get('episodes') or []
        if not requested:
            return None
        if not isinstance(requested, list):
            raise ValueError('装置的作用集数格式不正确。')
        for episode in requested:
            number = episode_number(episode)
            if not number:
                raise ValueError('作用集数必须是正整数。')
            scope.add(number)
    return scope


def validate_episode_bindings(device, payload, known):
    requested = (device.get('links') or {}).get('episodes') or []
    if not requested:
        return
    if not isinstance(requested, list):
        raise ValueError('装置的作用集数格式不正确。')
    scope = {episode_number(value) for value in requested}
    if None in scope:
        raise ValueError('作用集数必须是正整数。')
    scenes = {str(s['id']):s for s in records(payload.get('scenes')) if isinstance(s,dict) and s.get('id')}
    available = {episode_number(s.get('episode')) for s in scenes.values()}
    if not scope <= available:
        raise ValueError('所选集数没有场景，请先在画布中设置场景所属集数。')
    links = device.get('links') or {}
    scene_ids = set(links.get('scenes') or [])
    scene_ids.update(x.get('scene_id') for x in records(device.get('setup_nodes'))+records(device.get('payoff_nodes')) if isinstance(x,dict))
    if device.get('attached_scene_id'): scene_ids.add(device['attached_scene_id'])
    attached_event = known.get(('event',str(device.get('attached_event_id') or '')))
    if attached_event and attached_event.get('scene_id'): scene_ids.add(attached_event['scene_id'])
    for target in links.get('targets') or []:
        node = known.get((target.get('type'),target.get('id'))) if isinstance(target,dict) else None
        if node:
            if target['type']=='scene': scene_ids.add(target['id'])
            elif target['type'] in {'event','dialogue'} and node.get('scene_id'): scene_ids.add(node['scene_id'])
    for sid in scene_ids:
        if sid and sid in scenes and episode_number(scenes[sid].get('episode')) not in scope:
            raise ValueError('所选场景或事件不属于装置的作用集数，请调整目标或集数。')

def validate_bindings(devices, payload):
    known = {(x['type'], x['id']):x for x in catalog(payload)}
    for device in devices:
        validate_episode_bindings(device, payload, known)
        targets = (device.get('links') or {}).get('targets', [])
        if not isinstance(targets, list):
            raise ValueError('装置作用目标格式不正确。')
        for target in targets:
            if not isinstance(target, dict) or (target.get('type'), target.get('id')) not in known:
                raise ValueError('装置作用于不存在的元素，请重新选择。')
            if target['id'] == device['id']:
                raise ValueError('装置不能以自身作为作用目标。')


def validate_updates(updates, payload, devices, scene_impacts):
    if not isinstance(updates, list):
        raise ValueError('模型未返回元素级影响清单，请重新分析。')
    known = {(x['type'], x['id']): x for x in catalog(payload)}
    seen = set()
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError('元素影响格式错误。')
        key = (update.get('type'), update.get('id'))
        target = known.get(key)
        patch = update.get('patch')
        if not target:
            raise ValueError('模型返回了不存在的元素，请重新分析；尚未应用任何修改。')
        if key in seen:
            raise ValueError('模型重复修改同一元素，请重新分析；尚未应用任何修改。')
        if not isinstance(patch, dict) or not patch:
            raise ValueError(f"模型没有为“{target['title']}”提供具体修改字段，请重新分析。")
        if not all(isinstance(update.get(k), str) and update[k].strip() for k in ('reason', 'before', 'after')):
            raise ValueError('元素修改缺少完整的因果与前后对照。')
        for field, value in patch.items():
            if field not in target['editable_fields']:
                raise ValueError('模型尝试修改不支持的元素字段，未应用。')
            if field in {'rules', 'examples', 'consequences', 'foreshadowing'}:
                valid = isinstance(value, list) and all(isinstance(x, str) for x in value)
            elif field == 'strength':
                valid = type(value) is int and 1 <= value <= 10
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ValueError('模型返回了无效的元素字段值，未应用。')
        if key[0] == 'plot_device' and not any(d['id'] == key[1] for d in devices):
            raise ValueError('模型尝试改写已删除的装置。')
        if key[0] in {'scene', 'event', 'dialogue'}:
            sid = key[1] if key[0] == 'scene' else target.get('scene_id')
            impact = next((i for i in scene_impacts if i['scene_id'] == sid), None)
            if sid and not impact:
                raise ValueError('元素修改没有同步到所属场景，请重新分析。')
            if impact and key[0] == 'scene' and any(field in patch and patch[field] != impact[field] for field in ('purpose', 'conflict')):
                raise ValueError('场景与元素修改不一致，未应用。')
            if impact and key[0] == 'event' and 'description' in patch:
                event = next((e for e in impact['event_updates'] if e['event_id'] == key[1]), None)
                if not event or patch['description'] != event['description']:
                    raise ValueError('事件与场景改写不一致，未应用。')
        seen.add(key)
        update['title'] = target['title']
        update['label'] = target['label']


def apply_updates(payload, updates):
    for update in updates:
        key = SPECS[update['type']][0]
        values = records(payload.get(key))
        if update['type']=='worldview' and not values and isinstance(payload.get('worldview'),dict):
            target = payload['worldview']
            if str(target.get('id') or 'worldview_primary') != update['id']:
                raise ValueError('世界观目标已不存在，请重新分析。')
        else:
            target = next(x for x in values if str(x['id']) == update['id'])
        target.update(copy.deepcopy(update['patch']))
        if update['type'] == 'worldview' and isinstance(payload.get('worldview'), dict):
            world = payload['worldview']
            if world.get('id') == update['id'] or (not world.get('id') and len(values) <= 1):
                world.update(copy.deepcopy(update['patch']))
