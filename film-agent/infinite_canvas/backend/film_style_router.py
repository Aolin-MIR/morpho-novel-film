"""Select a picture grammar by film genre and the scene's dramatic job."""

SCENE_PURPOSES = {
    'ritual': '制度、仪式、身份秩序或讽刺在可见的站位中体现',
    'pursuit': '追逐、突围、战斗或迫近的即时危险',
    'revelation': '发现规则、物证、世界尺度或因果反转',
    'decision': '人物选择、内部冲突及其后果',
    'aftermath': '权力崩塌、损失、余波或告别',
}

CAMERA_METHODS = {
    'observed': '保持可辨的机位和观察距离，由画内调度推动视觉重心。',
    'follow-action': '先立空间轴线，等待角色动作启动后跟随，并在动作落定时停住。',
    'passing-obstruction': '沿真实的前景遮挡移动，借显现与隐藏控制观众何时知道新信息。',
    'depth-reframe': '维持同一机位关系，用纵深调度、视差或焦面转移，把控制权交给另一人物或物证。',
    'continuous-parallax': '不间断移过两个景深层，以近景掠过与稳定远景表现空间，揭示应有前后状态。',
    'subjective-return': '长镜以同一地标起落。沿途变更光线、声场或观察尺度，回到地标时意义已变。',
    'continuous-tableau': '固定长镜内先建立严格秩序，再靠进出画与站位改变打破秩序。让变化有足够时间可读。',
}

PURPOSE_CAMERAS = {
    'ritual': ('observed', 'passing-obstruction', 'depth-reframe', 'continuous-tableau'),
    'pursuit': ('observed', 'follow-action', 'passing-obstruction', 'continuous-parallax'),
    'revelation': ('observed', 'passing-obstruction', 'depth-reframe', 'continuous-parallax', 'subjective-return'),
    'decision': ('observed', 'follow-action', 'depth-reframe', 'subjective-return', 'continuous-tableau'),
    'aftermath': ('observed', 'depth-reframe', 'continuous-parallax', 'subjective-return', 'continuous-tableau'),
}

LONG_TAKES = frozenset({'continuous-parallax', 'subjective-return', 'continuous-tableau'})


FILM_STYLES = {
    'dystopian-satire': {
        'look': '心理写实绘画。用干燥笔触、清晰明暗和克制表演表现讽刺。配色服从当前作品的参考原画。',
        'scenes': {
            'ritual': ('anderson-tableau', '韦斯·安德森式正面布景和精确的群像入场。用中轴交代秩序，以错位、停步和横移揭开层级。'),
            'pursuit': ('subjective-action', '跟旅人的行为和视线，用短侧移、剪影和动作切点保住地形、速度与实际后果。'),
            'revelation': ('controlled-reveal', '先按正面布景建立规则，再从一个可辨的遮挡后揭开唯一异常。'),
            'decision': ('psychological-closeup', '让干油画半调托住脸与手，用视线、留白和相对距离表现选择。'),
            'aftermath': ('aftermath-tableau', '用水平地景和偏心静幅留出空位。风、衣摆或最后一次脚步继续运动，留环境声。'),
        },
    },
    'mythic-journey': {
        'look': '明亮有层次的夜玉金箔。青玉、骨白、纸灰和少量哑光旧金。',
        'scenes': {
            'ritual': ('layered-ceremony', '用门廊和衣纹分层布置仪式。光从具体门窗或灯面来，场面的蔚然由人的行动开启。'),
            'pursuit': ('clear-mythic-action', '画面先交代地形和人物相对位置，常速跟手脚动作，不跨既定方向。'),
            'revelation': ('mythic-scale', '以人物和固定地标作尺度参照，将异变由一处可见触发点推向远景。'),
            'decision': ('ink-and-hand', '用自然面光和可读半调看人物的手、目光与近处材质。'),
            'aftermath': ('engraved-memory', '旧纸与矿物线条用于叙述回响，保留方向、地标和人的可辨轮廓。'),
        },
    },
    'historical-war': {
        'look': '历史写实，麻、漆木、暗铁和烟火具有实物质感，人物与军势在地形中可读。',
        'scenes': {
            'ritual': ('court-blocking', '轴线交代将领、来使和退路，礼仪变化直接表现关系。'),
            'pursuit': ('tactical-geography', '用地平线、旗帜、水流或尘路交代军势，切点接实际进退并保持方向。'),
            'revelation': ('strategic-reveal', '先让观众看见一个具体调度信号，再由响应动作揭示策略。'),
            'decision': ('command-and-consequence', '先拍下令或犹豫的手与眼，再呈现受命者和可见代价。'),
            'aftermath': ('material-aftermath', '用同一战场地标和残留烟火表现代价，压低配乐让空间与人物反应读清。'),
        },
    },
    'scientific-mystery': {
        'look': '冷峻科学写实，稳定器材与物理参照。异常经明确因果逐步扩大。',
        'scenes': {
            'ritual': ('clinical-geometry', '规则化的设备和人员站位交代机构，动作细微偏差成为悬念。'),
            'pursuit': ('causal-thriller', '动作剪辑让观众看清危险出现、识别和人物应对的时间顺序。'),
            'revelation': ('inference-to-scale', '同一镜从局部证据引到可比较尺度，以稳定地标和时序验证规则变化。'),
            'decision': ('isolated-observer', '先有反常事件，再拍人物可辨的判断，仪器和背景作为参照。'),
            'aftermath': ('cold-aftermath', '维持接触、物理与空间连续，把不可逆的结果留在清楚的远中近关系中。'),
        },
    },
}


def route_scene(genre, purpose):
    if not isinstance(genre, str) or genre not in FILM_STYLES:
        raise ValueError('必须在工作流中指定已登记的影片题材。')
    if not isinstance(purpose, str) or purpose not in SCENE_PURPOSES:
        raise ValueError('每场必须指定可辨认的剧情功能。')
    film = FILM_STYLES[genre]
    route, instruction = film['scenes'][purpose]
    return {'genre': genre, 'purpose': purpose, 'id': route,
            'look': film['look'], 'instruction': instruction,
            'camera_options': {key: CAMERA_METHODS[key] for key in PURPOSE_CAMERAS[purpose]}}
