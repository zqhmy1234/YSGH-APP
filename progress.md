# Session Progress Log — 忆述光华

## Current State

**Last Updated:** 2026-08-17 00:26
**Active Feature:** M1 主线（Sprint 2）→ **Part 1 事件聚合正式原型完成（497 合成 + 500 真实截图双基准）**；**Part 2 RAG 管线骨架完成（集成测试全过）**
**阶段：** Sprint 1 T1/T2 全部完成 ✅ / M1 Part 1 完成 ✅（真实数据基准已合并）/ **M1 Part 2 RAG 开发中**

> ⚠️ 团队结构变更（2026-08-16 用户拍板）：**用户同时承担 T1（后端）+ T2（客户端核心）**，与 Agent 一起完成 Sprint 1 内 T1/T2 全部任务；T3/T4/P1/P2/P3 另行安排。

## Status

### What's Done

- [x] **M1 Part 1 真实数据基准（场景15）**：500 张真实截图（C:\Users\ghf\Pictures\Screenshots，3078 张按月分层抽样）替代合成生成器；全量进日卡片/时间窗聚类 65 簇/折叠与 ground truth 一致/46ms/增量旧簇保留（已合并 develop 6966f13）
- [x] **M1 Part 2 RAG 管线骨架**：BGE-M3 dense+sparse（手动 sparse_linear 实现，ST 3.x 无 sparse 模块）+ Qdrant named vectors 混合检索 RRF 0.7/0.3 + 查询路由/改写（规则兑底）+ 溯源 + 降级；test_rag.py 6 项集成测试全过（真实 Qdrant + 本地 BGE-M3）
- [x] **Sprint 2（M1）规划产出**：忆述光华_Sprint2规划.md（P0=聚合正式原型+RAG+收尾）+ AI 开发成本估算（三档，推荐混合档 500-1500 元/月）
- [x] Git 仓库初始化（D:\GuangH-App，git init 2026-08-16）
- [x] Harness 初始化（AGENTS.md / feature_list.json / progress.md / init.sh / session-handoff.md，按项目定制）
- [x] 通读全部交付文档（18 份：开工总结 / MVP v3 / 开发决策 32 项 / 开发规划 7 人版 / 测试清单 140 项 / Schema 28 表 / 外部 API / 转达稿 / 深度设计 01-05e 共 9 份）
- [x] 对项目理解已向用户汇报，待审核确认

### What's In Progress

- [x] S1-02（T1）：FastAPI 骨架 + 28 表 DDL + OpenAPI 契约 + mock server + PG 隔离库（31 表）
- [x] S1-02（T1）**认证真实接入 DB**：微信建用户/手机号验证码/refresh 轮换吊销（AUTH-001/003/005/006），6 项集成测试
- [x] S1-02（T1）**内容真实入库**：contents 表 + 去重 + RQ 入队 + 游标分页（API-002/006/016），认证保护
- [x] S1-02（T1）**OpenAPI 契约导出**：docs/openapi.json（12 路径）+ 消费方说明
- [x] S1-08（T1）：RQ 队列 + **备份脚本**（backup_pg.ps1：dump+SHA256+保留 7 份）
- [x] S1-13（T2）：事件聚合原型（10 项验证全过）
- [x] Pre-Commit 审核 Agent + 测试 Agent（pytest 34 项 94.4% 覆盖率，hook 强制）
- [x] **S1-01（T2）POC 五测全部 PASS → 结论 GO，D7 Gate 提前达成**
- [ ] 微信 code2session 真实接入（待 appid/secret）
- [ ] COS STS 真实签名（待腾讯云密钥）
- [ ] RAG 图片塔（Qwen3-VL）+ 500 张截图图片基准 + 双层 Rerank（待 DASHSCOPE key）

### What's Next

1. M1 Part 2 RAG 收尾：500 张截图图片基准（Screenshots 已有 3078 张）+ 图片塔（拿 DASHSCOPE key 后）
2. 微信 code2session / COS STS 真实密钥接入（拿到 key 后）
3. CI 配置（GitHub Actions）
4. 2026-08-23 D7 结论正式产出（证据已齐）

## Blockers / Risks

- [ ] 产品部确认项未拍板（验收转达稿 4 硬 + 6 建议 + 3 新增；关怀文案库/骨架池/隐私措辞待产品部提供）
- [ ] 团队缺口：至少 1 名能写 Android 原生 Kotlin 的成员（UTS 插件本质是原生开发）
- [ ] UTS 三件套 POC 未验证（全局 Gate，M0 W1-D7 出结论）
- [ ] 合规申请（企微认证/ICP 备案/软著）未提交（M0 第一天启动，最易卡脖子）
- [ ] 外部 API Key（高德/腾讯云/百度/百炼/COS）未开通

## Decisions Made

- **技术栈已全部拍板**（开发决策清单 32 项收敛）：uni-app x 双轨 / FastAPI+RQ+PG / 微信 unionid+JWT / Qdrant / Sentry 云版等——开发阶段不再重开决策
- **开发周期**：19 周 + 15% 缓冲 ≈ 22 周（7 人：技术 4 + 产品 3）
- **成本**：100 用户月成本 ≈330-365 元，年度预算 ≈3.9-4.9 千元

## Notes for Next Session

- 交付文档是定稿参考，修改需用户明确同意
- 本项目为文档驱动开发：开工前所有深度设计（B1-B5e）已收敛，代码阶段按里程碑执行
- 仓库当前只有文档 + harness，无代码；代码按 T1（后端）/T2（客户端核心）/T3（客户端支撑）/T4（Windows+运维）四条线落地
