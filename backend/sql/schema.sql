-- ============================================================
-- 忆述光华 MVP · PostgreSQL Schema（28 表 10 域）
-- 依据：《忆述光华_数据库Schema设计.md》（2026-08-14 定稿）
-- 全局约定：
--   软删除：业务表带 deleted_at + deleted_by（B4-2，30 天物理清理）
--   时间：timestamptz（UTC 存储，展示转本地）
--   主键：用户相关 UUID；日志/基础设施类 bigserial
--   扩展：contents.extra JSONB
-- ============================================================

-- ========== 1. 用户与认证域（5 表） ==========

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unionid       text UNIQUE,                 -- 微信生态身份主键（Q1-6）
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
    id            bigserial PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id),
    openid        text NOT NULL UNIQUE,
    channel       text NOT NULL,               -- wechat_kf / wechat_app / miniprogram
    bound_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (                         -- 可吊销 refresh_token
    id            bigserial PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id),
    device_id     text NOT NULL,
    platform      text NOT NULL,               -- android / windows
    refresh_token text,
    last_active_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, device_id)
);

CREATE TABLE sms_codes (                       -- 防刷
    id            bigserial PRIMARY KEY,
    phone         text NOT NULL,
    code          text NOT NULL,
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
    entity_id     text,
    action        text NOT NULL,
    before        jsonb,
    after         jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_log_user ON audit_log(user_id, created_at);

-- ========== 2. 内容域（3 表） ==========

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
CREATE INDEX idx_contents_user_created ON contents(user_id, created_at);
CREATE INDEX idx_contents_user_type ON contents(user_id, content_type);
CREATE INDEX idx_contents_taken_at ON contents(user_id, taken_at);

CREATE TABLE content_tags (                    -- 多对多（分歧 B）
    content_id   uuid NOT NULL REFERENCES contents(id),
    tag_id       bigint NOT NULL,
    confidence   double precision,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_id, tag_id)
);
CREATE INDEX idx_content_tags_tag ON content_tags(tag_id);

CREATE TABLE tags (
    id            bigserial PRIMARY KEY,
    user_id       uuid REFERENCES users(id),  -- NULL = 全局标签
    name          text NOT NULL,
    kind          text NOT NULL DEFAULT 'custom',  -- custom / l3_topic
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

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

-- ========== 3. 事件域（3 表） ==========

CREATE TABLE events (                          -- 四层模型 L0-L3
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    level          int NOT NULL,               -- 0-3
    parent_event_id uuid REFERENCES events(id),
    title          text,
    title_source   text,                       -- llm / template / user
    cover_content_id uuid,
    start_time     timestamptz,
    end_time       timestamptz,
    place          text,
    tags           jsonb,
    emotion        jsonb,                      -- 主导+峰值（B5-a 段级合并）
    sensitivity    text,                       -- 敏感标记（B5-b）
    confidence     double precision,           -- <0.7 待确认
    status         text NOT NULL DEFAULT 'draft',  -- draft/confirmed/rejected
    generated_by   text,                       -- device / cloud
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    deleted_by     uuid
);
CREATE INDEX idx_events_user_level ON events(user_id, level);
CREATE INDEX idx_events_user_start ON events(user_id, start_time);

CREATE TABLE event_items (                     -- photo_event 泛化（分歧 A）
    content_id   uuid NOT NULL REFERENCES contents(id),
    event_id     uuid NOT NULL REFERENCES events(id),
    event_level  int NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_id, event_id)
);
CREATE INDEX idx_event_items_event ON event_items(event_id);

CREATE TABLE event_edit_log (                  -- B3-5 用户合并/拆分/确认
    id          bigserial PRIMARY KEY,
    event_id    uuid NOT NULL REFERENCES events(id),
    user_id     uuid NOT NULL REFERENCES users(id),
    action      text NOT NULL,                 -- merge/split/confirm/rename
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== 4. 画像域（4 表） ==========

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

CREATE TABLE profile_sensitive (               -- 画像级敏感，永不过期
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    topic       text NOT NULL,
    topic_hash  text,                          -- HMAC 盲索引（B1-6 加密检索）
    locked      bool NOT NULL DEFAULT false,   -- 用户显式标记
    added_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, topic)
);

