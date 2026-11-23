"""Choose and validate editing operations from evidence shared by the adjoining shots."""

from math import hypot, isfinite

from film_speech import mandarin_word_match

METHODS = {
    'graphic-match': '让不同时间的轮廓、明暗和视线落点相接，画面先对上再揭开新地点。',
    'action-match': '从动作中间切，接住相位、运动方向、接触点和动作后果。',
    'sound-lead': '下一场带辨识度的现场声先到，画面随后确认声源。',
    'sound-tail': '让上一场的声音跨画面保持，直到下一场的动作接管声场。',
    'occlusion': '用镜头里真实经过的布、门或人体遮挡交接新空间。',
    'parallel-tableau': '并置相近构图中不同的站位或行动，让差异产生意义。',
    'reaction': '直接切到收到信息的人及其行为，保留时间和空间方向。',
    'registered-restyle': '让同一运动与空间的可见特征保持对位，再渐变材质、光线或画风。用不同层次的锚点检查尺度与转角。',
    'foreground-handoff': '用前景经过的全遮挡交接连续运镜，遮挡前后保留速度、方向、主声场和人物状态。',
    'motivated-ellipsis': '剪去观众可以推断的时长。保留起因和足以验证结果的痕迹。',
    'perspective-reframe': '从客观空间切到角色的感受，保留同一声源或动作以辨认视角变化。',
    'rhythmic-disjunction': '有意识地打断已经建立的节奏或轴线。让观众知道为何此刻出现断裂或如何重新定位。',
    'choice-consequence': '由一个可辨的决定切到它的第一项后果。以动作或对手接到的信息过门。',
    'parallel-action': '在同一时段切换有因果关系的行动线，保持共同时间信号和各方知道的信息。',
    'intellectual-montage': '用两个独立可读的行为或形象相邻产生第三层含义。以构图、声音或事件交代不同时间。',
    'eyeline-inference': '先交代观察者的视线，再给他看到的物证，必要时稍后才看反应。',
    'rhythmic-montage': '依据真实动作和声音的节奏改换镜长，留出观众读懂揭示与后果的时间。',
    'distance-montage': '用隔段重现的形象、动作或声音回收意义，相邻镜仍须独立交代本场动作。',
    'temporal-handoff': '用已经认识的实物、人物状态或地标提示时间发生变化，再接住此前的人物行动。',
}

EVIDENCE_FIELDS = {
    'foreground-handoff': ('occluder', 'motion_direction', 'audio_anchor'),
    'motivated-ellipsis': ('cause', 'visible_aftereffect'),
    'perspective-reframe': ('observer', 'shared_anchor', 'perception_shift'),
    'rhythmic-disjunction': ('expressive_break', 'reorientation'),
    'choice-consequence': ('choice_action', 'visible_consequence'),
    'parallel-action': ('shared_clock', 'first_side_knows', 'second_side_knows'),
    'intellectual-montage': ('image_pair', 'inferred_meaning', 'time_space_cue'),
    'eyeline-inference': ('observer', 'gaze_direction', 'visible_evidence'),
    'rhythmic-montage': ('movement_or_sound', 'rhythmic_change', 'readable_aftereffect'),
    'distance-montage': ('original_motif', 'return_cue', 'changed_meaning'),
    'temporal-handoff': ('seeded_image', 'visible_period_change', 'present_action'),
}


def required_text(data, keys):
    value = {key: data.get(key) for key in keys}
    if any(not isinstance(item, str) or not item.strip() for item in value.values()):
        raise ValueError('剪辑证据缺少有效字段：' + '、'.join(keys))
    return {key: item.strip() for key, item in value.items()}


def point(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) not in (int, float) or not isfinite(v) or not 0 <= v <= 1 for v in value)):
        raise ValueError('画面对位须使用有效的归一化坐标。')
    return value


def registered_evidence(evidence):
    raw = evidence.get('anchors')
    if not isinstance(raw, list) or len(raw) < 3 or any(not isinstance(a, dict) for a in raw):
        raise ValueError('媒介转换须提供至少三个有名称的画面对位锚点。')
    anchors = [{**required_text(a, ('name',)), 'exit_xy': point(a.get('exit_xy')),
                'entry_xy': point(a.get('entry_xy'))} for a in raw]
    if len({a['name'] for a in anchors}) != len(anchors):
        raise ValueError('媒介转换对位锚点不能重复。')
    if any(hypot(*(a-b for a, b in zip(anchor['exit_xy'], anchor['entry_xy']))) > .008 for anchor in anchors):
        raise ValueError('媒介转换出现位置跳动，须先校准锚点再渐变。')
    start, *rest = [a['exit_xy'] for a in anchors]
    if not any(abs((a[0] - start[0]) * (b[1] - start[1]) - (a[1] - start[1]) * (b[0] - start[0])) >= .02
               for i, a in enumerate(rest) for b in rest[i + 1:]):
        raise ValueError('媒介转换对位需要分布在画面的三个方向点。')
    overlap = evidence.get('overlap_frames')
    if type(overlap) is not int or overlap < 2:
        raise ValueError('媒介转换须有至少两帧可对位的渐变。')
    return {'anchors': anchors, 'overlap_frames': overlap}


