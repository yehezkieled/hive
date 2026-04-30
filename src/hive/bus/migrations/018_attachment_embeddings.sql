-- 018_attachment_embeddings.sql — add embedding columns to attachments.
--
-- Sprint 17 stored uploaded files but didn't embed them. Sprint 18 closes
-- the loop: images and text-extractable docs get a Voyage 1024d vector
-- so the auto-retrieve flow can surface relevant uploads in agent prompts.
-- Non-embeddable mime types (zip, video, etc.) leave embedding NULL and
-- are skipped by the search query's WHERE clause.
--
-- ``embed_text`` keeps a copy of whatever text was fed to the embedder so
-- the auto-retrieve block can show a snippet without re-extracting.

ALTER TABLE attachments ADD COLUMN embedding vector(1024);
ALTER TABLE attachments ADD COLUMN embed_text TEXT;

CREATE INDEX attachments_embedding_idx
    ON attachments
    USING hnsw (embedding vector_cosine_ops);
