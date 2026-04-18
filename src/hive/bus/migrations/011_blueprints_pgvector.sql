-- Sprint 11: Blueprints table with pgvector column for semantic similarity search.
-- Stores semantic blueprints (title, body, tags) with OpenAI embedding vectors.
-- HNSW index optimizes cosine-distance retrieval; NULL embeddings are skipped.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS blueprints (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT[] NOT NULL DEFAULT '{}',
    embedding   vector(1536),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for cosine distance (pgvector <=> operator).
-- Built on the embedding column; NULL embeddings are skipped by the planner.
CREATE INDEX IF NOT EXISTS blueprints_embedding_idx
    ON blueprints
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS blueprints_created_at_idx
    ON blueprints (created_at DESC);
