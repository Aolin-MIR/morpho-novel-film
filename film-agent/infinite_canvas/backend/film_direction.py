"""Compile a film's palette, staging and sound decisions into shot instructions."""

from math import isfinite

from film_cut_methods import validate_plan
from film_style_router import LONG_TAKES, route_scene

PALETTE_SLOTS = ('world', 'subject', 'signal')
SHOT_FIELDS = ('blocking', 'camera_motive', 'optical_change', 'text_prop', 'cut_anchor', 'music_reason')
LIGHT_FIELDS = ('source', 'direction', 'fill', 'tonal_detail')
FRAMING_FIELDS = ('camera_position', 'optical_axis', 'focal_subject', 'secondary_read', 'negative_space')
EFFECT_FIELDS = ('physics', 'texture', 'interaction', 'protected', 'last_state')
EFFECT_ROUTES = {
    'h3': '镜头内可辨的材质现象',
    'code-layer': '从所拍运动和深度产生的独立合成画层',
    'code-shot': '具有可重复运动与物理材质的程序镜头',
}


def field(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'导演规格缺少 {key}。')
    return value.strip()


def section(row, key, fields):
    raw = row.get(key)
    if not isinstance(raw, dict):
        raise ValueError(f'导演规格缺少 {key}。')
    return {name: field(raw, name) for name in fields}


def camera_plan(raw, route, duration):
    if not isinstance(raw, dict):
        raise ValueError('导演规格缺少可执行的运镜计划。')
    if type(duration) not in (int, float) or not isfinite(duration) or duration <= 0:
        raise ValueError('运镜计划需要有效的镜头时长。')
    method = field(raw, 'method')
    if method not in route['camera_options']:
        raise ValueError(f'运镜方法 {method} 与当前剧情功能不符。')
    interval = raw.get('stages')
    long_take = method in LONG_TAKES
    if not isinstance(interval, list) or not (3 if long_take else 2) <= len(interval) <= 5:
        raise ValueError('运镜计划须分出起势与收束。长镜还要有中途揭示。')
    stages, last = [], -1
    for stage in interval:
        if not isinstance(stage, dict):
            raise ValueError('运镜阶段格式无效。')
        at = stage.get('at')
        if type(at) not in (int, float) or not isfinite(at) or not last < at < duration:
            raise ValueError('运镜阶段须严格按时间递增，且都在本镜内。')
        stages.append({'at': at, 'action': field(stage, 'action'), 'camera': field(stage, 'camera')})
        last = at
    if stages[0]['at'] != 0 or (long_take and (duration < 7 or last < duration * .6)):
        raise ValueError('运镜从开镜开始。长镜要保留至少 7 秒且末段要覆盖收束。')
    return {'method': method, 'instruction': route['camera_options'][method],
            'why': field(raw, 'why'), 'stages': stages, 'long_take': long_take}


