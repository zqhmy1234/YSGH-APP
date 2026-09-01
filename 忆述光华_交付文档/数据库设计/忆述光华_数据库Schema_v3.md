# 忆述光华 · 数据库 Schema v3

> 版本：v3｜日期：2026-08-18｜整理：官海峰（T1 后端）
> 基线：赵清欢 2026-08-18《数据库表结构》v2（32 表）
> 用途：团队定稿用唯一事实源。

---

## 〇、v3 相对 v2 变更总览

| # | 类型 | 变更 | 裁决 |
|---|---|---|---|
| E1 | 必要补充 | echo_history 补 shown_date + 部分唯一索引（每天≤1条约束） | ✅ 纳入 |
| E2 | 必要补充 | 新增 sync_field_versions（字段级 LWW 版本表） | ✅ 纳入 |
| E3 | 必要补充 | 新增 messages（统一消息中心） | ✅ 纳入 |
| E4 | 必要补充 | 新增 profile_dimension_pending（B1 维度扩展队列） | ✅ 纳入 |
| F1 | 修正 | sensitive_words.level 语义 = 三层词表来源（预置/画像驱动/违规回流） | ✅ 修正 |
| F2 | 确认 | event_items 不含 event_level（层级经 JOIN events.level） | ✅ 确认 |
| D1 | 决策建议 | tags 新增 kind（custom / l3_topic） | ⏳ 建议纳入，待确认 |
| D2 | 决策建议 | profile_sensitive 新增 topic_hash（HMAC 盲索引） | ⏳ 建议纳入，待确认 |
| D3 | 外键确认 | content_tags.tag_id 显式声明 REFERENCES tags(id) | ✅ 确认（v2 已含） |

**表数**：v2 32 张 + E2/E3/E4 新增 3 张 = **35 张**（E1/D1/D2 为表内变更）。

---

## 一、4 项必要补充（系统与业务原因）

### E1. echo_history 补约束（回响每天 ≤1 条）

**变更**：新增 `shown_date date` 列 + `UNIQUE(user_id, event_id)` + 部分唯一索引：

```sql
CREATE UNIQUE INDEX uq_echo_history_daily ON echo_history (user_id, shown_date)
    WHERE action IS DISTINCT FROM 'dismiss';
```

**系统原因**：
1. v2/定稿只有注释"回响每天≤1条"无约束，并发双请求下两条记录都能插入（PG 对 NULL 不做唯一冲突），节流形同虚设；
2. UNIQUE(user_id, event_id) 因 event_id 恒 NULL（无事件回响）在 PG 中不生效，必须部分唯一索引兜底；
3. `dismiss`（划掉）不计入每日配额，故 WHERE 排除；
4. shown_date 显式落列而非 `timestamptz::date` 表达式索引——后者非 IMMUTABLE 不能建索引（已实测踩坑）。

**业务原因**：回响是"每日关怀"机制（B5-a），产品承诺每天最多 1 条、划掉不再出现；无约束则用户一天收到多条 = 打扰，直接违反产品验收。

### E2. sync_field_versions（字段级 LWW 版本表）

**变更**：新增表（B4-2 字段级"最后写入胜出"的云端权威存储）：

```sql
CREATE TABLE sync_field_versions (
    entity_type text NOT NULL,     -- content / event / profile
    entity_id   uuid NOT NULL,
    field       text NOT NULL,     -- tags/title/place...
    user_id     uuid NOT NULL REFERENCES users(id),  -- 实体归属（越权校验）
    value       jsonb,             -- 字段当前权威值
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted     boolean NOT NULL DEFAULT false,      -- 实体级墓碑
    PRIMARY KEY (entity_type, entity_id, field)
);
```

**系统原因**：B4-2 字段级 LWW 在定稿只挂在 sync_state.cursor_version（一个单调游标）——游标只能表达"同步到哪个版本"，**表达不了"每个字段最后一次写入的版本"**，字段级冲突解决（手机改 title、Windows 同时改 tags，谁赢）没有落点。此表是字段级 LWW 唯一正确建模。

**业务原因**：跨设备同步（手机 + Windows）是核心场景（B4 离线优先）；无字段级版本则"旧设备覆盖新修改"造成用户数据丢失，这是数据安全红线。