CREATE TABLE profile_l2_evidence (             -- L2 维度证据
    id          bigserial PRIMARY KEY,
    dimension   text NOT NULL,
    user_id     uuid NOT NULL REFERENCES users(id),
    evidence_content_ids jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== 5. 纠错域（2 表） ==========

CREATE TABLE correction_log (
    id               bigserial PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES users(id),
    content_id       uuid REFERENCES contents(id),
    content_embedding vector(1024),            -- 向量索引（pgvector；MVP 可先用 Qdrant 外置）
    old_label        text,
    new_label        text,
    source           text,                     -- active / echo / org
    confidence       double precision,
    is_global_candidate bool NOT NULL DEFAULT false,  -- 共性纠错标记
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_correction_log_user ON correction_log(user_id, created_at);

CREATE TABLE sensitive_words (                 -- 三层词表
    id          bigserial PRIMARY KEY,
    word        text NOT NULL,
    level       text NOT NULL,                 -- 通用/谨慎/严格
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
    result      text,
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

CREATE TABLE echo_history (                    -- 回响每天≤1 条/划掉不再出现
    id          bigserial PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id),
    event_id    uuid REFERENCES events(id),
    shown_at    timestamptz NOT NULL DEFAULT now(),
    action      text,                          -- respond / dismiss / suppressed
    fingerprint text,
    UNIQUE (user_id, event_id)
);

-- ========== 8. 同步域（3 表） ==========

CREATE TABLE sync_state (                      -- B4-2 字段级 LWW
    id            bigserial PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id),
    device_id     text NOT NULL,
    cursor_version bigint NOT NULL DEFAULT 0,
    last_sync_at  timestamptz,
    UNIQUE (user_id, device_id)
);

CREATE TABLE offline_queue (                   -- 云端幂等去重
    id          bigserial PRIMARY KEY,
    op_id       text NOT NULL UNIQUE,
    user_id     uuid NOT NULL REFERENCES users(id),
    device_id   text NOT NULL,
    op_type     text NOT NULL,
    payload     jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'pending',  -- pending/processing/done/failed
    retry_count int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deleted_logs (                    -- 软删除 30 天清理对账
    id             bigserial PRIMARY KEY,
    content_id     uuid NOT NULL,
    deleted_by     uuid,
    deleted_at     timestamptz NOT NULL DEFAULT now(),
    cleanup_status text NOT NULL DEFAULT 'pending'  -- pending/done（向量/COS 已清）
);

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

CREATE TABLE geo_cache (                       -- B3-3 逆编码缓存（高德合规：≤30 天）
    geohash     text PRIMARY KEY,              -- 精度 6（≈1.2km）
    place       text,
    city        text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_request_logs (                 -- 成本归因 + 异步任务状态
    id          bigserial PRIMARY KEY,
    user_id     uuid REFERENCES users(id),
    provider    text NOT NULL,                 -- aliyun/baidu/tencent/amap
    engine      text NOT NULL,
    task_type   text NOT NULL,
    tokens      int NOT NULL DEFAULT 0,
    calls       int NOT NULL DEFAULT 1,
    cost_est    double precision NOT NULL DEFAULT 0,
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
    total_cost  double precision NOT NULL DEFAULT 0,
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

-- ========== 11. 设置域（1 表） ==========

CREATE TABLE app_settings (
    user_id              uuid PRIMARY KEY REFERENCES users(id),
    ai_engine            text NOT NULL DEFAULT 'cloud',  -- cloud / ollama
    notification_prefs   jsonb NOT NULL DEFAULT '{}',    -- 回响频率等
    cross_device_visible bool NOT NULL DEFAULT true,
    import_sessions      jsonb NOT NULL DEFAULT '{}'     -- Windows 批量导入进度
);

-- ============================================================
-- 说明：
-- 1. contents.perceptual_hash 唯一约束含 NULL（PostgreSQL 默认允许多 NULL），
--    非照片内容（text/voice）不填哈希，不受唯一约束影响。
-- 2. correction_log.content_embedding 使用 vector(1024) 需 pgvector 扩展；
--    MVP 若向量检索全部走 Qdrant，此列可替换为 qdrant_point_id text。
-- 3. profile_sensitive.topic_hash 为 HMAC 盲索引预留列（加密 key 与索引 key 分离）。
-- ============================================================
