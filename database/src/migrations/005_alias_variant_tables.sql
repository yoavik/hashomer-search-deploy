-- Migration 005: Move QUERY_ALIASES and HEBREW_VARIANTS to DB tables
-- These were previously hardcoded in search.py. DB tables allow editing
-- without code changes and make it easy to add new entries over time.
--
-- Run with: psql -U postgres -d semantic_search -f migrations/005_alias_variant_tables.sql

-- ============================================================
-- Table: query_aliases
-- Maps English terms/phrases to their Hebrew equivalents for search expansion.
-- ============================================================
CREATE TABLE IF NOT EXISTS query_aliases (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,          -- English term (lowercased)
    target TEXT NOT NULL,          -- Hebrew equivalent
    category TEXT,                 -- optional grouping (politics, military, economy, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source)
);

-- ============================================================
-- Table: hebrew_variants
-- Maps a canonical Hebrew form to ASR transcription variants.
-- ============================================================
CREATE TABLE IF NOT EXISTS hebrew_variants (
    id SERIAL PRIMARY KEY,
    canonical TEXT NOT NULL,       -- canonical form (e.g. "חיזבאללה")
    variant TEXT NOT NULL,         -- ASR variant (e.g. "חיזבולה")
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(canonical, variant)
);

CREATE INDEX IF NOT EXISTS idx_query_aliases_source ON query_aliases(source);
CREATE INDEX IF NOT EXISTS idx_hebrew_variants_canonical ON hebrew_variants(canonical);

