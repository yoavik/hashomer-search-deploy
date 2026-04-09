#!/usr/bin/env python3
"""
Audio Segmentation Pipeline
============================
Classifies broadcast audio into speech / music / noise / silence segments.
Runs AFTER recording, BEFORE diarization+transcription.

Pipeline:
  Stage 1: Silero VAD  → speech vs non-speech timestamps
  Stage 2: Music/noise classifier:
           - inaSpeechSegmenter (if available, best for broadcast)
           - OR spectral heuristics fallback (torch only, no TF needed)
  Stage 3: Merge into a segment map with labels
  Stage 4: Output noise_map JSON consumed by index_transcripts.py

Usage:
    # Segment a single MP3
    python audio_segmenter.py --file recent_recordings/kan-bet/2026-03-19_08-00.mp3

    # Batch: process all recordings for a station/date
    python audio_segmenter.py --batch [--station kan-bet] [--date 2026-03-19]

    # Force spectral fallback even if INA is available
    python audio_segmenter.py --file recording.mp3 --no-ina

    # Show summary of existing noise maps
    python audio_segmenter.py --stats

Output:
    noise_maps/{station}/{date_time}_noisemap.json

Requirements:
    Core: pip install torchaudio pydub
    Better accuracy: pip install inaSpeechSegmenter tensorflow
    System: ffmpeg
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch

# ============================================================
# Configuration
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

NOISE_MAP_DIR = Path(__file__).parent / "noise_maps"
RECENT_DIR = Path(__file__).parent / "recent_recordings"

# Silero VAD parameters
VAD_THRESHOLD = 0.5
VAD_MIN_SPEECH_MS = 250
VAD_MIN_SILENCE_MS = 100
VAD_WINDOW_SIZE_SAMPLES = 512  # Silero expects exactly 512 samples at 16kHz
VAD_SAMPLE_RATE = 16000

# Energy-based classification for non-speech regions
SPECTRAL_WINDOW_SEC = 2.0   # classify in 2-second windows
SILENCE_ENERGY_THRESHOLD = -35  # dB, below = silence
# For broadcast: non-speech + energy = music (songs, jingles, ad audio)

# Segment merging
MERGE_GAP = 1.0
MIN_SEGMENT_DURATION = 0.5

# Check INA availability
_HAS_INA = None
def has_ina():
    global _HAS_INA
    if _HAS_INA is None:
        try:
            from inaSpeechSegmenter import Segmenter
            _HAS_INA = True
        except ImportError:
            _HAS_INA = False
    return _HAS_INA


# ============================================================
# Audio loading
# ============================================================

def load_audio_torch(filepath, target_sr=VAD_SAMPLE_RATE):
    """Load audio as mono torch tensor at target sample rate.
    Uses pydub (ffmpeg) for decoding, then converts to torch tensor."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(filepath))
    audio = audio.set_channels(1).set_frame_rate(target_sr).set_sample_width(2)

    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples = samples / 32768.0  # int16 -> float32 [-1, 1]
    waveform = torch.from_numpy(samples)

    return waveform, target_sr


# ============================================================
# Stage 1: Silero VAD
# ============================================================

_silero_model = None

def get_silero_model():
    global _silero_model
    if _silero_model is None:
        _silero_model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
    return _silero_model


def run_silero_vad(filepath):
    """Run Silero VAD. Returns (speech_segments, audio_duration_sec)."""
    model = get_silero_model()
    waveform, sr = load_audio_torch(filepath, VAD_SAMPLE_RATE)

    speech_timestamps = _get_speech_timestamps(
        waveform, model, sr,
        threshold=VAD_THRESHOLD,
        min_speech_duration_ms=VAD_MIN_SPEECH_MS,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS,
        window_size_samples=VAD_WINDOW_SIZE_SAMPLES,
    )

    segments = [
        {"start": round(ts["start"] / sr, 3), "end": round(ts["end"] / sr, 3), "label": "speech"}
        for ts in speech_timestamps
    ]
    return segments, len(waveform) / sr


