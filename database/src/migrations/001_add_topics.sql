-- Migration: Add topics table and topic_id to chunks
-- Run against existing database to add topic clustering support

-- Topics table
CREATE TABLE IF NOT EXISTS topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    centroid vector(1536),
    chunk_count INT NOT NULL DEFAULT 1,
    label TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topics_centroid ON topics
    USING hnsw (centroid vector_cosine_ops);

-- Add topic_id to chunks
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS topic_id UUID REFERENCES topics(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_topic ON chunks(topic_id);

-- Find nearest topic centroid
CREATE OR REPLACE FUNCTION find_nearest_topic(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.8
)
RETURNS TABLE (
    topic_id uuid,
    similarity float,
    chunk_count int
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        t.id,
        1 - (t.centroid <=> query_embedding) AS similarity,
        t.chunk_count
    FROM topics t
    WHERE 1 - (t.centroid <=> query_embedding) >= match_threshold
    ORDER BY t.centroid <=> query_embedding
    LIMIT 1;
END;
$$;

-- Update topic centroid incrementally
CREATE OR REPLACE FUNCTION update_topic_centroid(
    p_topic_id uuid,
    new_embedding vector(1536),
    old_count int
)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE topics SET
        centroid = (centroid * old_count + new_embedding) / (old_count + 1),
        chunk_count = chunk_count + 1,
        updated_at = NOW()
    WHERE id = p_topic_id;
END;
$$;

-- Expanded search: find matching chunks + related chunks from same topics
CREATE OR REPLACE FUNCTION search_expanded(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.3,
    match_count int DEFAULT 10,
    expand_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    chunk_id uuid,
    chunk_text text,
    start_time float,
    end_time float,
    station text,
    media_type text,
    segment_time timestamptz,
    s3_audio_key text,
    similarity float,
    topic_id uuid,
    match_type text
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    -- Direct matches
    (SELECT
        c.id, c.text, c.start_time, c.end_time,
        m.station, m.media_type, m.segment_time, m.s3_audio_key,
        1 - (c.embedding <=> query_embedding) AS similarity,
        c.topic_id,
        'direct'::text AS match_type
    FROM chunks c
    JOIN media m ON c.media_id = m.id
    WHERE 1 - (c.embedding <=> query_embedding) > match_threshold
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY c.embedding <=> query_embedding
    LIMIT match_count)

    UNION ALL

    -- Topic-expanded matches
    (SELECT
        c2.id, c2.text, c2.start_time, c2.end_time,
        m2.station, m2.media_type, m2.segment_time, m2.s3_audio_key,
        1 - (c2.embedding <=> query_embedding) AS similarity,
        c2.topic_id,
        'topic'::text AS match_type
    FROM chunks c2
    JOIN media m2 ON c2.media_id = m2.id
    WHERE c2.topic_id IN (
        SELECT DISTINCT c3.topic_id
        FROM chunks c3
        WHERE 1 - (c3.embedding <=> query_embedding) > match_threshold
            AND c3.topic_id IS NOT NULL
        ORDER BY c3.embedding <=> query_embedding
        LIMIT match_count
    )
    AND c2.id NOT IN (
        SELECT c4.id
        FROM chunks c4
        WHERE 1 - (c4.embedding <=> query_embedding) > match_threshold
        ORDER BY c4.embedding <=> query_embedding
        LIMIT match_count
    )
    AND (filter_station IS NULL OR m2.station = filter_station)
    AND (filter_media_type IS NULL OR m2.media_type = filter_media_type)
    ORDER BY c2.embedding <=> query_embedding
    LIMIT expand_count);
END;
$$;
