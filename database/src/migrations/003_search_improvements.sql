-- Migration 003: Search improvements
-- Adds prefix-stripped text search, quality score, and improved indexing
--
-- Run with: psql -U postgres -d semantic_search -f migrations/003_search_improvements.sql

-- ============================================================
-- 1. Hebrew prefix-aware text search function
-- ============================================================
-- Strips common Hebrew prefixes (ב,ל,ה,מ,ו,ש,כ) from words
-- to create additional searchable tokens alongside originals.
-- Example: "בישראל" → "בישראל ישראל", "לתקציב" → "לתקציב תקציב"

CREATE OR REPLACE FUNCTION expand_hebrew_prefixes(input_text TEXT)
RETURNS TEXT AS $$
DECLARE
    word TEXT;
    result TEXT := input_text;
    stripped TEXT;
    prefixes TEXT[] := ARRAY['ב','ל','ה','מ','ו','ש','כ'];
    p TEXT;
BEGIN
    FOREACH word IN ARRAY string_to_array(input_text, ' ')
    LOOP
        -- Only process Hebrew words (3+ chars)
        IF length(word) >= 3 THEN
            FOREACH p IN ARRAY prefixes
            LOOP
                IF left(word, 1) = p THEN
                    stripped := substr(word, 2);
                    IF length(stripped) >= 2 THEN
                        result := result || ' ' || stripped;
                    END IF;
                    EXIT; -- only strip one prefix
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================
-- 2. Add expanded text search columns
-- ============================================================
-- These include prefix-stripped variants for better Hebrew matching

-- Segments: drop old generated column and recreate with expansion
ALTER TABLE segments DROP COLUMN IF EXISTS text_search_expanded;
ALTER TABLE segments ADD COLUMN text_search_expanded tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', expand_hebrew_prefixes(text))) STORED;
CREATE INDEX IF NOT EXISTS idx_segments_fts_expanded ON segments USING GIN(text_search_expanded);

-- Sentences: same treatment
ALTER TABLE sentences DROP COLUMN IF EXISTS text_search_expanded;
ALTER TABLE sentences ADD COLUMN text_search_expanded tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', expand_hebrew_prefixes(text))) STORED;
CREATE INDEX IF NOT EXISTS idx_sentences_fts_expanded ON sentences USING GIN(text_search_expanded);

-- ============================================================
-- 3. Add sentence quality score column
-- ============================================================
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS quality_score FLOAT;

-- ============================================================
-- 4. Updated RPC functions using expanded text search
-- ============================================================

-- Sentence keyword search (uses expanded tsvector)
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
      AND (filter_station IS NULL OR m.station = filter_station)
      AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Segment keyword search (uses expanded tsvector)
CREATE OR REPLACE FUNCTION search_segments_keyword(
    search_query TEXT,
    match_count INT DEFAULT 20,
    filter_station TEXT DEFAULT NULL,
    filter_media_type TEXT DEFAULT NULL
)
RETURNS TABLE (
    segment_id UUID,
    segment_text TEXT,
    start_time FLOAT,
    end_time FLOAT,
    topic_id UUID,
    station TEXT,
    media_type TEXT,
    segment_time TIMESTAMPTZ,
    s3_audio_key TEXT,
    rank FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT seg.id AS segment_id, seg.text AS segment_text,
           seg.start_time, seg.end_time, seg.topic_id,
           m.station, m.media_type, m.segment_time, m.s3_audio_key,
           ts_rank(seg.text_search_expanded, plainto_tsquery('simple', search_query))::float AS rank
    FROM segments seg
    JOIN media m ON seg.media_id = m.id
    WHERE seg.text_search_expanded @@ plainto_tsquery('simple', search_query)
      AND (filter_station IS NULL OR m.station = filter_station)
      AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;
