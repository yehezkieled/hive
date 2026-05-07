-- 024_attachment_chunks.sql — chunked embeddings for attachments.
--
-- Sprint 28 mirrors Sprint 26 (blueprint_chunks) for uploaded files.
-- Long text/PDF attachments are split into ~500-token chunks before
-- embedding so retrieval ranks against the matching section instead of
-- one whole-document vector. Image attachments stay as 1-chunk-each
-- (the chunk text is the filename; the embedding is the image's
-- multimodal vector) — keeps the schema uniform.
--
-- The HNSW index lives on the chunks table now. The partial null-
-- embedding index mirrors blueprint_chunks (023) so the rechunk script
-- can find unembedded rows cheaply.
--
-- ``attachments.embedding`` and ``attachments.embed_text`` are dropped.
-- The 11 existing attachments are re-embedded via ``rechunk_attachments.py``
-- (~$0.001 in Voyage calls) — cheaper than writing a SQL migration that
-- preserves them as single-chunk rows.

CREATE TABLE attachment_chunks (
    id BIGSERIAL PRIMARY KEY,
    attachment_id BIGINT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    embedding vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (attachment_id, chunk_index)
);

CREATE INDEX attachment_chunks_embedding_idx
    ON attachment_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX attachment_chunks_embedding_null_idx
    ON attachment_chunks (id)
    WHERE embedding IS NULL;

DROP INDEX IF EXISTS attachments_embedding_idx;
DROP INDEX IF EXISTS attachment_embedding_null_idx;
ALTER TABLE attachments DROP COLUMN IF EXISTS embedding;
ALTER TABLE attachments DROP COLUMN IF EXISTS embed_text;