### E3. messages（统一消息中心）

**变更**：新增表（S4-07 推送 + S4-08 消息中心同表）：

```sql
CREATE TABLE messages (
    id       bigserial PRIMARY KEY,
    user_id  uuid NOT NULL REFERENCES users(id),
    channel  text NOT NULL DEFAULT 'in_app',  -- in_app / push
    msg_type text NOT NULL,   -- daily_review / voice_done / care_followup / echo
    title    text NOT NULL,
    body     text NOT NULL,
    payload  jsonb NOT NULL DEFAULT '{}',
    status   text NOT NULL DEFAULT 'unread',  -- unread / read / archived
    sent_at  timestamptz NOT NULL DEFAULT now(),
    read_at  timestamptz
);
```

**系统原因**：定稿无消息域，但推送/站内信是明确需求（语音完成通知、每日复盘 22:00 推送、关怀追问）；in-app 与 push 同表可统一"已读/未读"状态机，避免两套存储。

**业务原因**：消息中心是 MVP 交互闭环（B5-a 关怀 + 复盘推送）；无此表则推送落点只能塞进 ai_request_logs 或日志表，语义混乱。

### E4. profile_dimension_pending（B1 维度扩展队列）

**变更**：新增表（B1"枚举无值→排队→累计人工确认"机制）：

```sql
CREATE TABLE profile_dimension_pending (
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    dimension   text NOT NULL,            -- relation_core / life_events / values_priority
    raw_answer  text NOT NULL,            -- 未命中枚举的原始回答
    count       int NOT NULL DEFAULT 1,   -- 同类累计（同 user+dimension+raw 合并）
    status      text NOT NULL DEFAULT 'pending',  -- pending / confirmed / rejected
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_pdp_user_dim_raw UNIQUE (user_id, dimension, raw_answer)
);
```

**系统原因**：B1 画像维度是枚举化治理，用户回答未命中枚举时需排队累积、达阈值转人工确认后扩展枚举——没有队列表则该流程无法落地，只能丢数据。

**业务原因**：画像枚举是"搜索重排/复盘/追问/回响"的地基（B1 开篇 Q37：画像错了全错）；扩展队列保证枚举随真实用户演化，是画像系统自生长的机制。

---

## 二、2 项修正（系统与业务原因）

### F1. sensitive_words.level 语义修正

**变更**：level 语义 = **三层词表来源**（v2 已正确，v3 明确枚举并保留 integer 类型）：

| level | 含义 | 来源 |
|---|---|---|
| 1 | 预置基础词表 | 涉政/涉黄/暴力标准词（B5-b ①） |
| 2 | 画像敏感标记驱动 | 用户"别再提前任"→ 个人词表（B5-b ②） |
| 3 | 违规词回流 | 百炼检测违规 → 自动加入本地规则表（B5-b ③） |

**原因**：B5-b 深度设计明确三层敏感词表为"预置 / 画像驱动 / 违规回流"三类（预置基础词表、画像敏感标记驱动、违规词回流）。level 仅表达词表来源层级，**不表达敏感度分级**——敏感度属 B1 画像三层披露的另一个概念，勿混用。

### F2. event_items 不含 event_level

**变更**：确认 v2 删除正确——event_items 仅 (content_id, event_id) + UNIQUE 约束，事件层级经 `JOIN events.level` 获取。

**原因**：event_level 是 events 的属性，冗余到 event_items 构成传递依赖（违反 3NF），且存在"content 在 L0 簇、父事件在 L2"的层级不一致风险（同 content 可属多级事件，event_level 单值表达不了）。

---

## 三、2 项决策建议（待团队确认）

### D1. tags.kind（custom / l3_topic）——建议新增

**建议**：`tags` 新增 `kind text NOT NULL DEFAULT 'custom'`（custom=用户自定义 / l3_topic=L3 主题流自动生成）。

**原因**：四层模型 L3（备考/笔记并行长事件）的标签是**自动生成**的，与用户手打标签行为不同（自动标签可被确认转正、可被批量清理）；无 kind 无法区分"标签来源"，L3 主题流管理（回收/转正）无从做起。event_tags 只解决"事件↔标签"关系，解决不了"标签来源层级"。

