#!/usr/bin/env python3
"""Pipeline trigger: finds new transcripts in S3, diarizes via RunPod, indexes via local service.

Designed to run as a cron job on the EC2 instance hosting embed+index services.

Usage:
    python trigger_pipeline.py                    # process last 2 hours
    python trigger_pipeline.py --hours 6          # process last 6 hours
    python trigger_pipeline.py --dry-run          # list files without processing
    python trigger_pipeline.py --station kan-bet  # only process one station
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import requests

# Load .env if present
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

# Config
AWS_KEY_ID = os.environ.get("AWS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET")
S3_BUCKET = os.environ.get("S3_BUCKET", "yoav-radio-recordings")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")
RUNPOD_DIARIZE_ENDPOINT = os.environ.get("RUNPOD_DIARIZE_ENDPOINT")  # endpoint ID

INDEX_SERVICE_URL = os.environ.get("INDEX_SERVICE_URL", "http://localhost:8003")

RADIO_STATIONS = ["kan-bet", "glz", "galei-israel", "103fm"]
TV_STATIONS = ["tv/n12", "tv/knesset", "tv/kan11", "tv/reshet13", "tv/ch14"]
ALL_STATIONS = RADIO_STATIONS + TV_STATIONS


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_SECRET,
        region_name=S3_REGION,
    )


def list_recent_transcripts(s3, station: str, since: datetime) -> list[str]:
    """List *_transcript.json files in S3 for a station, modified after `since`."""
    prefix = f"{station}/"
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("_transcript.json"):
                continue
            if obj["LastModified"].replace(tzinfo=timezone.utc) >= since:
                keys.append(key)
    return keys


def has_diarized(s3, transcript_key: str) -> bool:
    """Check if a _diarized.json already exists for this transcript."""
    diarized_key = transcript_key.replace("_transcript.json", "_diarized.json")
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=diarized_key)
        return True
    except s3.exceptions.ClientError:
        return False


def diarize_via_runpod(transcript_key: str) -> dict:
    """Call RunPod serverless diarize endpoint and wait for result."""
    url = f"https://api.runpod.ai/v2/{RUNPOD_DIARIZE_ENDPOINT}/runsync"
    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"input": {"key": transcript_key}}

    resp = requests.post(url, headers=headers, json=payload, timeout=660)
    resp.raise_for_status()
    result = resp.json()

    if result.get("status") == "FAILED":
        raise RuntimeError(f"Diarization failed: {result.get('error', result)}")

    # For async jobs, poll until complete
    if result.get("status") == "IN_QUEUE" or result.get("status") == "IN_PROGRESS":
        job_id = result["id"]
        status_url = f"https://api.runpod.ai/v2/{RUNPOD_DIARIZE_ENDPOINT}/status/{job_id}"
        for _ in range(120):  # up to 10 minutes
            time.sleep(5)
            status_resp = requests.get(status_url, headers=headers, timeout=30)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            if status_data.get("status") == "COMPLETED":
                return status_data.get("output", {})
            if status_data.get("status") == "FAILED":
                raise RuntimeError(f"Diarization failed: {status_data.get('error', status_data)}")
        raise RuntimeError(f"Diarization timed out for {transcript_key}")

    return result.get("output", {})


def index_via_service(diarized_key: str) -> dict:
    """Call the local index service to index a diarized transcript."""
    url = f"{INDEX_SERVICE_URL}/index"
    payload = {"key": diarized_key}

    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Trigger diarize->index pipeline for new recordings")
    parser.add_argument("--hours", type=float, default=2, help="Look back N hours (default: 2)")
    parser.add_argument("--station", type=str, default=None, help="Process only this station")
    parser.add_argument("--dry-run", action="store_true", help="List files without processing")
    parser.add_argument("--skip-diarized", action="store_true", default=True,
                        help="Skip files that already have _diarized.json (default: true)")
    parser.add_argument("--force-reindex", action="store_true",
                        help="Re-index even if diarized file exists")
    args = parser.parse_args()

    if not RUNPOD_API_KEY or not RUNPOD_DIARIZE_ENDPOINT:
        print("ERROR: Set RUNPOD_API_KEY and RUNPOD_DIARIZE_ENDPOINT env vars", file=sys.stderr)
        sys.exit(1)

    s3 = get_s3_client()
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    stations = [args.station] if args.station else ALL_STATIONS

    print(f"Looking for transcripts since {since.isoformat()} across {len(stations)} stations...")

    total_found = 0
    total_processed = 0

    for station in stations:
        transcripts = list_recent_transcripts(s3, station, since)
        if not transcripts:
            continue

        # Filter out already-diarized
        if args.skip_diarized and not args.force_reindex:
            transcripts = [k for k in transcripts if not has_diarized(s3, k)]

        if not transcripts:
            continue

        total_found += len(transcripts)
        print(f"\n{station}: {len(transcripts)} new transcript(s)")

        for key in transcripts:
            print(f"  {key}")
            if args.dry_run:
                continue

            try:
                # Step 1: Diarize
                print(f"    Diarizing...", end=" ", flush=True)
                diarize_result = diarize_via_runpod(key)
                diarized_key = diarize_result.get("diarized_key", key.replace("_transcript.json", "_diarized.json"))
                print(f"done ({diarize_result.get('speaker_count', '?')} speakers, {diarize_result.get('turn_count', '?')} turns)")

                # Step 2: Index
                print(f"    Indexing...", end=" ", flush=True)
                index_result = index_via_service(diarized_key)
                print(f"done ({index_result.get('sentences_count', '?')} sentences, {index_result.get('segments_count', '?')} segments)")

                total_processed += 1

            except Exception as e:
                print(f"\n    ERROR: {e}")

    print(f"\nDone. Found {total_found}, processed {total_processed}.")


if __name__ == "__main__":
    main()
