import os
from pathlib import Path


def load_env():
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


load_env()

# AWS S3
AWS_KEY_ID = os.environ.get("AWS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET")
S3_BUCKET = os.environ.get("S3_BUCKET", "yoav-radio-recordings")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Embedding model (multilingual-e5-large, 1024d)
MODEL_EMBED = os.environ.get("MODEL_EMBED", "intfloat/multilingual-e5-large")

# Embed service URL (used by index service)
EMBED_SERVICE_URL = os.environ.get("EMBED_SERVICE_URL", "http://embed:8000")

# HuggingFace token (for diarization)
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")

# Diarization
DIARIZATION_MODEL = os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")
SAMPLE_RATE = 16_000

# Stations
RADIO_STATIONS = ["kan-bet", "glz", "galei-israel", "103fm"]
TV_STATIONS = ["tv/n12", "tv/knesset", "tv/kan11", "tv/reshet13", "tv/ch14"]
ALL_STATIONS = RADIO_STATIONS + TV_STATIONS

# Segmentation config
SEGMENT_SIMILARITY_THRESHOLD = 0.55
SEGMENT_MAX_DURATION_SECONDS = 300
SEGMENT_MIN_SENTENCES = 3
SEGMENT_WINDOW_SIZE = 6

# Topic clustering
TOPIC_CLUSTER_THRESHOLD = 0.80
