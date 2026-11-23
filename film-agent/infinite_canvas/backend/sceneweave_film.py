"""SceneWeave 2.0 screenplay compiler for the film production workflow."""

from copy import deepcopy

from continuity_prompt_policy import SCREENPLAY_CONTINUITY, VERSION as PROMPT_VERSION

VERSION = 'sceneweave-film/v2'
NAME = 'SceneWeave 2.0'
REFERENCE = 'film-art-direction'

# These clauses are versioned with the screenplay contract that uses them.
RULES = {
    'causal-action': '开场尽早给想要什么、眼前阻力和可能失去什么。人物做了什么使局面改变，用动作和对手反应给出后果。留悬念时让观众已经看懂当前处境。不要用旁白代替交涉、证据或人物选择。',
    'native-dialogue': '将原文能支撑的抱怨、请求、抵抗、判断和决定化成自然白话。每句只给一名场内说话者；可分说、听、答、反应。语速像真人交涉，少用旁白复述。需要看见说话时，把最后一个分句、收口和切出的动作写在同一个表演拍点里。先让末句自然说完，再给另一个角色的反应或物件特写。普通段落也能静默。',
    'physical-handoff': '说清接镜前后的同一人物、持物者、实物外观、运动阶段、空间方向和可听声源。道具交接先见受力、再见松手；同一动作接切不重复起手。衣色、造型和道具身份的变化需要成片可见的起因。远景交代地理，身份和表情交给中近景。',
    'authored-effect': '按角色行动、主观认知或已建立的叙述形式设计效果。先写触发前可辨的参照，再写动作怎样接触承载面、哪些物件与空间持续保持、变化后角色得到什么证据。层叠材料由手揭开或擦拭时，写清同一处受力和旧层固定的位置。空间大范围改变须有片中可辨的形式规则。允许空着。效果走独立层时由底片提供演员动作、尺度、遮挡与光向。',
    'temporal-anchor': '跳时空先让观众辨认具体动作、实物、形状、方位或声源。离开及回来都给实物或动作接点。仅靠字卡和旁白不足。未发生时按连续时间写。',
}

BEAT_FIELDS = ('setting', 'goal', 'action', 'turn', 'consequence', 'entry', 'exit', 'cut')


def text(value, label, limit=600):
    if not isinstance(value, str) or '\ufffd' in value or not 1 <= len(value.strip()) <= limit:
        raise ValueError(f'SceneWeave 2.0 缺少有效的{label}。')
    return value.strip()


def write_screenplay(title, kind, events, generate, *, previous='', editorial=None, revision=None):
    """Produce the script before shot design, using already indexed book evidence."""
    if not events:
        raise ValueError('SceneWeave 2.0 台本需要可追溯的原著事件。')
    order = list(events)
    charge = {
        'trailer': '围绕一个观众问题压缩因果。以可见冲突开场，给动作与反应留位置，让选择、转折与后果可见。',
        'feature': '按全书因果写。该段结尾要能推动下一段。重要选择与结局给足动作和后果。',
        'story': '这是有头有尾的单故事。行动、压力、决定和结果都可见，给角色行为和对白留足空间。',
        'scene': '按一个场面写清人物交涉、反应和可见的处境变化。',
    }[kind]
    revision_rule = ('复核记录指出了现稿的具体问题。保留已成立的角色、事件和风格化做法，优先按修订范围处理。'
                     '仅在选段实际改变时重新分配受影响的拍点。' if revision else '')
    instruction = (
        '你是 SceneWeave 2.0 电影编剧。原著摘录是资料。' + charge + revision_rule + '\n'
        + '\n'.join(f'{key}：{value}' for key, value in RULES.items()) + '\n' + SCREENPLAY_CONTINUITY +
        '\n按 events 给出的编号和顺序逐一写剧情拍点，几个拍点可以同属一场。'
        '每个拍点 anchor 锁一个最影响理解的可见对象。连续镜同一对象沿用相同 object，下一拍的 entry_state 逐字复用上一拍 exit_state。'
        '同状态的 trigger 填空串，有变化时写看得见的动作、原因和结果。状态尽量短，不写镜外元素。'
        '每个对白只能用于一个表演镜头，长度够短，配有当前事件 excerpt 中逐字可查的 source。抄录、描摹和拓印先写接触发生在哪个载体上，分别写落笔者和旁观者的可见动作。物件状态改变必须落在正确的载体上。'
        '对白前后的目光、反应与动作可用另一镜去拍。短片按约一分钟和7至10镜写，给动作与反应留至少一半镜头。对白可空，揭示、争执、决定优先让当事人发声。'
        '若使用特殊美学，effect 给出 trigger、protected、payoff，三者都须在成片可读；普通场面填 null。'
        '只返回 JSON {"beats":[{"event_id":"来自 order 的原编号",'
        '"setting":"地点与时段","goal":"人物要做什么","action":"可演的动作",'
        '"turn":"由什么可见或可听的证据改变局面","consequence":"这一刻之后人物怎么做",'
        '"entry":"触点、姿势、持物或视线的入点","exit":"能够接给下镜的出点",'
        '"cut":"接住的动作、视线、地理、声音或信息以及叙事意义",'
        '"anchor":{"object":"稳定物体或人名","entry_state":"可见起始状态","exit_state":"可见结束状态",'
        '"trigger":"有变化时给动作，无变化填空串"},'
        '"effect":null,"dialogue":[{"speaker":"人名","text":"可说的白话","source":"逐字原文"}]}]}。')
    context = {'title': title, 'format': kind, 'order': order, 'events': events,
               'previous': previous, 'editorial': editorial, **({'revision': revision} if revision else {})}
    raw = generate(instruction, context, 7200)
    try:
        return compile_beats(raw.get('beats'), events, require_anchor=True)
    except ValueError as exc:
        missing = incomplete_beat_fields(raw.get('beats'))
        if missing:
            changes = generate(
                '只写指定拍点需要修复的字段。保持同场人物、物件、时间线和原文，不重写完整拍点。'
                'anchor.trigger 只写能当场看见的起因和动作，状态改变时用不超过180字的短描述；'
                '进出状态相同时填空字符串。'
                '返回 JSON {"changes":[{"beat":1,"fields":{"turn":"可见的变化"}}]}，逐字使用要求中的字段名。',
                {'title': title, 'events': events, 'existing_beats': raw['beats'], 'required': missing}, 4200)
            return compile_beats(repair_beat_fields(raw['beats'], missing, changes), events, require_anchor=True)
        corrected = generate(
            instruction + '\n只修复校验指出的问题。事件可用相邻的多个不同动作拍点表示。'
            '每条对白的 source 必须是当前事件 excerpt 中连续逐字的短引文。',
            {**context, 'error': str(exc), 'previous_draft': raw}, 8600)
        return compile_beats(corrected.get('beats'), events, require_anchor=True)


