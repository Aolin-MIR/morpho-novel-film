"""Shared continuity guidance for treatment, shot planning and H3 production."""

VERSION = 'shot-local-continuity/v2'

SCREENPLAY_CONTINUITY = (
    '把全片风格与本镜实物分层。只写当前看得见或听得见的衣色、材质、道具和声源，跨段母题留给剪辑或合成。'
    '先写接过来时谁在哪里、哪一只手或哪个支点承重、道具在哪一侧。变化写成观众可见的触发。'
    '在交接里先碰到、再承重、最后松手，持有者完成合理运动后才能离开画面。后镜从完成的相位继续。'
    '参考分工写清同一人的身份、这场的服装、无演员的地点底图、道具本体和运动轨迹。'
    '场面省略或换时空前给一个观众已经认得的动作、地标、光源或可定位声音；不能只靠字幕。'
    '正常物理和有意变形分别写。后者先立基准，写清由谁做何事、变化落在哪个表面、哪几样实物始终一样。'
)

POSITIVE_STAGING = (
    'Keep the photographed space and its established occupants stable. Each object follows its visible path '
    'from the opening placement to the stated finish. Bodies move from their stated points of support. '
    'Continue each motion through its visible result, then settle into the stated ending.'
)

COMPOSITION_SCOPES = {
    'layout': 'framing, blocking and the opening positions only',
    'body': 'the described subject’s visible body proportions and silhouette only',
    'wardrobe': 'the described garment construction, material and base color only',
}

LOCAL_RENDER_CONTINUITY = (
    '沿用本镜指定的主体、服装底色、剪裁和具体材质。本镜已有的纹样附着原表面，随透视、受光和遮挡变化。'
    '背景、空气和素面按当前实景维持。道具的轮廓、尺度和承重关系贯穿可见动作。'
    '只有已写明的画内触发改变物件状态或在指定表面产生效果。'
)
