import math

from shared.config import MODEL_EMBED
from shared.text_processing import parse_query_operators, build_tsquery_from_parsed

_model = None
_reranker = None

RERANKER_MODEL = "HeTree/HeCross"


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f"Loading embedding model: {MODEL_EMBED}")
        _model = SentenceTransformer(MODEL_EMBED, device="cpu")
        print(f"Model loaded. Dim={_model.get_sentence_embedding_dimension()}")
    return _model


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        print(f"Loading reranker: {RERANKER_MODEL}")
        _reranker = CrossEncoder(RERANKER_MODEL)
        print("Reranker loaded.")
    return _reranker


def rerank(query: str, texts: list[str]) -> list[float]:
    """Score query-document pairs using HeCross cross-encoder."""
    model = get_reranker()
    pairs = [(query, t[:512]) for t in texts]
    scores = model.predict(pairs)
    return [float(s) for s in scores]


def sanitize_embedding(emb: list[float]) -> list[float] | None:
    clean = [0.0 if (math.isnan(v) or math.isinf(v)) else v for v in emb]
    if all(v == 0.0 for v in clean):
        return None
    return clean


def generate_embeddings_he(texts: list[str], batch_size: int = 64, mode: str = "passage") -> list[list[float] | None]:
    """Generate e5-large embeddings. mode='passage' for indexing, 'query' for search."""
    model = get_model()
    prefixed = [f"{mode}: {t}" for t in texts]
    embs = model.encode(prefixed, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True)
    return [sanitize_embedding(e.tolist()) for e in embs]


def generate_embeddings(
    texts: list[str], batch_size: int = 64
) -> list[list[float] | None]:
    """Generate e5-large embeddings for passage texts."""
    return generate_embeddings_he(texts, batch_size)


# ============================================================
# Query expansion (English→Hebrew aliases, morphological)
# ============================================================

QUERY_ALIASES = {
    "two-state solution": "פתרון שתי מדינות",
    "two state solution": "פתרון שתי מדינות",
    "abraham accords": "הסכמי אברהם",
    "oslo accords": "הסכמי אוסלו",
    "trump": "טראמפ",
    "netanyahu": "נתניהו",
    "bibi": "ביבי",
    "knesset": "כנסת",
    "likud": "ליכוד",
    "coalition": "קואליציה",
    "opposition": "אופוזיציה",
    "sovereignty": "ריבונות",
    "annexation": "סיפוח",
    "settlements": "התנחלויות",
    "settlers": "מתנחלים",
    "tariffs": "מכסים",
    "sanctions": "סנקציות",
    "iron dome": "כיפת ברזל",
    "idf": 'צה"ל',
    "air force": "חיל האוויר",
    "hezbollah": "חיזבאללה",
    "hamas": "חמאס",
    "iran": "איראן",
    "missile": "טיל",
    "missiles": "טילים",
    "ceasefire": "הפסקת אש",
    "hostages": "חטופים",
    "strait of hormuz": "מצר הורמוז",
    "ballistic": "בליסטי",
    "gdp": "תוצר מקומי גולמי",
    "inflation": "אינפלציה",
    "cost of living": "יוקר המחיה",
    "budget": "תקציב",
    "oil prices": "מחירי נפט",
    "high-tech": "הייטק",
    "high tech": "הייטק",
    "startup": "סטארטאפ",
    "el al": "אל על",
    "netflix": "נטפליקס",
    "champions league": "ליגת האלופות",
    "barcelona": "ברצלונה",
    "haredi": "חרדים",
    "ultra-orthodox": "חרדים",
    "conscription": "גיוס",
    "education": "חינוך",
    "bagrut": "בגרויות",
    "egypt": "מצרים",
    "sinai": "סיני",
    "lebanon": "לבנון",
    "syria": "סוריה",
    "qatar": "קטר",
    "flights": "טיסות",
    "airport": "שדה תעופה",
}

HEBREW_VARIANTS = {
    "חיזבאללה": ["חיזבולה", "חזבאללה", "חזבולה"],
    "נתניהו": ["ביבי", "נתניאהו"],
    "אל על": ["אלעל"],
    "הייטק": ["היי טק", "הי-טק"],
    "טראמפ": ["טרמפ", "טרמאפ"],
}

