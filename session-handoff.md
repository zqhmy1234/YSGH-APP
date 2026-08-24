# Session Handoff — 忆述光华

> 交接文档（2026-08-20 维护重写）。目标：**任何 Agent/成员接手后 15 分钟内能上手**。
> 完整状态见 [progress.md](progress.md)（状态卡 + 完成/待办）；本文件只留"下一步怎么办"。

## 当前状态（2026-08-24 更新）

- **仓库**：D:\GuangH-App（develop 分支）；团队 GitHub（zqhmy1234/YSGH-APP）main 分支已上传 = 本地 3869111 单快照，**与远程一致、无他人新提交**；本地 feature/m1-* 分支与 tag v0.1.0-sprint1 未推送
- **门禁**：pytest 215 passed（14 deselected，覆盖率 75.20%）+ ruff 全绿 + review_agent 全绿（pre-commit 强制；2026-08-24 已修 research 段旧模块路径）
- **教训登记**：`scripts/lessons.py` + review_agent 集成（失败未登记教训 → 阻断 commit，程序化强制）
- **测试基础设施**：PG（本地服务）/ Redis / Qdrant（docker 容器）——**Docker 重启后容器会 Exited，需手动 `docker start yishu-redis yishu-qdrant`**

## 新成员启动步骤（15 分钟上手）

1. **读**：`AGENTS.md`（操作指令）→ `progress.md`（状态+完成/待办）→ `docs/lessons.md`（环境陷阱+教训）→ 本文件
2. **环境**：`.\init.ps1` 校验基线；确认 PG/Redis/Qdrant 运行；模型在 `backend/models/`（清单见该目录 README.md）
3. **密钥**：按 `skills/infisical-secrets/SKILL.md`（`infisical run --env=dev -- <cmd>` 注入；本地 .env 有 MOCK_EXTERNAL_AI=true 会走 mock，真实调用需显式 `$env:MOCK_EXTERNAL_AI='false'`）
4. **测试**：`cd backend && python -m pytest -q`（全量）；`pytest -m rag`（RAG 集成）
5. **提交**：直接 `git commit`（pre-commit 自动跑 review_agent + 教训检查）

## 团队 GitHub 上传注意事项

- **禁止上传**：`.env`（含 MOCK_EXTERNAL_AI 等）、任何 API key、`.cowork-temp/`、个人隐私文件（见 .gitignore）
- **密钥管理**：全部走 Infisical（dev/prod 各 10 条），代码零硬编码
- **文档位置**（已归位）：
  - harness：根目录 AGENTS.md / progress.md / session-handoff.md / feature_list.json / init.ps1 / init.sh
  - 教训/陷阱：`docs/lessons.md`（唯一权威）
  - 交付文档：`忆述光华_交付文档/`（定稿参考，修改需用户同意）
  - 运行文档：`docs/`（拿key后推进计划 / B2对照 / 生产兜底审计等）
- **模型资产**：`backend/models/README.md`（防重复下载；大模型文件不入库）

## 已知阻塞 / 风险

- 缺外部凭证：微信 appid/secret、企微回调、Sentry DSN、高德 key（对应 F6/Sentry/逆地理阻塞）
- 合规三申请未启动（企微认证/ICP/软著，M0 硬依赖，5-6 周串行）
- UTS POC 需 Android 原生 Kotlin 人力（全局 Gate）
- 客户端（APP/Windows）整体未启动；后端 API 约 70% 就绪
- RAG 上线评测集（50 条真实查询）等团队数据

## 下一步（按优先级）

1. 上传团队 GitHub（先 .gitignore 审查 + 文档归位确认）
2. 等团队：50 条真实查询 / 100-200 碎片 / 20-50 录音 / 照片样本 / 事件真值 / 纠错样本
3. 后端可继续：语音 COS 联调、事件 L2/L3、搜索降级契约、备份补全、性能压测
4. 敏感词规则已升级（开源词库+网址黑名单+号码打码）；可加"命中统计"迭代词表
