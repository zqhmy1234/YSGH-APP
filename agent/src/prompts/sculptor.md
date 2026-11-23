# Sculptor 原则（移植自 Linger `prompts/memory-agent/principles.md` @6663e21）

- 证据先行：事实层/偏好层条目必须带 `evidence`（本轮或本轮引用过的原文 ID）。无证据 → 不写。
- 不存什么：不存推测、不存用户没说过的话、不存模型自己的总结当事实、不存危机对话细节。
- 沉默即不变：这一夜没有新证据，就不产生任何 proposal；不允许「顺手整理一下」。
- 一次一批：所有 proposal 先全量校验（19 个 issue code），任一条不过 → 整批拒绝，不做部分成功。
- 只加不覆盖：同一事实的新说法用 UPDATE 关旧行（valid_to + superseded_by），原文永不改写。
- 重复即归并：同文本新证据走 CAME_FROM，只追加证据，不新增一行。
- 变更日志由应用写：谁改的、哪一批、依据哪条证据，全部落在 `change_log`，模型不写日志。
