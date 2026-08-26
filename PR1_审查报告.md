# PR #1 需要改进的地方（给 mohanarjun183-cpu）

## 1. 合并目标分支选错了 ⚠️
PR 现在指向 `main`，但团队主干是 `develop`（`main` 早停更了）。请改 base 到 `develop`，否则合进来会和主线脱节。

## 2. `numpy` 没进 `requirements.txt`
运行时多处 `import numpy`（音频解码、情绪置信度、SenseVoice 推理），现在只靠 `funasr-onnx`/`modelscope` 间接带。请显式写上 `numpy==<pin>`，否则上游一改依赖树就崩。

## 3. 生产环境首次推理才下载模型，没预置 🟠
SenseVoice ONNX（~241MB）+ SentencePiece 走 ModelScope，ffmpeg 走 `imageio-ffmpeg`，都是**跑的时候才下**。生产服务器如果出不了 ModelScope 网或者冷启动敏感，情绪检测会直接 `AUDIO_DECODE_FAILED` 或首请求超时。请像 grounding-dino 那样把模型 + ffmpeg **预烤进镜像**，或在部署文档里写明预置步骤 + 加一道"模型可加载"冒烟。

## 4. `workspace_id` 的 base URL 硬编码了 `cn-beijing`
`asr.py` 里 workspace 存在就硬编码拼 `cn-beijing.maas.aliyuncs.com`。目前 fun-asr-flash 确实是北京，自洽；但万一以后 workspace 在新加坡就 URL 错位。建议改成按 workspace 实际地域拼 URL。低优先，但有空顺手改了。

## 5. 本地 SenseVoice 情绪方向符合设计，但应改成异步
查了 B5-a 设计文档：
- 情绪本就是双通道之一、必落库带置信度（line 59）→ **每次都算情绪是对的，不是 bug，不用删**。
- 文档 line 86 拍板：情绪优先走阿里云 API 一体输出；若 FunASR API 不带情绪则**退化为本地 CPU 自部署**（明确"异步可接受"）。当前主通道 `fun-asr-flash` 百炼文档确认只返回文本、不带情绪 → 本地 SenseVoice 是现实必要的退化方案，**方向符合设计，不用改成"云情绪优先"**。
- 但实现上是**无条件同步**串在主转写后面跑，会拖慢整体响应。建议改成**异步/非阻塞**（设计本意就是异步可接受），并保留"主通道未来若返回情绪就跳过本地"的开关，别把退化方案焊死成唯一路径。

---

**已确认 OK、不用改的**：主通道模型名 `fun-asr-flash-2026-06-15` 真实存在（百炼文档核对过），key 也确认是北京地域，能通。状态机、生产拒 mock、无密钥硬编码、单测覆盖都做得对。
