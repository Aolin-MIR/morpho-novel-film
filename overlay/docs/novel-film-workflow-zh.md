# 小说影片制作简要说明

## 从原文到成片

| 层 | 怎么做 |
| --- | --- |
| 原文与叙事 | 本机调用文本 API。全书预告完整分批阅读上传正文，从前、中、后三段选取原文时刻，按钩子、世界揭示、危机、停顿和悬念编排为 45–60 秒。独立审稿检查镜头和旁白，拒绝剧透和误导性因果。单场剧情短片选连续片段，长片阅读全书八段并按因果完整讲述。 |
| 导演与摄影 | 先定一条世界规则和一个会变义的视觉母题。逐镜规定动作、光源、构图、景别、运镜、色彩和切点。近黑白与饱和色跟随时空、视角或剧情转折。预告的奇观按尺度建立、揭示、人物反应或可见后果来拍，优先挑选人物与奇观同框的镜头试拍。 |
| 半自动拍法 | 模型提出「画面实际呈现的动作」和「剪辑与声音如何承接」。可在制作台修改拍法、交接与已有旁白。保存拍法后重新编译 H3 提示词，保存旁白后按新台词配音。剪辑交接涉及的其他改动由创作者决定。 |
| 美术与素材 | 每张人物、场景和道具参考图先指定可辨识的形状、具体材质和受光方式。人物核对五官与关节，道具核对尺度与使用痕迹，场景核对地标、透视与光源。通过审片才存入项目素材库，相同设定自动复用。博物馆、实拍档案与美术试稿用于前期设计。 |
| 生成与质检 | H3 渲染单镜和现场声。普通镜在本地检查画幅、音轨、时长和解码，按分镜时长剪辑。每部片最多三个镜头送 Qwen3.8-Omni-Flash：试拍以及至多两个额外奇观镜。每次请求最多 90 秒，合格即收，每镜至多两次生成。返修用尽则暂停，恢复时复用同一片段和衔接条件的已有结论。 |
| 旁白与混音 | 画外音就是旁白。台本标记入点、停顿和表演，百炼指令配音使用同一音色，混音时将旁白居中。H3 保留对白和现场声。LLM 另排独立音效的入点、声像、距离和空间反射，百炼生成干声，本地按实际剪辑切点做立体声混音。音乐避开留白段，声音较强时自动压低。独立音效需要百炼 Workspace ID。 |

## 艺术要求如何进入成片

| 制作层 | 实际交付与检查 |
| --- | --- |
| 叙事和作者意识 | 全书预告从前、中、后部取材，以一个观众问题串联。用有时间锚的并置、有限视角和延迟揭示组织悬念。视觉母题在后段改变意义。长片按全书因果写分段，悬念、光色和母题也进入逐段分镜。 |
| 角色、地点和道具 | 人物先辨轮廓、重心、比例和服装结构，非真人造型依靠物种形态。环境设固定地标和光源，物件写尺寸、工艺和磨损。每份参考明确负责身份、空间或道具。审核通过的资产可复用。 |
| 摄影和视觉奇观 | 每镜留一个可见动作和新的剧情信息。用景别、固定机位、遮挡揭示和有动机的移动组织节奏。最强特效交代尺度参照、变化过程、可见后果和不能变的对象。调色由视角、实际光源或剧情动机触发。 |
| 发给 H3 的文本 | 把可见动作、机位、时序、收镜及连续状态放在前面，再给构图、布光、色彩和按上传顺序编号的参考用途。普通首帧说明发送一次，用户修改后的开场构图单独传入。收镜是文本约束。这个流程用 Ref2VA 参考图，首尾画面没有独立帧锁定。参考分工借鉴 [MiniMax 官方提示词 skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)。目前使用中文指令，官方英文格式待同镜实拍比较。 |
| 画外音和声场 | 台本从原著人称、句长、讽刺和留白提炼语气。低沉中文旁白补足内心、误判或代价，奇观揭示优先让观众听见空间。配乐可留空。独立音效按声源和距离入轨，只有它跨过的镜头才要求 H3 排除同一声源。 |
| 验收与版本 | 预告独立审稿检查误导性剪辑、景别或信息重复、母题回响和色彩动机。关键镜头审查检查动作顺序、收镜状态和可见证据。普通镜头用本地媒体检查。失败时按具体缺陷修同一镜，保留现有次数上限。 |

