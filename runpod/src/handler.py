"""RunPod serverless handler for HaShomer diarization service.

Wraps the existing diarization logic for RunPod's serverless infrastructure.
Input is passed via event["input"] with the same fields as the FastAPI DiarizeRequest.
"""

import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

import runpod

from shared.config import DIARIZATION_MODEL
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


def handler(event):
    input_data = event.get("input", {})

    # Resolve audio and transcript keys from various input formats
    audio_key = None
    transcript_key = None

    audio_url = input_data.get("audio_url")
    transcript_url = input_data.get("transcript_url")

    if audio_url and transcript_url:
        _, audio_key = parse_s3_url(audio_url)
        _, transcript_key = parse_s3_url(transcript_url)
    elif input_data.get("Records") or input_data.get("key"):
        event_info = parse_s3_event(input_data)
        if not event_info:
            return {"error": "Could not parse S3 event"}
        key = event_info["key"]
        if key.endswith("_transcript.json"):
            transcript_key = key
            stem = key.replace("_transcript.json", "")
            audio_key = stem + (".mp4" if "tv/" in key else ".mp3")
        else:
            return {"error": f"Expected transcript key, got: {key}"}
    else:
        return {"error": "Provide audio_url+transcript_url or S3 event (Records/key)"}

    if not audio_key or not transcript_key:
        return {"error": "Could not determine audio and transcript keys"}

    s3 = get_s3_client()

    # Download transcript
    transcript = read_json_from_s3(s3, transcript_key)
    if "segments" not in transcript:
        return {"error": f"Transcript missing segments: {transcript_key}"}

    # Download audio to temp file
    suffix = Path(audio_key).suffix
    audio_path = download_s3_to_tempfile(s3, audio_key, suffix=suffix)

    # Resolve timezone
    tz = ISRAEL_TZ
    tz_name = input_data.get("timezone")
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, ValueError):
            return {"error": f"Invalid timezone: {tz_name}"}

    try:
        pipeline, device = load_pyannote_pipeline()

        turns = run_diarization(
            pipeline, audio_path,
            min_speakers=input_data.get("min_speakers"),
            max_speakers=input_data.get("max_speakers"),
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

        return {
            "diarized_key": diarized_key,
            "speaker_count": len({t.speaker for t in turns}),
            "turn_count": len(turns),
            "segment_count": len(diarized.get("segments", [])),
            "recording_start": recording_start,
        }
    finally:
        audio_path.unlink(missing_ok=True)


runpod.serverless.start({"handler": handler})
