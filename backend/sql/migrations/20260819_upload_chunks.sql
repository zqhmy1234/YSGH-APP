-- WP-C · 20260819 upload_tasks + upload_chunks（S5-03 COS 分片/断电续传）
-- 对齐 backend/app/db/models.py UploadTask/UploadChunk

CREATE TABLE IF NOT EXISTS upload_tasks (
    id                UUID PRIMARY KEY,
    user_id           UUID NOT NULL,
    client_upload_id  VARCHAR NOT NULL,
    file_name         VARCHAR NOT NULL,
    file_size         BIGINT NOT NULL,
    chunk_size        BIGINT NOT NULL,
    chunk_count       BIGINT NOT NULL,
    file_key          VARCHAR NOT NULL,
    storage           VARCHAR NOT NULL DEFAULT 'fake',
    status            VARCHAR NOT NULL DEFAULT 'pending',  -- pending/uploading/completed/failed
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_upload_tasks_user_client UNIQUE (user_id, client_upload_id)
);
CREATE INDEX IF NOT EXISTS ix_upload_tasks_user_id ON upload_tasks (user_id);

CREATE TABLE IF NOT EXISTS upload_chunks (
    id           UUID PRIMARY KEY,
    upload_id    UUID NOT NULL,
    chunk_index  BIGINT NOT NULL,
    chunk_hash   VARCHAR NOT NULL,
    size         BIGINT NOT NULL,
    status       VARCHAR NOT NULL DEFAULT 'uploaded',  -- uploaded 即已落存储
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_upload_chunks_task_index UNIQUE (upload_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS ix_upload_chunks_upload_id ON upload_chunks (upload_id);
