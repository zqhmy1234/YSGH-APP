# 忆述光华 Sprint 3 规划 · 同步与并行线

> 生成：2026-08-20（补写）｜依据：《忆述光华_开发规划+分工.md》+ Sprint 1/2 完成状态 + progress.md 实际交付记录
> ⚠️ 说明：本文件为**补写**——原始 Sprint 3 规划文档在交付文档交付时即缺失（git 历史无此文件，非误删）。
> 内容按 2026-08-17 前后实际完成的工作还原（Sprint 3 实际已执行完毕，见 progress.md 08-17 记录）。
> 配套：《忆述光华_Sprint2规划.md》（上一周期）｜《忆述光华_Sprint4规划.md》（下一周期）

## Sprint Plan: Sprint 3（同步线 + 并行四线）

**Dates:** 2026-08-17 — 2026-08-19（实际执行窗口）｜**Team:** 用户（T1+T2 双线）+ Agent（虚拟队友）
**Sprint Goal:** 数据同步闭环（B4）+ 四项并行功能落地（回响/纠错微调/图片基准/冷启动访谈）+ 系统性代码审查修复。

**Success Criteria（实际达成）：**
1. S3-01 B4 数据同步 API：字段级 LWW + op_id 幂等 + 软删墓碑 + 增量拉取 → 端云一致（M4 门禁基础）
2. S3-02 回响机制（P2-ECHO）：去年今日 + 每天 ≤1 条 + 敏感排除
3. S3-03 共性纠错微调流水线：correction 累计 ≥50 触发 SetFit 微调（reflow_global.py）
4. S3-04 RAG 图片基准基建：500 张截图语料 + Top3 评估框架（corpus-A 雏形）
5. S3-05 F7 冷启动访谈：产品部三问 → 画像维度激活
6. S3-06 系统性代码审查修复：CRITICAL 5 + MAJOR 7 + MINOR 5（另一 Agent 审查）

## Sprint Backlog（实际交付记录）

### P0（已交付）

| # | 任务 | 状态 | 验证 |
|---|---|---|---|
| S3-01 | B4 数据同步：POST /sync/push + GET /sync/pull（LWW/游标幂等/软删） | ✅ 完成 | 6 项测试全过；OpenAPI 19 路径 |
| S3-02 | 回响 API（去年今日 · 每天≤1 · 敏感排除 · 划掉不再出现） | ✅ 完成 | 5 项测试全过（commit 9fef0b1） |
| S3-03 | 共性纠错微调流水线（reflow_global.py，≥50 触发 + 备份 + 门禁） | ✅ 完成 | 审查后补备份/staging/门禁 4 项测试 |
| S3-04 | RAG 图片基准基建（500 张截图语料 + 评估框架） | ✅ 完成 | corpus-A 雏形（后续 Sprint 4/5 完成索引） |
| S3-05 | F7 冷启动访谈（三问 → 画像维度 + 扩展队列） | ✅ 完成 | 4 项测试全过 |
| S3-06 | 系统性审查修复（CRITICAL 5/MAJOR 7/MINOR 5） | ✅ 完成 | 见下方修复清单 |

### 审查修复要点（S3-06，commit 3908e6a / 81fe404）

1. sync 越权（CRITICAL）：push_ops 越权拒绝 + op_id 幂等隔离 + offline_queue 复合唯一
2. 增量聚合丢数据（CRITICAL）：同日增量改照片级 union；深夜 23:30-1:00 归属前一天
3. 认证陷阱（CRITICAL）：生产默认 JWT_SECRET 拒绝启动；微信 mock 登录 501；验证码哈希 + 每日上限
4. 鉴权补齐（MAJOR）：search/classify/events 需登录；游标分页改 (created_at, id)
5. 护栏语义（MAJOR）：mock 也过规则预检；Qdrant URL 配置化；worker 不伪造 done
6. echo 竞态（MAJOR）：部分唯一索引 (user_id, shown_date) WHERE action<>'dismiss'
7. benchmark 污染（MAJOR）：run_eval 改用独立 collection yishu_benchmark

## 交接说明

- Sprint 3 与 Sprint 4 并行线（线 A 微信 / 线 B 推送）实际在同一窗口完成（见 Sprint 4 规划）
- 遗留：RAG 图片塔真实索引待 key（Sprint 4 拿 key 后完成 439/500）；code2session 待微信凭证
