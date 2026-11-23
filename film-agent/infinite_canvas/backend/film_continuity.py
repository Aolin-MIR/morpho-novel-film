"""Preflight for story beats, physical state changes and deliberate visual effects."""
from math import isfinite


PROMOTIONAL_EDITING = (
    '开场第一拍就给可见或可闻的冲突，前三秒让观众察觉有人在对抗、某种规则被打破或有具体代价。'
    '用声音先行、画内触点及后果形成关联。开场的动静反差来自那件事，画面和音轨都保留第一条关键对白。'
    '尽早交代谁想要什么、谁或什么在阻挡、失败会失去什么。'
    '选再次行动、局势翻转和后果作为重音，用视线、质地、景别变化或可辨认的环境声把段落接成一件事。'
    '可在转折前减少声部或停一下，使实际出现的物证、选择或局面变化承担冲击。'
    '重音后留一拍让观众理解后果。提高压力靠处境变化，省掉重复信息，保留完整对白。'
    '构图、声音、光线和受控合成只有在冲突可读时才加力度，不为追求声量而覆盖对白。'
    '可调换揭示顺序，保持可推理因果。短预告保留关键答案，长版按全书范围兑现主要冲突。'
)


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name}需要写成观众可辨的具体内容。')
    return value.strip()


def interval(value, duration, name):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(point) not in (int, float) or not isfinite(point) for point in value)
            or not 0 <= value[0] < value[1] <= duration):
        raise ValueError(f'{name}必须有镜内有效的起止。')
    return tuple(value)


def physical_performance(beat, duration):
    if not isinstance(beat, dict):
        raise ValueError('身体动作须写明身体、发力与结果。')
    for field in ('subject', 'support', 'action', 'result'):
        text(beat.get(field), '身体动作 ' + field)
    return interval(beat.get('window'), duration, '身体动作')


def protected_materials(materials):
    if not isinstance(materials, dict) or not materials:
        raise ValueError('写明本镜需要锁定的具体表面与材质。')
    for item, description in materials.items():
        text(item, '承载表面')
        text(description, f'{item}的图案与底材')
    return materials


def preflight(shot, duration):
    plan = shot['continuity']
    if not isinstance(plan, dict):
        raise ValueError('续拍镜头须有场次、入镜、出镜和节奏。')
    scene = text(plan.get('scene'), '连续场次')
    before, after = plan.get('before'), plan.get('after')
    if not isinstance(before, dict) or not before or not isinstance(after, dict) or before.keys() != after.keys():
        raise ValueError('入镜和出镜必须登记同一组需要跟踪的对象。')
    for state in (before, after):
        for subject, value in state.items():
            text(subject, '状态对象')
            text(value, subject)
    changed = {key for key in before if before[key] != after[key]}
    changes = plan.get('changes', {})
    if not isinstance(changes, dict) or changed != changes.keys():
        raise ValueError('每处状态变化须写明发生原因，未变化的对象无需填写。')
    for key, cause in changes.items():
        if not isinstance(cause, dict):
            raise ValueError(f'{key}须写明变化的实际动作和时间。')
        text(cause.get('action'), f'{key}的动作')
        interval(cause.get('window'), duration, key)

    beat = plan.get('beat')
    if not isinstance(beat, dict):
        raise ValueError('本镜须交代观众的问题及所得。')
    text(beat.get('question'), '本镜接过的问题')
    text(beat.get('gain'), '本镜新增可见证据')
    interval(beat.get('window'), duration, '可用动作')
    impact = beat.get('impact')
    if impact is not None:
        if not isinstance(impact, dict):
            raise ValueError('宣传片重音需要具体冲突、变化与后果。')
        for field in ('pressure', 'turn', 'consequence'):
            text(impact.get(field), f'重音 {field}')
        lead, peak = interval(impact.get('window'), duration, '戏剧重音')
        start, end = beat['window']
        if not start <= lead < peak <= end:
            raise ValueError('戏剧重音必须在能看到的主要动作窗口内。')
    if 'performance' in plan:
        start, end = physical_performance(plan['performance'], duration)
        lead, tail = beat['window']
        if not lead <= start < end <= tail:
            raise ValueError('接触、发力和完成动作须在能验证的画面窗口内。')
    if 'materials' in plan:
        protected_materials(plan['materials'])
    cues = shot.get('speech', [])
    prior_end = 0
    for cue in cues:
        start, end = interval(cue.get('window'), duration, '同期对白')
        if start < prior_end or not start <= cue['at'] < end:
            raise ValueError('同期对白需按各自说话时间排开，并包含预定开口点。')
        prior_end = end

    effect = plan.get('effect')
    if effect is not None:
        if not isinstance(effect, dict) or effect.get('execution') != 'post':
            raise ValueError('受控特效须由独立合成层执行。')
        for field in ('setup', 'trigger', 'carrier', 'result'):
            text(effect.get(field), '特效 ' + field)
        interval(effect.get('window'), duration, '特效')
    return scene, before, after


