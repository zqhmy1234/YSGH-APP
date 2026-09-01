-- ============================================================
-- 忆述光华 MVP · PostgreSQL Schema v3（38 表 11 域）
-- 依据：《忆述光华_数据库Schema_v3.md》（2026-08-18 权威版）
-- 全局约定：
--   软删除：业务表带 deleted_at + deleted_by（B4-2，30 天物理清理）
--   时间：timestamptz（UTC 存储，展示转本地）
--   主键：用户相关 UUID；日志/基础设施类 bigserial
--   扩展：contents.extra JSONB
-- 实现注记（与 v3 文档的差异，均为有意保留）：
--   a. users.unionid 可空 —— 手机号直登业务（v3 基线 NOT NULL 与业务冲突）
--   b. offline_queue UNIQUE(user_id, op_id) —— 代码审查 CRITICAL 安全修复（防跨用户碰撞）
--   c. correction_log 保留 content_type / qdrant_point_id —— 纠错服务依赖（MVP 向量走 Qdrant）
--   d. profile_dimension_history.value text —— 维度值为枚举字符串
--   e. echo_history 不建 UNIQUE(user_id, event_id)（PG 多 NULL 不冲突，无效约束）
-- 2026-08-26：38 表（补 profile_annotation_pool 等 Wave0 迁移表）+ CREATE EXTENSION vector
--   （CI 从零建库缺 pgvector 扩展致 test_vector_extension 挂；镜像需 pgvector/pgvector:pg16）
-- ============================================================

-- pgvector 扩展（v3 后无向量列，预留纠错向量/同步；本地/CI/生产统一在此建）
CREATE EXTENSION IF NOT EXISTS vector;

-- ========== 1. 用户与认证域（5 表） ==========

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unionid       text UNIQUE,                 -- 微信生态身份主键（Q1-6；可空=手机号直登）
    phone         text UNIQUE,                 -- 备用登录
    nickname      text,
    avatar        text,
    status        int NOT NULL DEFAULT 1,      -- 1 正常 2 冻结
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    deleted_by    uuid
);

CREATE TABLE user_wechat_bindings (            -- 一个 unionid 多 openid
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id),
    openid        text NOT NULL UNIQUE,
    channel       text NOT NULL,               -- wechat_kf / wechat_app / miniprogram
    bound_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (                         -- 可吊销 refresh_token
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id),
    device_id     text NOT NULL,
    platform      text NOT NULL,               -- android / windows
    refresh_token text,
    refresh_token_hash text,                    -- TD-P3 M6/G1 R6#8：HMAC-SHA256+独立密钥（`hmac$`前缀）；存量无前缀为 SHA-256
    refresh_rotated_at timestamptz,             -- TD-P3 M6：最后轮换时间
    last_active_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, device_id)
);