def bind_direction_shots(shots, value, profile, previous=None, *, require_plan=False):
    """Bind a complete scene. A cut can restore a color signal only through an event."""
    if not isinstance(value, list) or len(value) != len(shots):
        raise ValueError('每镜须有一份导演规格。')
    motifs = profile['signals']
    palette_bank = profile.get('palettes')
    if palette_bank is None:
        fixed_palette = {key: field(profile['palette'], key) for key in PALETTE_SLOTS}
    else:
        if not isinstance(palette_bank, dict) or not palette_bank:
            raise ValueError('多场景色彩规格缺少已命名的配色。')
        palette_bank = {name: {key: field(palette, key) for key in PALETTE_SLOTS}
                        for name, palette in palette_bank.items()}
    roles = profile['roles']
    for i, (shot, row) in enumerate(zip(shots, value, strict=True), 1):
        if not isinstance(row, dict) or type(row.get('shot')) is not int or row['shot'] != i:
            raise ValueError(f'第 {i} 镜的导演规格顺序无效。')
        route = route_scene(profile['genre'], row.get('purpose'))
        signal, music = field(row, 'signal'), field(row, 'music')
        staged = row.get('roles')
        if (signal not in motifs or music not in ('score', 'space')
                or not isinstance(staged, dict) or not staged
                or any(key not in roles for key in staged)):
            raise ValueError(f'第 {i} 镜的信号色、角色原型或声音安排无效。')
        event = row.get('signal_event')
        if not isinstance(event, str):
            raise ValueError(f'第 {i} 镜没有提供色彩变化字段。')
        changed = bool(previous and signal != previous['signal_id'])
        if changed != bool(event.strip()):
            raise ValueError(f'第 {i} 镜换色须有可见剧情触发点，延续时留空。')
        bound = {key: field(row, key) for key in SHOT_FIELDS}
        for name, fields in (('light_plan', LIGHT_FIELDS), ('framing_plan', FRAMING_FIELDS)):
            if require_plan or name in row:
                bound[name] = section(row, name, fields)
        if require_plan or 'camera_plan' in row:
            bound['camera_plan'] = camera_plan(row.get('camera_plan'), route, shot.get('clip_seconds'))
        edit = row.get('editorial_join')
        if require_plan and previous is not None and edit is None:
            raise ValueError(f'第 {i} 镜必须给出切入理由和可见的前后证据。')
        if edit is not None:
            if previous is None:
                raise ValueError('首镜无需填写入镜剪辑。')
            bound['editorial_join'] = validate_plan(edit)
        if row.get('effect_design') is not None:
            if not shot.get('spectacle'):
                raise ValueError('特效设计须依附本镜已登记的奇观和剧情结果。')
            bound['effect_design'] = section(row, 'effect_design', ('route', *EFFECT_FIELDS))
            if bound['effect_design']['route'] not in EFFECT_ROUTES:
                raise ValueError('选择已登记的画面制作路径。')
        palette = fixed_palette if palette_bank is None else None
        if palette_bank is not None:
            palette_id = field(row, 'palette')
            if palette_id not in palette_bank:
                raise ValueError(f'第 {i} 镜用了未登记的场景配色。')
            change = row.get('palette_event')
            changed_palette = bool(previous and palette_id != previous['palette_id'])
            if not isinstance(change, str) or changed_palette != bool(change.strip()):
                raise ValueError(f'第 {i} 镜须为场景配色变化提供可见过门，延续时留空。')
            palette = palette_bank[palette_id]
            bound.update(palette_id=palette_id, palette_event=change.strip())
        bound.update(route=route, palette=palette, signal_id=signal, signal=field(motifs, signal),
                     signal_event=event.strip(), roles={key: f'{field(roles, key)}。{field(staged, key)}'
                                                        for key in staged}, music=music)
        shot['direction'] = bound
        previous = bound


