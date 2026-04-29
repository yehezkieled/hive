CREATE TABLE attachments (
    id            BIGSERIAL PRIMARY KEY,
    file_path     TEXT NOT NULL,
    original_name TEXT,
    mime_type     TEXT,
    size_bytes    BIGINT,
    source        TEXT NOT NULL,
    actor         TEXT,
    forwarded_to  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX attachments_created_at_desc ON attachments (created_at DESC);