def _get_speech_timestamps(
    waveform, model, sr,
    threshold=0.5,
    min_speech_duration_ms=250,
    min_silence_duration_ms=100,
    window_size_samples=512,
):
    """Extract speech timestamps using Silero VAD."""
    model.reset_states()
    min_speech_samples = int(min_speech_duration_ms * sr / 1000)
    min_silence_samples = int(min_silence_duration_ms * sr / 1000)

    speech_probs = []
    for i in range(0, len(waveform), window_size_samples):
        chunk = waveform[i:i + window_size_samples]
        if len(chunk) < window_size_samples:
            chunk = torch.nn.functional.pad(chunk, (0, window_size_samples - len(chunk)))
        prob = model(chunk.unsqueeze(0), sr).item()
        speech_probs.append(prob)

    triggered = False
    speeches = []
    current_start = 0
    temp_end = 0

    for i, prob in enumerate(speech_probs):
        sample_pos = i * window_size_samples

        if prob >= threshold and not triggered:
            triggered = True
            current_start = sample_pos

        if prob < threshold and triggered:
            temp_end = sample_pos
            if i + 1 < len(speech_probs):
                silence_duration = 0
                for j in range(i + 1, len(speech_probs)):
                    if speech_probs[j] >= threshold:
                        break
                    silence_duration += window_size_samples
                    if silence_duration >= min_silence_samples:
                        break
                if silence_duration >= min_silence_samples:
                    if temp_end - current_start >= min_speech_samples:
                        speeches.append({"start": current_start, "end": temp_end})
                    triggered = False
            else:
                if temp_end - current_start >= min_speech_samples:
                    speeches.append({"start": current_start, "end": temp_end})
                triggered = False

    if triggered:
        end = len(waveform)
        if end - current_start >= min_speech_samples:
            speeches.append({"start": current_start, "end": end})

    return speeches


# ============================================================
# Stage 2a: inaSpeechSegmenter (preferred, needs TF)
# ============================================================

_ina_segmenter = None

def get_ina_segmenter():
    global _ina_segmenter
    if _ina_segmenter is None:
        from inaSpeechSegmenter import Segmenter
        _ina_segmenter = Segmenter()
    return _ina_segmenter


def run_ina_segmenter(filepath):
    """Run inaSpeechSegmenter. Returns [{start, end, label}].
    Labels: speech, music, silence."""
    seg = get_ina_segmenter()
    result = seg(str(filepath))

    segments = []
    for label, start, end in result:
        if label in ("male", "female", "speech"):
            mapped = "speech"
        elif label == "music":
            mapped = "music"
        elif label == "noEnergy":
            mapped = "silence"
        else:
            mapped = "noise"
        segments.append({"start": round(start, 3), "end": round(end, 3), "label": mapped})

    return segments


# ============================================================
# Stage 2b: Spectral heuristics fallback (torch only)
# ============================================================

def run_spectral_classifier(filepath):
    """Energy-based classifier for non-speech regions.

    For broadcast audio, the key insight is:
    - Silero VAD handles speech detection (the hard part)
    - Non-speech regions with energy = music/jingles/ad-audio
    - Non-speech regions without energy = silence

    This is a simple but effective heuristic for radio/TV.
    For better music classification, install inaSpeechSegmenter.

    Returns [{start, end, label}]."""
    waveform, sr = load_audio_torch(filepath, VAD_SAMPLE_RATE)
    window_samples = int(SPECTRAL_WINDOW_SEC * sr)

    segments = []
    for i in range(0, len(waveform), window_samples):
        chunk = waveform[i:i + window_samples]
        start_sec = i / sr
        end_sec = min((i + window_samples) / sr, len(waveform) / sr)

        if len(chunk) < sr * 0.5:  # skip < 0.5s remainder
            break

        rms = torch.sqrt(torch.mean(chunk ** 2)).item()
        rms_db = 20 * np.log10(rms + 1e-10)

        if rms_db < SILENCE_ENERGY_THRESHOLD:
            label = "silence"
        else:
            # Has energy — could be speech, music, or noise.
            # We label it "music" here; the merge stage will override
            # to "speech" if VAD detected speech in this region.
            label = "music"

        segments.append({"start": round(start_sec, 3), "end": round(end_sec, 3), "label": label})

    return _merge_adjacent(segments)


