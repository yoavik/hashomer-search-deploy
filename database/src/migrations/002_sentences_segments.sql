-- ============================================================
-- Migration 002: Replace chunks with sentences + segments (dual embedding)
--
-- New hierarchy: sentences > segments > topics
-- Dual embeddings: NeoDictaBERT (768d) + e5-large (1024d)
-- ============================================================

-- 1. Create sentences table
CREATE TABLE IF NOT EXISTS sentences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id UUID REFERENCES media(id) ON DELETE CASCADE,
    segment_id UUID,
    sentence_index INT NOT NULL,
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    text TEXT NOT NULL,
    embedding_he vector(768),
    embedding_ml vector(1024),
    speaker TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sentences_media ON sentences(media_id);
CREATE INDEX IF NOT EXISTS idx_sentences_segment ON sentences(segment_id);
CREATE INDEX IF NOT EXISTS idx_sentences_emb_he ON sentences USING hnsw (embedding_he vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_sentences_emb_ml ON sentences USING hnsw (embedding_ml vector_cosine_ops);

ALTER TABLE sentences ADD COLUMN IF NOT EXISTS text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX IF NOT EXISTS idx_sentences_fts ON sentences USING GIN(text_search);

-- 2. Create/update segments table
CREATE TABLE IF NOT EXISTS segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id UUID REFERENCES media(id) ON DELETE CASCADE,
    topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
    segment_index INT NOT NULL,
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    text TEXT NOT NULL,
    embedding_he vector(768),
    embedding_he_direct vector(768),
    embedding_ml vector(1024),
    embedding_ml_direct vector(1024),
    speakers TEXT[],
    sentence_count INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_segments_media ON segments(media_id);
CREATE INDEX IF NOT EXISTS idx_segments_topic ON segments(topic_id);
CREATE INDEX IF NOT EXISTS idx_segments_emb_he ON segments USING hnsw (embedding_he vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_segments_emb_he_direct ON segments USING hnsw (embedding_he_direct vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_segments_emb_ml ON segments USING hnsw (embedding_ml vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_segments_emb_ml_direct ON segments USING hnsw (embedding_ml_direct vector_cosine_ops);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'segments' AND column_name = 'text_search'
    ) THEN
        ALTER TABLE segments ADD COLUMN text_search tsvector
            GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_segments_fts ON segments USING GIN(text_search);

-- 3. FK from sentences to segments
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'sentences_segment_id_fkey'
    ) THEN
        ALTER TABLE sentences
            ADD CONSTRAINT sentences_segment_id_fkey
            FOREIGN KEY (segment_id) REFERENCES segments(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 4. Rename topics.chunk_count to segment_count
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'topics' AND column_name = 'chunk_count'
    ) THEN
        ALTER TABLE topics RENAME COLUMN chunk_count TO segment_count;
    END IF;
END $$;

-- 5. Drop old chunks table
DROP TABLE IF EXISTS chunks CASCADE;

-- 6. Drop old functions
DROP FUNCTION IF EXISTS search_semantic CASCADE;
DROP FUNCTION IF EXISTS search_keyword CASCADE;
DROP FUNCTION IF EXISTS search_expanded CASCADE;
DROP FUNCTION IF EXISTS get_chunks_context CASCADE;
