"""Compact, explicit context selection; keep the full story outline and constraints."""
from collections import defaultdict, deque
from device_targets import SPECS, catalog, records

CAUSAL = {'causes', 'influences', 'affects', 'triggers', 'leads_to', 'motivates',
          'enables', 'prevents', 'setup_in', 'payoff_in', 'binds_to', 'participates_in', 'contains'}


def targets(device):
    if not device:
        return set()
    links = device.get('links') or {}
    ids = [device.get('attached_scene_id'), device.get('attached_event_id')]
    ids += [x.get('scene_id') for x in records(device.get('setup_nodes')) + records(device.get('payoff_nodes'))]
    ids += [x.get('id') for x in records(links.get('targets'))]
    for key in ('scenes', 'characters', 'relationships', 'events', 'themes', 'props'):
        ids += records(links.get(key))
    return {str(x) for x in ids if isinstance(x, (str, int)) and x}


def select_script_context(payload, delta):
    nodes = {str(n['id']): {**n, '_kind': kind} for kind, (key, _, _) in SPECS.items()
             for n in records(payload.get(key)) if isinstance(n, dict) and n.get('id')}
    if not records(payload.get('worlds')) and isinstance(payload.get('worldview'),dict) and payload['worldview']:
        world = payload['worldview']; wid = str(world.get('id') or 'worldview_primary')
        nodes[wid] = {**world, 'id':wid, '_kind':'worldview'}
    edges = defaultdict(set)

    def edge(a, b):
        a, b = str(a or ''), str(b or '')
        if a in nodes and b in nodes and a != b:
            edges[a].add(b)

    for nid, node in nodes.items():
        sid = node.get('scene_id') or node.get('sceneId') or node.get('attached_scene_id')
        if sid:
            edge(nid, sid); edge(sid, nid)
        if node['_kind'] == 'scene':
            for cid in records(node.get('characters_present')):
                edge(cid.get('id') if isinstance(cid, dict) else cid, nid)
            for eid in records(node.get('events')):
                eid = eid.get('id') if isinstance(eid, dict) else eid
                edge(nid, eid); edge(eid, nid)
        if node['_kind'] == 'connection':
            a, b = node.get('source_id') or node.get('sourceId'), node.get('target_id') or node.get('targetId')
            edge(nid, a); edge(nid, b)
            relation = str(node.get('relationship_type') or node.get('relationshipType') or '').lower()
            if relation in CAUSAL:
                edge(a, b)
            elif relation == 'depends_on':
                edge(b, a)
        if node['_kind'] == 'plot_device':
            for target in targets(node):
                edge(nid, target)
        if node['_kind'] == 'arc_milestone':
            edge(node.get('character_id'), nid)
    seeds = set()
    for change in delta:
        seeds |= targets(change.get('before')) | targets(change.get('after'))
        if change['id'] in nodes:
            seeds.add(change['id'])
    queue, reached = deque(seeds), set(seeds)
    while queue:
        for neighbor in edges[queue.popleft()]:
            if neighbor not in reached:
                reached.add(neighbor); queue.append(neighbor)
    scenes = records(payload.get('scenes'))
    candidates = {str(s['id']) for s in scenes if str(s['id']) in reached}
    # With no explicit scene scope, inspect every scene rather than guessing a target.
    if not candidates:
        return {str(s['id']) for s in scenes}
    full = set(candidates)
    for i, scene in enumerate(scenes):
        if str(scene['id']) in candidates:
            full.update(str(scenes[j]['id']) for j in (i-1, i+1) if 0 <= j < len(scenes))
    return full


def analysis_context(payload, devices, delta, guides):
    full = select_script_context(payload, delta)
    scene_fields = ('id', 'episode', 'title', 'name', 'purpose', 'conflict', 'setting', 'mood', 'characters_present')
    scenes = []
    for scene in records(payload.get('scenes')):
        packet = {k: scene[k] for k in scene_fields if scene.get(k) not in (None, '', [])}
        if str(scene['id']) in full:
            packet['script_text'] = scene.get('script_text') or ''
        scenes.append(packet)
    # Each field appears once; editable field contracts are shared by type.
    elements = [{k:v for k,v in n.items() if k not in {'editable_fields', 'label'} and
                 (k != 'values' or n['type'] not in {'character', 'scene', 'event'})} for n in catalog(payload)]
    return {'worldview': payload.get('worldview'), 'characters': records(payload.get('characters')),
            'scenes': scenes, 'events': records(payload.get('events')), 'element_catalog': elements,
            'editable_fields_by_type': {kind:list(fields) for kind, (_, _, fields) in SPECS.items()},
            'device_type_guides': guides(devices), 'device_changes': delta, 'devices_after': devices,
            'script_context_scope': {'full_text_scene_ids': sorted(full),
                'note': '所有场景均提供结构摘要；仅关联场景及相邻场景提供正文。图关联仅为检索线索；可据完整剧情摘要判断范围外的影响，不可臆造未提供的对白细节。'},}


LABELS = {'purpose':'目的', 'conflict':'冲突', 'goal':'目标', 'motivation':'动机', 'obstacle':'阻碍',
          'arc':'人物弧线', 'background':'背景', 'dialogue_style':'对白风格', 'description':'描述',
          'setting':'地点', 'mood':'氛围', 'consequences':'后果', 'foreshadowing':'伏笔',
          'strength':'关系强度', 'rules':'规则', 'culture':'文化', 'location':'地点',
          'political_system':'权力结构', 'social_hierarchy':'社会阶层', 'examples':'例子',
          'content':'内容', 'subtext':'潜台词', 'note':'说明'}


def describe_fields(values):
    lines = []
    for key, value in values.items():
        if value is None or value == '' or value == []:
            continue
        text = '；'.join(str(x) for x in value) if isinstance(value, list) else str(value)
        lines.append(f'{LABELS.get(key, key)}：{text}')
    return '\n'.join(lines) or '尚未设置'
