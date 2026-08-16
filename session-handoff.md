# Session Handoff — 忆述光华

## Current Status

- Goal: Sprint 2（M1 核心管线）推进 —— 事件聚合 ✅ / RAG 骨架 ✅ / **SetFit 分类服务 ✅（667dbb1 已合并 develop）** / ASR 接口先行 ✅ / CI+D7 收尾 ✅
- 并行开发批次完成：`feature/m1-asr-guardrail` + `feature/s2-05-ci-d7` 已提交且已集成最新 develop（组合树 review_agent 全绿），**待合并 develop**
- 主树：develop @ 667dbb1，另一 Agent 工作区（其 main.py classify 注册未提交，见 Blockers）
- 并行分支（git worktree）：`D:\GuangH-App-wt-asr` / `D:\GuangH-App-wt-ci`

## Completed This Session（并行开发批次 2026-08-17 01:2x-02:0x）

- [x] **S2-04 ASR 双通道接口 + 护栏先行**（分支 `feature/m1-asr-guardrail`，git worktree `D:\GuangH-App-wt-asr`）：
  - `backend/app/services/external/asr.py`：FunASR（百炼 paraformer-v2）+ SenseVoice（sensevoice-v1）双通道，共用 DASHSCOPE key，失败自动降级，mock 确定性兜底（拿 key 零代码切换）
  - `backend/app/api/asr.py`：POST /api/v1/asr/transcribe（multipart wav ≤8MB + preferred 参数，转写→护栏集成）+ POST /api/v1/guard/check
  - `backend/app/schemas/asr.py`：emotion 声学情绪映射（B5-c 情绪关怀）
  - test_asr.py 11 项全过；OpenAPI 契约 14 路径；review_agent 全绿
- [x] **S2-05 CI + D7 文档**（分支 `feature/s2-05-ci-d7`，git worktree `D:\GuangH-App-wt-ci`）：
  - `.github/workflows/ci.yml`：postgres+redis 服务容器 → yishu 隔离库 → review_agent 全量门禁（与本地同源）；HF_HUB_OFFLINE=0 供 CI 下载 BGE-M3
  - `docs/D7_POC结论.md`：POC 五测全过 → **结论 GO**（证据链完整，8/23 评审用）
  - harness 修复：init.ps1/init.sh/AGENTS.md 文件名错误（开工总结README.md → 开工总结.md），基线验证恢复通过
- [x] **环境修复**：`embedding.py` HF_HUB_OFFLINE=1 默认离线（huggingface.co HEAD 超时导致 RAG 测试挂 2min+，本机网络不可达 HF）

## Verification Evidence

| Check | Command | Result |
|---|---|---|
| ASR 分支审核 | `python scripts/review_agent.py`（wt-asr） | ✅ 全绿（54 文件编译 / ruff / 46+6+smoke 测试 / 无密钥） |
| CI 分支审核 | 同上（wt-ci） | ✅ 全绿 |
| harness 基线 | `.\init.ps1`（wt-ci） | ✅ 初始化通过（18 文档 + 5 harness 文件） |
| OpenAPI | 重新导出 docs/openapi.json | ✅ 14 路径（含 asr/guard） |

## Files Changed

- `feature/m1-asr-guardrail`：backend/app/services/external/asr.py（新）、api/asr.py（新）、schemas/asr.py（新）、tests/test_asr.py（新）、main.py、embedding.py、docs/OpenAPI契约.md、docs/openapi.json（8 文件 +822）
- `feature/s2-05-ci-d7`：.github/workflows/ci.yml（新）、docs/D7_POC结论.md（新）、init.ps1、init.sh、AGENTS.md、embedding.py、progress.md、feature_list.json（8 文件 +152）

## Decisions Made

- ASR 双通道复用 DASHSCOPE_API_KEY（paraformer/sensevoice 均在百炼），不新增外部 key；阿里云 NLS 可后续零成本替换适配层
- CI 门禁 = 本地 review_agent 同源，避免两套测试口径漂移
- 并行隔离用 git worktree（主树是另一 Agent 工作区，避免 main.py 等文件冲突）

## Blockers / Risks

- [ ] ⚠️ **SetFit Agent 的 main.py（classify 路由注册）尚未提交**（主树工作区 ` M backend/app/main.py`）——合并我的分支到 develop 前，先让其提交 main.py，避免覆盖冲突
- [ ] 两个并行分支已集成 develop 并全绿，待合并 develop（`git -C D:\GuangH-App-wt-asr merge` 前先 checkout develop；progress.md 冲突已手工合并过一次，后续冲突按同法处理）
- [ ] 外部 key 未到：DASHSCOPE（RAG 图片塔 + ASR 真实转写）、微信 appid/secret、腾讯云（COS STS）
- [ ] huggingface.co 本机不可达：任何新模型下载需走 hf-mirror.com 或离线包（embedding.py 已默认 HF_HUB_OFFLINE=1）
- [ ] CI 首次运行需下载 BGE-M3（~2.2GB），建议后续加 HF 缓存 action

## Next Session Startup

1. 主树：读 AGENTS.md + feature_list.json + progress.md + 本 handoff；跑 `.\init.ps1`
2. 合并并行分支到 develop（先 review_agent 再 merge）：
   `git -C D:\GuangH-App-wt-asr merge` 前先 `git checkout develop`（在主树或 worktree 均可）
3. SetFit 完成后：分类 API 联调 + 分类 ≥75% 基准（S2-03）
4. 拿 DASHSCOPE key 后：RAG 图片塔 + 500 张截图图片基准 + ASR 真实转写验证
5. 推 GitHub 后：CI 首次跑通（模型下载 ~2.2GB，建议后续加 HF 缓存 action）
