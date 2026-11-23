"""Model-authored device interventions shared by original and adapted scripts."""
import copy
import hashlib
import json
import time
from byok import generate_text
from device_type_catalog import guides
from device_context import analysis_context, describe_fields
from device_targets import SPECS, catalog, validate_bindings, validate_updates, apply_updates, episode_scope, episode_number

MODEL = 'qwen3.8-flash'


def suggest(payload, device):
    if not isinstance(device, dict) or not str(device.get('device_type') or '').strip():
        raise ValueError('请先选择装置类型。')
    validate_bindings([device], payload)
    targets = (device.get('links') or {}).get('targets', [])
    selected = [item for item in catalog(payload) if any(t['type'] == item['type'] and t['id'] == item['id'] for t in targets)]
    context = analysis_context(payload, items(payload.get('plot_devices')),
                               [{'id':device.get('id','draft'), 'before':None, 'after':device}], guides)
    context.update(device_draft=device, selected_targets=selected, device_type_guides=guides([device]))
    serialized = json.dumps(context, ensure_ascii=False, default=str, separators=(',', ':'))
    if len(serialized) > 180000:
        raise ValueError('项目过长，请分集后生成装置方案。')
    start = time.monotonic()
    raw = generate_text('用户不知道如何填写戏剧装置的作用。结合其选中的装置类型、目标元素、当前剧情及用户修改意图，提出3个不同且具体可执行的方案。'
                        'selected_targets 非空时，每个方案必须直接作用于所有已选目标。已选事件的主要改动必须发生在该事件所属场景，不能移到别的场景替代。'
                        'description 必须原文写出已选人物、场景和事件的 title，并具体写出他们各自的变化。其他元素只允许必要的连带影响。'
                        '没有选目标时，根据已有剧情推荐合适位置，但不要创造不存在的人物或节点。每个方案写清铺垫动作、如何改变目标及何时回收，不写空泛术语。'
                        '描述用人物与场景中文名称，不用 scene_01 之类系统ID。严格落实选中类型的 definition 与 guidance，倒高潮不可改成假高潮。'
                        '原创和改编均不受原著限制；按用户意图调整。每个title最多12字，description尽量控制在120字，reason最多40字。'
                        '只输出 JSON {"suggestions":[{"title":"短标题","description":"可直接填写到剧情作用栏的具体指令","reason":"为什么适合已有故事"}]}。\n'+serialized,
                        model=MODEL, temperature=0.6, max_tokens=1800, timeout=90, json_response=True, enable_thinking=False)
    result = json.loads(raw)
    suggestions = result.get('suggestions') if isinstance(result, dict) else None
    if not isinstance(suggestions, list) or len(suggestions) != 3 or any(
            not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s[k].strip() for k in ('title', 'description', 'reason'))
            for s in suggestions):
        raise ValueError('模型未返回完整方案，请重新生成。')
    required_names = [item['title'] for item in selected if item['type'] in {'character', 'scene', 'event'}]
    if any(name not in proposal['description'] for proposal in suggestions for name in required_names):
        raise ValueError('模型方案未覆盖选中的人物或事件，请重新生成；不会替换你的目标。')
    return {'suggestions': suggestions, 'model': MODEL, 'elapsed_seconds': round(time.monotonic()-start, 2)}


