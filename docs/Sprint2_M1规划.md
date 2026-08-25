# Sprint 2 规划 · M1 主线（开发规划 v3 W3-6 对齐）

> ✅ **状态（2026-08-25 整理）：M1 门禁已达标**（hit_rate@3 0.9091 ≥ 0.70，见 [RAG测评报告](RAG测评报告_20260825.md)），本文档留档参考。
> 生成：2026-08-16｜依据：《忆述光华_开发规划+分工.md》M1 + Sprint 1 完成状态
> 前置：Sprint 1 全部完成（POC GO / 认证+内容真实入库 / 契约 / 备份 / 质量门禁）

## 分支规范（决策清单 #5：Git Flow 简化版）

- `main`：受保护基线（只接受 develop 合并，打 tag 发布）
- `develop`：集成分支（feature 完成后合并，跑全量审核）
- `feature/m1-*`：M1 各部分独立分支（每部分：开发 → review_agent 全绿 → 合并 develop）

## M1 功能分支拆分（按依赖排序）

| # | 分支 | 内容 | 依赖 | 验收（M1 门禁） |
|---|---|---|---|---|
| 1 | `feature/m1-event-aggregation` | 事件聚合正式原型：Python 全量管线（预处理/L0/L1/L2/L3 + 增量）+ 500 张测试照片基准 | — | AGG-001~008 正确率基准 |
| 2 | `feature/m1-rag` | RAG 检索管线：Qdrant + BGE-M3 + 查询路由/改写 + 双层 Rerank + 溯源 | 1（事件元数据消费） | **Top3≥70% + P95<3s**（M1 门禁核心） |
| 3 | `feature/m1-setfit` | SetFit 分类服务（5 类）+ 训练/推理 API | — | 分类≥75% |
| 4 | `feature/m1-asr-guardrail` | ASR 双通道（FunASR+SenseVoice）+ 百炼护栏接入 | — | 转写可用 + 护栏可用 |
| 5 | `feature/m1-dao` | 端侧 DAO（XView+SQLCipher+对账）—— 原型层 | 1 | DAO 读写加密可用 |

## 每部分完成标准（Definition of Done）

- [ ] 功能代码实现（backend/ 或 research/ 对应目录）
- [ ] 配套 pytest 测试（含集成测试），全量 `python scripts/review_agent.py` 绿
- [ ] 测试证据记录到 feature_list.json / progress.md
- [ ] 合并 develop 后同步更新 OpenAPI 契约（如涉及 API）
- [ ] 留清晰下一步（session-handoff.md）

## 当前 Sprint 2（M1）排期

- Part 1（本次）：`feature/m1-event-aggregation` —— 事件聚合正式原型
  - 前置：Sprint 1 已有 spike（research/event_aggregation/，10 项验证全过）
  - 本次升级：全量管线 + 500 张测试照片基准 + 增量聚合 + 端云阈值一致性（AGG-016）
- Part 2：RAG 管线（BGE-M3 本地部署 + Qdrant 容器 + 检索 API）
