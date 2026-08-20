-- ============================================================
-- 忆述光华 · 库表 v3 同步迁移（2026-08-18）
-- 目标：本地 PG 隔离库 yishu 34 表 → v3 35 表
-- 基线对照：忆述光华_数据库Schema_v3.md
-- 说明：开发库，无真实用户数据；events/event_items/content_tags 等均为 0 行
-- ============================================================

BEGIN;

-- 1. 新表 event_tags（v3 事件域：events.tags JSONB 规范化拆分，多对多）
CREATE TABLE IF NOT EXISTS event_tags (
    event_id uuid NOT NULL REFERENCES events(id),
    tag_id   bigint NOT NULL REFERENCES tags(id),
    PRIMARY KEY (event_id, tag_id)
);
COMMENT ON TABLE event_tags IS '事件标签多对多（v3：替代 events.tags JSONB）';

-- 2. events.tags JSONB 列删除（0 行非空，无迁移数据）
ALTER TABLE events DROP COLUMN IF EXISTS tags;

-- 3. event_items.event_level 删除（0 行；v3 F2：层级经 JOIN events.level，消除传递依赖）
ALTER TABLE event_items DROP COLUMN IF EXISTS event_level;

-- 4. sensitive_words.level text → integer（0 行；v3 F1：三层词表来源 1=预置 2=画像驱动 3=违规回流）
ALTER TABLE sensitive_words ALTER COLUMN level TYPE integer USING NULL::integer;
COMMENT ON COLUMN sensitive_words.level IS '三层词表来源：1=预置基础词表 2=画像敏感标记驱动 3=违规词回流（v3 F1）';

-- 5. content_tags.tag_id 外键补全（v3 D3：引用完整性）
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'content_tags_tag_id_fkey') THEN
        ALTER TABLE content_tags
            ADD CONSTRAINT content_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES tags(id);
    END IF;
END $$;

-- 6. audit_log.entity_id text → uuid（0 行；v3 类型统一）
ALTER TABLE audit_log ALTER COLUMN entity_id TYPE uuid USING NULL::uuid;

-- 7. guardrail_logs.result text → jsonb（0 行；v3 类型统一）
ALTER TABLE guardrail_logs ALTER COLUMN result TYPE jsonb USING NULL::jsonb;

-- 8. user_wechat_bindings.id bigserial → uuid（0 行，重建；v3 全局约定"用户相关用 UUID"）
DROP TABLE IF EXISTS user_wechat_bindings;
CREATE TABLE user_wechat_bindings (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   uuid NOT NULL REFERENCES users(id),
    openid    text NOT NULL UNIQUE,
    channel   text NOT NULL,               -- wechat_kf / wechat_app / miniprogram
    bound_at  timestamptz NOT NULL DEFAULT now()
);

-- 9. devices.id bigserial → uuid（1092 行，重建保留数据；v3 全局约定）
ALTER TABLE devices RENAME TO devices_v3_old;
CREATE TABLE devices (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id),
    device_id      text NOT NULL,
    platform       text NOT NULL,          -- android / windows
    refresh_token  text,
    last_active_at timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, device_id)
);
INSERT INTO devices (id, user_id, device_id, platform, refresh_token, last_active_at, created_at)
SELECT gen_random_uuid(), user_id, device_id, platform, refresh_token, last_active_at, created_at
FROM devices_v3_old;
DROP TABLE devices_v3_old;

-- 10. app_settings.cross_device_visible 默认值 true → false（v3 隐私默认关）
ALTER TABLE app_settings ALTER COLUMN cross_device_visible SET DEFAULT false;

-- ============================================================
-- 明确不做（实现偏差，见 v3 文档"实现注记"）：
--  a. users.unionid 保持可空 —— 手机号直登业务需要（当前 41 行 NULL）
--  b. offline_queue 保持 UNIQUE(user_id, op_id) —— 代码审查 CRITICAL 安全修复
--  c. correction_log 保留 content_type / qdrant_point_id —— 纠错服务依赖（MVP 向量走 Qdrant）
--  d. profile_dimension_history.value 保持 text —— ORM 为 String，维度值为枚举字符串
--  e. echo_history 已有 shown_date + uq_echo_history_daily（v3 E1 已满足）；
--     UNIQUE(user_id, event_id) 因 event_id 恒 NULL 在 PG 不生效，不建无效约束
-- ============================================================

COMMIT;

-- 校验
SELECT count(*) AS table_count FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
