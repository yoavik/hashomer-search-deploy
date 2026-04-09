"""RunPod serverless handler for audio segmentation + noise filtering.

Wraps audio_segmenter.py for RunPod's serverless GPU infrastructure.
Input via event["input"]:
  - s3_key: S3 key of the audio file (e.g. "kan-bet/2026-03-19_08-00.mp3")
  - station: station name (optional, inferred from key)
  - use_ina: force inaSpeechSegmenter (optional, default: auto)
"""

import json
import os
import tempfile
from pathlib import Path

import boto3
import runpod

AWS_KEY_ID = os.environ.get("AWS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET")
S3_BUCKET = os.environ.get("S3_BUCKET", "yoav-radio-recordings")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_SECRET,
        region_name=S3_REGION,
    )


def handler(event):
    input_data = event.get("input", {})
    s3_key = input_data.get("s3_key")
    if not s3_key:
        return {"error": "Missing s3_key"}

    station = input_data.get("station")
    if not station:
        parts = s3_key.split("/")
        if parts[0] == "tv" and len(parts) >= 3:
            station = f"{parts[0]}/{parts[1]}"
        elif len(parts) >= 2:
            station = parts[0]
        else:
            return {"error": "Could not infer station from s3_key, provide station param"}

    use_ina = input_data.get("use_ina")

    s3 = get_s3_client()

    # Download audio to temp file
    suffix = Path(s3_key).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    s3.download_file(S3_BUCKET, s3_key, tmp.name)
    audio_path = Path(tmp.name)

    try:
        from audio_segmenter import segment_audio

        segments, summary = segment_audio(str(audio_path), use_ina=use_ina)

        # Build noise map
        noise_spans = [s for s in segments if s["label"] != "speech"]
        speech_spans = [s for s in segments if s["label"] == "speech"]

        noise_map = {
            "noise_spans": noise_spans,
            "speech_spans": speech_spans,
            "all_segments": segments,
            "summary": summary,
        }

        # Upload noise map to S3
        filename = Path(s3_key).stem
        noisemap_key = f"noise_maps/{station}/{filename}_noisemap.json"
        body = json.dumps(noise_map, ensure_ascii=False, indent=2).encode("utf-8")
        s3.put_object(Bucket=S3_BUCKET, Key=noisemap_key, Body=body, ContentType="application/json")

        return {
            "noisemap_key": noisemap_key,
            "summary": summary,
        }
    finally:
        audio_path.unlink(missing_ok=True)


runpod.serverless.start({"handler": handler})
