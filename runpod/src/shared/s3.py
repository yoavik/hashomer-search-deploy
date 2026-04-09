import json
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

import boto3

from .config import AWS_KEY_ID, AWS_SECRET, S3_BUCKET, S3_REGION


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_SECRET,
        region_name=S3_REGION,
    )


def download_s3_to_tempfile(s3, key: str, suffix: str = "") -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    s3.download_file(S3_BUCKET, key, tmp.name)
    return Path(tmp.name)


def read_json_from_s3(s3, key: str) -> dict:
    response = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_json_to_s3(s3, key: str, data: dict):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/json")


def parse_s3_event(event: dict) -> dict | None:
    """Extract bucket and key from an S3 event notification.

    Supports:
    - Raw S3 event: {"Records": [{"s3": {"bucket": {...}, "object": {"key": ...}}}]}
    - Direct payload: {"bucket": "...", "key": "..."}
    - Direct payload: {"audio_url": "s3://...", "transcript_url": "s3://..."}
    """
    if "Records" in event:
        record = event["Records"][0]
        s3_info = record.get("s3", {})
        return {
            "bucket": s3_info.get("bucket", {}).get("name", S3_BUCKET),
            "key": unquote_plus(s3_info.get("object", {}).get("key", "")),
        }
    if "key" in event:
        return {
            "bucket": event.get("bucket", S3_BUCKET),
            "key": event["key"],
        }
    return None


def parse_s3_url(url: str) -> tuple[str, str]:
    """Parse s3://bucket/key URL into (bucket, key)."""
    if url.startswith("s3://"):
        parts = url[5:].split("/", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    return S3_BUCKET, url