-- ============================================================
-- Seed data: query_aliases
-- ============================================================
INSERT INTO query_aliases (source, target, category) VALUES
    -- Politics / Diplomacy
    ('two-state solution', 'פתרון שתי מדינות', 'politics'),
    ('two state solution', 'פתרון שתי מדינות', 'politics'),
    ('abraham accords', 'הסכמי אברהם', 'politics'),
    ('oslo accords', 'הסכמי אוסלו', 'politics'),
    ('trump', 'טראמפ', 'politics'),
    ('netanyahu', 'נתניהו', 'politics'),
    ('bibi', 'ביבי', 'politics'),
    ('knesset', 'כנסת', 'politics'),
    ('likud', 'ליכוד', 'politics'),
    ('coalition', 'קואליציה', 'politics'),
    ('opposition', 'אופוזיציה', 'politics'),
    ('sovereignty', 'ריבונות', 'politics'),
    ('annexation', 'סיפוח', 'politics'),
    ('settlements', 'התנחלויות', 'politics'),
    ('settlers', 'מתנחלים', 'politics'),
    ('tariffs', 'מכסים', 'politics'),
    ('tariff', 'מכס', 'politics'),
    ('customs', 'מכס', 'politics'),
    ('trade war', 'מלחמת סחר', 'politics'),
    ('sanctions', 'סנקציות', 'politics'),
    ('supreme court', 'בית המשפט העליון', 'politics'),
    ('judicial reform', 'רפורמה משפטית', 'politics'),
    ('democracy', 'דמוקרטיה', 'politics'),
    ('protest', 'מחאה', 'politics'),
    ('protests', 'הפגנות', 'politics'),
    -- Military / War
    ('iron dome', 'כיפת ברזל', 'military'),
    ('david''s sling', 'קלע דוד', 'military'),
    ('arrow', 'חץ', 'military'),
    ('idf', 'צה"ל', 'military'),
    ('air force', 'חיל האוויר', 'military'),
    ('navy', 'חיל הים', 'military'),
    ('hezbollah', 'חיזבאללה', 'military'),
    ('hamas', 'חמאס', 'military'),
    ('iran', 'איראן', 'military'),
    ('iranian', 'איראני', 'military'),
    ('missile', 'טיל', 'military'),
    ('missiles', 'טילים', 'military'),
    ('rocket', 'רקטה', 'military'),
    ('rockets', 'רקטות', 'military'),
    ('ceasefire', 'הפסקת אש', 'military'),
    ('hostages', 'חטופים', 'military'),
    ('hostage deal', 'עסקת חטופים', 'military'),
    ('strait of hormuz', 'מצר הורמוז', 'military'),
    ('hormuz', 'הורמוז', 'military'),
    ('ballistic', 'בליסטי', 'military'),
    ('drone', 'כטבם', 'military'),
    ('drones', 'מלט', 'military'),
    ('tunnel', 'מנהרה', 'military'),
    ('tunnels', 'מנהרות', 'military'),
    ('reserve', 'מילואים', 'military'),
    ('reservists', 'מילואימניקים', 'military'),
    -- Economy
    ('gdp', 'תוצר מקומי גולמי', 'economy'),
    ('inflation', 'אינפלציה', 'economy'),
    ('cost of living', 'יוקר המחיה', 'economy'),
    ('budget', 'תקציב', 'economy'),
    ('defense budget', 'תקציב הביטחון', 'economy'),
    ('oil prices', 'מחירי נפט', 'economy'),
    ('oil', 'נפט', 'economy'),
    ('brent', 'ברנט', 'economy'),
    ('high-tech', 'הייטק', 'economy'),
    ('high tech', 'הייטק', 'economy'),
    ('startup', 'סטארטאפ', 'economy'),
    ('housing', 'דיור', 'economy'),
    ('real estate', 'נדלן', 'economy'),
    ('salary', 'משכורת', 'economy'),
    ('strike', 'שביתה', 'economy'),
    ('tax', 'מס', 'economy'),
    ('taxes', 'מיסים', 'economy'),
    -- Media / Brands
    ('el al', 'אל על', 'media'),
    ('netflix', 'נטפליקס', 'media'),
    ('champions league', 'ליגת האלופות', 'media'),
    ('barcelona', 'ברצלונה', 'media'),
    ('premier league', 'פרמייר ליג', 'media'),
    ('world cup', 'גביע העולם', 'media'),
    -- Social
    ('haredi', 'חרדים', 'social'),
    ('haredim', 'חרדים', 'social'),
    ('ultra-orthodox', 'חרדים', 'social'),
    ('conscription', 'גיוס', 'social'),
    ('draft', 'גיוס', 'social'),
    ('education', 'חינוך', 'social'),
    ('bagrut', 'בגרויות', 'social'),
    ('teachers', 'מורים', 'social'),
    ('shelter', 'מקלט', 'social'),
    ('shelters', 'מקלטים', 'social'),
    ('disabled', 'נכים', 'social'),
    ('accessibility', 'נגישות', 'social'),
    ('refugees', 'פליטים', 'social'),
    -- Geography
    ('egypt', 'מצרים', 'geography'),
    ('egyptian', 'מצרי', 'geography'),
    ('sinai', 'סיני', 'geography'),
    ('lebanon', 'לבנון', 'geography'),
    ('syria', 'סוריה', 'geography'),
    ('qatar', 'קטר', 'geography'),
    ('saudi', 'סעודיה', 'geography'),
    ('saudi arabia', 'ערב הסעודית', 'geography'),
    ('gaza', 'עזה', 'geography'),
    ('west bank', 'יהודה ושומרון', 'geography'),
    ('jerusalem', 'ירושלים', 'geography'),
    ('tel aviv', 'תל אביב', 'geography'),
    ('flights', 'טיסות', 'geography'),
    ('flight', 'טיסה', 'geography'),
    ('airport', 'שדה תעופה', 'geography'),
    ('ben gurion', 'בן גוריון', 'geography'),
    ('united nations', 'האומות המאוחדות', 'geography'),
    ('un', 'האום', 'geography')
ON CONFLICT (source) DO UPDATE SET target = EXCLUDED.target, category = EXCLUDED.category;

-- ============================================================
-- Seed data: hebrew_variants
-- ============================================================
INSERT INTO hebrew_variants (canonical, variant) VALUES
    ('חיזבאללה', 'חיזבולה'),
    ('חיזבאללה', 'חזבאללה'),
    ('חיזבאללה', 'חזבולה'),
    ('נתניהו', 'ביבי'),
    ('נתניהו', 'נתניאהו'),
    ('אל על', 'אלעל'),
    ('הייטק', 'היי טק'),
    ('הייטק', 'הי-טק'),
    ('טראמפ', 'טרמפ'),
    ('טראמפ', 'טרמאפ'),
    ('שבסת', 'שבאס')
ON CONFLICT (canonical, variant) DO NOTHING;
