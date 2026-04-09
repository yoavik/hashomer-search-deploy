import re

# Repeated character pattern: same char 3+ times
_REPEATED_CHAR_RE = re.compile(r'(.)\1{2,}')
# Repeated word pattern: same word 3+ times
_REPEATED_WORD_RE = re.compile(r'\b(\S+)(?:\s+\1){2,}\b')
# Sentence boundary
_SENTENCE_RE = re.compile(r'(?<=[.?!।])\s+')


def clean_asr_text(text: str) -> str:
    text = _REPEATED_CHAR_RE.sub(r'\1\1', text)
    text = _REPEATED_WORD_RE.sub(r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def is_junk_sentence(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 3:
        return True
    if len(set(cleaned.replace(' ', ''))) <= 1:
        return True
    return False


def unite_speaker_segments(segments: list[dict]) -> list[dict]:
    """Merge consecutive segments from the same speaker into single turns."""
    if not segments:
        return []

    turns = []
    current = {
        "text": segments[0].get("text", "").strip(),
        "start": segments[0].get("start", 0),
        "end": segments[0].get("end", 0),
        "speaker": segments[0].get("speaker", "UNKNOWN"),
        "speaker_confidence": segments[0].get("speaker_confidence", 0),
    }

    for seg in segments[1:]:
        speaker = seg.get("speaker", "UNKNOWN")
        text = seg.get("text", "").strip()
        if not text:
            continue

        if speaker == current["speaker"]:
            current["text"] += " " + text
            current["end"] = seg.get("end", current["end"])
            current["speaker_confidence"] = min(
                current["speaker_confidence"],
                seg.get("speaker_confidence", 0),
            )
        else:
            if current["text"]:
                turns.append(current)
            current = {
                "text": text,
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "speaker": speaker,
                "speaker_confidence": seg.get("speaker_confidence", 0),
            }

    if current["text"]:
        turns.append(current)
    return turns


def split_into_sentences(segments: list[dict]) -> list[dict]:
    """Split transcript segments into individual sentences with timestamps."""
    sentences = []
    for seg in segments:
        raw = clean_asr_text(seg.get("text", ""))
        if not raw or is_junk_sentence(raw):
            continue
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", seg_start)
        seg_dur = seg_end - seg_start
        speaker = seg.get("speaker")

        parts = _SENTENCE_RE.split(raw)
        parts = [p.strip() for p in parts if p.strip() and not is_junk_sentence(p.strip())]

        if len(parts) <= 1:
            s = {"text": raw, "start": seg_start, "end": seg_end}
            if speaker:
                s["speaker"] = speaker
            sentences.append(s)
            continue

        total_chars = sum(len(p) for p in parts)
        cursor = seg_start
        for p in parts:
            frac = len(p) / total_chars if total_chars else 0
            dur = seg_dur * frac
            s = {
                "text": p,
                "start": round(cursor, 2),
                "end": round(cursor + dur, 2),
            }
            if speaker:
                s["speaker"] = speaker
            sentences.append(s)
            cursor += dur

    return sentences


# ============================================================
# Commercial break detection
# ============================================================

AD_START_CUES = [
    "הפסקה קצרה", "הפסקת פרסומ", "לטובת הפרסומ", "לפרסומות",
    "נחזור אחרי", "ממשיכים אחרי", "נשמע אחרי",
    "נחזור מיד", "עוד רגע נחזור", "אחרי ההפסקה",
    "הולכים להפסקה", "נעצור לרגע", "ועכשיו לפרסומות",
    "פרסומות ונחזור", "עוד מעט נחזור",
]

AD_END_CUES = [
    "ברוכים הבאים", "חזרנו", "אנחנו חוזרים",
    "בחזרה", "שוב איתכם", "חוזרים ל",
    "בואו נמשיך", "אז חזרנו", "וחזרנו",
    "ממשיכים", "נמשיך",
]

AD_CONTENT_PATTERNS = [
    "סופר פארם", "ביוטי דייס", "ביוטי דייז", "שופרסל", "רמי לוי",
    "יינות ביתן", "אושר עד", "בסניפי", "פריט שני ב",
    "אחוז הנחה", "% הנחה", "70% הנח", "50% הנח",
    "לשבוע אחד בלבד", "למשך שבוע", "עד גמר המלאי",
    "לפרטים נוספים", "לפרטים התקשרו", "התקשרו עכשיו",
    "היכנסו ל", "בכפוף לתקנון", "ט.ל.ח", "טלח",
    "1-800-", "1800", "*6", "*9", "באתר שלנו", "הזמינו עכשיו",
    "ביטוח", "פוליסה", "הלוואה",
    "מגוון מוצרי", "מוצרי איפור", "מוצרי טיפוח",
    # Price-based ad patterns
    "במחירים הכי נמוכים", "אפשר יותר נמוך", "שקלים בלבד",
    "במבצע מיוחד", "שבמבצע", "רק ב-", "במחיר מיוחד",
    "אמריקן קומפורט", "גולדן בריץ", "גולדן ברידג",
    "קונים ב", "מיטה ומזרן", "תיסוי מפנקת",
    "9,990 שקלים", "6,990 שקלים", "שקלים וגם",
    "הכל כדי ש", "מסכים קונים", "תנורים במחירים", "מקררים במחירים",
    # Specific store/brand slogans (only when combined with brand)
    "נמוך מדי במחסן",
]

# Station jingles and show intros — not ads but not searchable content
STATION_JINGLES = [
    "גלי ישראל החדשות",
    "רדיו גלי ישראל",
    "גלי צהל",
    "כאן רשת ב",
    "כאן מורשת",
    "103 אפ אם",
    "רדיו 103",
]

_PRICE_RE = re.compile(r'\d[,.]?\d{3}\s*שקל')


def _is_ad_sentence(text: str) -> bool:
    text_lower = text.lower()
    matches = sum(1 for p in AD_CONTENT_PATTERNS if p in text_lower)
    if matches >= 2:
        return True
    # Single pattern + price mention = ad
    if matches >= 1 and _PRICE_RE.search(text):
        return True
    # Repeated "price" phrases = ad
    if text.count("מחירים הכי נמוכים") >= 2:
        return True
    if text.count("שקלים") >= 2 and _PRICE_RE.search(text):
        return True
    # Slogan + brand combo (e.g., "המחיר נמוך מדי" + "מחסני חשמל")
    if "המחיר נמוך מדי" in text and ("מחסני חשמל" in text or "מחסן חשמל" in text):
        return True
    return False


def _is_jingle(text: str) -> bool:
    """Check if text is a station jingle/ID (short, matches known patterns)."""
    words = text.split()
    if len(words) > 8:
        return False
    for jingle in STATION_JINGLES:
        if jingle in text:
            return True
    return False


# ============================================================
# Query operator parsing ("", AND, OR, NOT)
# ============================================================

_QUOTED_PHRASE_RE = re.compile(r'"([^"]+)"')


def parse_query_operators(query: str) -> dict:
    """Parse search operators from a query string.

    Supports:
      - "phrase" — exact phrase match (kept as unit, no morphological expansion)
      - AND / OR  — boolean operators (Hebrew: או)
      - NOT / -   — exclusion (Hebrew: לא)

    Returns dict with:
      - phrases: list of quoted phrase strings
      - terms: list of regular (non-quoted) terms to expand normally
      - excluded: list of terms prefixed with NOT or -
      - operator: default boolean operator between terms ("and" or "or")
      - raw_for_embedding: clean text for embedding (no operators, phrases kept as units)
      - has_operators: bool — whether any operators were detected
    """
    phrases = _QUOTED_PHRASE_RE.findall(query)
    # Remove quoted phrases from query for further parsing
    remaining = _QUOTED_PHRASE_RE.sub('', query).strip()

    # Normalize operator tokens
    remaining = re.sub(r'\bAND\b', ' ', remaining, flags=re.IGNORECASE)
    has_or = bool(re.search(r'\bOR\b|\bאו\b', remaining, flags=re.IGNORECASE))
    remaining = re.sub(r'\bOR\b|\bאו\b', ' ', remaining, flags=re.IGNORECASE)

    # Extract NOT / - exclusions
    excluded = []
    tokens = remaining.split()
    clean_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.upper() in ("NOT", "לא") and i + 1 < len(tokens):
            excluded.append(tokens[i + 1])
            i += 2
            continue
        if token.startswith("-") and len(token) > 1:
            excluded.append(token[1:])
            i += 1
            continue
        clean_tokens.append(token)
        i += 1

    # Build clean text for embeddings: phrases as units + regular terms
    embedding_parts = list(phrases) + clean_tokens
    raw_for_embedding = " ".join(embedding_parts)

    has_operators = bool(phrases) or bool(excluded) or has_or

    return {
        "phrases": phrases,
        "terms": clean_tokens,
        "excluded": excluded,
        "operator": "or" if has_or else "and",
        "raw_for_embedding": raw_for_embedding,
        "has_operators": has_operators,
    }


def build_tsquery_from_parsed(parsed: dict, morph_variants: set[str] | None = None) -> str:
    """Convert parsed query operators into a PostgreSQL tsquery string.

    Uses 'simple' config so no stemming — we handle morphology ourselves.

    Returns a tsquery expression string suitable for to_tsquery('simple', ...).
    """
    parts = []

    # Quoted phrases → use <-> (phrase operator: words must be adjacent)
    for phrase in parsed["phrases"]:
        words = phrase.split()
        if words:
            phrase_tsq = " <-> ".join(f"'{w}'" for w in words)
            parts.append(f"({phrase_tsq})")

    # Regular terms → each becomes a token, optionally OR'd with morphological variants
    for term in parsed["terms"]:
        if morph_variants:
            # Find variants that are derived from this term
            term_variants = [v for v in morph_variants if _term_matches_variant(term, v)]
            if term_variants:
                all_forms = [f"'{term}'"] + [f"'{v}'" for v in term_variants[:5]]
                parts.append(f"({' | '.join(all_forms)})")
            else:
                parts.append(f"'{term}'")
        else:
            parts.append(f"'{term}'")

    if not parts:
        return ""

    # Join parts with the default operator
    joiner = " | " if parsed["operator"] == "or" else " & "
    result = joiner.join(parts)

    # Append NOT exclusions
    for exc in parsed["excluded"]:
        result = f"({result}) & !'{exc}'"

    return result


def _term_matches_variant(term: str, variant: str) -> bool:
    """Check if a variant is likely derived from the given term."""
    if len(term) < 2 or len(variant) < 2:
        return False
    # Share at least 2-char root
    t = term
    # Strip single Hebrew prefix from both
    if t and t[0] in "בלהמושכ":
        t_stripped = t[1:]
    else:
        t_stripped = t
    if variant and variant[0] in "בלהמושכ":
        v_stripped = variant[1:]
    else:
        v_stripped = variant
    # Check if they share a common root (first 2+ chars)
    min_len = min(len(t_stripped), len(v_stripped))
    if min_len < 2:
        return False
    return t_stripped[:2] == v_stripped[:2]


def detect_commercial_spans(sentences: list[dict]) -> set[int]:
    """Detect commercial content. Returns set of sentence indices to filter out."""
    ad_indices = set()

    # Strategy 1: Cue-based span detection
    in_ad = False
    ad_start_idx = None

    for i, sent in enumerate(sentences):
        text = sent.get("text", "").lower()

        if not in_ad:
            for cue in AD_START_CUES:
                if cue in text:
                    in_ad = True
                    ad_start_idx = i
                    break

        if in_ad:
            for cue in AD_END_CUES:
                if cue in text and i > ad_start_idx:
                    for j in range(ad_start_idx, i + 1):
                        ad_indices.add(j)
                    in_ad = False
                    break

            if in_ad and ad_start_idx is not None and i - ad_start_idx > 60:
                for j in range(ad_start_idx, i):
                    ad_indices.add(j)
                in_ad = False

    # Strategy 2: Inline ad content detection
    for i, sent in enumerate(sentences):
        if i not in ad_indices and _is_ad_sentence(sent.get("text", "")):
            ad_indices.add(i)

    # Strategy 3: Station jingles (short, not searchable content)
    for i, sent in enumerate(sentences):
        if i not in ad_indices and _is_jingle(sent.get("text", "")):
            ad_indices.add(i)

    return ad_indices