### D2. profile_sensitive.topic_hash（HMAC 盲索引）——建议新增

**建议**：新增 `topic_hash text`（HMAC 盲索引，加密 key 与索引 key 分离）。

**原因**：B1-6 敏感话题需加密存储（隐私设计），加密后无法 LIKE 检索，盲索引是"加密后仍可精确匹配"的标准解法。此列**空着不影响**（未启用加密时不用），但建表即留位，避免上线后加列（PG 加列虽易，但历史数据回填麻烦）。**可后置**：若 MVP 明确敏感话题暂不加密，可砍（团队定）。

---

## 四、DDL 补全

### D3. content_tags.tag_id 外键

v2 已正确声明，v3 确认：

```sql
content_id uuid NOT NULL REFERENCES contents(id),
tag_id     bigint NOT NULL REFERENCES tags(id),
```

**原因**：tag_id 外键必须显式声明，否则标签删除后关联记录残留（悬空引用），违反引用完整性。v2 已声明，v3 确认保留。

---

## 五、完整表清单（35 表，11 域）

| 域 | 表 | 备注 |
|---|---|---|
| 用户认证（5） | users / user_wechat_bindings / devices / sms_codes / audit_log | 同 v2 |
| 内容（4） | contents / tags(+kind ⏳D1) / content_tags(FK 确认) / voice_segments | |
| 事件（4） | events / event_items(无 level) / event_tags / event_edit_log | 同 v2 |
| 画像（5） | user_profile / profile_dimension_history / profile_sensitive(+topic_hash ⏳D2) / profile_l2_evidence / **profile_dimension_pending(E4)** | +1 |
| 纠错（2） | correction_log / sensitive_words(level 语义修正) | |
| 模板护栏（2） | question_templates / guardrail_logs | 同 v2 |
| 交互（2） | question_history / echo_history(+约束 E1) | |
| 同步（4） | sync_state / offline_queue / deleted_logs / **sync_field_versions(E2)** | +1 |
| 微信（1） | wechat_messages | 同 v2 |
| 基础设施（4） | geo_cache / ai_request_logs / api_cost_stats / finetune_jobs | 同 v2 |
| 消息（1） | **messages(E3)** | +1 |
| 设置（1） | app_settings | 同 v2 |

**合计 35 表**（v2 32 + E2/E3/E4 3 张）。

## 六、实现注记（本地库与本文档的有意偏差）

本地 yishu 库已按 v3 同步（35 表），但保留 4 处实现级偏差（均已在 schema.sql 头部注明，不冲突设计定稿）：

| # | 偏差 | 原因 |
|---|---|---|
| 1 | users.unionid 保持可空 | 手机号直登业务需要（v3 基线 NOT NULL 与业务冲突，当前 41 行 NULL） |
| 2 | offline_queue 保持 UNIQUE(user_id, op_id) | 代码审查 CRITICAL 安全修复（防跨用户 op_id 碰撞幂等误跳过） |
| 3 | correction_log 保留 content_type / qdrant_point_id | 纠错服务依赖（MVP 向量走 Qdrant 外置） |
| 4 | profile_dimension_history.value 保持 text | 维度值为枚举字符串，JSONB 无增益 |

另：echo_history 不建 UNIQUE(user_id, event_id)（PG 多 NULL 不冲突，无效约束），已用部分唯一索引 uq_echo_history_daily 兜底（v3 E1）。

## 七、ER 图

基于 v3 重画，拆 3 张（业务域 / 画像与交互 / 支撑域），补全关系线：

- [忆述光华_库表ER图_业务域.png](忆述光华_库表ER图_业务域.png)：用户·内容·事件（users/认证/contents/tags/events/event_items/event_tags 等 12 表）
- [忆述光华_库表ER图_画像交互.png](忆述光华_库表ER图_画像交互.png)：画像·纠错·交互·消息（user_profile 族/correction_log/question_history/echo_history/messages/app_settings 等 12 表）
- [忆述光华_库表ER图_支撑域.png](忆述光华_库表ER图_支撑域.png)：同步·基础设施·护栏（sync 族/geo_cache/ai_request_logs/api_cost_stats/finetune_jobs/sensitive_words 等 12 表）