# Hebrew concept synonyms — maps terms to related Hebrew phrases
# These help when the embedding model doesn't understand concept equivalence
HEBREW_CONCEPT_ALIASES = {
    "אינפלציה": "עליית מחירים",
    "יוקר המחייה": "עליית מחירים",
    "יוקר מחייה": "עליית מחירים",
    "דפלציה": "ירידת מחירים",
    "מיתון": "האטה כלכלית",
    "אבטלה": "מובטלים פיטורים",
    "משכנתא": "הלוואה דירה משכנתאות",
    "בורסה": "מניות שוק ההון",
    "פיגוע": "טרור פצועים הרוגים",
    "נדלן": "דירות מחירי דיור שכירות",
    "שביתה": "עצירת עבודה שובתים",
    "הפגנה": "מחאה מפגינים",
    "סנקציות": "עיצומים",
    "נורמליזציה": "הסכמי שלום",
    "פליטים": "מהגרים מבקשי מקלט",
    "אקלים": "התחממות גלובלית סביבה",
    "סייבר": "אבטחת מידע האקרים",
    "קורונה": "מגפה חיסונים תחלואה",
}

_HE_SUFFIXES = ["ים", "ות", "ה", "י", "ן", "ת", "ית", "יים", "יות"]
_HE_PREFIXES = ["ב", "ל", "ה", "מ", "ו", "ש", "כ"]


def _is_hebrew(word: str) -> bool:
    return any('\u0590' <= c <= '\u05FF' for c in word)


def expand_hebrew_morphology(query_text: str, skip_words: set[str] | None = None) -> set[str]:
    words = query_text.split()
    additions = set()

    for word in words:
        if skip_words and word in skip_words:
            continue
        if not _is_hebrew(word) or len(word) < 3:
            continue

        base_forms = {word}
        if len(word) >= 3 and word[0] in "בלהמושכ":
            base_forms.add(word[1:])

        stems = set()
        for base in list(base_forms):
            for suffix in sorted(_HE_SUFFIXES, key=len, reverse=True):
                if base.endswith(suffix) and len(base) - len(suffix) >= 2:
                    stems.add(base[:-len(suffix)])

        for stem in stems:
            additions.add(stem)
            for suffix in _HE_SUFFIXES:
                variant = stem + suffix
                if variant != word:
                    additions.add(variant)

        for base in base_forms:
            if base != word:
                additions.add(base)
            if base in HEBREW_VARIANTS:
                additions.update(HEBREW_VARIANTS[base])

        if word in HEBREW_VARIANTS:
            additions.update(HEBREW_VARIANTS[word])

    return additions


def expand_query(text: str) -> tuple[str, list[str]]:
    """Expand query with Hebrew aliases for English terms."""
    parsed = parse_query_operators(text)

    terms_text = " ".join(parsed["terms"])
    text_lower = terms_text.lower().strip()
    additions = []

    if text_lower in QUERY_ALIASES:
        additions.append(QUERY_ALIASES[text_lower])

    words = text_lower.split()
    for w in words:
        if w in QUERY_ALIASES:
            additions.append(QUERY_ALIASES[w])
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if bigram in QUERY_ALIASES:
            additions.append(QUERY_ALIASES[bigram])

    for w in terms_text.split():
        if w in HEBREW_VARIANTS:
            additions.extend(HEBREW_VARIANTS[w])

    # Hebrew concept aliases (e.g., אינפלציה → עליית מחירים)
    if text_lower in HEBREW_CONCEPT_ALIASES:
        additions.append(HEBREW_CONCEPT_ALIASES[text_lower])
    for w in terms_text.split():
        if w in HEBREW_CONCEPT_ALIASES:
            additions.append(HEBREW_CONCEPT_ALIASES[w])
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if bigram in HEBREW_CONCEPT_ALIASES:
            additions.append(HEBREW_CONCEPT_ALIASES[bigram])

    seen = set()
    unique = []
    for a in additions:
        if a not in seen:
            seen.add(a)
            unique.append(a)

    expanded = parsed["raw_for_embedding"]
    if unique:
        expanded = expanded + " " + " ".join(unique)
        return expanded, unique
    return expanded, []