def items(value):
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def fingerprint(payload):
    # Ignore runtime timestamps, graph caches and review metadata.
    data = {key: items(payload.get(key)) for key, _, _ in SPECS.values()}
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {'created_at', 'updated_at', 'coverage', 'atomic_facts'}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    data['worldview'] = payload.get('worldview')
    data['adaptation'] = (payload.get('workspace_meta') or {}).get('sceneweave')
    return hashlib.sha256(json.dumps(clean(data), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def changes(before, after):
    old = {str(d['id']): d for d in items(before)}
    new = {str(d['id']): d for d in items(after)}
    keys = ('title', 'device_type', 'description', 'attached_scene_id', 'attached_event_id', 'setup_nodes', 'payoff_nodes', 'links')
    result = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if a and b and all(a.get(k) == b.get(k) for k in keys):
            continue
        result.append({'id': key, 'operation': 'add' if not a else 'delete' if not b else 'update', 'before': a, 'after': b})
    return result


def validate_devices(devices, payload):
    if not isinstance(devices, list) or len(devices) > 100:
        raise ValueError('装置列表无效，最多支持 100 个装置。')
    validate_bindings(devices, payload)
    ids = set()
    scenes = {str(s['id']) for s in items(payload.get('scenes'))}
    events = {str(s['id']) for s in items(payload.get('events'))}
    for d in devices:
        if not isinstance(d, dict) or not all(str(d.get(k) or '').strip() for k in ('id', 'title', 'device_type', 'description')):
            raise ValueError('请填写每个装置的名称、类型和具体作用。')
        if d['id'] in ids:
            raise ValueError('装置 ID 重复。')
        ids.add(d['id'])
        for field, allowed in (('attached_scene_id', scenes), ('attached_event_id', events)):
            if d.get(field) and d[field] not in allowed:
                raise ValueError('装置绑定了不存在的场景或事件。')
        for binding in list(d.get('setup_nodes') or []) + list(d.get('payoff_nodes') or []):
            if binding.get('scene_id') and binding['scene_id'] not in scenes:
                raise ValueError('铺垫或回收场景不存在。')
        for sid in (d.get('links') or {}).get('scenes', []):
            if sid not in scenes:
                raise ValueError('关联场景不存在。')


class ModelValidationError(ValueError):
    def __init__(self,message,response):
        super().__init__(message);self.response=response


def analyze(payload,devices):
    correction=None
    for attempt in range(3):
        try:return _analyze_once(payload,devices,correction)
        except ModelValidationError as exc:
            if attempt==2:raise
            from byok import recovery_notice
            recovery_notice('影响判断未通过校验，正在根据具体原因修正：'+str(exc))
            correction={'previous_response':exc.response,'validation_error':str(exc),
                'requirements':'按错误修正后返回完整影响分析，保持目标ID、集数及字段约束，不得删除必需事件。'}


def _analyze_once(payload, devices, correction=None):
    validate_devices(devices, payload)
    delta = changes(payload.get('plot_devices'), devices)
    if not delta:
        raise ValueError('装置尚未改变。')
    scenes = items(payload.get('scenes'))
    if not scenes:
        raise ValueError('请先创建剧情场景，再分析装置影响。')
    scoped_devices = [change['after'] if change['after'] is not None else change['before'] for change in delta]
    allowed_episodes = episode_scope(scoped_devices, payload)
    context = analysis_context(payload, devices, delta, guides)
    if correction:context['correction']=correction
    context['allowed_episodes'] = sorted(allowed_episodes) if allowed_episodes is not None else None
    context['allowed_scene_ids'] = [str(s['id']) for s in scenes if allowed_episodes is None or episode_number(s.get('episode')) in allowed_episodes]
    raw_context = json.dumps(context, ensure_ascii=False, default=str, separators=(',', ':'))
    if len(raw_context) > 180000:
        raise ValueError('项目内容过长，请先分集后分析；不会截断原著或忽略场景。')
    start = time.monotonic()
    raw = generate_text(
        '分析用户添加、修改、删除戏剧装置对剧情的真实影响。不要仅沿连线扩散，不要把所有场景都判为受影响。'
        '删除时指出失去依据的铺垫、选择、线索或回收，并提出修复。区分直接影响和因果连带影响。'
        '原创与改编使用相同规则，均按用户意图自由改写，不受原著人物命运、事件或结局限制。'
        '只输出 JSON: {"summary":"总体判断","risks":["剧情衔接风险"],"impacts":[{"scene_id":"已有ID",'
        '"level":"direct或downstream","reason":"具体因果解释","purpose":"新场景目的","conflict":"新冲突",'
        '"instruction":"给编剧的具体改写要求","event_updates":[{"event_id":"本场已有事件ID","description":"修改后事件"}]}]}。每场最多一次，未受影响的场景不要列出。'
        '每个受影响场景的 event_updates 必须覆盖该场所有已有事件，没有事件则为空数组。'
        '另必须返回 element_updates 数组（无元素字段变化时为空）。装置 links.targets 是用户指定的精细作用目标，可能是人物、关系、事件、主题、世界规则、对白或弧线节点。'
        '逐个判断这些目标的影响，并解释如何传到场景。element_updates 每项为 {"type":"catalog中的type","id":"已有ID","reason":"因果说明","patch":{"editable_fields_by_type中该类型允许的字段":"更新值"}}。'
        '注意：element_updates 只列确实有字段变更的既有元素；不改变资料的目标只在summary说明，不要为其输出空patch。每个type/id只能出现一次，精确复制element_catalog的type和id。禁止为本次新建装置输出element_updates，它已包含在devices_after。'
        'allowed_episodes非空时，impacts 的 scene_id 只能从 allowed_scene_ids 中选择，只能修改这些场景及其所属事件和对白；其他集的场景不可改写。场景已有episode是硬约束，不得改变集数。'
        '只改必要字段，保留节点ID和连线端点。列表字段仍返回完整字符串数组，strength为1到10整数。'
        '事件description须与对应场景event_updates完全一致；场景purpose/conflict须与impacts完全一致。事件、对白、场景修改必须同步所属场景impacts。'
        '若无实质影响允许空 impacts，但 summary 必须说明理由。不要生成剧本正文。'
        '保持精炼：summary最多60字，risks最多3条且每条40字；每项reason最多50字、purpose和conflict各40字、instruction最多90字；事件description最多80字。'
        '不输出before、after，不复述原文和分析过程，不重复同一原因；只输出真实需要改动的对象和字段。'
        'element_catalog提供类型和ID，editable_fields_by_type提供可修改字段；完整人物、场景和事件值在对应列表中。\n'+raw_context,
        model=MODEL, temperature=0.25, max_tokens=min(6000, max(1800, 450*len(scenes))), timeout=120, json_response=True, enable_thinking=False)
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or not str(result.get('summary') or '').strip() or not isinstance(result.get('impacts'), list):
            raise ValueError('模型影响分析格式不完整，请重新分析。')
        seen = set()
        events_by_scene = {}
        for event in items(payload.get('events')):
            events_by_scene.setdefault(str(event.get('scene_id') or event.get('sceneId') or ''), set()).add(str(event['id']))
        scene_by_id = {str(s['id']): s for s in scenes}
        known = set(scene_by_id)
        for impact in result['impacts']:
            if not isinstance(impact, dict):
                raise ValueError('模型影响分析格式不正确，请重新分析。')
            sid = str(impact.get('scene_id') or '')
            if sid not in known or sid in seen or impact.get('level') not in {'direct', 'downstream'}:
                raise ValueError('模型返回未知、重复场景或无效影响级别，未应用。')
            if allowed_episodes is not None and episode_number(scene_by_id[sid].get('episode')) not in allowed_episodes:
                raise ValueError('分析结果超出了所选集数，未应用任何修改，请重新分析。')
            impact['before'] = describe_fields({k:scene_by_id[sid].get(k) for k in ('purpose','conflict')})
            impact['after'] = impact.get('instruction')
            if not all(isinstance(impact.get(k), str) and impact[k].strip() for k in ('reason', 'before', 'after', 'purpose', 'conflict', 'instruction')):
                raise ValueError('模型未给出完整的因果和改写依据，未应用。')
            event_updates = impact.get('event_updates')
            expected_events = events_by_scene.get(sid, set())
            if not isinstance(event_updates, list) or len(event_updates) != len(expected_events) or any(not isinstance(e,dict) for e in event_updates) or {str(e.get('event_id')) for e in event_updates} != expected_events or any(not str(e.get('description') or '').strip() for e in event_updates):
                raise ValueError('模型未同步更新受影响场景的事件，未应用。')
            seen.add(sid)
        source_catalog = {(n['type'],n['id']):n for n in catalog(payload)}
        updates = result.get('element_updates', [])
        if not isinstance(updates,list):
            raise ValueError('模型未返回有效的元素修改，请重新分析。')
        for update in updates:
            if isinstance(update, dict) and isinstance(update.get('patch'), dict):
                source = source_catalog.get((update.get('type'),update.get('id')), {}).get('values', {})
                update['before'] = describe_fields({k:source.get(k) for k in update['patch']})
                update['after'] = describe_fields(update['patch'])
        validate_updates(result.get('element_updates', []), payload, devices, result['impacts'])
        result.setdefault('element_updates', [])
        result.update(model=MODEL, elapsed_seconds=round(time.monotonic()-start, 2), changes=delta,
                      baseline_hash=fingerprint(payload), proposed_devices=devices)
        return result
    except ValueError as exc:
        raise ModelValidationError(str(exc),raw) from exc


def analyze_cached(payload, devices, store, namespace):
    """Exact project + story + draft cache. Never reuse across a content revision."""
    validate_devices(devices, payload)
    baseline = fingerprint(payload)
    draft_hash = hashlib.sha256(json.dumps(devices, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':'), default=str).encode()).hexdigest()
    key = f'morpho:device-impact:v4:{namespace}:{baseline}:{draft_hash}'
    cached = store.get(key)
    if cached:
        result = json.loads(cached)
        if result.get('baseline_hash') != baseline or result.get('proposed_devices') != devices:
            raise ValueError('分析记录与当前修改不一致，请重新分析。')
        return result
    result = analyze(payload, devices)
    store.setex(key, 900, json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return result


def rewrite(payload, analysis, progress=None):
    if fingerprint(payload) != analysis['baseline_hash']:
        raise ValueError('项目已在分析后改变，请重新分析，避免覆盖新修改。')
    result = copy.deepcopy(payload)
    result['plot_devices'] = copy.deepcopy(analysis['proposed_devices'])
    apply_updates(result, analysis.get('element_updates', []))
    scenes = items(result.get('scenes'))
    by_id = {str(s['id']): s for s in scenes}
    impacts = analysis['impacts']
    comparisons = []
    texts = {}
    structure_only = not any(str(s.get('script_text') or '').strip() for s in scenes)
    if structure_only:
        for impact in impacts:
            scene = by_id[impact['scene_id']]
            comparisons.append({'scene_id': scene['id'], 'title': scene.get('title') or scene['id'],
                                'before': '\n'.join(str(scene.get(k) or '') for k in ('purpose', 'conflict')),
                                'after': impact['purpose']+'\n'+impact['conflict'], 'reason': impact['reason']})
            scene.update(purpose=impact['purpose'], conflict=impact['conflict'])
    for index in range(0, 0 if structure_only else len(impacts), 2):
        batch = impacts[index:index+2]
        if progress:
            progress(index, len(impacts))
        context = {'worldview': result.get('worldview'), 'characters': items(result.get('characters')),
                   'device_type_guides': guides(result['plot_devices']), 'devices_after': result['plot_devices'], 'device_changes': analysis['changes'],
                   'element_updates': analysis.get('element_updates', []), 'element_catalog_after': catalog(result),
                   'complete_impact_plan': impacts, 'targets': batch,
                   'story_context': [{k: s.get(k) for k in ('id','title','purpose','conflict','script_text')} for s in scenes]}
        for attempt in range(3):
            raw = generate_text('按照已确认的装置影响分析修改指定场景。修改动作、对白和因果，不要仅在正文里提及装置名称。'
                                '保持场景 ID；按用户修改意图调整人物与情节，原创和改编均不受原著限制。删除装置时消除依赖并补全因果。'
                                '未指定场景保持原样，衔接其已存在正文。动作与对白简洁，只保留推动剧情和人物选择的内容；不复述分析，不输出前言、总结或解释。输出 JSON {"scenes":[{"scene_id":"指定ID","script_text":"完整可拍摄场景正文"}]}，'
                                '必须且只能返回 targets 中每个场景。\n'+json.dumps(context, ensure_ascii=False, default=str),
                                model=MODEL, temperature=0.5, max_tokens=6500, timeout=240, json_response=True, enable_thinking=False)
            try:
                response = json.loads(raw)
                written = response.get('scenes') if isinstance(response, dict) else None
                expected = {i['scene_id'] for i in batch}
                if (not isinstance(written, list) or len(written) != len(expected)
                        or any(not isinstance(scene, dict) for scene in written)
                        or {scene.get('scene_id') for scene in written} != expected):
                    raise ValueError('模型未准确返回目标场景，全部修改尚未保存。')
                if any(not isinstance(e.get('script_text'),str) or len(e['script_text'].strip())<80 for e in written):
                    raise ValueError('场景正文不完整，请返回每个目标场景的完整正文。')
                break
            except ValueError as exc:
                if attempt==2:raise
                from byok import recovery_notice
                recovery_notice('正在校正场面正文：'+str(exc))
                context={**context,'correction':{'previous_response':raw,'validation_error':str(exc),'requirements':'精确返回targets全部scene_id及各自完整script_text，不能遗漏或多加场景。'}}
        for entry in written:
            sid = entry['scene_id']; body = entry.get('script_text')
            if not isinstance(body, str) or len(body.strip()) < 80:
                raise ValueError('模型返回的场景正文不完整，未保存。')
            old = by_id[sid]; impact = next(i for i in batch if i['scene_id']==sid)
            comparisons.append({'scene_id': sid, 'title': old.get('title') or old.get('name') or sid,
                                'before': old.get('script_text') or '', 'after': body, 'reason': impact['reason']})
            old.update(purpose=impact['purpose'], conflict=impact['conflict'], script_text=body)
            texts[sid] = body
    event_map = {str(e['id']): e for e in items(result.get('events'))}
    for impact in impacts:
        for update in impact.get('event_updates', []):
            event_map[update['event_id']]['description'] = update['description']
    # Drop only relations incident to deleted devices; generated links are enriched by the workspace serializer.
    deleted = {c['id'] for c in analysis['changes'] if c['operation']=='delete'}
    if isinstance(result.get('connections'), list):
        result['connections'] = [c for c in result['connections'] if not ({c.get('source_id'),c.get('target_id'),c.get('sourceId'),c.get('targetId')} & deleted)]
    review = {k: v for k,v in analysis.items() if k != 'proposed_devices'}
    review['mode'] = 'structure' if structure_only else 'script'
    review['comparisons'] = comparisons
    result.setdefault('workspace_meta', {})['device_impact_review'] = review
    return result, texts, review
