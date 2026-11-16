"""Script-grounded shot packages. Produces text only; never calls a video API."""
import copy
import json
import time
from datetime import datetime, timezone

from byok import generate_text
from device_impact import fingerprint, items

AESTHETIC_DIRECTION = (
    '先确立一种贯穿全片的造型媒介，用轮廓、负空间和光的方向建立层次。一个画面只保留一个视觉重心。'
    '按叙事动机选择纸、木、墨、矿物颜料或其他具体材质。表面只在有意义的位置表现纹理和高光。'
    '非真人设计用比例、剪影和结构表现个体与物种，避免真人脸贴兽耳、网红五官、塑料皮肤和摄影棚美颜。'
    '限制主色与强调色的数量，亮色集中在关键物体或事件。暗部保留形体，高光柔和过渡。'
    '避免泛油光、皮肤磨皮、无动机的轮廓光、满屏青橙或紫蓝霓虹、镜头光斑与粒子堆砌。'
    '大场面用清晰的尺度关系、光的传播与空间变化形成冲击。'
)
SHOT_GRADES = {
    'vivid': '只让关键色鲜明，环境压低，保留颜色与材质层次',
    'monochrome': '近黑白，按明度分层，暗部有细节，高光有过渡',
    'natural': '以少量主色统一画面，保留局部色差和自然明度过渡',
}

STYLES = [
    {'id': 'cinema', 'label': '写实电影', 'guide': '自然肤质，克制表演，动机明确的运镜，实景光源，柔和高光与细腻颗粒。'},
    {'id': 'noir', 'label': '黑色悬疑', 'guide': '低调布光，明暗分割，冷暖局部对比，遮挡式构图，缓慢逼近与留白。'},
    {'id': 'intimate', 'label': '亲密生活流', 'guide': '自然散射光，近距离观察，轻微手持，停顿和细节反应，低饱和生活色彩。'},
    {'id': 'epic', 'label': '史诗奇幻', 'guide': '宏大空间与人物尺度对照，层次丰富的景深，仪式性构图，统一材质与世界规则。'},
    {'id': 'anime', 'label': '二维动画', 'guide': '清晰线稿，统一角色设定，赛璐璐阴影，有节制的夸张动作，背景与人物画风一致。'},
    {'id': 'stopmotion', 'label': '定格手作', 'guide': '可触摸的微缩材质，手作布景，定格节奏，稳定比例与一致的模型表面细节。'},
    {'id': 'ink', 'label': '东方水墨', 'guide': '宣纸与墨色层次，留白构图，简练色彩，动作轮廓清晰，墨色变化服务情绪。'},
    {'id': 'printmaking', 'label': '版画与剪影', 'guide': '非真人造型，用压纹纸、木刻和分层剪影塑造角色与空间。大色块、纸白留白、少量矿物色，不做写实肤质或抛光塑料。'},
    {'id': 'custom', 'label': '完全自定义', 'guide': ''},
]
ADAPTERS = [
    {'id': 'minimax', 'label': 'MiniMax H3', 'guide': '按镜头顺序描述人物行动、运镜和声音。'},
    {'id': 'wan', 'label': '万相 Wan 3.0', 'guide': '按时间写主体、场景、动作、镜头和声音；参考素材编号必须与上传顺序一致。'},
    {'id': 'seedance', 'label': 'Seedance 分镜叙事', 'guide': '按动作先后描述人物、运镜和声音，单镜头保持清楚的空间关系与可执行动作。'},
    {'id': 'kling', 'label': '可灵动作镜头', 'guide': '明确主体动作、方向、景别与时间变化；人物表演与相机运动分别描述。'},
    {'id': 'veo', 'label': 'Google Veo', 'guide': '明确主体、环境、构图、运动、光线与声音；对白注明说话人，声音独立描述。'},
    {'id': 'runway', 'label': 'Runway Gen-4.5', 'guide': '图生视频以动作、相机运动、方向与时间变化为主；画面外观交给首帧，声音单独后期制作。'},
]
SOURCES = [
    {'title': 'Wan 3.0 官方提示词指南 · 2026-10-16', 'url': 'https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-prompt-guide'},
    {'title': 'Runway Gen-4.5 图生视频指南', 'url': 'https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide'},
    {'title': 'Google DeepMind Veo 提示词指南', 'url': 'https://deepmind.google/models/veo/prompt-guide/'},
    {'title': 'Camera Artist · 2026-04', 'url': 'https://arxiv.org/abs/2604.09195'},
    {'title': 'DrawVideo · 2026-10 修订', 'url': 'https://arxiv.org/abs/2605.23508'},
]
STEPS = [
    '核对剧本、人物与戏剧装置，确认本次镜头覆盖范围。',
    '生成并审核人物定妆、场景和关键道具参考图；全片复用同一套素材。',
    '按首帧提示词制作构图，核对人物站位、视线、道具状态和光线。',
    '逐镜生成短片：图生视频提交首帧和运动提示词；文生视频提交完整画面提示词。',
    '检查角色外观、动作物理、轴线、口型与剧情信息；不合格的镜头单独重做。',
    '按镜头顺序剪辑，补充对白、环境声和音乐，最后调色与字幕制作。',
]


