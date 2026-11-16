# Morpho 小说影片工作流

把中文小说编排成全书预告、单场剧情短片或全书长片。在 Morpho 的视频制作台导入原文，生成分镜、人物和场景设定，逐镜渲染、验收并导出 MP4 与可编辑 OpenFilm 工程。

本仓库独立维护小说影片功能，以扩展形式安装到 Morpho。它复用 Morpho 的项目、账户、队列和制作台。安装需要持有 Morpho 工作区的基线提交 `b71aed249a42f7f514b0b6a35ef0e5a5161ad5f4`。

## 主要内容

- 阅读原著与选段。预告从全书取材，长片按情节因果编排。
- 导演与美术约束。人物辨识点、材质和场景地标，逐镜构图、运镜、首尾状态、色彩动机与特效后果。
- H3 提示词与渲染。按上传顺序标注参考图用途，支持配置经 SSH 使用的 ComfyUI 工作流，自动查询和下载镜头。
- 中文旁白、临场声和音乐。按镜头剪辑和混音，关键镜头可按具体缺陷返修。

## 在 WSL 接入

在有上述提交的 Morpho 仓库中建立独立工作树。以下命令假定扩展仓库与 Morpho 原仓库位于同一目录。

```bash
git -C morpho-demo worktree add --detach ../morpho-film-worktree b71aed249a42f7f514b0b6a35ef0e5a5161ad5f4
python3 morpho-novel-film/install.py morpho-film-worktree --check
python3 morpho-novel-film/install.py morpho-film-worktree
```

依照 Morpho 的部署方式启动 Redis、后端、RQ `generation` 工作进程和前端，然后从项目进入视频制作。安装器检查基线和目标文件状态。接入后在新工作树运行相关测试。

```bash
python -m pytest -q tests/test_novel_film_runtime.py tests/test_video_workflow.py tests/test_model_providers.py
```

本机文本网关需要在后端和工作进程配置 `MORPHO_LOCAL_STORY_GATEWAY=1`、`MORPHO_STORY_API_URL` 和可选的 `MORPHO_STORY_API_MODEL`。密钥文件为 `~/.config/morpho-local/story-api-key`。SSH 渲染需要 `MORPHO_H3_SSH_HOST`、`MORPHO_H3_SSH_PORT` 以及实际 ComfyUI 工作流的本地绑定。OpenFilm 导出依赖 Node、浏览器、FFmpeg 和中文字体。参考图、配音及模型审片沿用当前接口，各项凭据在运行端配置。

DSW 双 H100 环境、视频预演作为 H3 参考和本地生图属于待实拍接通的方案。[制作说明](overlay/docs/novel-film-workflow-zh.md)也记录了原工作区的历史试拍及本地素材路径。那些试拍和素材留在原工作区。
