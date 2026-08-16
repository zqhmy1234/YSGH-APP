# 忆述光华 MVP · 数据库 Schema 设计

> 日期：2026-08-14｜整理：技术部（海峰）
> 依据：认证体系 + B1-B5-e 全部深度设计 + 两轮实体审计（自审 + subagent 独立审计）
> 存储：PostgreSQL（JSONB 扩展字段 + 软删除全局模式）

## 0. 全局约定
- **软删除**：所有业务表带 `deleted_at` + `deleted_by`（B4-2，30 天物理清理，deleted_logs 对账）
- **时间**：统一 `timestamptz`（UTC 存储，展示转本地）
- **ID**：bigserial 或 UUID（用户相关用 UUID，日志类 bigserial）
- **审计**：用户对画像/事件/标签的人工修改写入 audit_log（B1-6）

## 1. 用户与认证域（5 表）

### users
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| unionid | text UNIQUE | 微信生态身份主键（Q1-6） |
| phone | text UNIQUE NULL | 备用登录 |
| nickname / avatar | text | 展示 |
| status | int | 1 正常 2 冻结 |
| created_at / updated_at / deleted_at | | |

### user_wechat_bindings（一个 unionid 多 openid）
user_id FK、openid UNIQUE、channel（wechat_kf / wechat_app / miniprogram）、bound_at

### devices
user_id FK、device_id、platform（android/windows）、refresh_token（**可吊销**，subagent 建议）、last_active_at

### sms_codes（防刷）
phone、code、expire_at、used_at、created_at；索引(phone, created_at)

### audit_log（B1-6 对话式修改记录）
user_id、actor（user/ai）、entity_type（profile/event/tag/...）、entity_id、action、before/after（JSONB）、created_at

## 2. 内容域（3 表）

### contents（核心表）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id FK | | 索引(user_id, created_at) |
| content_type | text | photo/text/voice/article（物理类型） |
| content_class | text | 待办/灵感/情绪/引用/混合（语义分类） |
| class_source | text | setfit/llm/rule/user |
| model_version | text | 分类模型版本（微调追溯，简化自 model_versions） |
| text | text | OCR 结果/转写/原文 |
| taken_at | timestamptz | 拍摄/记录时间 |
| gps_lat / gps_lng | float | |
| place | text | 逆编码地名（geo_cache） |
| perceptual_hash | text | **感知哈希去重**（同用户唯一索引） |
| tags 见 content_tags | | |
| emotion | jsonb | {value, confidence, source}（B5-a） |
| sensitive_tags | jsonb | 敏感话题标签（B5-b 内容级） |
| sensitive_status | text | 正常/待复核/已遮蔽/已解除（subagent 建议） |
| qdrant_text_id / qdrant_image_id | text | 向量点引用 |
| cos_key / thumbnail_key | text | 原件/缩略图 |
| source | text | wechat/app/windows/import |
| extra | jsonb | EXIF/时长/尺寸等差异字段 |
| status | text | processing/done/failed |
| created_at / updated_at / deleted_at | | |

### content_tags（多对多，分歧 B 已定）
content_id FK、tag_id FK、confidence；唯一(content_id, tag_id)

### voice_segments（B5-a 长录音分段）
content_id FK、seg_no、start_sec/end_sec、segment_text、segment_emotion jsonb

## 3. 事件域（3 表）

### events
| 字段 | 说明 |
|---|---|
| id、user_id、level（0-3）、parent_event_id | 四层模型 |
| title、title_source（llm/template/user） | |
| cover_content_id | 封面 |
| start_time / end_time | 时间范围 |
| place | |
| tags[]（jsonb） | |
| emotion jsonb（主导+峰值） | B5-a 段级合并 |
| sensitivity | 敏感标记（B5-b） |
| confidence | B3-5（<0.7 待确认） |
| status | draft/confirmed/rejected（用户背书，subagent 建议） |
| generated_by | device/cloud（端侧 L0/L1 vs 云侧 L2/L3） |
| deleted_at | |

### event_items（photo_event 泛化，分歧 A 已定）
content_id FK、event_id FK、event_level；唯一(content_id, event_id)

### event_edit_log（B3-5 用户合并/拆分/确认记录）
event_id、user_id、action（merge/split/confirm/rename）、detail jsonb、created_at

## 4. 画像域（4 表）

### user_profile
user_id PK、version、dimensions jsonb（**稀疏高维枚举**，GIN 索引）、token_usage、last_rebuilt_at