def validate(method, before, after, reason, *, exit_xy=None, entry_xy=None, evidence=None):
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('必须选用已登记的剪辑方法。')
    parts = required_text({'before': before, 'after': after, 'reason': reason}, ('before', 'after', 'reason'))
    result = {'method': method, **parts, 'direction': METHODS[method]}
    if method == 'graphic-match':
        left, right = point(exit_xy), point(entry_xy)
        if hypot(left[0] - right[0], left[1] - right[1]) > .20:
            raise ValueError('出镜和入镜的视线位置偏差过大。')
        result.update(exit_xy=left, entry_xy=right)
    if method in EVIDENCE_FIELDS or method == 'registered-restyle':
        if not isinstance(evidence, dict):
            raise ValueError('所选剪辑方法需要局部证据。')
        result['evidence'] = registered_evidence(evidence) if method == 'registered-restyle' else (
            required_text(evidence, EVIDENCE_FIELDS[method]))
    return result


def validate_plan(value):
    if not isinstance(value, dict):
        raise ValueError('剪辑选择须包含两个切点和具体意图。')
    return validate(value.get('method'), value.get('before'), value.get('after'), value.get('reason'),
                    exit_xy=value.get('exit_xy'), entry_xy=value.get('entry_xy'), evidence=value.get('evidence'))


def require_distinct_source_windows(windows):
    """Reject repeated frame intervals, including non-adjacent uses of the same source."""
    reviewed = {}
    for identity, start, end, label in windows:
        if not identity or not 0 <= start < end:
            raise ValueError(f'镜头的底片或时间范围无效：{label}。')
        earlier = reviewed.setdefault(identity, [])
        for left, right, previous in earlier:
            if start < right and end > left:
                raise ValueError(f'镜头「{label}」重复使用了「{previous}」中的源画面。')
        earlier.append((start, end, label))


EDITORIAL_POLICY = (
    '先标出本场观众需要看懂或感到的变化，找实际表演、物证、声音或时间标记。'
    '给一次有效眼神、动作接触或完整白话台词留足可读时间。'
    '根据手头画面和声源选择手法，保持必要的台词与动作前后余量。'
    '先定对白和关键反应的取用范围，再安排配乐、字卡、字幕和特效。'
    '按实际出点与入点验证，不用节奏、图形或音效覆盖缺失的剧情信息。'
)

# An edge may have multiple useful approaches. Order follows the dramatic purpose.
EDITORIAL_CHOICES = {
    'reveal': (
        ('eyeline-inference', {'same_eyeline', 'visible_evidence'}),
        ('sound-lead', {'identified_source', 'clean_audio_handle'}),
        ('occlusion', {'shared_occluder', 'preserved_direction'}),
        ('reaction', {'visible_reaction'}),
    ),
    'action': (
        ('action-match', {'shared_action_phase', 'preserved_direction', 'visible_result'}),
        ('foreground-handoff', {'shared_occluder', 'preserved_direction', 'shared_sound'}),
        ('reaction', {'visible_reaction'}),
    ),
    'decision': (
        ('choice-consequence', {'visible_choice', 'visible_result'}),
        ('sound-tail', {'identified_source', 'clean_audio_handle', 'visible_result'}),
        ('reaction', {'visible_reaction'}),
    ),
    'time': (
        ('temporal-handoff', {'seeded_visual_cue', 'recognizable_time_change', 'visible_result'}),
        ('motivated-ellipsis', {'visible_cause', 'visible_result'}),
        ('parallel-action', {'shared_clock', 'connected_actions'}),
    ),
    'medium-change': (
        ('registered-restyle', {'registered_anchors', 'picture_handles'}),
    ),
    'meaning': (
        ('intellectual-montage', {'meaningful_image_pair', 'time_space_cue'}),
        ('parallel-tableau', {'matched_shapes', 'meaningful_difference'}),
        ('graphic-match', {'matched_shapes', 'preserved_direction'}),
        ('distance-montage', {'seeded_visual_cue', 'meaningful_difference'}),
    ),
}


def editorial_candidates(purpose, evidence):
    """Return choices that the described picture and sound can actually support."""
    if purpose not in EDITORIAL_CHOICES or not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence):
        raise ValueError('须为当前切点提供已知的剧情功能和可核实的线索。')
    available = set(evidence)
    return [{'method': method, 'description': METHODS[method]}
            for method, needed in EDITORIAL_CHOICES[purpose] if needed <= available]


def protect_native_dialogue(cut, media_sha256, lines, *, source_duration=None):
    """Keep the verified spoken unit and its original visible performance together."""
    inside, outside = cut
    if not lines:
        raise ValueError('缺少经转写确认的原生对白范围。')
    for line in lines:
        words_ok = line.get('exact') is True
        if not words_ok and line.get('expected') and line.get('transcript'):
            words_ok = mandarin_word_match(line['expected'], line['transcript']) == 'pronoun_homophone'
        if line.get('sha256') != media_sha256 or not words_ok:
            raise ValueError('对白依据与该底片不一致或尚未通过转写。')
        start, end = line.get('start_seconds'), line.get('end_seconds')
        if 'end_seconds' in line and end is None:
            # Older ASR receipts use null to mean through the end of the actual decoded source.
            end = source_duration
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not inside <= start < end <= outside):
            raise ValueError('此剪切会截断已确认的原生对白。')
