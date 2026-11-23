"""Select compatible visual treatments for a night-jade sequence."""

NIGHT_JADE_GUIDE = (
    '夜玉金箔。青灰玉石、骨白、氧化铜绿和少量旧金。人物采用有重量的写实绘画造型，'
    '保留发式、衣褶、轮廓和伤痕等识别点。用可定位的光和有层次的明度给空间定形。'
)

MATTE_SURFACES = (
    '皮肤保留自然肌理和局部血色，头发以发束和发丝受光。服装呈干燥粗织布和毡的宽而柔的漫反射，'
    '石面呈粗糙颗粒与交叠层理。只有实际沾水处局部反光，金箔只在指定的小面积实物上显色。'
    '自然曝光，以脸、做动作的手和关键物件为测光目标，给暗面可读的半调，高光有纹理。'
    '禁用整幅黑雾、湿漆肤质、亮膜、高对比橙青和覆盖全画面的光晕。'
)

PURPOSES = {
    'action': '人物行动、冲突或反应',
    'wonder': '因角色行动揭开的空间、材质或奇观',
    'record': '碑记、梦中证词、追忆或可辨认的叙述层',
    'ceremony': '公开仪式、榜单揭晓或灯下权力场面',
}
TREATMENTS = {
    'night_jade': '青灰和石灰白组织空间，脸和手先读清。用环境反光分开衣褶，金色只在实物局部受光。',
    'jade_reveal': '奇观沿真实光源展开骨白、青玉和少量旧金。角色中间调保持稳定，让背景层层打开。',
    'engraved_record': '暖灰纸、细刻线与近黑白明度，用同一剪影保留人物和地标。',
    'gold_ceremony': '暖灰墙、哑光深青和哑光织金划分仪式空间。以虚掩门窗或柔和灯面照亮人的表情。',
}
PREFERRED = {
    'action': 'night_jade',
    'wonder': 'jade_reveal',
    'record': 'engraved_record',
    'ceremony': 'gold_ceremony',
}


def style_axes(shot):
    treatment = shot.get('visual_treatment', {})
    return {
        'version': treatment.get('id', 'scene'),
        'trigger': treatment.get('trigger', shot.get('grade_reason', '')),
        'exposure': shot.get('lighting', ''),
        'color': shot.get('color_design', ''),
        'framing': shot.get('composition', ''),
        'movement': shot.get('camera', ''),
        'surface': TREATMENTS.get(treatment.get('id'), ''),
        'continuity': shot.get('continuity', ''),
        'sound': shot.get('sound', ''),
        **({'direction': shot['direction']} if 'direction' in shot else {}),
    }


def generation_direction(treatment):
    return f'本镜以此为准。{TREATMENTS[treatment]} {MATTE_SURFACES}'


def assign_night_jade(shot, previous):
    purpose = shot.get('visual_purpose')
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ValueError('夜玉金箔分镜须选择可辨认的视觉用途。')
    edge = shot.get('visual_join')
    if edge not in ('hold', 'motivated'):
        raise ValueError('镜头须注明延续前镜或由剧情触发造型变化。')
    trigger = shot.get('visual_trigger')
    if edge == 'motivated' and (not isinstance(trigger, str) or not trigger.strip()):
        raise ValueError('造型变化须注明观众看得见的光源、场面或叙述层变化。')

    before = previous['visual_treatment']['id'] if previous else None
    preferred = PREFERRED[purpose]
    if before and edge == 'hold':
        treatment = before
    else:
        treatment = preferred
    changed = bool(before and treatment != before)
    shot['visual_treatment'] = {
        'id': treatment,
        'purpose': purpose,
        'transition': 'motivated' if changed else 'hold',
        'trigger': trigger.strip() if changed else '',
        'direction': TREATMENTS[treatment],
    }
    shot['grade'] = {
        'night_jade': 'natural',
        'jade_reveal': 'vivid',
        'engraved_record': 'monochrome',
        'gold_ceremony': 'vivid',
    }[treatment]
    shot['grade_reason'] = trigger.strip() if changed else ('延续前镜的光色与材质。' if previous else '建立全片的光色。')