# ============================================================
# Stage 3: Merge VAD + classifier
# ============================================================

def merge_segmentations(vad_segments, classifier_segments, audio_duration, use_ina=False):
    """Merge Silero VAD and classifier (INA or spectral) results.

    - VAD is authoritative for speech boundaries (more precise)
    - Classifier provides music/noise/silence labels for non-speech
    - When INA: music overrides VAD speech (INA is confident about music)
    - When spectral: VAD speech overrides everything (spectral can't tell speech from music)
    """
    resolution = 0.1
    n_bins = int(audio_duration / resolution) + 1

    vad_mask = np.zeros(n_bins, dtype=bool)
    cls_labels = np.full(n_bins, "", dtype=object)

    for seg in vad_segments:
        start_bin = int(seg["start"] / resolution)
        end_bin = min(int(seg["end"] / resolution), n_bins - 1)
        vad_mask[start_bin:end_bin + 1] = True

    for seg in classifier_segments:
        start_bin = int(seg["start"] / resolution)
        end_bin = min(int(seg["end"] / resolution), n_bins - 1)
        cls_labels[start_bin:end_bin + 1] = seg["label"]

    final_labels = np.full(n_bins, "silence", dtype=object)
    for i in range(n_bins):
        cls = cls_labels[i]
        has_speech = vad_mask[i]

        if has_speech:
            if cls == "music" and use_ina:
                # INA explicitly says music — trust it even if VAD hears speech
                # (singing has speech-like patterns)
                final_labels[i] = "music"
            else:
                # VAD says speech — override classifier
                final_labels[i] = "speech"
        else:
            # No speech from VAD
            if cls == "music":
                final_labels[i] = "music"
            elif cls == "noise":
                final_labels[i] = "noise"
            elif cls == "silence" or cls == "":
                final_labels[i] = "silence"
            else:
                final_labels[i] = "silence"

    segments = _bins_to_segments(final_labels, resolution)
    return segments


def _bins_to_segments(labels, resolution):
    if len(labels) == 0:
        return []

    segments = []
    current_label = labels[0]
    current_start = 0

    for i in range(1, len(labels)):
        if labels[i] != current_label:
            duration = (i - current_start) * resolution
            if duration >= MIN_SEGMENT_DURATION:
                segments.append({
                    "start": round(current_start * resolution, 3),
                    "end": round(i * resolution, 3),
                    "label": current_label,
                    "duration": round(duration, 3),
                })
            current_start = i
            current_label = labels[i]

    duration = (len(labels) - current_start) * resolution
    if duration >= MIN_SEGMENT_DURATION:
        segments.append({
            "start": round(current_start * resolution, 3),
            "end": round(len(labels) * resolution, 3),
            "label": current_label,
            "duration": round(duration, 3),
        })

    return _merge_adjacent(segments)


def _merge_adjacent(segments, gap=MERGE_GAP):
    if not segments:
        return []
    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg["label"] == prev["label"] and seg["start"] - prev["end"] <= gap:
            prev["end"] = seg["end"]
            prev["duration"] = round(prev["end"] - prev["start"], 3)
        else:
            merged.append(seg.copy())
    return merged


# ============================================================
# Full pipeline
# ============================================================

