# Linger 规则摘要（据 `src/memory/README.md` 与 `apply.ts` 整理，供移植对照）

## 检索与证据

- RRF 融合，`k=60`；最多 6 个 alternates，每个 alternate 作为独立一路投票（不合并成一条 query）。
- 时间/日期/事件窗口**只加权、不过滤**：问三月，答案在一月也要能召回。
- 两列分开排序：摘要（overview）与原文（detail）；摘要作为**隐藏注记**，不进 system prompt。
- 空结果必须先把**原文列**打满再报“没有记录”。
- 降级通道要显式标记，不能与“确实没有”混为一谈。

## 写入（apply.ts）

- 顺序：validate 全批 → snapshot → apply → 任一失败 rollback。
- change log 由**应用**写，不由模型写。
- 条目带 valid_from/valid_to（事实时间）与 recorded_at（记录时间）。
- 同一文本的新证据只追加证据（came_from），不新增一行。
- 用户修正永久优先，算法不得覆盖。

## 候选校验（wiki-candidate-validation.ts）

- 19 个 issue code；整批通过才允许落盘。
- 事实条目必须带证据 ID，且证据必须在允许范围内。
- 更新/撤回必须指向存在的条目。

## memory-agent.ts

- 限步循环 propose → validate → finish；未完成项作为债务记录。
- 历史上下文压缩到约 85%，避免无限增长。

以上为行为约束，不包含上游源码；源码请按 `README.md` 的 `upstream_diff.py --online` 同步。