def options(value, *, default_style='cinema'):
    if not isinstance(value, dict):
        raise ValueError('视频提示词选项无效。')
    result = {k: value.get(k, default) for k, default in {
        'adapter': 'wan', 'style': default_style, 'custom_style': '', 'aspect_ratio': '16:9',
        'mode': 'image', 'duration': 6, 'model': 'qwen3.8-flash', 'scope': 'all', 'scene_ids': [],
    }.items()}
    if result['adapter'] not in {a['id'] for a in ADAPTERS} or result['style'] not in {s['id'] for s in STYLES}:
        raise ValueError('请选择有效的视频模型体系和风格。')
    if result['aspect_ratio'] not in {'16:9', '9:16'} or result['mode'] not in {'image', 'text', 'references'} or result['duration'] not in {4, 6, 8}:
        raise ValueError('画幅、生成方式或镜头秒数无效。')
    if result['mode'] == 'references' and result['adapter'] != 'minimax':
        raise ValueError('多图参考需要使用 MiniMax H3。')
    if result['model'] not in {'qwen3.8-flash', 'qwen3.8-max-0902'}:
        raise ValueError('提示词仅使用已授权的 Qwen Flash 或 Max。')
    if not isinstance(result['custom_style'], str) or len(result['custom_style']) > 3000:
        raise ValueError('自定义风格最多 3000 字。')
    if result['style'] == 'custom' and not result['custom_style'].strip():
        raise ValueError('请填写自定义风格。')
    if result['scope'] not in {'all', 'selected'} or not isinstance(result['scene_ids'], list):
        raise ValueError('场景范围无效。')
    if result['scope'] == 'selected' and not result['scene_ids']:
        raise ValueError('请至少选择一个场景。')
    return result


def text_field(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'模型缺少完整字段：{key}，请重试。')
    return value.strip()


def call_json(instruction, context, settings, max_tokens):
    encoded = json.dumps(context, ensure_ascii=False, default=str)
    if len(encoded) > 160000:
        raise ValueError('剧本超过单次分镜上下文上限，请按场景选择生成。')
    raw = generate_text(instruction + '\n输入资料：\n' + encoded, model=settings['model'],
                        temperature=0.45, max_tokens=max_tokens, timeout=240,
                        json_response=True, enable_thinking=False)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('模型未返回有效的制作方案。')
    return result


def first_frame_prompt(shot, bible):
    return (f'{bible["visual_medium"]}。{shot["first_frame"]}。构图：{shot["composition"]}。'
            f'布光：{shot["lighting"]}。色彩和明度：{shot["color_design"]}。')


def compile_prompt(shot, settings, bible):
    look = bible['visual_style']
    duration = settings['duration']
    designed = bool(shot.get('composition') and bible.get('visual_medium'))
    motion = (f"{duration}秒单一连续镜头。动作：{shot.get('screen_action', shot['action'])}"
              f"\n相机：{shot['camera']}\n时间顺序：{shot['timing']}")
    if designed:
        motion += f"\n结束画面：{shot['last_frame']}\n固定人物、道具与场景状态：{shot['continuity']}"
    if effect := shot.get('spectacle'):
        motion += (f'\n视觉变化：先{effect["setup"]}；随后{effect["reveal"]}；最后{effect["payoff"]}。'
                   f'全程保持稳定：{effect["protected"]}。')
    visual = f"{look} 画幅{settings['aspect_ratio']}。"
    if designed:
        visual += '\n开场画面与造型媒介：' + first_frame_prompt(shot, bible)
        visual += f"\n调色：{SHOT_GRADES[shot['grade']]}。动机：{shot['grade_reason']}"
        visual += f"\n全片色彩规则：{bible['color_script']}。"
        if settings.get('style_guide'):
            visual += '\n' + settings['style_guide']
        visual += ('\n用轮廓和实际光源分层，保留暗部形体与材质细节，强调色集中在指定位置。'
                   '避免磨皮、油亮塑料、无光源的泛光和满屏霓虹。')
    else:
        visual += f"{shot['first_frame']} 光线：{shot['lighting']}"
    if shot.get('composition') and not designed:
        visual += f"\n构图：{shot['composition']}。调色：{SHOT_GRADES[shot['grade']]}。动机：{shot['grade_reason']}"
        if shot.get('color_design'):
            visual += f"\n用色与明度分配：{shot['color_design']}。"
    if settings['mode'] == 'image':
        body = motion + '\n延续参考图的造型媒介、轮廓和材质。' if designed else motion
    elif designed:
        body = motion + '\n' + visual
    else:
        body = visual + '\n' + motion
    if settings['adapter'] == 'wan':
        body = f"镜头 1（0–{duration}秒）：" + body
    if settings['adapter'] != 'runway':
        if shot['dialogue']:
            body += '\n对白（保持原文）：' + shot['dialogue']
        body += '\n声音：' + shot['sound']
        if shot.get('composition'):
            body += '\n保留现场对白、动作和环境声。不要生成配乐或画外旁白。'
        if sources := shot.get('post_sound_sources'):
            body += '\n下列声音留给后期，请从本镜原生音轨中排除：' + '、'.join(sources) + '。'
    return body