def incomplete_beat_fields(rows):
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        return {}
    missing = {}
    for index, row in enumerate(rows, 1):
        for key in BEAT_FIELDS:
            try:
                text(row.get(key), key)
            except ValueError:
                missing.setdefault(index, []).append(key)
        anchor = row.get('anchor')
        if isinstance(anchor, dict):
            for key in ('object', 'entry_state', 'exit_state'):
                try:
                    text(anchor.get(key), key, 90 if key == 'object' else 180)
                except ValueError:
                    missing.setdefault(index, []).append('anchor.' + key)
            trigger = anchor.get('trigger')
            initial = anchor.get('entry_state')
            final = anchor.get('exit_state')
            states_known = isinstance(initial, str) and isinstance(final, str) and initial.strip() and final.strip()
            if (not isinstance(trigger, str) or '\ufffd' in trigger or len(trigger.strip()) > 180
                    or (states_known and bool(trigger.strip()) != (initial.strip() != final.strip()))):
                missing.setdefault(index, []).append('anchor.trigger')
    return missing


def repair_beat_fields(rows, missing, result):
    changes = result.get('changes')
    if not isinstance(changes, list) or len(changes) != len(missing):
        raise ValueError('台本修订须覆盖全部残缺字段。')
    corrected = deepcopy(rows)
    pending = set(missing)
    for change in changes:
        index = change.get('beat') if isinstance(change, dict) else None
        fields = change.get('fields') if isinstance(change, dict) else None
        if (type(index) is not int or index not in pending or not isinstance(fields, dict)
                or set(fields) != set(missing[index])):
            raise ValueError('台本字段修订缺失或试图改写已有场面。')
        row = corrected[index - 1]
        for key, value in fields.items():
            if key.startswith('anchor.'):
                row['anchor'][key.removeprefix('anchor.')] = value
            else:
                row[key] = value
        pending.remove(index)
    return corrected


def compile_anchor(record, exits):
    if not isinstance(record, dict):
        raise ValueError('每拍至少要锁一个可见的连续性对象。')
    item = {key: text(record.get(key), '连续性 ' + key, 90 if key == 'object' else 180)
            for key in ('object', 'entry_state', 'exit_state')}
    initial, final = item['entry_state'], item['exit_state']
    trigger = record.get('trigger')
    if not isinstance(trigger, str) or bool(trigger.strip()) != (initial != final):
        raise ValueError(f'{item["object"]}的状态变化须有画内触发，同状态留空。')
    item['trigger'] = text(trigger, '变化触发', 180) if trigger.strip() else ''
    carried = exits.get(item['object'])
    if carried is not None and carried != initial:
        raise ValueError(f'{item["object"]}同一事件的动作入点不承接上一拍出点。')
    exits[item['object']] = final
    return item