def picture_direction(direction, *, opening=False):
    p = direction['palette']
    route = direction['route']
    lines = [
        f'造型与拍摄手法。{route["look"]} {route["instruction"]}',
        f'色彩分工。空间 {p["world"]}。人物 {p["subject"]}。强调色规则 {p["signal"]}。当前只用 {direction["signal"]}。',
        '人物与布景。' + '；'.join(direction['roles'].values()) + '。',
        '通过动作组织构图。' + direction['blocking'] + '。',
        '文字道具。' + direction['text_prop'] + '。',
    ]
    if light := direction.get('light_plan'):
        lines.append(f'布光。主光来自{light["source"]}，方向{light["direction"]}。'
                     f'填光{light["fill"]}。明暗层次保留{light["tonal_detail"]}。')
    if frame := direction.get('framing_plan'):
        lines.append(f'取景。机位{frame["camera_position"]}，视轴{frame["optical_axis"]}。'
                     f'第一眼先读{frame["focal_subject"]}，之后才读{frame["secondary_read"]}。'
                     f'留白在{frame["negative_space"]}。')
    if effect := direction.get('effect_design'):
        if effect['route'] == 'h3':
            lines.append(f'材质变化。{effect["physics"]}。细节尺度{effect["texture"]}。'
                         f'布景交互{effect["interaction"]}。持续保持{effect["protected"]}。'
                         f'收镜留下{effect["last_state"]}。')
        else:
            lines.append(f'构图和表演保留{effect["protected"]}，维持稳定的深度、光源、遮挡和运动。')
    if not opening:
        if camera := direction.get('camera_plan'):
            if camera['long_take']:
                lines.append('连续长镜。保留一条相机路径和连续调度，禁止插镜、重置场景和速度突变。')
            lines.append(f'运镜执行。{camera["instruction"]} {camera["why"]}。')
            lines.extend(f'{stage["at"]:g} 秒，{stage["action"]}。机位{stage["camera"]}。'
                         for stage in camera['stages'])
        if edit := direction.get('editorial_join'):
            lines.append(f'入镜切点按此实际状态接住。{edit["after"]}。')
        lines.extend(('运镜动机。' + direction['camera_motive'] + '。',
                      '画面变化及后果。' + direction['optical_change'] + '。',
                      '出镜视线或动作接点。' + direction['cut_anchor'] + '。'))
        if direction.get('palette_event'):
            lines.append('场景用色变化，接住画面里的过门。' + direction['palette_event'] + '。')
        if direction['signal_event']:
            lines.append('强调色切换，由这件可见的事触发。' + direction['signal_event'] + '。')
    return '\n'.join(lines)