def validate_shots(raw, scene, settings, asset_ids):
    shots = raw.get('shots')
    if not isinstance(shots, list) or not 1 <= len(shots) <= 24:
        raise ValueError('每场必须返回 1–24 个完整镜头；长场景请先拆分。')
    script = str(scene['script_text'])
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError('镜头结构无效。')
        for key in ('beat', 'source_excerpt', 'first_frame', 'last_frame', 'action', 'camera', 'timing',
                    'lighting', 'sound', 'continuity', 'transition', 'quality_check'):
            shot[key] = text_field(shot, key)
        if shot['source_excerpt'] not in script:
            raise ValueError('镜头引用未匹配剧本原文，已停止保存，避免凭空改写。')
        if not isinstance(shot.get('dialogue'), str):
            raise ValueError('对白字段无效。')
        refs = shot.get('reference_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in asset_ids for r in refs):
            raise ValueError(f'第 {index} 镜引用了未定义的参考素材。')
        shot.update(id=f"{scene['id']}-shot-{index:02d}", scene_id=scene['id'], duration_seconds=settings['duration'])
    return shots


def generate(payload, requested_options, progress=lambda *args: None):
    settings = options(requested_options)
    scenes = items(payload.get('scenes'))
    ids = {s['id'] for s in scenes}
    if settings['scope'] == 'selected':
        if any(sid not in ids for sid in settings['scene_ids']):
            raise ValueError('选择的场景不存在。')
        scenes = [s for s in scenes if s['id'] in settings['scene_ids']]
    if not scenes or any(not str(s.get('script_text') or '').strip() for s in scenes):
        raise ValueError('请先为所选范围生成或保存完整的场景正文。')
    if len(scenes) > 60:
        raise ValueError('单次最多处理 60 场，请选择本次需要制作的场景。')
    started = time.monotonic()
    progress('正在统一人物与视觉设定', 0, len(scenes))
    style = next(s for s in STYLES if s['id'] == settings['style'])
    context = {'settings': settings, 'style_guide': style['guide'], 'characters': payload.get('characters'),
               'worldview': payload.get('worldview'), 'scenes': scenes, 'devices': payload.get('plot_devices'),
               'adaptation_constraints': (payload.get('workspace_meta') or {}).get('sceneweave')}
    bible = call_json(
        '你是影视分镜导演。输入是已有剧本，不能新增剧情、改变角色关系或泄露尚未揭示的信息。'
        '统一制作视觉设定，原著边界和戏剧装置必须保留。外貌未明确时可提出统一的制作设计，但不能改变人物身份。'
        + AESTHETIC_DIRECTION +
        '风格具体到材质、色彩、光线和表演，不堆砌大师或作品名称。中文输出。只返回JSON：'
        '{"visual_style":"全片视觉规则","continuity_rules":"轴线、服装、道具、时间连续性规则",'
        '"assets":[{"id":"ref_01","name":"人物/场景/道具的中文名称","kind":"character或location或prop",'
        '"design_note":"哪些是原文明示，哪些是待确认的视觉设计",'
        '"image_prompt":"可直接生成定妆/场景/道具参考图的详细画面描述"}]}。'
        'assets 必须覆盖入镜人物、场景和关键道具。不得捏造已生成或已上传的素材URL。', context, settings, 6500)
    text_field(bible, 'visual_style'); text_field(bible, 'continuity_rules')
    assets = bible.get('assets')
    if not isinstance(assets, list) or not assets:
        raise ValueError('模型没有提供人物和场景参考素材清单。')
    asset_ids = set()
    for asset in assets:
        for key in ('id', 'name', 'kind', 'design_note', 'image_prompt'):
            text_field(asset, key)
        if asset['id'] in asset_ids or asset['kind'] not in {'character', 'location', 'prop'}:
            raise ValueError('参考素材标识重复或类型无效。')
        asset_ids.add(asset['id'])
    result_scenes = []
    previous = None
    for index, scene in enumerate(scenes):
        progress('正在拆解镜头与生成提示词', index, len(scenes))
        raw = call_json(
            '把本场完整正文按原顺序拆成可拍摄的连续短镜头，覆盖全部关键动作和对白，不是预告片摘要。'
            '每镜只有一个主要动作、一个可执行的相机运动；时长使用settings.duration，长对白拆镜，不塞入过多动作。'
            '每场1–24镜，dialogue保持原剧本对白及说话人，无对白用空字符串，不编新台词。'
            'source_excerpt必须逐字摘取本场script_text的连续原文片段，作为镜头依据。'
            'first_frame和last_frame分别写静态画面；action与timing只写运动及先后。'
            '保持视觉设定中人物外貌、服装、道具状态和空间轴线。按所选画幅安排构图。'
            '衔接上一场末镜；倒高潮、伏笔、误导等装置必须通过画面和节奏落实，不能提前揭底。'
            'reference_ids只引用bible.assets中真实ID。每个镜头至少关联一个素材。中文输出JSON：'
            '{"coverage_note":"全场关键动作与对白的覆盖说明", "shots":[{'
            '"beat":"戏剧目的", "source_excerpt":"逐字原文", "reference_ids":["ref_01"],'
            '"first_frame":"首帧构图和人物站位", "last_frame":"结束状态",'
            '"action":"具体动作与环境运动", "camera":"景别、角度与一个镜头运动",'
            '"timing":"动作在秒数内的先后", "lighting":"光线", "dialogue":"说话人：原文",'
            '"sound":"环境声、音效、音乐及停顿", "continuity":"与前镜的方向、道具和人物状态衔接",'
            '"transition":"剪辑衔接", "quality_check":"本镜验收要点"}]}。',
            {'scene': scene, 'bible': bible, 'settings': settings, 'previous_shot': previous,
             'devices': payload.get('plot_devices'), 'adaptation_constraints': context['adaptation_constraints']}, settings, 14000)
        shots = validate_shots(raw, scene, settings, asset_ids)
        for shot in shots:
            shot['video_prompt'] = compile_prompt(shot, settings, bible)
            shot['image_prompt'] = bible['visual_style'] + '。' + shot['first_frame'] + '。光线：' + shot['lighting']
        result_scenes.append({'scene_id': scene['id'], 'title': scene.get('title') or scene.get('name') or scene['id'],
                              'coverage_note': text_field(raw, 'coverage_note'), 'shots': shots})
        previous = shots[-1]
        progress('镜头提示词已完成', index + 1, len(scenes))
    return {'schema_version': 'video-production-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
            'source_hash': fingerprint(payload), 'options': settings, 'bible': bible, 'scenes': result_scenes,
            'steps': STEPS, 'sources': SOURCES, 'elapsed_seconds': round(time.monotonic()-started, 2),
            'status': 'completed', 'media_generated': False}


def edit_prompts(stored, changes):
    """Only editable prompt text can be changed; source provenance remains server-owned."""
    if not isinstance(changes, dict):
        raise ValueError('提示词修改格式无效。')
    result = copy.deepcopy(stored)
    shots = {s['id']: s for scene in result['scenes'] for s in scene['shots']}
    editable = dict(shots)
    editable.update({a['id']: a for a in result['bible']['assets']})
    narration = ((result.get('film') or {}).get('narration') or {}).get('cues', [])
    cues = {cue['shot_id']: cue for cue in narration}
    for item_id, patch in changes.items():
        if item_id not in editable or not isinstance(patch, dict) or set(patch) - {'video_prompt', 'image_prompt', 'screen_action', 'handoff', 'narration_text'}:
            raise ValueError('只能修改已有镜头或素材的提示词。')
        if 'screen_action' in patch and 'video_prompt' in patch:
            raise ValueError('请先保存镜头拍法，再修改生成提示词。')
        for key, value in patch.items():
            if key == 'narration_text':
                cue = cues.get(item_id)
                if not cue or not isinstance(value, str) or not value.strip() or len(value) > 160:
                    raise ValueError('只能修改已有旁白，每条须填写 1–160 字。')
                if sum(char.isalnum() for char in value) > (cue['end'] - cue['start']) * 3:
                    raise ValueError('旁白超出预留录制时间，请精简后保存。')
                cue['text'] = value.strip()
                continue
            if key not in editable[item_id] or not isinstance(value, str) or not value.strip() or len(value) > 12000:
                raise ValueError('提示词必须为 1–12000 字，且字段存在。')
            if key in {'screen_action', 'handoff'} and len(value) > 1500:
                raise ValueError('镜头拍法和剪辑交接每项最多 1500 字。')
            editable[item_id][key] = value
        if 'screen_action' in patch:
            shot = shots[item_id]
            settings = {**result['options'], 'duration': shot['duration_seconds']}
            shot['video_prompt'] = compile_prompt(shot, settings, result['bible'])
    result['edited_at'] = datetime.now(timezone.utc).isoformat()
    return result