CREATE TABLE sms_codes (                       -- 防刷
    id            bigserial PRIMARY KEY,
    phone         text NOT NULL,
    code          text NOT NULL,               -- G1/R6#9：SHA-256+盐 哈希（不存明文）
    salt          text,                        -- G1/R6#9：每码随机盐（存量行可空=无盐兼容）
    expire_at     timestamptz NOT NULL,
    used_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_sms_codes_phone_created ON sms_codes(phone, created_at);

CREATE TABLE audit_log (                       -- B1-6 对话式修改记录
    id            bigserial PRIMARY KEY,
    user_id       uuid REFERENCES users(id),
    actor         text NOT NULL,               -- user / ai
    entity_type   text NOT NULL,               -- profile / event / tag / ...
    entity_id     uuid,
    action        text NOT NULL,
    before        jsonb,
    after         jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_log_user ON audit_log(user_id, created_at);

-- ========== 2. 内容域（4 表） ==========

CREATE TABLE contents (                        -- 核心表
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id),
    content_type    text NOT NULL,             -- photo / text / voice / article
    content_class   text,                      -- 待办/灵感/情绪/引用/混合
    class_source    text,                      -- setfit / llm / rule / user
    model_version   text,                      -- 分类模型版本
    text            text,                      -- OCR 结果/转写/原文
    taken_at        timestamptz,               -- 拍摄/记录时间
    gps_lat         double precision,
    gps_lng         double precision,
    place           text,                      -- 逆编码地名（geo_cache）
    perceptual_hash text,                      -- 感知哈希去重（同用户唯一）
    client_generated_id varchar(64),           -- 创建幂等键（R4#4，客户端生成，同用户唯一）
    emotion         jsonb,                     -- {value, confidence, source}（B5-a）
    sensitive_tags  jsonb,                     -- 敏感话题标签（B5-b 内容级）
    sensitive_status text NOT NULL DEFAULT '正常',  -- 正常/待复核/已遮蔽/已解除
    qdrant_text_id  text,                      -- 向量点引用
    qdrant_image_id text,
    cos_key         text,                      -- 原件
    thumbnail_key   text,                      -- 缩略图
    source          text,                      -- wechat / app / windows / import
    extra           jsonb,                     -- EXIF/时长/尺寸等差异字段
    status          text NOT NULL DEFAULT 'processing',  -- processing/done/failed
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    deleted_by      uuid,
    CONSTRAINT uq_contents_user_hash UNIQUE (user_id, perceptual_hash)
);
CREATE UNIQUE INDEX uq_contents_user_client_generated_id ON contents(user_id, client_generated_id) WHERE client_generated_id IS NOT NULL;
CREATE INDEX idx_contents_user_created ON contents(user_id, created_at);
CREATE INDEX idx_contents_user_type ON contents(user_id, content_type);
CREATE INDEX idx_contents_taken_at ON contents(user_id, taken_at);

CREATE TABLE tags (                            -- custom=用户自定义 / l3_topic=L3 主题流（v3 D1）
    id            bigserial PRIMARY KEY,
    user_id       uuid REFERENCES users(id),  -- NULL = 全局标签
    name          text NOT NULL,
    kind          text NOT NULL DEFAULT 'custom',
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE content_tags (                    -- 多对多（分歧 B）
    content_id   uuid NOT NULL REFERENCES contents(id),
    tag_id       bigint NOT NULL REFERENCES tags(id),
    confidence   double precision,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_id, tag_id)
);
CREATE INDEX idx_content_tags_tag ON content_tags(tag_id);

CREATE TABLE voice_segments (                  -- B5-a 长录音分段
    id            bigserial PRIMARY KEY,
    content_id    uuid NOT NULL REFERENCES contents(id),
    seg_no        int NOT NULL,
    start_sec     double precision,
    end_sec       double precision,
    segment_text  text,
    segment_emotion jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_id, seg_no)
);

-- ========== 3. 事件域（4 表） ==========

CREATE TABLE events (                          -- 四层模型 L0-L3
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    level          int NOT NULL,               -- 0-3
    parent_event_id uuid REFERENCES events(id),
    title          text,
    title_source   text,                       -- llm / template / user / device
    cover_content_id uuid,
    start_time     timestamptz,
    end_time       timestamptz,
    place          text,
    emotion        jsonb,                      -- 主导+峰值（B5-a 段级合并）
    sensitivity    text,                       -- 敏感标记（B5-b）
    confidence     double precision,           -- <0.7 待确认
    status         text NOT NULL DEFAULT 'draft',  -- draft/confirmed/rejected
    generated_by   text,                       -- device / cloud
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    deleted_by     uuid,
    client_event_id varchar(64)           -- 端侧事件幂等键（S-SY-1，同步迁移 a1b2c3d4e5f6）
);
CREATE INDEX idx_events_user_level ON events(user_id, level);
CREATE INDEX idx_events_user_start ON events(user_id, start_time);
CREATE UNIQUE INDEX uq_events_user_client_event ON events(user_id, client_event_id) WHERE client_event_id IS NOT NULL;

CREATE TABLE event_items (                     -- photo_event 泛化（分歧 A）；层级 JOIN events.level（v3 F2）
    content_id   uuid NOT NULL REFERENCES contents(id),
    event_id     uuid NOT NULL REFERENCES events(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_id, event_id)
);
CREATE INDEX idx_event_items_event ON event_items(event_id);

CREATE TABLE event_tags (                      -- 事件标签多对多（v3：替代 events.tags JSONB）
    event_id   uuid NOT NULL REFERENCES events(id),
    tag_id     bigint NOT NULL REFERENCES tags(id),
    PRIMARY KEY (event_id, tag_id)
);
CREATE INDEX idx_event_tags_tag ON event_tags(tag_id);

