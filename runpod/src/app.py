import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.config import S3_BUCKET, DIARIZATION_MODEL
from shared.s3 import (
    get_s3_client,
    download_s3_to_tempfile,
    read_json_from_s3,
    upload_json_to_s3,
    parse_s3_event,
    parse_s3_url,
)
from zoneinfo import ZoneInfo

from diarization import (
    ISRAEL_TZ,
    load_pyannote_pipeline,
    run_diarization,
    build_diarized_transcript,
)

app = FastAPI(title="HaShomer Diarization Service")


class DiarizeRequest(BaseModel):
    audio_url: str | None = None
    transcript_url: str | None = None
    station: str | None = None
    timezone: str | None = None  # e.g. "Asia/Jerusalem" (default), "UTC"
    min_speakers: int | None = None
    max_speakers: int | None = None
    # S3 event fields
    Records: list | None = None
    key: str | None = None
    bucket: str | None = None


class DiarizeResponse(BaseModel):
    diarized_key: str
    speaker_count: int
    turn_count: int
    segment_count: int
    recording_start: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "diarize"}


@app.on_event("startup")
def startup():
    print("Loading pyannote pipeline...")
    load_pyannote_pipeline()
    print("Pipeline loaded.")


@app.post("/diarize", response_model=DiarizeResponse)
def diarize(request: DiarizeRequest):
    # Resolve audio and transcript keys from various input formats
    audio_key = None
    transcript_key = None

    if request.audio_url and request.transcript_url:
        _, audio_key = parse_s3_url(request.audio_url)
        _, transcript_key = parse_s3_url(request.transcript_url)
    elif request.Records or request.key:
        event_info = parse_s3_event(request.model_dump())
        if not event_info:
            raise HTTPException(400, "Could not parse S3 event")
        key = event_info["key"]
        if key.endswith("_transcript.json"):
            transcript_key = key
            stem = key.replace("_transcript.json", "")
            audio_key = stem + (".mp4" if "tv/" in key else ".mp3")
        else:
            raise HTTPException(400, f"Expected transcript key, got: {key}")
    else:
        raise HTTPException(400, "Provide audio_url+transcript_url or S3 event")

    if not audio_key or not transcript_key:
        raise HTTPException(400, "Could not determine audio and transcript keys")

    s3 = get_s3_client()

    # Download files
    transcript = read_json_from_s3(s3, transcript_key)
    if "segments" not in transcript:
        raise HTTPException(400, f"Transcript missing segments: {transcript_key}")

    suffix = Path(audio_key).suffix
    audio_path = download_s3_to_tempfile(s3, audio_key, suffix=suffix)

    # Resolve timezone
    tz = ISRAEL_TZ
    if request.timezone:
        try:
            tz = ZoneInfo(request.timezone)
        except (KeyError, ValueError):
            raise HTTPException(400, f"Invalid timezone: {request.timezone}")

    try:
        pipeline, device = load_pyannote_pipeline()

        turns = run_diarization(
            pipeline, audio_path,
            min_speakers=request.min_speakers,
            max_speakers=request.max_speakers,
        )

        diarized = build_diarized_transcript(
            transcript=transcript,
            turns=turns,
            transcript_key=transcript_key,
            audio_key=audio_key,
            model_name=DIARIZATION_MODEL,
            device=device,
            tz=tz,
        )

        # Write diarized JSON back to S3
        diarized_key = transcript_key.replace("_transcript.json", "_diarized.json")
        upload_json_to_s3(s3, diarized_key, diarized)

        recording_start = diarized.get("diarization", {}).get("recording_start")

        return DiarizeResponse(
            diarized_key=diarized_key,
            speaker_count=len({t.speaker for t in turns}),
            turn_count=len(turns),
            segment_count=len(diarized.get("segments", [])),
            recording_start=recording_start,
        )
    finally:
        audio_path.unlink(missing_ok=True)
