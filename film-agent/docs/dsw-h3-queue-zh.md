# DSW H3 视频队列

统一入口是个人工作区的 Film Agent。0 号和 1 号卡各有一个 ComfyUI 实例和一个领取任务的进程，分别监听 DSW 回环地址 28188 和 28191。两卡共用模型权重，1 号卡的输入、输出和缓存放在个人工作区独立目录。显存被其他任务占用时，该卡等待释放，已就绪的卡继续处理队列。CPU 剪辑、台本与回传独立运行。

## 提交和查看

已有 Harness 拍摄单继续走 `scripts/dispatch_dsw_film_batch.py`。分镜和参考上传后，拍摄单登记本地交付路径，由 WSL 的回传服务校验 SHA-256 后保留原片。任务规格使用 `"kind":"render"`，队列为 `video`。任务可依赖同一项目中此前提交的任务。首次领取时记录物理 GPU，worker 重启后继续查询原卡已有的镜头编号。已经生成的镜头不会再生成一遍。

已在 DSW 的调用方可以直接把任务 JSON 传给下列入口。镜头规格或提示词二选一，所有素材路径均相对于 `/mnt/AIRSSD/user_workspace/liaolin2`。任何项目都可以提交，相同输入和依赖会得到相同的任务编号。输入素材有新版本时会生成新编号。

```bash
agent=/mnt/AIRSSD/user_workspace/liaolin2/morpho-personal/film-agent/code/scripts/dsw_film_agent.py
python3 "$agent" submit < render-job.json
python3 "$agent" status "$job_id"
python3 "$agent" gpu-status
```

```json
{
  "project": "mirror-flowers",
  "kind": "render",
  "inputs": {
    "shot_contract": "morpho-personal/film-agent/inputs/example/shot.json",
    "references": ["morpho-personal/film-agent/inputs/example/character.png"],
    "quality": "final",
    "size": "1536x864",
    "duration": 6
  }
}
```

常驻进程位于个人 tmux 会话内，关闭 SSH 不影响运行。DSW 容器重新启动后，用同一入口恢复服务和持久队列。若容器重启打断了 ComfyUI 内存中的某条渲染，原编号仍保留。先查镜头原件，再明确安排返修，避免自动重复消耗 GPU。

```bash
ssh work_personal bash /mnt/AIRSSD/user_workspace/liaolin2/morpho-personal/film-agent/code/scripts/start_dsw_film_agent.sh
```

单故事关键镜与夜间批次脚本也提交到这个队列。SceneWeave 剧本可转成同样的拍摄单。Morpho 网页旧版保有独立的直连 ComfyUI 入口。

## 实拍验收

2026-11-19 从同一入口提交两条预览，任务均未指定卡号。0 号领取 9b490fc611022641b9537a61，1 号领取 321fb1426fc47278b62bfd2b。两条同时进入 ComfyUI，均完成 832×480、24 fps、3.750 秒的视频和 AAC 音频。WSL 自动回传后，文件的 SHA-256 与 DSW 记录一致。再次执行启动脚本，两实例保持原进程，返回空闲待命。59 项相关测试通过，覆盖双 worker 竞争领取、依赖、断开后保留卡号、旧队列迁移与连续性编译。
