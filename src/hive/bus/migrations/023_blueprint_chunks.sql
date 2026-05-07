-- 023_blueprint_chunks.sql — chunked embeddings for blueprints.
--
-- Sprint 26 splits blueprint bodies into ~500-token chunks before embedding,
-- so retrieval can rank against the most relevant section instead of a single
-- whole-body vector. Each blueprint row gets N rows in blueprint_chunks
-- (N >= 1, even for short bodies). The auto-retrieve flow then prepends the
-- best-matching chunk under the parent blueprint title — sharper context, less
-- prompt bloat.
--
-- The HNSW index lives on the chunks table now. The partial null-embedding
-- index mirrors Sprint 18's pattern (019_attachment_embedding_null_index.sql)
-- so the future rechunk script can find unembedded rows cheaply.
--
-- ``blueprints.embedding`` is dropped — the corpus is empty (0 rows) at
-- migration time, so there's nothing to migrate. The column is dead weight
-- once chunks own all embeddings.

CREATE TABLE blueprint_chunks (
    id BIGSERIAL PRIMARY KEY,
    blueprint_id BIGINT NOT NULL REFERENCES blueprints(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    embedding vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (blueprint_id, chunk_index)
);

CREATE INDEX blueprint_chunks_embedding_idx
    ON blueprint_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX blueprint_chunks_embedding_null_idx
    ON blueprint_chunks (id)
    WHERE embedding IS NULL;

DROP INDEX IF EXISTS blueprints_embedding_idx;
ALTER TABLE blueprints DROP COLUMN IF EXISTS embedding;