DIRECTION_REQUEST = (
    '为已有镜头写导演执行规格。保持动作、原著证据、人物身份和空间轴线。艺术表达由观看位置、情绪和节奏驱动。'
    '可采用现代主义或后现代主义的省略、主观视点、媒介并置和不可靠感知。形式变化要让观众更深理解冲突，'
    '并保留必要的地标、声源或动作。隐喻依故事而生。尊重形体、接触和真实材质，以可读的细节托住超现实。'
    '故障艺术只在荒诞影片或已有明确形式铺垫的段落选用。写清铺垫、触发、视觉规律与可读结果。'
    '普通电视和局部闪烁不足以建立这个意图。观众容易误看成穿帮时，这镜用表演、构图或现场声讲清变化。'
    '以角色视线和连续的身体动作驱动特效。保留同一人物、空间尺度、投影与遮挡；'
    '明确为了剧情有意改变的那条规则，以及画面和声音里要维持的实际参照。'
    '效果设计的 interaction 交代观众先认得的状态、可见触发和谁做了它；protected 保留仍应可信的规则；'
    'last_state 交代事件改变的第一项可见后果。安排切点时，观众应可凭镜头理解这三件事。'
    '把图层对到实际表演和接触点。把握持、走位、发现和后果拍在可辨的时间里，避免放大或漂浮的肢体盖过角色。'
    '相同原则适用于光、雨、水和程序奇观，先辨认可见声源或光源，再让效果响应实际运动。'
    '逐镜从 scene_purposes 选择目的，按 routes 给出的拍摄手法落地；profile.craft 是片级共用原则。'
    '依照 profile.palette 分配空间与人物色彩，从 signals 选唯一强调色。'
    '如有 profile.palettes，每镜加 palette 和 palette_event。palette 必须选已登记的键。'
    '仅在场景、天气或光源有可见过门时切换配色，改动时填写 palette_event，延续或首镜填空。'
    '只有光源、道具或人物的可见动作'
    '可以改变强调色。沿用上镜 signal 时 signal_event 填空，否则明确写画面触发点。全片首镜填空。'
    'blocking 写人、布景的相对位置、遮挡和运动，允许中轴构图，但禁止镜像复制同一人和整套布景。'
    'roles 只为本镜可见角色或群体指定原型和一个与布景或其他人物的可见关系，使用 profile.roles 提供的键。'
    'camera_motive 写何时启停和为了让观众看见什么。optical_change 写景深、光学变形或视觉变换的触发与后果，'
    '无渐变则写需要稳定的光学关系。text_prop 写需后期排版的实际文字和承载物，或明确无需文字。'
    'cut_anchor 要写该镜最后观众注意哪个实物或动作、它在画面什么位置，以及下镜如何接住。'
    '每镜选择 routes 对应 camera_options 的一种方法。camera_plan.stages 给 2–5 个时间点，从 0 秒开始，'
    '之后递增且小于 clip_seconds。起势、变化和收束服从角色动作，避免重复镜头技巧。'
    'continuous-parallax、subjective-return、continuous-tableau 用在至少 7 秒的镜头，'
    '保留三个阶段和一个能感知的空间或视角揭示，末阶段不早于镜长的六成。'
    'light_plan 指出具体主光、相对人物的方向、有理由的填光方式和需读出的脸部或材质半调。'
    'framing_plan 指出摄影机相对人物或地标的高低与方位、光轴、第一视觉重心、第二层次及留白位置。'
    '每个入镜从 cut_methods 选一种 editorial_join。以动作、声音、注意点或知识变化组织切点，解释为什么是此刻。'
    '用 editorial_policy 与 editing_triggers 检查切换目的及现有证据。普通直切可用 reaction 或 choice-consequence。'
    '对白说完整、听者作出实际反应，再考虑让声画跨镜。保护有剧情作用的停顿。长镜按表演决定切点。'
    '蒙太奇依题材调用。同一时间并行动作用 parallel-action，有据的观念并置用 intellectual-montage，'
    '怀疑到看见物证用 eyeline-inference，节奏调度用 rhythmic-montage，跨段回响用 distance-montage。'
    '有时序跳转使用 temporal-handoff，指出观众曾见过的物证、改变后的可见状态与现在时的未完成动作。'
    '视觉提示来自形状、光位、磨损、站位或动作，不可只写年代字幕或由旁白宣告。'
    '选择需证据的方法时，按 cut_evidence 提供字段。graphic-match 需 exit_xy 和 entry_xy。全片首镜留空。'
    '奇观可选 effect_design，从 effect_routes 选择执行方式。只在情节值得的地方给效果，写清具体材质的成因、'
    '表面细节尺度、接触阴影/反射/散射/遮挡等环境响应、受保护的对象和效果结束后的状态。'
    '需要程序合成时如实填 code-layer 或 code-shot，给后期留下带光方向及运动的底板。'
    'music 用 score 表示允许音乐，space 表示现场声留白。music_reason 写配乐进入、退出或留白的情节理由。'
    '让音乐按动作与情绪出入。独立使用现场声或安静的镜头填 space，配乐没有分段数量和覆盖比限制。'
    '只返回 JSON {"shots":[{"shot":1,"purpose":"剧情功能键","signal":"键","signal_event":"","blocking":"…",'
    '"roles":{"已登记原型键":"在本镜中的位置或行动"},"camera_motive":"…","optical_change":"…",'
    '"text_prop":"…","cut_anchor":"…","music":"space","music_reason":"…",'
    '"camera_plan":{"method":"…","why":"…","stages":[{"at":0,"action":"…","camera":"…"}]},'
    '"light_plan":{"source":"…","direction":"…","fill":"…","tonal_detail":"…"},'
    '"framing_plan":{"camera_position":"…","optical_axis":"…","focal_subject":"…","secondary_read":"…","negative_space":"…"},'
    '"editorial_join":null 或 {"method":"…","before":"…","after":"…","reason":"…","evidence":{}},'
    '"effect_design":null 或 {"route":"…","physics":"…","texture":"…","interaction":"…","protected":"…","last_state":"…"}}]}。'
)


def validate_music_direction(rows, shots):
    marks = {index for cue in rows for index in range(cue['first'], cue['last'] + 1)}
    expected = {index for index, shot in enumerate(shots, 1) if shot['direction']['music'] == 'score'}
    if marks != expected:
        raise ValueError('实际配乐须逐镜遵循导演的进场、退出和现场声留白。')
