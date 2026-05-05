-- 019_attachment_embedding_null_index.sql — partial index for embedding backfill.
--
-- Sprint 18 ships a backfill loop that scans attachments with embedding IS NULL
-- and embeds them in batches. Without a partial index, each iteration does a
-- full table scan as the attachments table grows. This index covers only the
-- unembedded rows so the backfill stays cheap and shrinks to zero work once
-- everything is embedded.

CREATE INDEX IF NOT EXISTS attachment_embedding_null_idx
ON attachments (id)
WHERE embedding IS NULL;