def segment_audio(filepath, use_ina=None):
    """Run the full segmentation pipeline.
    use_ina: True=force INA, False=force spectral, None=auto-detect.
    Returns (segments, summary_dict)."""
    filepath = Path(filepath)

    if use_ina is None:
        use_ina = has_ina()

    print(f"  Stage 1: Silero VAD...")
    vad_segments, audio_duration = run_silero_vad(filepath)
    speech_sec = sum(s["end"] - s["start"] for s in vad_segments)
    print(f"    {len(vad_segments)} speech regions, "
          f"{speech_sec:.1f}s speech / {audio_duration:.1f}s total")

    if use_ina:
        print(f"  Stage 2: inaSpeechSegmenter...")
        classifier_segments = run_ina_segmenter(filepath)
    else:
        print(f"  Stage 2: Spectral classifier (torch)...")
        classifier_segments = run_spectral_classifier(filepath)

    cls_counts = defaultdict(int)
    for s in classifier_segments:
        cls_counts[s["label"]] += 1
    print(f"    {len(classifier_segments)} segments: " +
          ", ".join(f"{v} {k}" for k, v in cls_counts.items()))

    print(f"  Stage 3: Merging...")
    merged = merge_segmentations(vad_segments, classifier_segments, audio_duration, use_ina=use_ina)

    summary = {
        "audio_duration": round(audio_duration, 2),
        "total_segments": len(merged),
        "classifier": "ina" if use_ina else "spectral",
    }
    for label in ("speech", "music", "silence", "noise"):
        label_segs = [s for s in merged if s["label"] == label]
        dur = sum(s["duration"] for s in label_segs)
        summary[f"{label}_segments"] = len(label_segs)
        summary[f"{label}_seconds"] = round(dur, 2)
        summary[f"{label}_pct"] = round(100 * dur / audio_duration, 1) if audio_duration > 0 else 0

    return merged, summary


# ============================================================
# Noise map I/O
# ============================================================

def noise_map_path(station, filename):
    base = filename.replace(".mp3", "_noisemap.json")
    return NOISE_MAP_DIR / station / base


def save_noise_map(station, filename, segments, summary):
    path = noise_map_path(station, filename)
    path.parent.mkdir(parents=True, exist_ok=True)

    noise_spans = [s for s in segments if s["label"] != "speech"]
    speech_spans = [s for s in segments if s["label"] == "speech"]

    noise_map = {
        "noise_spans": noise_spans,
        "speech_spans": speech_spans,
        "all_segments": segments,
        "summary": summary,
        "detected_at": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(noise_map, ensure_ascii=False, indent=2))
    return path


def load_noise_map(station, filename):
    path = noise_map_path(station, filename)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_noise_spans(station, filename):
    """Load noise span timestamps for filtering.
    Returns list of (start, end, label) tuples, or None."""
    nm = load_noise_map(station, filename)
    if nm is None:
        return None
    return [(s["start"], s["end"], s["label"]) for s in nm.get("noise_spans", [])]


# ============================================================
# Batch processing
# ============================================================

def process_file(filepath, station, use_ina=None):
    filepath = Path(filepath)
    print(f"\n{station}/{filepath.name}")
    segments, summary = segment_audio(filepath, use_ina=use_ina)
    out_path = save_noise_map(station, filepath.name, segments, summary)
    print(f"  Result: {summary['speech_pct']}% speech, "
          f"{summary['music_pct']}% music, "
          f"{summary['silence_pct']}% silence, "
          f"{summary['noise_pct']}% noise")
    print(f"  Saved: {out_path}")
    return summary


def batch_process(stations, dates, overwrite=False, use_ina=None):
    total_files = 0
    total_speech = 0
    total_duration = 0

    for station in stations:
        station_dir = RECENT_DIR / station
        if not station_dir.exists():
            continue
        for mp3_path in sorted(station_dir.glob("*.mp3")):
            if dates and mp3_path.name[:10] not in dates:
                continue
            if not overwrite and noise_map_path(station, mp3_path.name).exists():
                continue
            try:
                summary = process_file(mp3_path, station, use_ina=use_ina)
                total_files += 1
                total_speech += summary["speech_seconds"]
                total_duration += summary["audio_duration"]
            except Exception as e:
                print(f"  Error: {e}")

    if total_files > 0:
        print(f"\nDone! {total_files} files processed")
        print(f"Total: {total_speech:.0f}s speech / {total_duration:.0f}s audio "
              f"({100 * total_speech / total_duration:.1f}% speech)")
        print(f"Noise maps saved to {NOISE_MAP_DIR}/")
    else:
        print("No new files to process.")