### profile_dimension_history（B1 历史值保留最近 10 条）
user_id、dimension、value、updated_at；按维度滚动裁剪

### profile_sensitive（画像级敏感，永不过期）
user_id、topic、locked bool（用户显式标记）、added_at

### profile_l2_evidence（L2 维度证据）
dimension、user_id、evidence_content_ids[]、created_at

## 5. 纠错域（2 表）

### correction_log
id、user_id、content_id、content_embedding（向量索引）、old_label、new_label、source（active/echo/org）、confidence、is_global_candidate（共性纠错标记）、created_at；每用户 500 条上限

### sensitive_words（三层词表：预置/画像驱动/违规回流）
word、level、user_id NULL（全局）、created_at

## 6. 模板与护栏域（2 表）

### question_templates（B5-b 骨架池 30-50 个，产品运营维护）
id、type（ask/echo/care）、category（photo/text/voice）、template_text、slot_vars[]、status（active/frozen）、usage_count、created_at

### guardrail_logs（B5-b 审计/成本）
user_id、content、engine（rule/bailian）、result、cost_tokens、created_at

## 7. 交互状态域（2 表，防重复触发关键）

### question_history（B5-a 关怀节流 + Q40 频率控制）
user_id、content_id、template_id、fingerprint（防换措辞绕过，subagent 建议）、response text、asked_at、send_status（sent/failed/read）

### echo_history（回响每天≤1 条/划掉不再出现）
user_id、event_id、shown_at、action（respond/dismiss/suppressed）、fingerprint

## 8. 同步域（2 表）

### sync_state（B4-2 字段级 LWW）
user_id、device_id、cursor_version、last_sync_at

### offline_queue（云端幂等去重）
op_id UNIQUE、user_id、device_id、op_type、payload jsonb、status、retry_count

### deleted_logs（软删除 30 天清理对账，subagent 建议）
content_id、deleted_by、deleted_at、**cleanup_status（向量/COS 是否已清，防孤儿资源）**

## 9. 微信通道域（1 表）

### wechat_messages
msg_id UNIQUE（幂等）、user_id、msg_type（text/image/link/voice）、content、media_id、status（processed/failed）、created_at

## 10. 基础设施域（4 表）

### geo_cache（B3-3 逆编码缓存）
geohash PK（精度 6）、place、city、updated_at；**高德合规：缓存不超 30 天**

### ai_request_logs（成本归因 + 异步任务状态）
user_id、provider（aliyun/baidu/tencent/amap）、engine、task_type、tokens/calls、cost_est、status、created_at

### api_cost_stats（ai_request_logs 定时聚合视图）
provider、date、total_tokens、total_calls、total_cost

### finetune_jobs（B5-c 共性纠错微调）
id、trigger（≥50 条共性纠错）、dataset_count、model、status、started_at/finished_at

## 11. 设置域（1 表）

### app_settings
user_id PK、ai_engine（cloud/ollama）、notification_prefs jsonb（回响频率）、cross_device_visible bool、import_sessions jsonb（Windows 批量导入进度，可选云端）

---

## 统计
**28 张表**（10 域）：用户认证 5 / 内容 3 / 事件 3 / 画像 4 / 纠错 2 / 模板护栏 2 / 交互状态 2 / 同步 3 / 微信 1 / 基础设施 4 / 设置 1

## 关键索引
- contents：UNIQUE(user_id, perceptual_hash)｜(user_id, created_at)｜(user_id, content_type)
- events：(user_id, level)｜(user_id, start_time)
- event_items：UNIQUE(content_id, event_id)
- content_tags：UNIQUE(content_id, tag_id)
- wechat_messages：UNIQUE(msg_id)｜offline_queue：UNIQUE(op_id)
- question_history：UNIQUE(user_id, fingerprint)
- user_profile：dimensions GIN 索引｜correction_log：embedding 向量索引
- geo_cache：PK(geohash)

## 审计记录
- 自审第一遍：补 voice_segments / profile_dimension_history / question_templates / guardrail_logs / question_history / echo_history / sensitive_words / api_cost_stats（8 项）
- 自审第二遍：补 event_items 泛化（文字/语音也进事件）/ geo_cache / audit_log / question_history.response
- subagent 审计：补 tags/content_tags 多对多 / model_version+finetune_jobs / ai_request_logs / import_sessions / perceptual_hash / events.status / sensitive_status / deleted_logs 清理状态 / devices 吊销 / fingerprint 防绕过（合并 care_records 入 question/echo_history）
