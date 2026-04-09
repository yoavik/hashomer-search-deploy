-- Migration 004: Add quality filters to search functions
-- Filters out low-quality sentences (quality_score > 0.15) at the SQL level
-- to reduce noise from filler, greetings, and short fragments in search results.
-- Segment-level functions are left unchanged — short segment penalization is
-- handled post-ranking in search.py (_merge_results word-count penalty).
--
-- Run with: psql -U postgres -d semantic_search -f migrations/004_quality_filters.sql

-- ============================================================
-- Sentence-level search functions with quality_score filter
-- ============================================================

-- Hebrew sentence semantic search
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
        AND (s.quality_score IS NULL OR s.quality_score > 0.15)
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY s.embedding_he <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Multilingual sentence semantic search
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
        AND (s.quality_score IS NULL OR s.quality_score > 0.15)
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY s.embedding_ml <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Sentence keyword search
CREATE OR REPLACE FUNCTION search_sentences_keyword(
    search_query TEXT,
    match_count INT DEFAULT 20,
    filter_station TEXT DEFAULT NULL,
    filter_media_type TEXT DEFAULT NULL
)
RETURNS TABLE (
    sentence_id UUID,
    sentence_text TEXT,
    segment_id UUID,
    start_time FLOAT,
    end_time FLOAT,
    speaker TEXT,
    station TEXT,
    media_type TEXT,
    segment_time TIMESTAMPTZ,
    s3_audio_key TEXT,
    rank FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT s.id AS sentence_id, s.text AS sentence_text, s.segment_id,
           s.start_time, s.end_time, s.speaker,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           ts_rank(s.text_search_expanded, plainto_tsquery('simple', search_query))::float AS rank
    FROM sentences s
    JOIN media m ON s.media_id = m.id
    WHERE s.text_search_expanded @@ plainto_tsquery('simple', search_query)
      AND (s.quality_score IS NULL OR s.quality_score > 0.15)
      AND (filter_station IS NULL OR m.station = filter_station)
      AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Sentence trigram search
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
        AND (s.quality_score IS NULL OR s.quality_score > 0.15)
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY similarity(s.text, search_query) DESC
    LIMIT match_count;
END;
$$;