CREATE TABLE event_edit_log (                  -- B3-5 用户合并/拆分/确认
    id          bigserial PRIMARY KEY,
    event_id    uuid NOT NULL REFERENCES events(id),
    user_id     uuid NOT NULL REFERENCES users(id),
    action      text NOT NULL,                 -- merge/split/confirm/rename
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== 4. 画像域（5 表） ==========

CREATE TABLE user_profile (
    user_id           uuid PRIMARY KEY REFERENCES users(id),
    version           int NOT NULL DEFAULT 1,
    dimensions        jsonb NOT NULL DEFAULT '{}',  -- 稀疏高维枚举（GIN 索引）
    token_usage       bigint NOT NULL DEFAULT 0,
    last_rebuilt_at   timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_profile_dimensions ON user_profile USING GIN (dimensions);

CREATE TABLE profile_dimension_history (       -- 历史值保留最近 10 条
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    dimension   text NOT NULL,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_pdh_user_dim ON profile_dimension_history(user_id, dimension, updated_at);

CREATE TABLE profile_dimension_pending (       -- B1 维度扩展队列：枚举无值→排队，累计人工确认（v3 E4）
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    dimension   text NOT NULL,                 -- relation_core / life_events / values_priority
    raw_answer  text NOT NULL,                 -- 未命中枚举的原始回答
    count       int NOT NULL DEFAULT 1,        -- 同类累计（同 user+dimension+raw 合并）
    status      text NOT NULL DEFAULT 'pending',  -- pending / confirmed / rejected
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_pdp_user_dim_raw UNIQUE (user_id, dimension, raw_answer)
);

CREATE TABLE profile_sensitive (               -- 画像级敏感，永不过期（v1.1 修订：话题×处置 5 级）
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    topic       text NOT NULL,
    topic_hash  text,                          -- HMAC 盲索引（v3 D2：B1-6 加密检索预留）
    disposition text NOT NULL DEFAULT 'forbid',-- allow/mention/caution/review/forbid（5 级处置）
    evidence    jsonb NOT NULL DEFAULT '[]',
    locked      bool NOT NULL DEFAULT false,   -- 用户显式标记
    added_at    timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, topic)
);

CREATE TABLE profile_l2_evidence (             -- L2 维度证据
    id          bigserial PRIMARY KEY,
    dimension   text NOT NULL,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    evidence_content_ids jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_l2_evidence_user_dim ON profile_l2_evidence(user_id, dimension);  -- 画像证据溯源查询（S6-6）

CREATE TABLE profile_annotation_pool (         -- B1 低置信度事件池（设计 2.3，迁移 b0b1c2d3e4f5）
    id               bigserial PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES users(id),
    event_id         text,
    raw_text         text NOT NULL,
    dimension        text,
    candidate_value  text,
    confidence       double precision NOT NULL DEFAULT 0,
    status           text NOT NULL DEFAULT 'pending',  -- pending / reviewed / confirmed / discarded
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_profile_annotation_pool_user ON profile_annotation_pool(user_id, status);

-- ========== 5. 纠错域（2 表） ==========

CREATE TABLE correction_log (
    id               bigserial PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES users(id),
    content_id       uuid REFERENCES contents(id),
    content_type     text NOT NULL DEFAULT 'text',  -- photo/text/voice（B5-c-3：同类型先比）
    qdrant_point_id  text,                     -- MVP：纠错向量存 Qdrant corrections 集合
    old_label        text,
    new_label        text,
    source           text,                     -- active / echo / org
    confidence       double precision,
    is_global_candidate bool NOT NULL DEFAULT false,  -- 共性纠错标记
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_correction_log_user ON correction_log(user_id, created_at);

CREATE TABLE sensitive_words (                 -- 三层词表（v3 F1：来源层级）
    id          bigserial PRIMARY KEY,
    word        text NOT NULL,
    level       int NOT NULL,                  -- 1=预置基础词表 2=画像敏感标记驱动 3=违规词回流
    user_id     uuid REFERENCES users(id),     -- NULL = 全局
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (word, user_id)
);

-- ========== 6. 模板与护栏域（2 表） ==========

CREATE TABLE question_templates (              -- B5-b 骨架池 30-50 个
    id            bigserial PRIMARY KEY,
    type          text NOT NULL,               -- ask / echo / care
    category      text NOT NULL,               -- photo / text / voice
    template_text text NOT NULL,
    slot_vars     jsonb NOT NULL DEFAULT '[]',
    status        text NOT NULL DEFAULT 'active',  -- active / frozen
    usage_count   int NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE guardrail_logs (                  -- B5-b 审计/成本
    id          bigserial PRIMARY KEY,
    user_id     uuid REFERENCES users(id),
    content     text,
    engine      text NOT NULL,                 -- rule / bailian
    result      jsonb,
    cost_tokens int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== 7. 交互状态域（2 表，防重复触发关键） ==========

CREATE TABLE question_history (                -- B5-a 关怀节流 + Q40 频率控制
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    content_id  uuid REFERENCES contents(id),
    template_id bigint REFERENCES question_templates(id),
    fingerprint text NOT NULL,                 -- 防换措辞绕过
    response    text,
    asked_at    timestamptz NOT NULL DEFAULT now(),
    send_status text NOT NULL DEFAULT 'sent',  -- sent / failed / read
    UNIQUE (user_id, fingerprint)
);

CREATE TABLE echo_history (                    -- 回响每天≤1条/划掉不再出现（v3 E1）
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    event_id    uuid REFERENCES events(id),
    shown_at    timestamptz NOT NULL DEFAULT now(),
    shown_date  date,                          -- 展示日（本地日界，应用写入）
    action      text,                          -- respond / dismiss / suppressed
    fingerprint text
);
-- 部分唯一索引兜底"每天≤1 条"（dismiss 不计入；并发双请求只有一条能插入）
CREATE UNIQUE INDEX uq_echo_history_daily ON echo_history (user_id, shown_date)
    WHERE action IS DISTINCT FROM 'dismiss';

-- ========== 8. 同步域（4 表） ==========

CREATE TABLE sync_state (                      -- B4-2 每端同步游标
    id            bigserial PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id),
    device_id     text NOT NULL,
    cursor_version bigint NOT NULL DEFAULT 0,
    last_sync_at  timestamptz,
    UNIQUE (user_id, device_id)
);

CREATE TABLE offline_queue (                   -- 云端幂等去重（user+op 复合唯一：防跨用户碰撞，审查 CRITICAL）
    id          bigserial PRIMARY KEY,
    op_id       text NOT NULL,
    user_id     uuid NOT NULL REFERENCES users(id),
    device_id   text NOT NULL,
    op_type     text NOT NULL,
    payload     jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'pending',  -- pending/processing/done/failed
    retry_count int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, op_id)
);
CREATE INDEX idx_offline_queue_user_id ON offline_queue(user_id, id);  -- 增量拉取游标扫描（S6-4）

CREATE TABLE deleted_logs (                    -- 软删除 30 天清理对账
    id             bigserial PRIMARY KEY,
    content_id     uuid NOT NULL,
    deleted_by     uuid,
    deleted_at     timestamptz NOT NULL DEFAULT now(),
    cleanup_status text NOT NULL DEFAULT 'pending'  -- pending/done（向量/COS 已清）
);
CREATE INDEX idx_deleted_logs_cleanup ON deleted_logs(cleanup_status, deleted_at);  -- 30 天清理扫描（S6-4）

CREATE TABLE sync_field_versions (             -- B4-2 字段级 LWW 版本存储（云端权威，v3 E2）
    entity_type  text NOT NULL,                -- content / event / profile
    entity_id    uuid NOT NULL,
    field        text NOT NULL,                -- 字段名（tags/title/place...）
    user_id      uuid NOT NULL REFERENCES users(id),  -- 实体归属（越权校验）
    value        jsonb,                        -- 字段当前权威值
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted      boolean NOT NULL DEFAULT false,  -- 软删除墓碑（entity 级）
    PRIMARY KEY (entity_type, entity_id, field)
);
CREATE INDEX idx_sync_fv_entity ON sync_field_versions(entity_type, entity_id);
CREATE INDEX idx_sync_fv_user ON sync_field_versions(user_id);

-- ========== 9. 微信通道域（1 表） ==========

CREATE TABLE wechat_messages (
    id          bigserial PRIMARY KEY,
    msg_id      text NOT NULL UNIQUE,          -- 幂等
    user_id     uuid REFERENCES users(id),
    msg_type    text NOT NULL,                 -- text / image / link / voice
    content     text,
    media_id    text,
    status      text NOT NULL DEFAULT 'processing',  -- processed / failed
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== 10. 基础设施域（4 表） ==========

CREATE TABLE upload_tasks (                   -- S5-03 分片上传任务（CI 快照补同步，对齐 models.UploadTask）
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL,
    client_upload_id text NOT NULL,           -- 客户端幂等键（重传复用）
    file_name      text NOT NULL,
    file_size      bigint NOT NULL,
    chunk_size     bigint NOT NULL,
    chunk_count    bigint NOT NULL,
    file_key       text NOT NULL,
    storage        text NOT NULL DEFAULT 'fake',
    status         text NOT NULL DEFAULT 'pending',  -- pending/uploading/completed/failed
    completed_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_upload_id)
);
CREATE INDEX idx_upload_tasks_user ON upload_tasks(user_id);

CREATE TABLE upload_chunks (                  -- S5-03 分片状态（断电续传依据，对齐 models.UploadChunk）
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_id    uuid NOT NULL,
    chunk_index  bigint NOT NULL,
    chunk_hash   text NOT NULL,
    size         bigint NOT NULL,
    status       text NOT NULL DEFAULT 'uploaded',
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (upload_id, chunk_index)
);
CREATE INDEX idx_upload_chunks_upload ON upload_chunks(upload_id);

CREATE TABLE geo_cache (                       -- B3-3 逆编码缓存（高德合规：≤30 天）
    geohash     text PRIMARY KEY,              -- 精度 6（≈1.2km）
    place       text,
    city        text,
    province    text,                          -- 对齐迁移 4d00dfec7b46（province 列）
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_request_logs (                 -- 成本归因 + 异步任务状态
    id          bigserial PRIMARY KEY,
    user_id     uuid REFERENCES users(id),
    provider    text NOT NULL,                 -- aliyun/baidu/tencent/amap
    engine      text NOT NULL,
    task_type   text NOT NULL,
    tokens      bigint NOT NULL DEFAULT 0,
    calls       int NOT NULL DEFAULT 1,
    cost_est    numeric NOT NULL DEFAULT 0,
    status      text NOT NULL DEFAULT 'done',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_logs_provider_date ON ai_request_logs(provider, created_at);

CREATE TABLE api_cost_stats (                  -- ai_request_logs 定时聚合视图
    id          bigserial PRIMARY KEY,
    provider    text NOT NULL,
    stat_date   date NOT NULL,
    total_tokens bigint NOT NULL DEFAULT 0,
    total_calls bigint NOT NULL DEFAULT 0,
    total_cost  numeric NOT NULL DEFAULT 0,
    UNIQUE (provider, stat_date)
);

CREATE TABLE finetune_jobs (                   -- B5-c 共性纠错微调
    id            bigserial PRIMARY KEY,
    trigger       text NOT NULL,               -- >=50 条共性纠错
    dataset_count int NOT NULL DEFAULT 0,
    model         text,
    status        text NOT NULL DEFAULT 'pending',  -- pending/running/done/failed
    started_at    timestamptz,
    finished_at   timestamptz
);

-- ========== 11. 消息域（1 表，v3 E3） ==========

CREATE TABLE messages (                        -- 统一消息中心：in-app + push 同表
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    channel     text NOT NULL DEFAULT 'in_app',-- in_app / push
    msg_type    text NOT NULL,                 -- daily_review / voice_done / care_followup / echo
    title       text NOT NULL,
    body        text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',   -- 内容 id / 语音 id / 模板标记
    status      text NOT NULL DEFAULT 'unread',-- unread / read / archived
    sent_at     timestamptz NOT NULL DEFAULT now(),
    read_at     timestamptz
);
CREATE INDEX idx_messages_user_sent ON messages(user_id, sent_at DESC);
CREATE INDEX idx_messages_user_id ON messages(user_id, id);  -- 消息按 id 分页/游标（S6-4）

-- ========== 12. 设置域（1 表） ==========

CREATE TABLE app_settings (
    user_id              uuid PRIMARY KEY REFERENCES users(id),
    ai_engine            text NOT NULL DEFAULT 'cloud',  -- cloud / ollama
    notification_prefs   jsonb NOT NULL DEFAULT '{}',    -- 回响频率等
    cross_device_visible bool NOT NULL DEFAULT false,    -- v3：隐私默认关
    import_sessions      jsonb NOT NULL DEFAULT '{}'     -- Windows 批量导入进度
);

-- ============================================================
-- 说明：
-- 1. contents.perceptual_hash 唯一约束含 NULL（PostgreSQL 默认允许多 NULL），
--    非照片内容（text/voice）不填哈希，不受唯一约束影响。
-- 2. sync_field_versions.user_id 部分依赖复合主键（实体归属冗余），
--    为越权校验的有意反规范化（v3 评审三.2）。
-- 3. profile_sensitive.topic_hash 为 HMAC 盲索引预留列（加密 key 与索引 key 分离）。
-- ============================================================
