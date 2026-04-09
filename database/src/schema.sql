-- ============================================================
-- Semantic Search Schema for HaShomer (v2 — HE-only, halfvec)
-- ============================================================
-- Single embedding model: e5-large (multilingual, 1024d)
-- Uses halfvec for 50% storage reduction
-- IVFFlat indexes for disk-friendly operation
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- Hebrew morphological text expansion function
-- ============================================================
-- Strips Hebrew prefixes (ב,ל,ה,מ,ו,ש,כ) AND generates common suffix
-- variants for each word. This enables FTS to match morphological forms:
--   "חרדים" → "חרדים חרד חרדי חרדית חרדיות"
--   "בתקציב" → "בתקציב תקציב תקציבים תקציבית"

CREATE OR REPLACE FUNCTION expand_hebrew_text(input_text TEXT)
RETURNS TEXT AS $$
-- Expands Hebrew text for FTS by stripping prefixes and suffixes to find stems.
-- Does NOT generate new suffix variants (to avoid false friends like חרד→חרדה=anxiety).
-- Only adds stripped forms: "חרדים" → adds "חרד", "בתקציב" → adds "תקציב".
-- This way "חרדי" in a query matches a document containing "חרדים" because both
-- reduce to stem "חרד" in the tsvector.
DECLARE
    word TEXT;
    result TEXT := input_text;
    stripped TEXT;
    stem TEXT;
    prefixes TEXT[] := ARRAY['ב','ל','ה','מ','ו','ש','כ'];
    suffixes TEXT[] := ARRAY['ים','ות','ה','י','ן','ת','ית','יים','יות'];
    p TEXT;
    s TEXT;
    base_forms TEXT[];
    base TEXT;
BEGIN
    FOREACH word IN ARRAY string_to_array(input_text, ' ')
    LOOP
        IF length(word) < 3 THEN
            CONTINUE;
        END IF;

        base_forms := ARRAY[word];

        -- Strip single-char Hebrew prefix (ב,ל,ה,מ,ו,ש,כ)
        FOREACH p IN ARRAY prefixes
        LOOP
            IF left(word, 1) = p AND length(word) >= 3 THEN
                stripped := substr(word, 2);
                IF length(stripped) >= 2 THEN
                    result := result || ' ' || stripped;
                    base_forms := array_append(base_forms, stripped);
                END IF;
                EXIT;
            END IF;
        END LOOP;

        -- Strip suffixes to find stems (add stems only, not new variants)
        FOREACH base IN ARRAY base_forms
        LOOP
            FOREACH s IN ARRAY suffixes
            LOOP
                IF length(base) > length(s) + 1 AND right(base, length(s)) = s THEN
                    stem := left(base, length(base) - length(s));
                    IF length(stem) >= 2 THEN
                        result := result || ' ' || stem;
                    END IF;
                    EXIT; -- only strip longest matching suffix
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================
-- pg_hunspell Hebrew dictionary (for future self-hosted PostgreSQL)
-- ============================================================
-- Uncomment when running on self-hosted PostgreSQL with Hebrew hunspell files:
--
-- CREATE TEXT SEARCH DICTIONARY hebrew_hunspell (
--     TEMPLATE = ispell,
--     DictFile = he_IL,
--     AffFile = he_IL,
--     StopWords = hebrew
-- );
-- CREATE TEXT SEARCH CONFIGURATION hebrew (COPY = simple);
-- ALTER TEXT SEARCH CONFIGURATION hebrew
--     ALTER MAPPING FOR word WITH hebrew_hunspell, simple;
--
-- Then change expand_hebrew_text to also use:
--   to_tsvector('hebrew', input_text) for hunspell-stemmed tokens

-- ============================================================
-- Table: media
-- ============================================================
CREATE TABLE IF NOT EXISTS media (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station TEXT NOT NULL,
    media_type TEXT NOT NULL,
    segment_time TIMESTAMPTZ NOT NULL,
    s3_audio_key TEXT NOT NULL,
    s3_transcript_key TEXT,
    duration_seconds FLOAT DEFAULT 900,
    status TEXT DEFAULT 'indexed',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(s3_transcript_key)
);

CREATE INDEX IF NOT EXISTS idx_media_station ON media(station);
CREATE INDEX IF NOT EXISTS idx_media_time ON media(segment_time DESC);
CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type);

-- ============================================================
-- Table: topics
-- ============================================================
CREATE TABLE IF NOT EXISTS topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    centroid halfvec(1024),
    segment_count INT NOT NULL DEFAULT 1,
    label TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Table: segments
