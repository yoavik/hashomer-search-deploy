import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.s3 import get_s3_client, read_json_from_s3, parse_s3_event, parse_s3_url
from indexer import index_transcript

app = FastAPI(title="HaShomer Indexing Service")


class IndexRequest(BaseModel):
    transcript_url: str | None = None
    station: str | None = None
    # S3 event fields
    Records: list | None = None
    key: str | None = None
    bucket: str | None = None


class IndexResponse(BaseModel):
    status: str
    media_id: str | None = None
    sentences_count: int | None = None
    segments_count: int | None = None
    reason: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "index"}


@app.post("/index", response_model=IndexResponse)
def index(request: IndexRequest):
    # Resolve transcript key
    transcript_key = None
    station = request.station

    if request.transcript_url:
        _, transcript_key = parse_s3_url(request.transcript_url)
    elif request.key:
        transcript_key = request.key
    elif request.Records:
        event_info = parse_s3_event(request.model_dump())
        if not event_info:
            raise HTTPException(400, "Could not parse S3 event")
        transcript_key = event_info["key"]
    else:
        raise HTTPException(400, "Provide transcript_url, key, or S3 event")

    if not transcript_key:
        raise HTTPException(400, "Could not determine transcript key")

    # Infer station from key if not provided
    if not station:
        # Key format: station/date_time_diarized.json or tv/station/date_time_diarized.json
        parts = transcript_key.split("/")
        if len(parts) >= 2:
            if parts[0] == "tv" and len(parts) >= 3:
                station = f"{parts[0]}/{parts[1]}"
            else:
                station = parts[0]
        else:
            raise HTTPException(400, "Could not infer station from key, provide station parameter")

    # Download and index
    s3 = get_s3_client()
    transcript = read_json_from_s3(s3, transcript_key)

    try:
        result = index_transcript(transcript, transcript_key, station)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return IndexResponse(**result)
