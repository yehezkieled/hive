-- 016_embedding_dim_1024.sql — switch from OpenAI 1536d to Voyage 1024d.
--
-- Existing rows used OpenAI's text-embedding-3-small (1536d). Vectors from
-- different embedding models live in different vector spaces and cannot
-- be compared, so there is no mathematical "convert" path — old rows are
-- truncated and any blueprints worth keeping must be re-saved (which
-- re-embeds them via the new provider).
--
-- The HNSW index is dimension-bound, so it must be dropped before the
-- column ALTER and recreated afterwards.

TRUNCATE blueprints;

DROP INDEX IF EXISTS blueprints_embedding_idx;

ALTER TABLE blueprints ALTER COLUMN embedding TYPE vector(1024);

CREATE INDEX blueprints_embedding_idx
    ON blueprints
    USING hnsw (embedding vector_cosine_ops);
