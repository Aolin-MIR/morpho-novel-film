"""Book-trailer editorial decisions, adapted from Lotus's staged pipeline."""
from sceneweave_film import text

VERSION = 'lotus-editorial/v2'
URL = 'https://github.com/DanielWLiu07/Lotus'
ROLES = {'flow', 'silence', 'accent'}


def plan_trailer(title, reading, generate, *, revision=None, allow_final_reveal=False):
    """Select one closed set and an editable beat plan before the screenplay."""
    candidates = reading['candidates']
    finale = ('后段可以选结局或关键反转，按影片效果选择并交代其后果。' if allow_final_reveal
              else '后段选主题、代价或反应，保住最终答案。')
    withheld_field = '' if allow_final_reveal else '"withheld":"答案边界",'
    instruction = (
        '为小说做电影宣传片的选段和剪辑构思。比较人物困境、世界规则、意外物证等可能的切入点后选一个最能持续吸引的。'
        '观众在前三秒看到具体冲突或它的可辨声源。交代一个人物目标，压力和代价逐渐可见，让一个观众问题把这些场面串起来。'
        '真实动作、眼神、实物形状、已辨认的空间或可定位声音接住跳切。用节奏差、停顿和声场组织强弱。'
        '先从 coverage_ids 中的 1、2、3 三组各选至少一个原编号，再决定排列与补充。一次出现的连续表演可以展开为若干镜。'
        'beats 按剪辑顺序，最多 8 个，少的可展开有意义的反应。不要规定对所有影片相同的节拍序列。'
        '每拍注明 cut_evidence（可见或可听的上下一拍联系）、sound_role（flow、silence 或 accent）和理由。'
        '强音避开原生对白，局势变动的真实声音可担任重音。silence 留出现场声和表演。'
        + finale
        + ('对照上一版与独立审稿，保留能成立的拍点；只有结尾尺度、选段或因果问题需要时才调选段。' if revision else '')
        + '只返回 JSON {"audience_question":"问题",' + withheld_field +
        '"first_conflict":"前三秒能看见或听见什么冲突","last_reveal":"最后一个实际呈现的转折或后果",'
        '"beats":[{"moment_id":"候选编号","purpose":"对观众的作用",'
        '"cut_evidence":"出入点的声音或动作","sound_role":"flow","sound_reason":"声音如何推进、留白或重击"}]}。')
    coverage_ids = {str(part): [key for key, row in candidates.items() if row['part'] == part]
                    for part in (1, 2, 3)}
    edit_notes = {key: revision[key] for key in ('notes', 'diagnosis', 'previous_editorial') if key in revision} if revision else {}
    context = {'title': title, **reading, 'coverage_ids': coverage_ids,
               **({'revision': edit_notes} if edit_notes else {})}
    budget = 7200 if revision else 3600
    raw = generate(instruction, context, budget)
    try:
        return validate_plan(raw, candidates, allow_final_reveal=allow_final_reveal)
    except ValueError as exc:
        repaired = generate(
            instruction + '\n针对上一稿的问题修订，保持已成立的悬念、开场和选段。'
            '逐项用 coverage_ids 核对至少一条选段来自每个原著段落。必要时换掉一个重复作用的拍点。'
            + finale + '返回完整对象。',
            {**context, 'failure': str(exc), 'draft': raw}, budget)
        return validate_plan(repaired, candidates, allow_final_reveal=allow_final_reveal)


def validate_plan(raw, candidates, *, allow_final_reveal=False):
    fields = ('audience_question','first_conflict','last_reveal')
    if not allow_final_reveal:
        fields += ('withheld',)
    plan = {key: text(raw.get(key), key) for key in fields}
    nodes = raw.get('beats')
    if not isinstance(nodes, list) or not 3 <= len(nodes) <= 8:
        raise ValueError('Lotus 选段未返回可剪的完整节拍。')
    beats = {}
    for row in nodes:
        key = row.get('moment_id') if isinstance(row, dict) else None
        if not isinstance(key, str) or key not in candidates or key in beats or row.get('sound_role') not in ROLES:
            raise ValueError('Lotus 节拍包含未登记的场面、重复选段或无效声音设计。')
        beats[key] = {**{field: text(row.get(field), field)
                         for field in ('purpose', 'cut_evidence', 'sound_reason')},
                      'sound_role': row['sound_role']}
    if {candidates[key]['part'] for key in beats} != {1, 2, 3}:
        raise ValueError('宣传片选段要覆盖原著前、中、后。')
    return {'version': VERSION, 'source': URL, **plan, 'beats': beats}


def bind_beats(shots, plan):
    keys = [shot['moment_id'] for shot in shots]
    order = [key for index, key in enumerate(keys) if index == 0 or key != keys[index - 1]]
    if order != list(plan['beats']):
        raise ValueError('宣传片镜头须保留已定选段与拍点顺序。')
    for index, shot in enumerate(shots):
        beat = plan['beats'][shot['moment_id']]
        shot['promo_beat'] = {**beat, 'first_shot': index == 0 or keys[index] != keys[index - 1]}


def place_music_accents(cues, shots, voiced_shots):
    """Attach a planned lift to its first actual cut; keep quiet/voice beats clear."""
    for cue in cues:
        region = range(cue['first'], cue['last'] + 1)
        if any(shots[i - 1]['promo_beat']['sound_role'] == 'silence' for i in region):
            raise ValueError('宣传片配乐盖住了已指定的安静表演段。')
        accents = [i for i in region if shots[i - 1]['promo_beat']['first_shot']
                   and shots[i - 1]['promo_beat']['sound_role'] == 'accent'
                   and not shots[i - 1]['dialogue'] and i not in voiced_shots]
        if 'accent_shot' in cue and cue['accent_shot'] not in accents:
            raise ValueError('宣传片音乐重音只可落在指定拍点的无对白、无旁白切口。')
        if accents:
            cue.setdefault('accent_shot', accents[0])