制作工具的来源对应关系见下文。外部仓库的方法在 Morpho 内实现，实拍效果仍要通过 DSW 的人物镜与奇观镜确认。

## DSW 双 H100 部署与混合制作方案

DSW 自托管 ComfyUI 和 MiniMax H3 Base Ref2VA，Windows 的 WSL 继续运行 Morpho、文本侧 Luna、Three.js、OpenFilm 和 FFmpeg。Morpho 通过 SSH 隧道提交镜头，并在每次生成结束后取回成片。已核对开源模型支持图片和视频同时作为参考。当前 Morpho 的 SSH 渲染通道只上传图片，视频参考还需新增输入绑定并在 DSW 对实际节点做一次完整验证。[MiniMax 模型仓库](https://github.com/MiniMax-AI/MiniMax-H3) · [ComfyUI 官方说明](https://blog.comfy.org/p/minimax-h3-day-0-support-in-comfyui)

### 镜头怎么分工

| 镜头 | 制作方式 |
| --- | --- |
| 人物停顿、视线、手部接触和衣物运动 | H3 负责表演，人物与场景参考图锁定造型。复杂走位加三维预演，实测是否改善动作。 |
| 船转向、机位绕行、场景逐层出现 | 用 Three.js 编排相机和物体轨迹并导出无声音预演。H3 参考此视频的运动和机位，参考静态设定图确定材质、颜色和照明。 |
| 必须在指定时刻、指定位置精确出现的效果 | 能独立成镜的罗盘、航迹等用代码直接渲染。生成镜头内的光、雾、粒子只在有可靠遮挡和跟踪时单独合成。 |

《镜花缘》用罗盘偏转、船转向和玉桥显露做首段对照。先挑船转向这一镜，使用同一提示、参考图、分辨率和种子，分别生成纯图片参考、图片加预演两个版本。比较转向和尾流是否同向、机位是否保留、材质是否贴近设定。预演有收益才投入到同类镜头。《猫城记》另用一镜验证人物的目光、停顿和退让。全片共用角色参考、地点主参考、镜头轴线、光向和道具状态。自动检查解码、画幅和时长，集中复看两类难镜的实际运动。失败后最多定点返修一次，仍失败就留在待改状态。

### 上线顺序

1. 查 DSW 的 SSH 登录、GPU 显存和拓扑、驱动与 CUDA、主机内存、持久挂载和现有模型。首轮复用已存在且兼容的 ComfyUI 环境，固定节点版本、模型文件和导出的 API 工作流。先备齐一份 Ref2VA、文本编码器和音视频 VAE。50 GiB 连完整 BF16 去噪权重本身都装不下。若从零下载完整 BF16 Ref2VA、BF16 编码器及 VAE，建议预留 150–200 GiB 可用空间给权重、运行环境和短期输出，实际以现有文件和挂载余量为准。[MiniMax 集成目录中的权重尺寸](https://github.com/MiniMax-AI/awesome-minimax-h3-integration/blob/main/docs/run-h3-locally.md#checkpoints)
2. 先用一张 H100 跑 5 秒 480p，再跑实际片段所需的约 768p 图加视频参考，记录首镜和热机耗时、显存峰值、主机内存、导出与回传用时。若单卡配置连续稳定，再评估两卡各独立跑一镜。需要更大单镜、且设备拓扑和主机内存支持时，再评估双卡张量并行加文本编码器卸载。现有公开记录是两张 A100 80 GB，DSW 的 H100 需单测。[vLLM-Omni 双卡实测与配置](https://github.com/vllm-project/vllm-omni/blob/main/recipes/MiniMaxAI/MiniMax-H3.md#two-80-gb-gpus-a100-tp2-with-text-encoder-layerwise-offload)
3. 接通 SSH 渲染通道的参考视频输入，保留已有图片工作流。试拍用本机监控和 OpenFilm 回放。记录每镜工作流、输入文件及种子。生成、下载和验收分别记状态，解码确认后原子提交本地文件。断线后按远端任务号查询或补下载。DSW 只保存运行缓存，本地保留项目、片段及混音。已有可用持久卷优先放模型，避免每次重拉。[DSW 存储与 SSH 说明](https://www.alibabacloud.com/help/en/pai/create-and-manage-dsw-instances#typical-use-case-configurations)
4. 试拍统一声音策略。H3 现场声若无重复配乐或画外音则保留。需要完整控制时整条换成后期声场。既有百炼配音、独立音效生成和模型审片仍是单独的外部服务。首轮可用现有音效素材、授权音乐和本机混音验证画面，付费审片不作为 DSW 渲染的前置条件。

本节为待执行方案。DSW 的登录、权重、真实渲染和回传仍需上线后验证。后文涉及算家云的内容记录现有适配来源。

**奇观镜头的通用拍法**。先建立地标和尺度，再发生一个清晰的、难以实拍的变化，最后给观众一个可读的结果或人物反应。分镜同时指定哪些人、道具与结构必须保持稳定。H3 收到完整变化顺序，审片逐项核对。科幻可以用建筑尺度或群体运动，奇幻可以用非现实材料和光线，心理片也可以用反射与空间几何。特效由 H3 随镜头生成。

**全片造型和调色**。先定一种媒介和全片配色，再逐镜写主色、强调色落点和明度层级。选用版画与剪影时，用纸面、刻痕和剪影表现人物及空间。强调色与近黑白按剧情安排。规则随参考图和视频提示下发，关键镜头抽检造型漂移、过曝和无动机的霓虹。剪辑时对近黑白镜头额外压低色度，其余色彩设计由生成环节实现。

音乐曲库采用 Kevin MacLeod 的 CC BY 4.0 音乐。备选包括独奏钢琴、慢爵士、管弦乐、朦胧吉他、电子声场和音乐盒。项目记录与制作手册保留所用曲目的署名，发布时可复制到片尾或作品说明。[曲库与署名说明](https://incompetech.com/music/royalty-free/licenses/)

## 代码画面与可编辑成片

本机安装 [OpenFilm 0.3.2](https://github.com/openfilm/openfilm)。短片开工前检查 Node 22.18、浏览器、FFmpeg、中文字体和本地文本接口。GPT-6 Luna 根据全片媒介、世界概念、色彩设计与末镜构图，生成有限的 SVG 路径和三色方案。程序只接受受限路径指令，验证颜色、坐标范围和文字对比；随后编译成确定性的 HTML 动画。这个路线目前具体用于片尾书名与视觉母题，H3 继续负责人物、场景、运镜与情节行动。

已验收的 H3 镜头在本地逐帧转为 24 fps 图像序列，避免 WSL 浏览器随机跳转视频时取错帧。OpenFilm 工程按剪辑区间放置这些镜头、代码画面与最终混音，执行 `look` 的重复抽帧检查，再导出 MP4。原始镜头、逐帧素材、动画网页、配色方案和混音音轨一同保存在本机剪辑工程中，制作页可下载工程压缩包。画面段可在 OpenFilm Studio 中移动、裁切和修订；目前音频以已混好的单条音轨进入工程，细分声部继续在 Morpho 的混音流程中修改。

本机首次安装需在 WSL 使用代理完成下载。当前机器的 OpenFilm 命令位于 `~/.local/share/morpho/tools/openfilm/node_modules/.bin/openfilm`，Node 位于 `~/.local/share/morpho/tools/node-22.18/bin`。OpenFilm 逐帧导出在本机运行，不占用租来的 H3 显卡。文本接口只在生成图形方案时计费；再次预览和导出不调用文本模型。

### 代码影片路线的当前验收

先前的「代码动画」只把整张静态图推拉、平移和揭示，达不到主体持续行动、环境演化和情节转折的要求。制作台已经撤下这个入口，服务端也拒绝新建或恢复旧代码任务，以免为不合格路线购买 MiniMax H3 Image 静态图。原实现留作对照，不算已接通的新成片路线。

本机 RTX 3080 Laptop 显存 16 GB；MiniMax 官方给出的 H3 Image 模型和文本编码器所需权重约 135 GB，[部署说明](https://github.com/MiniMax-AI/MiniMax-H3-Image)不适合在本机运行。此前以平面角色切片、姿势切换、单张画作水面扰动做出的两部 16 秒开场，已被用户判定为动作和表演不成立。它们的 `motion-opening.json` 标为未通过视觉验收，只留作失败对照；不能进入自动成片。Three.js 的三维城市测试也只有简单几何，尚未达到美术要求。

[Crimson Loong](https://github.com/ChetasLua/crimson-loong) 的 90 秒代码短片将角色、三维地景、云雨、水、光效、反应特效、镜头和剧情节拍分成独立模块，每一帧由影片时间确定。[Deep Dive](https://github.com/BubbleBrainBB/awesome-fable-5.2) 则用计算着色器完成水下空间与运动。二者不能概括成一种 Fable 固定工作流：前者是逐片编写的 Three.js 电影，后者偏计算着色器。Crimson Loong 的[节拍与摄影代码](https://github.com/ChetasLua/crimson-loong/blob/main/src/88_film.js)给音画共用事件时间和世界坐标；[角色代码](https://github.com/ChetasLua/crimson-loong/blob/main/src/60_dragon.js)将路径、沿身体传播的游动、受击冲量与注视目标叠加。其水、建筑、植被、特效和声音另有独立模块。这种密度来自针对一部片反复制作，不能靠一个通用提示词或着色器代替。

代码镜头的下一次试拍须先完成一个有因果的 8–15 秒场景：角色以三维骨架或连续形变完成目标动作，脚和手有可信接触，环境在同一空间坐标对动作回应；镜头变化有叙事原因；任意时刻的画面可独立求值。先验证剪影和动作，再做材质、灯光、雾、粒子及调色，最后检查连续播放中的表演。画面推拉、海面纹理扰动、粒子飞过都不能代替主体动作。代码制作只在该试拍通过视觉验收后恢复为制作台选项；人物复杂表演可继续由 H3 视频完成。

Fable 5.2 首先是一个[擅长代码和三维/动画任务的模型](https://www.anthropic.com/claude/fable)，不是可以直接安装的电影框架。可迁移的是制作方式：把一段片当作完整软件工程，逐模块写角色、世界、镜头和声音，反复看实际渲染结果并针对缺陷改动。即使换用该模型，角色设计、素材、导演规格和逐镜验收仍须由 Morpho 提供；目前本机文本接口只有 Luna，没有把 Fable 5.2 误写成已接通。

### 代码电影 skills 的取舍

| Skill / 方法 | 可用于什么 | 当前决定 |
| --- | --- | --- |
| [idea-to-3d 的 Director](https://github.com/jackywest/idea-to-3d/blob/main/skills/idea-to-3d/references/cinematography.md) | 五行故事卡、4–8 个因果节拍、角色路径/动作/道具、以动机决定镜头、画幅/遮挡/穿模审计。 | 作为下一次三维试拍的导演与验收规格；其默认可爱低模人物不用于最终角色。尚未作为 Morpho 运行依赖安装。 |
| [varg-previz](https://github.com/vargHQ/skills/blob/main/varg-previz/SKILL.md) 与 [video-animate](https://github.com/vargHQ/skills/blob/main/video-animate/SKILL.md) | 用灰模预演确定空间、机位和动作，再以角色/场景参考图约束外观；按元素选择代码或视频模型制作。 | 适合在付费渲染前发现空间与叙事错误。其生成端绑定 varg/Seedance，不直接装入当前 H3 队列；预演结果不能当作成片。 |
| [Every Frame Is Code](https://github.com/kiselas/every-frame-is-code/blob/main/14-long-form.md) | 长片的中心问题、贯穿变化的母题、章节回调、节奏表、先剪故事板再精修；[逐帧 QA](https://github.com/kiselas/every-frame-is-code/blob/main/12-render-qa.md) 用镜头缩略图和精确时间点检查重复与死画面。 | 用于全书片的结构和剪辑检查。其示例偏解释性动态图表，不能直接承担小说角色表演。 |
| [keyframe-coach](https://github.com/Guilherme-M-Mateus/keyframe-coach) 与 [blender-code-film](https://github.com/vargHQ/skills/tree/main/blender-code-film) | 人物姿势分段、脚底打滑/穿地检查；Blender 场景先出静帧再试渲。 | 有真实 3D 人物镜头时再采用相应工具；目前本机未装 Blender，不能把这些 skill 写成已接入。 |
| 本机 HyperFrames / GSAP skill | 字卡、合成层、旁白节奏、字幕和可控转场。 | 仅用于图文层的参考；它们不提供可信角色骨架与表演，现有 OpenFilm 仍负责最终合成。 |

评判顺序是剧情动作能否看懂、表演是否可信、构图与光是否有效、最后才是特效数量。现有样片在第一个条件就失败。之前被否定的两支开场由人工编写动作代码，不是 Luna 所作。随后对 Luna 做了一次独立的 8 秒 Three.js 代码测试：语法通过且能导出，但角色没有实际抵达墙面，台本承诺的进入城市没有发生，视觉也是简单几何。详见 `artifacts/research/luna-motion-probe/assessment.md`。这只证明单次短提示结果不合格，尚不能判断更完整工具与多轮制作下的能力上限。

对想兼顾精确调度和最终质感的镜头，[fal 的 MiniMax H3 Max 3D-to-Video](https://fal.ai/models/minimax/h3-max/3d-to-video) 公开支持以三维预演视频约束机位、空间和运动，再用参考图控制外观。这是可测试的桥接方案，但它是另一个付费 API，不能当成算家云现有 H3 镜像的功能，也尚未在 Morpho 接通。若直接用本地 H3 镜像，必须先实测其视频参考输入和实际保持动作的能力；不依据第三方 skill 的说明推断镜像已有该节点。

两支旧比较样片保存在 `artifacts/book-previews/`：《猫城记》42.4 秒，《镜花缘》44.8 秒。它们只有静态美术与运镜，应标为视觉素材测试，不能作为代码影片的质量证据。静态图来自内置 imagegen，旁白使用 Edge 在线中文男声；未实测 H3 Image 付费接口或 H3 视频模型。原文依据与音乐署名保存在各自的 `manifest.json`。

## GitHub 项目借鉴到哪里

| 项目 | Morpho 中实现的做法 |
| --- | --- |
| [Story Claw](https://github.com/ZC89757/story-claw) | 参考图复用、镜头队列与本地归档。 |
| [CineCrew](https://github.com/Ironieser/CineCrew) | 镜头规格与定点返修。审片同时看参考图和前一镜出点。 |
| [NovelVids](https://github.com/Anning01/novelvids) | 冻结制作快照，保留镜头版本、审片结果与素材。 |
| [Lotus](https://github.com/DanielWLiu07/Lotus) | 借鉴故事分析、视听分镜、独立审稿和音乐重音四步。预告用完整分批阅读替代入口截取，将全书可拍事件绑定到每个镜头。混音寻找真实音频能量上升点，与音乐段里的第一个危机切点对齐，保留画面与对白时序。 |

这些方法由 Morpho 自己的代码实现。当前四个仓库均未作为运行依赖安装。SceneWeave 原文切分与 Morpho 的后台队列、项目保存和制作台直接复用。算家云提供 H3 渲染环境。

## 新增 GitHub 工具的取舍

| 工具 | 能用于 Morpho 的部分 | 当前接入结论 |
| --- | --- | --- |
| [Drama Skills](https://github.com/zenstory-ai/drama-skills) | 原著依据到镜头的引用核对、人物换装与道具状态锁、首尾帧与配音时长一致性；另有 MiniMax H3 的挂图顺序方法。 | 这些约束与 Morpho 的资产 ID、原文证据、逐镜字段相近；适合把其跨文档检查规则转成 Morpho 结构化验证。它的 Markdown 项目格式和每批人工确认流程不直接装成运行依赖。 |
| [video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft) | 镜头运动卡、旁白锁节奏、可调时间线与导出剪映草稿。 | 主体为网页/产品宣传的 Remotion 动效，不能把其 150 镜直接用于小说人物表演。片头、字卡和章节过渡可借鉴；未导入它的 Remotion 组件。商业使用需核对 Remotion 自身许可。 |
| [OpenMontage](https://github.com/Open-Montage-desktop/OpenMontage) | 对标片的节奏拆解、免费档案视频的检索与真实运动素材剪辑。 | 可用于纪实或预告片中的档案片段；《猫城记》和《镜花缘》的现有非写实美术片不适合插入写实库存镜头。本轮不安装其桌面应用或替换 Morpho 管线。 |
| [OpenChatCut](https://github.com/0xsline/OpenChatCut) | 多轨剪辑、关键帧、音频压混、可撤销的自然语言修改及 MCP 编辑会话。 | 适合作为成片后的独立编辑台，但当前 Morpho 已导出 OpenFilm 工程。它是 AGPL-3.0，暂不把源代码并入 Morpho；若要给用户另一种编辑界面，应另做工程格式导入测试后接本地 MCP。 |

以上四项已阅读仓库说明并做适用性判断；本轮没有把四个外部项目安装为 Morpho 的运行依赖。已验证的代码动作测试由 Morpho/OpenFilm 导出，完整剧情代码片仍待制作。

## 两部片的参考方向

- **《猫城记》**。保留猫脸人、迷叶和无门窗的方盒城市。旅人的磨损护目镜、大蝎的宽肩与近距离压迫感、小蝎的窄轮廓和观察式表演作为设计草案。迷叶从稀缺物变成秩序失灵的可见标志。撞机段落可用硬侧光和近黑白，猫城初见用低机位宽景与受光尘层打开色彩。[火星地貌](https://science.nasa.gov/resource/detailed-panorama-of-mars-jezero-crater-delta/) · [民用飞行护目镜](https://airandspace.si.edu/collection-objects/goggles-flying-civilian/nasm_A19772776000)
- **《镜花缘》**。第 38–39 回以海上霞光、罗盘、玉桥、金门、千秋殿和轩辕国王为设计线索。让唐敖的停顿、多九公的辨向动作、林之洋的步态区分三人。殿外海雾漫反射，过金门后让织物金线和羽形图案被侧光逐层揭开，奇观跟随人物发现。博物馆图像用于材质、轮廓和工艺参考。[唐三彩仕女](https://www.metmuseum.org/art/collection/search/39526) · [凤鸟织锦](https://www.metmuseum.org/art/collection/search/62477) · [玉凤佩](https://digitalarchive.npm.gov.tw/opendata/Pub/Detail/48274?dep=U&mode=full)

本地参考图目录 `~/.local/share/morpho/novels/references/` 保存了三张 NASA 火星地貌图和一张 1930 年飞机档案照。[飞机档案](https://www.loc.gov/pictures/item/2016889610/) · [火星沙丘细节](https://science.nasa.gov/photojournal/textures-where-curiosity-rover-studied-a-martian-dune/) · [火星沙丘俯视](https://science.nasa.gov/photojournal/ripples-and-dunes/)

《猫城记》角色方向改用非真人剪影与版画。以轮廓、比例、压纹纸、刻痕和少量朱红塑造物种差异。试稿为 `cat-country/xiao-xie-printmaking-v1.png`，动效待试拍。借鉴纸片分层和背光、木刻黑白形体、墨色明度与留白的做法。[英国电影协会剪影档案](https://www.bfi.org.uk/inside-the-archive/features/object-week-lotte-reiniger-cut-out-figures-backgrounds-cinderella) · [MoMA 木刻](https://www.moma.org/s/ge/curated_ge/techniques/woodcut.html) · [东京国立博物馆《松林图》](https://www.tnm.jp/modules/r_free_page/index.php?id=657&lang=en)

## 待验收与研究

本地可验证方案、排队、监控、恢复、剪辑和混音。真实成片还需跑通文本接口、配音服务与算家云 H3 样片。算家云平台自动开关机需单独核对其控制接口。

研究保留一个待验证问题。给定同一情节和相同素材，让模型比较「直接拍」与「交给反应镜头、旁白、声场或剪辑」，看哪些叙事信息能在画面生成受限时由其他通道更准确、更有张力地传达。先记录人工改法和实际效果，再确定自动决策与对照实验。已有方法包含自动电影化剪辑和模拟观众评审，创新性需通过进一步查重和实验确认。[FilMaster](https://arxiv.org/abs/2506.18899)
