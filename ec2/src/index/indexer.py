import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shared.config import (
    EMBED_SERVICE_URL,
    SEGMENT_SIMILARITY_THRESHOLD,
    SEGMENT_MAX_DURATION_SECONDS,
    SEGMENT_MIN_SENTENCES,
    SEGMENT_WINDOW_SIZE,
    TOPIC_CLUSTER_THRESHOLD,
)
from shared.supabase_client import get_supabase
from shared.text_processing import (
    unite_speaker_segments,
    split_into_sentences,
    detect_commercial_spans,
)

_HEBREW_FILLER = {
    "כן", "לא", "נכון", "תראה", "אוקיי", "אוקי", "בסדר", "אה",
    "אהה", "אמ", "יאללה", "בדיוק", "וואלה", "טוב", "אז", "ככה",
    "נו", "הא", "מה", "אני",
}


# ============================================================
# Embed service client (HE only)
# ============================================================

def call_embed_texts(texts: list[str]) -> list:
    """Call the embed service to get NeoDictaBERT embeddings."""
    response = httpx.post(
        f"{EMBED_SERVICE_URL}/embed-texts",
        json={"texts": texts},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["embeddings_he"]


# ============================================================
# Noise detection
# ============================================================

def is_noise_segment(text: str) -> tuple[bool, str | None]:
    """Check if a segment is likely noise (music, jingle, ad, gibberish).

    Returns (is_noise, noise_type) where noise_type is one of:
    'short', 'repetitive', 'gibberish', None
    """
    words = text.split()
    word_count = len(words)

    # Too short to be meaningful
    if word_count < 5:
        return True, "short"

    # Highly repetitive text (jingles, music lyrics with repeated lines)
    if word_count >= 8:
        unique_ratio = len(set(words)) / word_count
        if unique_ratio < 0.3:
            return True, "repetitive"

    # 4-gram repetition (station IDs, ad jingles)
    if word_count >= 12:
        ngrams = [" ".join(words[i:i+4]) for i in range(word_count - 3)]
        max_repeat = max(Counter(ngrams).values())
        if max_repeat >= 3:
            return True, "repetitive"

    # Very short sentences with no Hebrew content (gibberish/music)
    hebrew_chars = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha > 0 and hebrew_chars / total_alpha < 0.3 and word_count < 15:
        return True, "gibberish"

    # Commercial/ad detection: price mentions + ad phrases
    import re
    ad_phrases = ["במחירים הכי נמוכים", "אפשר יותר נמוך", "שקלים בלבד",
                  "במבצע מיוחד", "שבמבצע", "רק ב-", "במחיר מיוחד",
                  "אמריקן קומפורט", "גולדן בריץ", "גולדן ברידג",
                  "מיטה ומזרן", "תיסוי מפנקת", "הכל כדי ש",
                  "מסכים קונים", "תנורים במחירים", "מקררים במחירים",
                  "נמוך מדי במחסן"]
    ad_count = sum(1 for p in ad_phrases if p in text)
    has_price = bool(re.search(r'\d[,.]?\d{3}\s*שקל', text))
    if ad_count >= 2 or (ad_count >= 1 and has_price):
        return True, "commercial"
    if text.count("מחירים הכי נמוכים") >= 2:
        return True, "commercial"
    # Slogan + brand combo
    if "המחיר נמוך מדי" in text and ("מחסני חשמל" in text or "מחסן חשמל" in text):
        return True, "commercial"

    # Station jingles
    jingles = ["גלי ישראל החדשות", "רדיו גלי ישראל", "גלי צהל",
               "כאן רשת ב", "כאן מורשת", "103 אפ אם", "רדיו 103"]
    if word_count <= 8 and any(j in text for j in jingles):
        return True, "jingle"

    return False, None


def is_noise_sentence(text: str) -> tuple[bool, str | None]:
    """Check if a sentence is likely noise."""
    words = text.split()
    if len(words) < 2:
        return True, "short"
    if len(words) >= 6:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.3:
            return True, "repetitive"
    return False, None


# ============================================================
# Vector math helpers
# ============================================================

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vec_mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    if n == 0:
        return []
    dim = len(vectors[0])
    return [sum(v[k] for v in vectors) / n for k in range(dim)]


def _vec_weighted_mean(vectors: list[list[float]], weights: list[float]) -> list[float]:
    total_w = sum(weights)
    if total_w == 0 or not vectors:
        return _vec_mean(vectors)
    dim = len(vectors[0])
    return [sum(v[k] * w for v, w in zip(vectors, weights)) / total_w
            for k in range(dim)]


def _sentence_weight(text: str) -> float:
    words = text.split()
    n = len(words)
    if n == 0:
        return 0.0
    filler_count = sum(1 for w in words if w in _HEBREW_FILLER)
    filler_ratio = filler_count / n
    length_weight = min(n / 8.0, 1.0)
    return length_weight * (1.0 - 0.7 * filler_ratio)


# ============================================================
# Sentence → Segment grouping (rolling centroid)
# ============================================================

def group_sentences_into_segments(
    sentences: list[dict],
    he_embeddings: list[list[float]],
    similarity_threshold: float = SEGMENT_SIMILARITY_THRESHOLD,
    max_duration: float = SEGMENT_MAX_DURATION_SECONDS,
    min_sentences: int = SEGMENT_MIN_SENTENCES,
    window_size: int = SEGMENT_WINDOW_SIZE,
) -> list[dict]:
    if not sentences:
        return []

    segments = []
    current = [0]

    for i in range(1, len(sentences)):
        seg_start = sentences[current[0]]["start"]
        would_be_duration = sentences[i]["end"] - seg_start

        window = current[-window_size:]
        centroid = _vec_mean([he_embeddings[j] for j in window])
        sim = _cosine_similarity(centroid, he_embeddings[i])

        topic_changed = sim < similarity_threshold and len(current) >= min_sentences
        too_long = would_be_duration >= max_duration

        if topic_changed or too_long:
            all_speakers = list(dict.fromkeys(
                sentences[j].get("speaker", "UNKNOWN") for j in current
            ))
            segments.append({
                "start_time": round(sentences[current[0]]["start"], 2),
                "end_time": round(sentences[current[-1]]["end"], 2),
                "text": " ".join(sentences[j]["text"] for j in current),
                "speakers": all_speakers,
                "sentence_indices": list(current),
            })
            current = [i]
        else:
            current.append(i)

    if current:
        all_speakers = list(dict.fromkeys(
            sentences[j].get("speaker", "UNKNOWN") for j in current
        ))
        segments.append({
            "start_time": round(sentences[current[0]]["start"], 2),
            "end_time": round(sentences[current[-1]]["end"], 2),
            "text": " ".join(sentences[j]["text"] for j in current),
            "speakers": all_speakers,
            "sentence_indices": list(current),
        })

    return segments


# ============================================================
# Supabase storage
# ============================================================

def is_already_indexed(transcript_key: str) -> bool:
    supabase = get_supabase()
    result = (
        supabase.table("media")
        .select("id")
        .eq("s3_transcript_key", transcript_key)
        .execute()
    )
    return len(result.data) > 0


def store_media_sentences_segments(
    media_data: dict,
    sentences: list[dict],
    segments: list[dict],
) -> tuple[str, int, int]:
    supabase = get_supabase()

    media_result = supabase.table("media").insert(media_data).execute()
    media_id = media_result.data[0]["id"]

    sentence_records = []
    for i, sent in enumerate(sentences):
        if sent.get("embedding_he") is None:
            continue
        noise, noise_type = is_noise_sentence(sent["text"])
        sentence_records.append({
            "media_id": media_id,
            "sentence_index": i,
            "start_time": sent["start"],
            "end_time": sent["end"],
            "text": sent["text"],
            "embedding_he": sent["embedding_he"],
            "speaker": sent.get("speaker"),
            "is_noise": noise,
            "noise_type": noise_type,
        })

    sentence_ids_by_index = {}
    for j in range(0, len(sentence_records), 100):
        batch = sentence_records[j:j + 100]
        result = supabase.table("sentences").insert(batch).execute()
        for row in result.data:
            sentence_ids_by_index[row["sentence_index"]] = row["id"]

    seg_count = 0
    for seg_i, seg in enumerate(segments):
        if seg.get("embedding_he") is None:
            continue
        noise, noise_type = is_noise_segment(seg["text"])
        seg_result = supabase.table("segments").insert({
            "media_id": media_id,
            "segment_index": seg_i,
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "text": seg["text"],
            "embedding_he": seg.get("embedding_he"),
            "speakers": seg["speakers"],
            "sentence_count": len(seg["sentence_indices"]),
            "is_noise": noise,
            "noise_type": noise_type,
        }).execute()
        segment_id = seg_result.data[0]["id"]
        seg["segment_id"] = segment_id
        seg_count += 1

        sent_ids = [sentence_ids_by_index[si] for si in seg["sentence_indices"]
                    if si in sentence_ids_by_index]
        if sent_ids:
            for sid in sent_ids:
                supabase.table("sentences").update(
                    {"segment_id": segment_id}
                ).eq("id", str(sid)).execute()

    return media_id, len(sentence_records), seg_count


def assign_topics(segments: list[dict], threshold: float = TOPIC_CLUSTER_THRESHOLD):
    supabase = get_supabase()

    for seg in segments:
        emb = seg.get("embedding_he")
        segment_id = seg.get("segment_id")
        if emb is None or segment_id is None:
            continue

        result = supabase.rpc("find_nearest_topic", {
            "query_embedding": emb,
            "match_threshold": threshold,
        }).execute()

        if result.data and len(result.data) > 0:
            topic = result.data[0]
            topic_id = topic["topic_id"]
            count = topic["segment_count"]

            supabase.rpc("update_topic_centroid", {
                "p_topic_id": topic_id,
                "new_embedding": emb,
                "old_count": count,
            }).execute()
        else:
            topic_result = supabase.table("topics").insert({
                "centroid": emb,
                "segment_count": 1,
            }).execute()
            topic_id = topic_result.data[0]["id"]

        supabase.table("segments").update({
            "topic_id": topic_id,
        }).eq("id", str(segment_id)).execute()


# ============================================================
# Parsing helpers
# ============================================================

def parse_segment_time(key: str, station: str) -> datetime | None:
    filename = key.replace(f"{station}/", "").replace("_diarized.json", "").replace("_transcript.json", "")
    try:
        dt = datetime.strptime(filename, "%Y-%m-%d_%H-%M")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def station_media_type(station: str) -> str:
    return "tv" if station.startswith("tv/") else "radio"


# ============================================================
# Main indexing pipeline
# ============================================================

def index_transcript(transcript: dict, transcript_key: str, station: str) -> dict:
    """Full indexing pipeline for a single diarized transcript (HE-only embeddings)."""
    if is_already_indexed(transcript_key):
        return {"status": "skipped", "reason": "already indexed"}

    if "segments" not in transcript:
        raise ValueError(f"Transcript missing segments: {transcript_key}")

    # Step 1: Unite speaker segments → split into sentences
    turns = unite_speaker_segments(transcript["segments"])
    if not turns:
        raise ValueError("No speaker turns found")

    sentences = split_into_sentences(turns)
    if not sentences:
        raise ValueError("No sentences after splitting")

    # Step 2: Filter out commercial breaks
    ad_indices = detect_commercial_spans(sentences)
    if ad_indices:
        sentences = [s for i, s in enumerate(sentences) if i not in ad_indices]
    if not sentences:
        raise ValueError("All sentences filtered as ads")

    # Step 3: Get HE embeddings from embed service
    sentence_texts = [s["text"] for s in sentences]
    he_embs = call_embed_texts(sentence_texts)

    for sent, he in zip(sentences, he_embs):
        sent["embedding_he"] = he

    # Filter sentences with failed embedding
    valid = [(s, s["embedding_he"]) for s in sentences if s.get("embedding_he") is not None]
    if not valid:
        raise ValueError("All embeddings failed")
    sentences, he_embs = zip(*valid)
    sentences = list(sentences)
    he_embs = list(he_embs)

    # Step 4: Group sentences into segments
    segments = group_sentences_into_segments(sentences, he_embs)
    if not segments:
        raise ValueError("No segments after grouping")

    # Step 5: Get direct segment embeddings
    seg_texts = [seg["text"] for seg in segments]
    he_direct = call_embed_texts(seg_texts)

    for seg, he_d in zip(segments, he_direct):
        # Use direct embedding of full segment text
        seg["embedding_he"] = he_d

    # Step 6: Parse metadata
    segment_time = parse_segment_time(transcript_key, station)
    if not segment_time:
        raise ValueError(f"Could not parse time from key: {transcript_key}")

    original_filename = transcript.get("filename", "")
    s3_audio_key = f"{station}/{original_filename}" if original_filename else ""

    media_data = {
        "station": station,
        "media_type": station_media_type(station),
        "segment_time": segment_time.isoformat(),
        "s3_audio_key": s3_audio_key,
        "s3_transcript_key": transcript_key,
    }

    # Step 7: Store in Supabase + assign topics
    media_id, n_sent, n_seg = store_media_sentences_segments(media_data, sentences, segments)
    assign_topics(segments)

    return {
        "status": "indexed",
        "media_id": str(media_id),
        "sentences_count": n_sent,
        "segments_count": n_seg,
    }