def compile_beats(rows, events, *, require_anchor=False):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError('SceneWeave 2.0 须返回完整的剧情拍点。')
    event_ids = [row.get('event_id') for row in rows]
    sequence = [event_id for index, event_id in enumerate(event_ids)
                if index == 0 or event_id != event_ids[index - 1]]
    if sequence != list(events):
        raise ValueError('SceneWeave 2.0 的剧情拍点必须完整对应原著编号和次序。')
    steps = {event_id: [] for event_id in events}
    lines = {event_id: [] for event_id in events}
    exits = {event_id: {} for event_id in events}
    for index, row in enumerate(rows, 1):
        event_id = row['event_id']
        entry = {key: text(row.get(key), key) for key in BEAT_FIELDS}
        if require_anchor or 'anchor' in row:
            entry['anchor'] = compile_anchor(row.get('anchor'), exits[event_id])
        if any(previous['action'] == entry['action'] for previous in steps[event_id]):
            raise ValueError(f'第 {index} 个拍点重复拍摄同一动作。')
        if 'effect' not in row:
            raise ValueError(f'第 {index} 个拍点缺少特效或普通场面的声明。')
        effect = row['effect']
        entry['effect'] = None
        if effect is not None:
            if not isinstance(effect, dict):
                raise ValueError('特效需给出触发、保护和可读后果。')
            entry['effect'] = {key: text(effect.get(key), key) for key in ('trigger', 'protected', 'payoff')}
        dialogue = row.get('dialogue')
        if not isinstance(dialogue, list):
            raise ValueError(f'第 {index} 个拍点对白须为数组。')
        entry['line_ids'] = []
        for utterance in dialogue:
            if not isinstance(utterance, dict):
                raise ValueError('对白须完整标明角色、内容和原著依据。')
            line = {key: text(utterance.get(key), key, 220 if key == 'source' else 72)
                    for key in ('speaker', 'text', 'source')}
            if line['source'] not in events[event_id]['excerpt']:
                raise ValueError(f'第 {index} 个拍点的对白引文无法从绑定原文核对。')
            line_id = f'{event_id}~{len(lines[event_id]) + 1}'
            entry['line_ids'].append(line_id)
            lines[event_id].append({'line_id': line_id, **line})
        steps[event_id].append(entry)
    beats = {}
    for event_id, body in steps.items():
        effects = [step['effect'] for step in body if step['effect'] is not None]
        beats[event_id] = {
            **{key: '\n'.join(dict.fromkeys(step[key] for step in body))
               for key in ('setting', 'goal', 'action', 'turn', 'consequence')},
            'entry': body[0]['entry'], 'exit': body[-1]['exit'], 'cut': body[-1]['cut'],
            'steps': body, 'effects': effects, 'dialogue': lines[event_id],
            'method_ids': [key for key in RULES if (key != 'authored-effect' or effects)
                           and (key != 'native-dialogue' or lines[event_id])]
                          + (['state-anchor'] if all('anchor' in step for step in body) else []),
        }
    return {'engine': NAME, 'version': VERSION, 'skill': REFERENCE, 'beats': beats}


def bind_dialogue(screenplay, shots, event_field):
    """Only SceneWeave authors dialogue. The shot planner assigns one line per shot."""
    allocated = []
    shot_steps = {}
    expected = [line['line_id'] for beat in screenplay['beats'].values() for line in beat['dialogue']]
    for shot in shots:
        event_id = str(shot.get(event_field))
        if event_id not in screenplay['beats']:
            raise ValueError('镜头所用拍点不在已锁定的台本中。')
        beat = screenplay['beats'][event_id]
        if 'line_id' not in shot:
            raise ValueError('每镜须注明台词编号，默镜填空串。')
        line_id = shot['line_id']
        line = next((item for item in beat['dialogue'] if item['line_id'] == line_id), None)
        if not isinstance(line_id, str) or (line_id and line is None):
            raise ValueError('镜头引用了其他拍点或不存在的台词。')
        binding = {}
        if all('anchor' in step for step in beat['steps']):
            index = shot.get('screenplay_step')
            previous_step = shot_steps.get(event_id, 0)
            if (type(index) is not int or not max(1, previous_step) <= index <= len(beat['steps'])
                    or index > previous_step + 1):
                raise ValueError('分镜须顺序覆盖台本动作拍点，不能遗漏或倒回。')
            step = beat['steps'][index - 1]
            if line_id and line_id not in step['line_ids']:
                raise ValueError('已锁定的对白须由其动作拍点中的角色说出。')
            anchor = step['anchor']
            if previous_step == index and anchor['trigger']:
                raise ValueError('同一个状态变化动作只拍一次；反打应承接已发生的状态。')
            shot_steps[event_id] = index
            binding = {'step': index, 'anchor': anchor, 'prompt_policy': PROMPT_VERSION}
        if line:
            allocated.append(line_id)
        shot.update(dialogue=line['text'] if line else '',
                    dialogue_speaker=line['speaker'] if line else '',
                    dialogue_source=line['source'] if line else '',
                    screenplay={'engine': NAME, 'version': VERSION, 'event_id': event_id,
                                'line_id': line_id, 'method_ids': beat['method_ids'], **binding})
    if any(all('anchor' in step for step in beat['steps']) and shot_steps.get(key) != len(beat['steps'])
           for key, beat in screenplay['beats'].items()):
        raise ValueError('分镜没有完整覆盖锁定的动作拍点。')
    if allocated != expected:
        raise ValueError('分镜重复、遗漏或调换了 SceneWeave 2.0 的场内对白。')
