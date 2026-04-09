import copy
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from zoneinfo import ZoneInfo

from shared.config import HF_TOKEN, DIARIZATION_MODEL, SAMPLE_RATE

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Matches patterns like 2026-03-18_21-30 or 2026-03-18_07-00
_DATETIME_RE = re.compile(r'(\d{4}-\d{2}-\d{2})[_T](\d{2})-(\d{2})')


@dataclass
class DiarizationTurn:
    start: float
    end: float
    speaker: str


_pipeline = None
_device = None


def load_pyannote_pipeline(model_name: str = DIARIZATION_MODEL, device: str | None = None):
    global _pipeline, _device
    if _pipeline is not None:
        return _pipeline, _device

    import huggingface_hub
    import torch
    from pyannote.audio import Pipeline

    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is required to load the pyannote diarization model.")

    huggingface_hub.login(token=HF_TOKEN)
    _pipeline = Pipeline.from_pretrained(model_name)

    _device = device
    if _device is None:
        _device = "cuda" if torch.cuda.is_available() else "cpu"

    _pipeline.to(torch.device(_device))
    return _pipeline, _device


def run_diarization(
    pipeline,
    audio_path: Path,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> list[DiarizationTurn]:
    import torchaudio

    kwargs = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    waveform, sr = torchaudio.load(str(audio_path))
    if sr != SAMPLE_RATE:
        waveform = torchaudio.functional.resample(waveform, sr, SAMPLE_RATE)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    audio_input = {"waveform": waveform, "sample_rate": SAMPLE_RATE}
    annotation = pipeline(audio_input, **kwargs)
    annotation = getattr(annotation, "speaker_diarization", annotation)

    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append(DiarizationTurn(
            start=round(float(turn.start), 3),
            end=round(float(turn.end), 3),
            speaker=str(speaker),
        ))
    return turns


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def assign_speaker_to_segment(
    segment: dict, turns: Iterable[DiarizationTurn]
) -> tuple[str, float]:
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    duration = max(end - start, 0.0)

    overlaps = {}
    best_turn = None
    best_overlap = 0.0

    for turn in turns:
        overlap = overlap_seconds(start, end, turn.start, turn.end)
        if overlap <= 0:
            continue
        overlaps[turn.speaker] = overlaps.get(turn.speaker, 0.0) + overlap
        if overlap > best_overlap:
            best_overlap = overlap
            best_turn = turn

    if overlaps:
        speaker, overlap = max(overlaps.items(), key=lambda item: item[1])
        confidence = overlap / duration if duration > 0 else 1.0
        return speaker, round(confidence, 3)

    if best_turn is not None:
        return best_turn.speaker, 0.0

    return "UNKNOWN", 0.0


def parse_recording_datetime(
    key: str, transcript: dict, tz: ZoneInfo | None = None,
) -> datetime | None:
    """Extract recording start datetime from S3 key or transcript metadata.

    Checks (in order):
    1. S3 key filename pattern: 2026-03-18_21-30
    2. Transcript 'timestamp' field: 2026-03-18T21:30:00
    3. Transcript 'filename' field: 2026-03-18_21-30.mp3

    Returns a timezone-aware datetime (defaults to Israel time).
    """
    tz = tz or ISRAEL_TZ

    # Try S3 key
    m = _DATETIME_RE.search(key)
    if m:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=tz)

    # Try transcript 'timestamp' field
    ts = transcript.get("timestamp", "")
    if ts:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz)
                return dt
            except ValueError:
                continue

    # Try transcript 'filename' field
    fname = transcript.get("filename", "")
    if fname:
        m = _DATETIME_RE.search(fname)
        if m:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=tz)

    return None


def _offset_to_datetime(offset_seconds: float, base: datetime) -> str:
    """Convert a relative offset (seconds from recording start) to ISO datetime string."""
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def make_timestamps_absolute(
    diarized: dict, base_dt: datetime,
) -> dict:
    """Add start_datetime / end_datetime to all segments and speaker_segments.

    Keeps original start/end (relative seconds) for audio seeking.
    Also sets the top-level 'timestamp' to a timezone-aware ISO string.
    """
    # Update top-level timestamp to be timezone-aware
    diarized["timestamp"] = base_dt.isoformat()
    diarized["timezone"] = str(base_dt.tzinfo)

    for seg in diarized.get("segments", []):
        seg["start_datetime"] = _offset_to_datetime(seg.get("start", 0), base_dt)
        seg["end_datetime"] = _offset_to_datetime(seg.get("end", 0), base_dt)

    for seg in diarized.get("speaker_segments", []):
        seg["start_datetime"] = _offset_to_datetime(seg.get("start", 0), base_dt)
        seg["end_datetime"] = _offset_to_datetime(seg.get("end", 0), base_dt)

    return diarized


def build_diarized_transcript(
    transcript: dict,
    turns: list[DiarizationTurn],
    transcript_key: str,
    audio_key: str,
    model_name: str,
    device: str,
    tz: ZoneInfo | None = None,
) -> dict:
    diarized = copy.deepcopy(transcript)
    diarized_segments = []

    for segment in diarized.get("segments", []):
        updated = dict(segment)
        speaker, confidence = assign_speaker_to_segment(segment, turns)
        updated["speaker"] = speaker
        updated["speaker_confidence"] = confidence
        diarized_segments.append(updated)

    diarized["segments"] = diarized_segments
    diarized["speaker_segments"] = [
        {"start": t.start, "end": t.end, "speaker": t.speaker}
        for t in turns
    ]
    diarized["diarization"] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "device": device,
        "s3_transcript_key": transcript_key,
        "s3_audio_key": audio_key,
        "speaker_count": len({turn.speaker for turn in turns}),
        "turn_count": len(turns),
    }

    # Make timestamps datetime-aware if we can determine the recording time
    base_dt = parse_recording_datetime(transcript_key, transcript, tz=tz)
    if base_dt is None:
        base_dt = parse_recording_datetime(audio_key, transcript, tz=tz)
    if base_dt is not None:
        make_timestamps_absolute(diarized, base_dt)
        diarized["diarization"]["recording_start"] = base_dt.isoformat()

    return diarized
