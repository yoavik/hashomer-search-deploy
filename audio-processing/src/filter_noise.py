#!/usr/bin/env python3
"""
Noise Filter for HaShomer Semantic Search
==========================================
Combines text-based ad detection + audio-based noise maps to flag
sentences and segments as noise in the DB. Flagged items are excluded
from search results via is_noise column.

Runs on already-indexed data — no re-index needed.

Usage:
    # Flag noise for a station/date
    python filter_noise.py --station kan-bet --date 2026-03-19

    # Batch all stations/dates
    python filter_noise.py --batch

    # Dry run (show what would be flagged)
    python filter_noise.py --station kan-bet --date 2026-03-19 --dry-run

    # Reset (unflag all noise for a station/date)
    python filter_noise.py --station kan-bet --date 2026-03-19 --reset

    # Show stats
    python filter_noise.py --stats

    # Use remote Supabase DB
    python filter_noise.py --station kan-bet --date 2026-03-19 --remote
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# Config
# ============================================================

def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

load_env()

LOCAL_DB_URL = "postgresql://postgres:postgres@localhost:54322/semantic_search"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_DB_HOST = os.environ.get("SUPABASE_DB_HOST", "")
SUPABASE_DB_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD", "")
DIARIZED_DIR = Path(__file__).parent / "diarized_transcripts"
NOISE_MAP_DIR = Path(__file__).parent / "noise_maps"

STATIONS = [
    "kan-bet", "glz", "galei-israel", "103fm",
    "tv/n12", "tv/knesset", "tv/kan11", "tv/reshet13", "tv/ch14",
]


def get_db(remote=False):
    import psycopg2
    if remote:
        if SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD:
            url = f"postgresql://postgres.{SUPABASE_DB_HOST}:{SUPABASE_DB_PASSWORD}@db.{SUPABASE_DB_HOST}.supabase.co:5432/postgres"
            return psycopg2.connect(url)
        # Fallback: try SUPABASE_DB_URL env var directly
        db_url = os.environ.get("SUPABASE_DB_URL")
        if db_url:
            return psycopg2.connect(db_url)
        print("Error: Set SUPABASE_DB_HOST + SUPABASE_DB_PASSWORD, or SUPABASE_DB_URL for --remote")
        sys.exit(1)
    return psycopg2.connect(LOCAL_DB_URL)


# ============================================================
# Text-based ad detection (reuse logic from index_transcripts.py)
# ============================================================

_AD_START_CUES = [
    "הפסקה קצרה", "הפסקת פרסומ", "לטובת הפרסומ",
    "נחזור אחרי", "ממשיכים אחרי", "נשמע אחרי",
    "נחזור מיד", "עוד רגע נחזור", "אחרי ההפסקה",
    "הולכים להפסקה", "נעצור לרגע",
]

_AD_END_CUES = [
    "ברוכים הבאים", "חזרנו", "אנחנו חוזרים",
    "בחזרה", "שוב איתכם", "חוזרים ל",
    "בואו נמשיך", "אז חזרנו",
]

_AD_CONTENT_PATTERNS = [
    "סופר פארם", "ביוטי דייס", "ביוטי דייז",
    "שופרסל", "רמי לוי", "יינות ביתן", "אושר עד",
    "בסניפי", "פריט שני ב",
    "אחוז הנחה", "% הנחה", "70% הנח", "50% הנח",
    "לשבוע אחד בלבד", "למשך שבוע", "עד גמר המלאי",
    "לפרטים נוספים", "לפרטים התקשרו",
    "התקשרו עכשיו", "היכנסו ל",
    "בכפוף לתקנון", "ט.ל.ח", "טלח",
    "1-800-", "1800", "*6", "*9",
    "באתר שלנו", "הזמינו עכשיו",
    "ביטוח", "פוליסה", "הלוואה", "אשראי",
    "מגוון מוצרי", "מוצרי איפור", "מוצרי טיפוח",
]

_JINGLE_PHRASES = [
    "כאן חדשות", "כאן ב׳", "כאן ב'", "רדיו כאן",
    "גלי ישראל", "גלי צה\"ל", "גלצ",
    "103fm", "103 fm", "103 אפ אם",
]


def _is_ad_sentence(text):
    text_lower = text.lower()
    matches = sum(1 for p in _AD_CONTENT_PATTERNS if p in text_lower)
    return matches >= 2


def detect_commercial_indices(sentences):
    """Detect commercial content. Returns dict of {sentence_index: noise_type}."""
    noise = {}

    # Strategy 1: Cue-based span detection
    in_ad = False
    ad_start_idx = None

    for i, sent in enumerate(sentences):
        text = sent["text"].lower()

        if not in_ad:
            for cue in _AD_START_CUES:
                if cue in text:
                    in_ad = True
                    ad_start_idx = i
                    break

        if in_ad:
            for cue in _AD_END_CUES:
                if cue in text and i > ad_start_idx:
                    for j in range(ad_start_idx, i + 1):
                        noise[j] = "commercial"
                    in_ad = False
                    break

            if in_ad and ad_start_idx is not None and i - ad_start_idx > 60:
                for j in range(ad_start_idx, i):
                    noise[j] = "commercial"
                in_ad = False

    # Strategy 2: Inline ad content
    for i, sent in enumerate(sentences):
        if i not in noise and _is_ad_sentence(sent["text"]):
            noise[i] = "commercial"

    return noise


def detect_jingle_indices(sentences):
    """Detect station jingles/IDs. Returns dict of {sentence_index: noise_type}."""
    noise = {}
    for i, sent in enumerate(sentences):
        text = sent["text"].lower()
        duration = sent["end_time"] - sent["start_time"]
        word_count = len(text.split())
        if duration < 15 and word_count < 20:
            for phrase in _JINGLE_PHRASES:
                if phrase in text:
                    noise[i] = "jingle"
                    break
    return noise


# ============================================================
# Audio-based noise detection (overlap with noise maps)
# ============================================================

def load_noise_spans(station, filename):
    """Load noise spans from audio_segmenter noise map.
    Returns list of (start, end, label) or None."""
    base = filename.replace("_diarized.json", "_noisemap.json").replace("_transcript.json", "_noisemap.json").replace(".mp3", "_noisemap.json")
    path = NOISE_MAP_DIR / station / base
    if not path.exists():
        return None
    try:
        nm = json.loads(path.read_text())
        return [(s["start"], s["end"], s["label"]) for s in nm.get("noise_spans", [])]
    except Exception:
        return None


def detect_audio_noise_indices(sentences, noise_spans):
    """Match sentence timestamps against audio noise spans.
    A sentence is flagged if >50% of its duration overlaps a noise span.
    Returns dict of {sentence_index: noise_type}."""
    if not noise_spans:
        return {}

    noise = {}
    for i, sent in enumerate(sentences):
        s_start = sent["start_time"]
        s_end = sent["end_time"]
        s_dur = s_end - s_start
        if s_dur <= 0:
            continue

        total_overlap = 0
        best_label = "noise"

        for ns_start, ns_end, ns_label in noise_spans:
            overlap_start = max(s_start, ns_start)
            overlap_end = min(s_end, ns_end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > 0:
                total_overlap += overlap
                if overlap > s_dur * 0.3:  # take label from biggest overlapping span
                    best_label = ns_label

        if total_overlap > s_dur * 0.5:
            noise[i] = best_label

    return noise


# ============================================================
# Combined detection
# ============================================================

def detect_all_noise(sentences, station, filename):
    """Run all detectors. Returns dict of {sentence_index: noise_type}.
    Priority: commercial > jingle > audio noise."""
    # Text-based
    commercial = detect_commercial_indices(sentences)
    jingle = detect_jingle_indices(sentences)

    # Audio-based
    noise_spans = load_noise_spans(station, filename)
    audio = detect_audio_noise_indices(sentences, noise_spans) if noise_spans else {}

    # Merge with priority
    merged = {}
    for i in set(list(commercial.keys()) + list(jingle.keys()) + list(audio.keys())):
        if i in commercial:
            merged[i] = "commercial"
        elif i in jingle:
            merged[i] = "jingle"
        elif i in audio:
            merged[i] = audio[i]

    return merged


# ============================================================
# DB operations
# ============================================================

def get_media_for_station_date(conn, station, date_str):
    """Get all media records for a station on a date.
    Returns list of (media_id, s3_transcript_key, s3_audio_key)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, s3_transcript_key, s3_audio_key
        FROM media
        WHERE station = %s
          AND segment_time::date = %s::date
        ORDER BY segment_time
    """, (station, date_str))
    return cur.fetchall()


def get_sentences_for_media(conn, media_id):
    """Get all sentences for a media record.
    Returns list of dicts with id, sentence_index, start_time, end_time, text."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, sentence_index, start_time, end_time, text
        FROM sentences
        WHERE media_id = %s
        ORDER BY sentence_index
    """, (media_id,))
    rows = cur.fetchall()
    return [
        {"id": r[0], "sentence_index": r[1], "start_time": r[2],
         "end_time": r[3], "text": r[4]}
        for r in rows
    ]


def update_noise_flags(conn, media_id, noise_map, dry_run=False):
    """Update is_noise and noise_type for sentences and propagate to segments.
    noise_map: {sentence_index: noise_type}
    Returns (flagged_sentences, flagged_segments)."""
    cur = conn.cursor()

    if dry_run:
        return len(noise_map), 0

    # Update sentences
    # First reset all for this media
    cur.execute("""
        UPDATE sentences SET is_noise = FALSE, noise_type = NULL
        WHERE media_id = %s
    """, (media_id,))

    # Flag noisy ones
    for sent_idx, noise_type in noise_map.items():
        cur.execute("""
            UPDATE sentences SET is_noise = TRUE, noise_type = %s
            WHERE media_id = %s AND sentence_index = %s
        """, (noise_type, media_id, sent_idx))

    # Propagate to segments: a segment is noise if ALL its sentences are noise
    cur.execute("""
        UPDATE segments seg SET
            is_noise = sub.all_noise,
            noise_type = CASE WHEN sub.all_noise THEN sub.dominant_type ELSE NULL END
        FROM (
            SELECT s.segment_id,
                   BOOL_AND(COALESCE(s.is_noise, FALSE)) AS all_noise,
                   MODE() WITHIN GROUP (ORDER BY s.noise_type) AS dominant_type
            FROM sentences s
            WHERE s.media_id = %s AND s.segment_id IS NOT NULL
            GROUP BY s.segment_id
        ) sub
        WHERE seg.id = sub.segment_id
    """, (media_id,))

    # Count flagged segments
    cur.execute("""
        SELECT COUNT(*) FROM segments
        WHERE media_id = %s AND is_noise = TRUE
    """, (media_id,))
    flagged_segments = cur.fetchone()[0]

    conn.commit()
    return len(noise_map), flagged_segments


def reset_noise_flags(conn, media_id):
    """Reset all noise flags for a media record."""
    cur = conn.cursor()
    cur.execute("UPDATE sentences SET is_noise = FALSE, noise_type = NULL WHERE media_id = %s", (media_id,))
    cur.execute("UPDATE segments SET is_noise = FALSE, noise_type = NULL WHERE media_id = %s", (media_id,))
    conn.commit()


# ============================================================
# Processing
# ============================================================

def process_media(conn, media_id, transcript_key, station, dry_run=False):
    """Process a single media record: detect noise and update DB."""
    sentences = get_sentences_for_media(conn, media_id)
    if not sentences:
        return 0, 0

    # Extract filename from transcript key (e.g., "kan-bet/2026-03-19_08-00_diarized.json")
    filename = transcript_key.split("/")[-1] if transcript_key else ""

    noise_map = detect_all_noise(sentences, station, filename)

    if not noise_map:
        return 0, 0

    flagged_sent, flagged_seg = update_noise_flags(conn, media_id, noise_map, dry_run=dry_run)
    return flagged_sent, flagged_seg


def process_station_date(conn, station, date_str, dry_run=False, reset=False):
    """Process all media for a station on a date."""
    media_records = get_media_for_station_date(conn, station, date_str)
    if not media_records:
        return 0, 0, 0

    total_sent = 0
    total_seg = 0
    total_media = 0

    for media_id, transcript_key, audio_key in media_records:
        if reset:
            reset_noise_flags(conn, media_id)
            total_media += 1
            continue

        flagged_sent, flagged_seg = process_media(
            conn, media_id, transcript_key, station, dry_run=dry_run)

        if flagged_sent > 0:
            action = "[DRY RUN] Would flag" if dry_run else "Flagged"
            print(f"    {transcript_key}: {action} {flagged_sent} sentences, {flagged_seg} segments")
            total_sent += flagged_sent
            total_seg += flagged_seg
            total_media += 1

    if reset:
        print(f"  Reset noise flags for {total_media} media records")

    return total_media, total_sent, total_seg


# ============================================================
# Stats
# ============================================================

def show_stats(conn):
    """Show noise flagging statistics."""
    cur = conn.cursor()

    cur.execute("""
        SELECT m.station, COUNT(*) AS total,
               SUM(CASE WHEN s.is_noise THEN 1 ELSE 0 END) AS noise,
               COUNT(DISTINCT s.noise_type) FILTER (WHERE s.is_noise) AS types
        FROM sentences s JOIN media m ON s.media_id = m.id
        GROUP BY m.station ORDER BY m.station
    """)
    rows = cur.fetchall()
    if not rows:
        print("No indexed data found.")
        return

    print("\nSentence noise stats:")
    print(f"  {'Station':<20} {'Total':>8} {'Noise':>8} {'%':>6}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*6}")
    for station, total, noise, types in rows:
        pct = 100 * noise / total if total > 0 else 0
        print(f"  {station:<20} {total:>8} {noise:>8} {pct:>5.1f}%")

    cur.execute("""
        SELECT m.station, COUNT(*) AS total,
               SUM(CASE WHEN seg.is_noise THEN 1 ELSE 0 END) AS noise
        FROM segments seg JOIN media m ON seg.media_id = m.id
        GROUP BY m.station ORDER BY m.station
    """)
    rows = cur.fetchall()
    print("\nSegment noise stats:")
    print(f"  {'Station':<20} {'Total':>8} {'Noise':>8} {'%':>6}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*6}")
    for station, total, noise in rows:
        pct = 100 * noise / total if total > 0 else 0
        print(f"  {station:<20} {total:>8} {noise:>8} {pct:>5.1f}%")

    # Breakdown by noise type
    cur.execute("""
        SELECT noise_type, COUNT(*)
        FROM sentences WHERE is_noise = TRUE
        GROUP BY noise_type ORDER BY COUNT(*) DESC
    """)
    rows = cur.fetchall()
    if rows:
        print("\nNoise type breakdown (sentences):")
        for noise_type, count in rows:
            print(f"  {noise_type or 'unknown':<20} {count:>8}")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Flag noise (ads, music, jingles, silence) in indexed data"
    )
    parser.add_argument("--station", help="Station to process")
    parser.add_argument("--date", help="Date to process (YYYY-MM-DD)")
    parser.add_argument("--from", dest="from_date", help="Start date")
    parser.add_argument("--to", dest="to_date", help="End date")
    parser.add_argument("--batch", action="store_true", help="Process all stations")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be flagged")
    parser.add_argument("--reset", action="store_true", help="Reset noise flags")
    parser.add_argument("--stats", action="store_true", help="Show noise statistics")
    parser.add_argument("--remote", action="store_true",
                        help="Use remote Supabase DB instead of local PostgreSQL")
    args = parser.parse_args()

    if not any([args.station, args.batch, args.stats]):
        parser.print_help()
        sys.exit(1)

    conn = get_db(remote=args.remote)
    if args.remote:
        print("Connected to remote Supabase DB")

    if args.stats:
        show_stats(conn)
        conn.close()
        return

    # Determine stations
    if args.batch:
        stations = STATIONS
    elif args.station:
        stations = [args.station]
    else:
        parser.print_help()
        sys.exit(1)

    # Determine dates
    dates = []
    if args.date:
        dates = [args.date]
    elif args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = (datetime.strptime(args.to_date, "%Y-%m-%d").date()
               if args.to_date else datetime.now().date())
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
    else:
        # Auto-detect dates from DB
        cur = conn.cursor()
        station_list = ",".join(f"'{s}'" for s in stations)
        cur.execute(f"""
            SELECT DISTINCT segment_time::date
            FROM media WHERE station IN ({station_list})
            ORDER BY segment_time::date
        """)
        dates = [row[0].strftime("%Y-%m-%d") for row in cur.fetchall()]

    if not dates:
        print("No dates to process.")
        conn.close()
        return

    grand_media = 0
    grand_sent = 0
    grand_seg = 0

    for station in stations:
        for date_str in dates:
            label = f"{station}/{date_str}"
            if args.reset:
                print(f"Resetting {label}...")
            else:
                print(f"Processing {label}...")

            n_media, n_sent, n_seg = process_station_date(
                conn, station, date_str,
                dry_run=args.dry_run, reset=args.reset)
            grand_media += n_media
            grand_sent += n_sent
            grand_seg += n_seg

    action = "Would flag" if args.dry_run else ("Reset" if args.reset else "Flagged")
    print(f"\nDone! {action}: {grand_sent} sentences, {grand_seg} segments "
          f"across {grand_media} media records")

    conn.close()


if __name__ == "__main__":
    main()