# ============================================================
# Stats
# ============================================================

def show_stats():
    if not NOISE_MAP_DIR.exists():
        print("No noise maps found.")
        return

    total_files = 0
    total_duration = 0
    label_seconds = defaultdict(float)

    for station_dir in sorted(NOISE_MAP_DIR.iterdir()):
        if not station_dir.is_dir():
            continue
        station_files = 0
        station_dur = 0
        station_labels = defaultdict(float)

        for nm_path in sorted(station_dir.glob("*_noisemap.json")):
            try:
                nm = json.loads(nm_path.read_text())
                summary = nm.get("summary", {})
                dur = summary.get("audio_duration", 0)
                station_files += 1
                station_dur += dur
                for label in ("speech", "music", "silence", "noise"):
                    sec = summary.get(f"{label}_seconds", 0)
                    station_labels[label] += sec
                    label_seconds[label] += sec
            except Exception:
                continue

        if station_files > 0:
            print(f"\n{station_dir.name}: {station_files} files, {station_dur / 60:.0f} min")
            for label in ("speech", "music", "silence", "noise"):
                sec = station_labels[label]
                pct = 100 * sec / station_dur if station_dur > 0 else 0
                print(f"  {label}: {sec:.0f}s ({pct:.1f}%)")

        total_files += station_files
        total_duration += station_dur

    if total_files > 0:
        print(f"\nTotal: {total_files} files, {total_duration / 60:.0f} min")
        for label in ("speech", "music", "silence", "noise"):
            sec = label_seconds[label]
            pct = 100 * sec / total_duration if total_duration > 0 else 0
            print(f"  {label}: {sec:.0f}s ({pct:.1f}%)")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Audio segmentation: classify broadcast audio into speech/music/noise/silence"
    )
    parser.add_argument("--file", type=str, help="Segment a single audio file")
    parser.add_argument("--batch", action="store_true", help="Batch process recordings")
    parser.add_argument("--stats", action="store_true", help="Show noise map statistics")
    parser.add_argument("--station", help="Filter by station")
    parser.add_argument("--date", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--from", dest="from_date", help="Start date")
    parser.add_argument("--to", dest="to_date", help="End date")
    parser.add_argument("--overwrite", action="store_true", help="Re-process existing noise maps")
    parser.add_argument("--no-ina", action="store_true",
                        help="Force spectral classifier even if inaSpeechSegmenter is available")
    args = parser.parse_args()

    if not any([args.file, args.batch, args.stats]):
        parser.print_help()
        sys.exit(1)

    use_ina = None  # auto-detect
    if args.no_ina:
        use_ina = False

    if args.stats:
        show_stats()
        return

    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: {filepath} not found")
            sys.exit(1)
        station = filepath.parent.name
        process_file(filepath, station, use_ina=use_ina)
        return

    if args.batch:
        if args.station:
            stations = [args.station]
        else:
            stations = sorted(
                d.name for d in RECENT_DIR.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )

        dates = None
        if args.date:
            dates = {args.date}
        elif args.from_date:
            start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
            end = (datetime.strptime(args.to_date, "%Y-%m-%d").date()
                   if args.to_date else datetime.now().date())
            dates = set()
            current = start
            while current <= end:
                dates.add(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)

        print(f"Processing {len(stations)} station(s)...")
        batch_process(stations, dates, overwrite=args.overwrite, use_ina=use_ina)


if __name__ == "__main__":
    main()