-- ============================================================
CREATE TABLE IF NOT EXISTS segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id UUID REFERENCES media(id) ON DELETE CASCADE,
    topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
    segment_index INT NOT NULL,
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    text TEXT NOT NULL,
    embedding_he halfvec(1024),           -- direct embedding of segment text
    speakers TEXT[],
    sentence_count INT NOT NULL DEFAULT 1,
    is_noise BOOLEAN DEFAULT FALSE,
    noise_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_segments_media ON segments(media_id);
CREATE INDEX IF NOT EXISTS idx_segments_topic ON segments(topic_id);

ALTER TABLE segments ADD COLUMN IF NOT EXISTS text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX IF NOT EXISTS idx_segments_fts ON segments USING GIN(text_search);
CREATE INDEX IF NOT EXISTS idx_segments_text_trgm ON segments USING GIN (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_segments_noise ON segments(is_noise) WHERE is_noise = TRUE;

-- Expanded text search (with morphological variants, populated by indexer)
ALTER TABLE segments ADD COLUMN IF NOT EXISTS text_search_expanded tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', expand_hebrew_text(text))) STORED;
CREATE INDEX IF NOT EXISTS idx_segments_fts_expanded ON segments USING GIN(text_search_expanded);

-- ============================================================
-- Table: sentences
-- ============================================================
CREATE TABLE IF NOT EXISTS sentences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id UUID REFERENCES media(id) ON DELETE CASCADE,
    segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
    sentence_index INT NOT NULL,
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    text TEXT NOT NULL,
    embedding_he halfvec(1024),
    speaker TEXT,
    is_noise BOOLEAN DEFAULT FALSE,
    noise_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sentences_media ON sentences(media_id);
CREATE INDEX IF NOT EXISTS idx_sentences_segment ON sentences(segment_id);

ALTER TABLE sentences ADD COLUMN IF NOT EXISTS text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX IF NOT EXISTS idx_sentences_fts ON sentences USING GIN(text_search);
CREATE INDEX IF NOT EXISTS idx_sentences_text_trgm ON sentences USING GIN (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sentences_noise ON sentences(is_noise) WHERE is_noise = TRUE;

ALTER TABLE sentences ADD COLUMN IF NOT EXISTS text_search_expanded tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', expand_hebrew_text(text))) STORED;
CREATE INDEX IF NOT EXISTS idx_sentences_fts_expanded ON sentences USING GIN(text_search_expanded);

-- ============================================================
-- Vector indexes (IVFFlat — disk-friendly, created after data load)
-- Run these AFTER populating data for best quality:
--   CREATE INDEX idx_sentences_emb_he ON sentences
--       USING ivfflat (embedding_he halfvec_cosine_ops) WITH (lists = 100);
--   CREATE INDEX idx_segments_emb_he ON segments
--       USING ivfflat (embedding_he halfvec_cosine_ops) WITH (lists = 100);
--   CREATE INDEX idx_topics_centroid ON topics
--       USING ivfflat (centroid halfvec_cosine_ops) WITH (lists = 50);
-- For small datasets (<10K rows), HNSW is fine:
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_sentences_emb_he ON sentences
    USING hnsw (embedding_he halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_segments_emb_he ON segments
    USING hnsw (embedding_he halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_topics_centroid ON topics
    USING hnsw (centroid halfvec_cosine_ops);

-- ============================================================
-- Query aliases & Hebrew variants (for query expansion)
-- ============================================================
CREATE TABLE IF NOT EXISTS query_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    UNIQUE(source)
);
CREATE INDEX IF NOT EXISTS idx_query_aliases_source ON query_aliases(source);

CREATE TABLE IF NOT EXISTS hebrew_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical TEXT NOT NULL,
    variant TEXT NOT NULL,
    UNIQUE(canonical, variant)
);
CREATE INDEX IF NOT EXISTS idx_hebrew_variants_canonical ON hebrew_variants(canonical);

-- ============================================================
-- RPC Functions — Sentence semantic search (HE only)
-- ============================================================

CREATE OR REPLACE FUNCTION search_sentences_semantic_he(
    query_embedding halfvec(1024),
    match_threshold float DEFAULT 0.18,
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

-- ============================================================
-- RPC Functions — Segment semantic search (HE only)
-- ============================================================

CREATE OR REPLACE FUNCTION search_segments_semantic_he(
    query_embedding halfvec(1024),
    match_threshold float DEFAULT 0.18,
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

-- ============================================================
-- RPC Functions — Keyword search
-- ============================================================

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
           ts_rank(COALESCE(s.text_search_expanded, s.text_search), plainto_tsquery('simple', search_query))::float AS rank
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE COALESCE(s.text_search_expanded, s.text_search) @@ plainto_tsquery('simple', search_query)
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
           ts_rank(COALESCE(seg.text_search_expanded, seg.text_search), plainto_tsquery('simple', search_query))::float AS rank,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE COALESCE(seg.text_search_expanded, seg.text_search) @@ plainto_tsquery('simple', search_query)
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

-- Keyword tsquery variants (operator-aware)
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
           ts_rank(COALESCE(s.text_search_expanded, s.text_search), to_tsquery('simple', tsquery_str))::float AS rank
    FROM sentences s JOIN media m ON s.media_id = m.id
    WHERE COALESCE(s.text_search_expanded, s.text_search) @@ to_tsquery('simple', tsquery_str)
        AND s.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

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
           ts_rank(COALESCE(seg.text_search_expanded, seg.text_search), to_tsquery('simple', tsquery_str))::float AS rank,
           seg.topic_id
    FROM segments seg JOIN media m ON seg.media_id = m.id
    WHERE COALESCE(seg.text_search_expanded, seg.text_search) @@ to_tsquery('simple', tsquery_str)
        AND seg.is_noise IS NOT TRUE
        AND (filter_station IS NULL OR m.station = filter_station)
        AND (filter_media_type IS NULL OR m.media_type = filter_media_type)
    ORDER BY rank DESC LIMIT match_count;
END;
$$;

-- ============================================================
-- RPC Functions — Topic expansion, context, stats
-- ============================================================

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

CREATE OR REPLACE FUNCTION get_segment_sentences(p_segment_ids uuid[])
RETURNS TABLE (
    segment_id uuid, sentence_id uuid, sentence_index int,
    start_time float, end_time float, text text, speaker text
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.segment_id, s.id, s.sentence_index,
           s.start_time, s.end_time, s.text, s.speaker
    FROM sentences s
    WHERE s.segment_id = ANY(p_segment_ids)
    ORDER BY s.segment_id, s.sentence_index;
END;
$$;

CREATE OR REPLACE FUNCTION find_nearest_topic(
    query_embedding halfvec(1024), match_threshold float DEFAULT 0.8
)
RETURNS TABLE (topic_id uuid, similarity float, segment_count int)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT t.id, 1 - (t.centroid <=> query_embedding) AS similarity, t.segment_count
    FROM topics t
    WHERE 1 - (t.centroid <=> query_embedding) >= match_threshold
    ORDER BY t.centroid <=> query_embedding LIMIT 1;
END;
$$;

CREATE OR REPLACE FUNCTION update_topic_centroid(
    p_topic_id uuid, new_embedding halfvec(1024), old_count int
)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    old_arr float8[];
    new_arr float8[];
    result_arr float8[];
    n int := old_count + 1;
BEGIN
    SELECT centroid::float8[] INTO old_arr FROM topics WHERE id = p_topic_id;
    new_arr := new_embedding::float8[];
    SELECT array_agg((old_arr[i] * old_count + new_arr[i]) / n)
      INTO result_arr
      FROM generate_series(1, 1024) AS i;
    UPDATE topics SET
        centroid = result_arr::halfvec(1024),
        segment_count = segment_count + 1,
        updated_at = NOW()
    WHERE id = p_topic_id;
END;
$$;

-- ============================================================
-- Trigram search
-- ============================================================

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

-- ============================================================
-- Stats
-- ============================================================

CREATE OR REPLACE FUNCTION get_index_stats()
RETURNS TABLE (
    station text, media_type text, segment_count bigint,
    sentence_count bigint, topic_count bigint,
    earliest timestamptz, latest timestamptz
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT m.station, m.media_type,
           COUNT(DISTINCT seg.id) AS segment_count,
           COUNT(DISTINCT s.id) AS sentence_count,
           COUNT(DISTINCT seg.topic_id) AS topic_count,
           MIN(m.segment_time) AS earliest, MAX(m.segment_time) AS latest
    FROM media m
    LEFT JOIN segments seg ON seg.media_id = m.id
    LEFT JOIN sentences s ON s.media_id = m.id
    GROUP BY m.station, m.media_type ORDER BY m.station;
END;
$$;
