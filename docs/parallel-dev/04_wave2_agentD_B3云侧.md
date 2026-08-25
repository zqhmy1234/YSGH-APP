# Wave 2 · Agent D（B3 云侧域）任务卡——docs/parallel-dev/04

## Mission
完成 B3 云侧 8 项：L2 LLM 语义归并+地点域连续、L3 生命周期+7 天窗、封面图选择、GPS 漂移完善、confirmed 保护、增量"先匹配后分裂"、OCR 内容维接入、L2 待确认区（API 侧）。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B3_events.md`（.cowork-temp/）——#1 L2 启发式占位（pipeline.py:237 注释"LLM 最终裁决在云侧"）、#2 无地点域连续、#3 L3 无 7 天窗/生命周期、#4 封面 cover_content_id 零写入、#5 时间戳校验、#6 GPS、#7 待确认区、#8 confirmed 保护、#9 增量、#17 OCR 内容维。
3. 现状：L0/L1 端云双实现已闭环（真机 10/10）；L2/L3 候选落库为 draft（events.py:196-256，标题"主题 · {tag}"）；`llm_ops/event_merge.py`（已建 stub，你的 LLM 归并入口）。
4. 注意：`backend/app/services/event_aggregation/pipeline.py` 与 `backend/app/services/pipeline.py` 是**两个不同文件**——你管前者。

## Scope（可改）
1. `backend/app/services/event_aggregation/`（st_dbscan.py、pipeline.py、run_validation.py）
2. `backend/app/services/events.py`、`backend/app/api/events.py`（独占）、`backend/app/schemas/event.py`
3. `backend/app/services/llm_ops/event_merge.py`（**你的 LLM 归并**）
4. `backend/tests/test_event_ops.py`、`test_event_sync.py`、`test_agg_reference.py`、`test_pipeline.py`（事件段）

## 绝不碰（只读）
`backend/app/services/pipeline.py`（内容管线，钩子经 pipeline_ext）；dashscope.py；models.py/migrations/（如需要新列：登记表需求）；`client/`（端侧由 Agent E 管，参数一致性靠 AGG 双跑测试）；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **L2 地点域连续**（5km/12hr）：候选分组加地点连续性判断（photo place/geohash 邻近 + 时间连续）。
2. **L2 LLM 归并裁决**：llm_ops/event_merge.merge_verdict——只看元数据（时间/地点/标签/OCR 摘要），qwen-flash 裁决 + 生成标题；≥0.7 转正，<0.7 保持 draft 进待确认。
3. **L3 7 天窗 + 生命周期**：同标签 7 天内 ≥3 次才成流；活跃 30 天→静默→归档状态机（可加 status/字段，先登记表需求）。
4. **封面图选择**：cover_content_id 赋值——人脸优先（腾讯 CI 人脸标签）+ 画面质量分 + 时间居中（L2）/不居中（L3）；用户可换封面。
5. **GPS 漂移完善**：单点众数纠正（现仅坐标置空）、系统性偏移降级"附近/某区"（逆编码粒度）、启用步行 6km/h 阈值（WALK_SPEED_MS 已定义未用）。
6. **confirmed 保护**：_write_l1_days / _write_upper_candidates 跳过用户已确认事件（status=confirmed + title_source=user 的 L1/L3 不再被算法追加/重建）。
7. **增量"先匹配后分裂"**：新照片先尝试并入现有事件（时间窗/地点邻近），超限才分裂（pipeline.py:153-157 现为独立成簇）。
8. **OCR 内容维接入**：L2/L3 候选使用 RawPhoto.ocr_text（无 GPS 照片主信号）。

## Dependencies
- llm_ops/base.chat_text（Wave 0 已建，只读）
- DASHSCOPE key（L2 归并真实调用；无 key 用 mock 通道 + 标注待验证）
- L2/L3 真值数据（50-100 张真实照片）——等团队，不影响代码实现（用 generate_test_photos 合成真值）

## DoD
1. 事件域测试全过（本地可跑部分；依赖 Qdrant 的标 CI）。
2. run_validation.py 各场景更新（含新场景：confirmed 保护、L3 7 天窗）。
3. 更新 .cowork-temp/audit_B3_events.md 状态列。
4. 完成消息：文件清单 + 测试 + 表需求（L3 生命周期字段等）+ 待 key 项。

## Integration
分支 `wave2-agentD`；与 Agent E/F 并行（D 管 events.py/api/event_aggregation；E 管 client + 新文件 event_items API；F 管 llm_ops.rerank/评测——D 的 event_merge.py 与 F 的 rerank.py 同包不同文件，安全）；merge 后全量测试 + OpenAPI 重导出。
