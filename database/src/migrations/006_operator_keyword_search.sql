-- Migration 006: Add operator-aware keyword search functions
-- These accept a pre-built tsquery string (from to_tsquery) instead of using plainto_tsquery.
-- Used when the query contains operators like "", AND, OR, NOT.

-- Sentence keyword search with pre-built tsquery
CREATE OR REPLACE FUNCTION search_sentences_keyword_tsquery(
    tsquery_str text, match_count int DEFAULT 20,
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
           ts_rank(s.text_search, to_tsquery('simple', tsquery_str))::float AS rank
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE s.text_search @@ to_tsquery('simple', tsquery_str)
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

-- Segment keyword search with pre-built tsquery
CREATE OR REPLACE FUNCTION search_segments_keyword_tsquery(
    tsquery_str text, match_count int DEFAULT 20,
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
           ts_rank(seg.text_search, to_tsquery('simple', tsquery_str))::float AS rank,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE seg.text_search @@ to_tsquery('simple', tsquery_str)
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;
