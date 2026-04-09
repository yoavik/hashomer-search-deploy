-- Migration: Add is_noise + noise_type columns to sentences and segments
-- Run against existing DB: psql -f migrations/001_add_noise_columns.sql

ALTER TABLE sentences ADD COLUMN IF NOT EXISTS is_noise BOOLEAN DEFAULT FALSE;
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS noise_type TEXT;

ALTER TABLE segments ADD COLUMN IF NOT EXISTS is_noise BOOLEAN DEFAULT FALSE;
ALTER TABLE segments ADD COLUMN IF NOT EXISTS noise_type TEXT;

CREATE INDEX IF NOT EXISTS idx_sentences_noise ON sentences(is_noise) WHERE is_noise = TRUE;
CREATE INDEX IF NOT EXISTS idx_segments_noise ON segments(is_noise) WHERE is_noise = TRUE;

-- Update all search RPC functions to exclude noise results.
-- (Re-creates each function with AND is_noise IS NOT TRUE filter.)

CREATE OR REPLACE FUNCTION search_sentences_semantic_he(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.3,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    sentence_id uuid, sentence_text text, segment_id uuid,
    start_time float, end_time float, speaker text,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, s.text, s.segment_id, s.start_time, s.end_time, s.speaker,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (s.embedding_he <=> query_embedding) AS similarity
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE 1 - (s.embedding_he <=> query_embedding) > match_threshold
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY s.embedding_he <=> query_embedding
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_sentences_semantic_ml(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    sentence_id uuid, sentence_text text, segment_id uuid,
    start_time float, end_time float, speaker text,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, s.text, s.segment_id, s.start_time, s.end_time, s.speaker,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (s.embedding_ml <=> query_embedding) AS similarity
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE 1 - (s.embedding_ml <=> query_embedding) > match_threshold
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY s.embedding_ml <=> query_embedding
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_semantic_he(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.3,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (seg.embedding_he <=> query_embedding) AS similarity, seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE 1 - (seg.embedding_he <=> query_embedding) > match_threshold
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY seg.embedding_he <=> query_embedding
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_semantic_ml(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (seg.embedding_ml <=> query_embedding) AS similarity, seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE 1 - (seg.embedding_ml <=> query_embedding) > match_threshold
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY seg.embedding_ml <=> query_embedding
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_sentences_keyword(
    search_query text, match_count int DEFAULT 20,
    filter_station text DEFAULT NULL, filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    sentence_id uuid, sentence_text text, segment_id uuid,
    start_time float, end_time float, speaker text,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, rank float
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, s.text, s.segment_id, s.start_time, s.end_time, s.speaker,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           ts_rank(s.text_search, plainto_tsquery('simple', search_query))::float AS rank
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE s.text_search @@ plainto_tsquery('simple', search_query)
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_keyword(
    search_query text, match_count int DEFAULT 20,
    filter_station text DEFAULT NULL, filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, rank float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           ts_rank(seg.text_search, plainto_tsquery('simple', search_query))::float AS rank,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.text_search @@ plainto_tsquery('simple', search_query)
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_by_topic(
    p_topic_ids uuid[], match_count int DEFAULT 20,
    filter_station text DEFAULT NULL, filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key, seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.topic_id = ANY(p_topic_ids)
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY m.segment_time DESC LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_trigram(
    search_query text,
    match_threshold float DEFAULT 0.15,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           similarity(seg.text, search_query)::float AS similarity,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.text % search_query
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY similarity(seg.text, search_query) DESC
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_sentences_trigram(
    search_query text,
    match_threshold float DEFAULT 0.15,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    sentence_id uuid, sentence_text text, segment_id uuid,
    start_time float, end_time float, speaker text,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, s.text, s.segment_id, s.start_time, s.end_time, s.speaker,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           similarity(s.text, search_query)::float AS similarity
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE s.text % search_query
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY similarity(s.text, search_query) DESC
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_semantic_he_direct(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.25,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (seg.embedding_he_direct <=> query_embedding) AS similarity,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.embedding_he_direct IS NOT NULL
        AND 1 - (seg.embedding_he_direct <=> query_embedding) > match_threshold
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY seg.embedding_he_direct <=> query_embedding
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION search_segments_semantic_ml_direct(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 20,
    filter_station text DEFAULT NULL,
    filter_media_type text DEFAULT NULL
)
RETURNS TABLE (
    segment_id uuid, segment_text text, start_time float, end_time float,
    station text, media_type text, segment_time timestamptz,
    s3_audio_key text, similarity float, topic_id uuid
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id, seg.text, seg.start_time, seg.end_time,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           1 - (seg.embedding_ml_direct <=> query_embedding) AS similarity,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.embedding_ml_direct IS NOT NULL
        AND 1 - (seg.embedding_ml_direct <=> query_embedding) > match_threshold
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY seg.embedding_ml_direct <=> query_embedding
    LIMIT match_count;
END;
$$;