def validate_slate(rows, *, required=False):
    if not isinstance(rows, list) or not rows:
        raise ValueError('拍摄单至少需要一个镜头。')
    previous = {}
    seen = set()
    for row in rows:
        contract = row['contract']
        identity = row['project'], row['shot']
        if identity in seen:
            raise ValueError(f'镜号重复 {row["shot"]}。')
        seen.add(identity)
        if 'continuity' not in contract:
            if required:
                raise ValueError(f'{row["shot"]} 缺少拍摄连续性规格。')
            continue
        try:
            scene, before, after = preflight(contract, row['duration'])
            key = row['project'], scene
            incoming = previous.get(key, {})
            conflicts = [subject for subject in incoming.keys() & before.keys() if incoming[subject] != before[subject]]
            if conflicts:
                raise ValueError('同场相邻镜状态跳变：' + '、'.join(sorted(conflicts)))
            previous[key] = {**incoming, **after}
        except ValueError as exc:
            raise ValueError(f'{row["shot"]} {exc}') from exc


def plate_notes(shot, duration, *, staged=False):
    if 'continuity' not in shot:
        return ''
    _, before, after = preflight(shot, duration)
    changes = shot['continuity'].get('changes', {})
    opening = [f'{key} = {value}' for key, value in before.items() if not staged or key in changes]
    rows = ['Opening state: ' + ' | '.join(opening) + '.'] if opening else []
    ordered = sorted(changes, key=lambda key: changes[key]['window'][0]) if staged else changes
    for key in ordered:
        cause = changes[key]
        start, end = cause['window']
        rows.append(f'{start:g}–{end:g}s: {cause["action"]} ending in {key} = {after[key]}.')
    stable = [key for key in before if before[key] == after[key]]
    if stable:
        if staged:
            rows.append('Throughout the shot retain ' + ' | '.join(f'{key} = {before[key]}' for key in stable) + '.')
        else:
            rows.append('These do not change: ' + ', '.join(stable) + '.')
    if movement := shot['continuity'].get('performance'):
        start, end = movement['window']
        rows.append(f'{movement["subject"]}: between about {start:g} and {end:g}s, '
                    f'{movement["support"]}. {movement["action"]}. '
                    f'Land on {movement["result"]}. Keep the natural speed of the performance.')
    if materials := shot['continuity'].get('materials'):
        rows.append('Follow these fixed surface appearances: '
                    + ' | '.join(f'{surface} = {look}' for surface, look in materials.items()) + '. '
                    'Lighting may travel across them; their underlying weave, tailoring and markings remain fixed.')
    if impact := shot['continuity']['beat'].get('impact'):
        rows.append('Performance pressure: ' + impact['pressure'] + '. Visible turn: ' + impact['turn']
                    + '. Land on ' + impact['consequence'] + '.')
    if (effect := shot['continuity'].get('effect')) is not None:
        rows.append('Show the physical cue ' + effect['trigger'] + ' on ' + effect['carrier']
                    + '. Keep the surface clean for compositing.')
    return ' '.join(rows)
