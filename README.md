# HaShomer Semantic Search

Self-contained deployment project for the HaShomer Hebrew broadcast media monitoring and semantic search system. Each subdirectory is an independent deployable unit with its own source code, requirements, and deploy scripts.

## Architecture

```
                         ┌──────────────┐
                         │ User Browser │
                         └──────┬───────┘
                                │ HTTPS
               ┌────────────────▼────────────────┐
               │   AWS EC2 — Caddy (:80/:443)    │
               │   Auto-HTTPS via Let's Encrypt   │
               ├─────────────────────────────────┤
               │   Frontend: index.html           │
               │                                  │
               │   /api/search  → search:8000     │
               │   /api/embed/* → embed:8000      │
               │   /api/index/* → index:8000      │
               │   /api/health  → health check    │
               └──────┬──────┬──────┬─────────────┘
                      │      │      │
           ┌──────────▼┐  ┌──▼──────▼──┐
           │  Embed     │  │  Search    │
           │  (Docker)  │  │  (Docker)  │
           │  e5-large  │◄─┤  RRF merge │
           │  HeCross   │  │  reranker  │
           └────────────┘  └─────┬──────┘
                                 │ Supabase RPC
                      ┌──────────▼──────────┐
                      │  Supabase PostgreSQL │
                      │  pgvector + pg_trgm  │
                      │  halfvec 1024d       │
                      └──────────────────────┘

    ┌────────────────────────┐
    │  Index Service         │
    │  (Docker, same EC2)    │◄── /api/index/*
    │  sentences → segments  │
    │  → topics → Supabase   │
    └────────────────────────┘

    Ingestion Pipeline (cron on EC2, every hour):

    S3 recordings ──► trigger_pipeline.py
                          │
              ┌───────────┼───────────┐
              │                       │
    ┌─────────▼─────────┐  ┌──────────▼──────────┐
    │  RunPod: Diarize  │  │  RunPod: Audio Seg  │
    │  pyannote 3.1 GPU │  │  Silero VAD + INA   │
    └─────────┬─────────┘  └──────────┬──────────┘
              │  _diarized.json       │  _noisemap.json
              │  → S3                 │  → S3
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  Index Service (EC2)  │
              │  → Supabase DB       │
              └───────────────────────┘
```

### Microservice Design

All EC2 services run as separate Docker containers via `docker-compose`. They share a Docker network and can be:

- **Colocated** (default): All on one EC2 t3.medium, sharing CPU/RAM
- **Separated**: Each service can be extracted to its own machine — just update URLs in `.env`. Each has its own `Dockerfile` and `requirements.txt`.

| Container | Internal Port | Caddy Route | Can Run Standalone |
|-----------|--------------|-------------|-------------------|
| `embed` | 8000 | `/api/embed/*` | Yes — stateless, CPU-bound |
| `search` | 8000 | `/api/search` | Yes — needs embed + Supabase |
| `index` | 8000 | `/api/index/*` | Yes — needs embed + Supabase |
| `caddy` | 80, 443 | Frontend + proxy | Yes — just config |

---

## Folder Structure

```
deploy/
├── README.md                               ← This file
│
├── ec2/                                    ← EC2: 3 API services + frontend
│   ├── deploy.sh                           ← Upload code & restart Docker services
│   ├── setup.sh                            ← First-time EC2 setup (Docker, systemd)
│   ├── .env.example                        ← Environment variables template
│   ├── requirements.txt                    ← Host-level Python deps
│   └── src/
│       ├── docker-compose.yml              ← Caddy + embed + search + index
│       ├── Caddyfile                       ← HTTPS reverse proxy (4 routes)
│       ├── frontend/
│       │   └── index.html                  ← Search UI
│       ├── embed/
│       │   ├── app.py                      ← Embedding endpoints
│       │   ├── embeddings.py               ← Model loading, query expansion
│       │   ├── Dockerfile
│       │   └── requirements.txt
│       ├── index/
│       │   ├── app.py                      ← Indexing endpoint
│       │   ├── indexer.py                  ← Sentence→segment→topic pipeline
│       │   ├── Dockerfile
│       │   └── requirements.txt
│       ├── search/
│       │   ├── app.py                      ← Search endpoint (RRF merge, reranking)
│       │   ├── Dockerfile
│       │   └── requirements.txt
│       └── shared/
│           ├── __init__.py
│           ├── config.py                   ← Central config (env vars, models, stations)
│           ├── s3.py                       ← S3 client utilities
│           ├── supabase_client.py          ← Supabase connection
│           └── text_processing.py          ← Sentence splitting, ad detection, operators
│
├── runpod/                                 ← RunPod: speaker diarization (GPU)
│   ├── deploy.sh                           ← Build & push Docker image
│   ├── .env.example
│   ├── requirements.txt
│   └── src/
│       ├── app.py, handler.py, diarization.py
│       ├── Dockerfile, Dockerfile.runpod
│       ├── requirements.txt
│       └── shared/                         ← Copy of shared utilities
│
├── audio-processing/                       ← RunPod: audio segmentation (GPU)
│   ├── deploy.sh                           ← Build & push Docker image
│   ├── .env.example
│   ├── requirements.txt
│   └── src/
│       ├── audio_segmenter.py              ← Silero VAD + music/noise classifier
│       ├── filter_noise.py                 ← DB noise flagging
│       ├── handler.py                      ← RunPod serverless handler
│       ├── Dockerfile.runpod
│       └── requirements.txt
│
├── database/                               ← PostgreSQL / Supabase
│   ├── setup.sh                            ← Schema + migrations setup
│   ├── requirements.txt
│   └── src/
│       ├── schema.sql                      ← Full database schema
│       └── migrations/                     ← Ordered migration files
│
└── pipeline/                               ← Ingestion pipeline automation
    ├── deploy.sh                           ← Upload to EC2 + setup cron
    ├── .env.example
    ├── requirements.txt
    └── src/
        └── trigger_pipeline.py             ← Cron: scan S3 → diarize → index
```

---

## EC2 API Endpoints

All endpoints are served via Caddy with auto-HTTPS:

| # | Endpoint | Method | Service | Description |
|---|----------|--------|---------|-------------|
| 1 | `/api/search` | POST | search | Hybrid semantic+keyword search with RRF merge and reranking |
| 2 | `/api/embed/embed-query` | POST | embed | Generate query embeddings + morphological expansion |
|   | `/api/embed/embed-texts` | POST | embed | Batch text embeddings for indexing |
|   | `/api/embed/rerank` | POST | embed | Cross-encoder reranking (HeCross) |
| 3 | `/api/index/index` | POST | index | Index a diarized transcript from S3 |
| **Test** | `/api/health` | GET | — | Health check |

### Search Request

```bash
curl -X POST https://your-domain.com/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "חדשות ביטחוניות", "mode": "hybrid", "limit": 20}'
```

**Parameters:**
- `query` (required): Search text, supports operators (`+required`, `-excluded`, `"phrase"`)
- `mode`: `hybrid` (default), `semantic`, `keyword`
- `station`: Filter by station (e.g., `kan-bet`)
- `mediaType`: Filter by `radio` or `tv`
- `limit`: Max results (default 20, max 50)

---

## What Each Component Does

| Component | Where | Why | What |
|-----------|-------|-----|------|
| **Embed Service** | EC2 Docker | CPU inference, shared by search+index | Loads e5-large (1024d) + HeCross reranker. Generates embeddings, expands Hebrew morphology, parses query operators. |
| **Search Service** | EC2 Docker | Full search logic on EC2 | 7-signal hybrid RRF search, cross-encoder reranking, operator support, NOT exclusion, quality scoring. |
| **Index Service** | EC2 Docker | Processes transcripts | Splits text → sentences → segments → topics, generates embeddings, detects noise, writes to Supabase. |
| **Caddy** | EC2 :80/:443 | Auto-HTTPS | Reverse proxy with Let's Encrypt. Routes `/api/*` to services, serves frontend. |
| **RunPod Diarize** | RunPod GPU | pyannote needs GPU, pay-per-use | Speaker diarization: who speaks when. Downloads from S3, writes `_diarized.json` back. |
| **RunPod Audio** | RunPod GPU | Silero VAD + music classifier | Classifies audio into speech/music/noise/silence. Produces noise maps for the indexer. |
| **Database** | Supabase | Managed PostgreSQL + pgvector, free tier | Stores sentences, segments, topics with 1024d halfvec embeddings. Hebrew morphological FTS. |
| **Pipeline** | EC2 cron (hourly) | Automation | Scans S3 → diarize (RunPod) → index (EC2). |

---

## How Search Works

### Search Modes

1. **Semantic** — vector similarity using e5-large 1024d embeddings
2. **Keyword** — full-text search with Hebrew morphological expansion
3. **Hybrid** (default) — 7-signal Reciprocal Rank Fusion

### The 7 Search Signals

| # | Signal | Weight | Description |
|---|--------|--------|-------------|
| 1 | Sentence HE semantic | 0.7 | Individual sentences via e5-large vectors |
| 2 | Segment HE semantic | 0.8 | Thematic groups via Hebrew vectors |
| 3 | Sentence keyword (FTS) | 1.5 | Full-text search on sentences |
| 4 | Segment keyword (FTS) | 1.5 | Full-text search on segments |
| 5 | Phrase match | 1.2 | Exact phrase matching |
| 6 | Sentence trigram | 0.4 | Fuzzy trigram matching |
| 7 | Topic expansion | 0.15 | Matches via topic cluster centroids |

**RRF merge**: Each result gets score `weight * sim_factor / (k + rank)` across all signals, sorted by total. Quality penalties for short/repetitive content.

### Query Features

- **Operators**: `+required`, `-excluded`, `"exact phrase"`
- **Hebrew morphology**: Strips prefixes (ב,ל,ה,מ,ו,ש,כ) and suffixes
- **Query expansion**: English→Hebrew aliases (e.g., "hostages" → "חטופים")
- **Concept expansion**: Hebrew synonyms (e.g., "אינפלציה" → "עליית מחירים")
- **Reranking**: HeCross cross-encoder reranks top-20 results
- **Cosine gate**: Drops semantic signals if best match similarity < 0.25

### Search Data Flow

```
User query → EC2 /api/search
  → Embed Service: generate embeddings + expand query + parse operators
  → Supabase RPC: 7 parallel searches (semantic HE, keyword, trigram, topic)
  → RRF merge + quality penalties + score floor
  → Post-filter: exclude NOT terms
  → HeCross cross-encoder rerank top-20
  → Enrich with per-sentence context
  → Return ranked results
```

---

## How Indexing Works

### Data Hierarchy

```
Recording (media)
  └── Sentences (individual linguistic units, each with embedding)
        └── Segments (thematic groups of 3+ sentences, max 5 min)
              └── Topics (clusters of similar segments, 80% similarity)
```

### Pipeline Steps

1. **Input**: `_diarized.json` from S3
2. **Speaker merging**: Combine consecutive segments from same speaker
3. **Sentence splitting**: Individual sentences with timestamps
4. **Commercial detection**: Remove ad breaks, jingles, station IDs
5. **Embedding**: Generate 1024d vectors via e5-large
6. **Segment grouping**: Group by thematic similarity (cosine > 0.55), sliding window of 6
7. **Topic clustering**: Assign to topic clusters (centroid similarity > 0.80)
8. **Noise filtering**: Flag short, repetitive, gibberish content
9. **Storage**: Write to Supabase as halfvec with IVFFlat ANN indexes

### Key Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `SEGMENT_SIMILARITY_THRESHOLD` | 0.55 | Min cosine to group into segment |
| `SEGMENT_MAX_DURATION_SECONDS` | 300 | Max 5 min per segment |
| `SEGMENT_MIN_SENTENCES` | 3 | Min sentences before segment break |
| `SEGMENT_WINDOW_SIZE` | 6 | Rolling centroid window |
| `TOPIC_CLUSTER_THRESHOLD` | 0.80 | Min similarity for topic clustering |

---

## Deployment Order

### 1. Database (Supabase)

```bash
./deploy/database/setup.sh --supabase
```

Creates PostgreSQL schema with pgvector, Hebrew FTS, RPC functions.

**Collect**: `SUPABASE_URL`, `SUPABASE_KEY`

### 2. EC2 (3 API Services + Frontend)

```bash
# First time: EC2 instance (Ubuntu 24.04, t3.medium, 100GB)
# Security group: ports 22, 80, 443 only (services are internal via Caddy)
ssh -i key.pem ubuntu@EC2_IP "bash -s" < deploy/ec2/setup.sh

# Copy .env.example → .env, fill in values, then:
./deploy/ec2/deploy.sh EC2_IP key.pem
```

Starts 4 Docker containers. First startup downloads ~3GB of models.

**Verify**:
```bash
curl https://your-domain.com/api/health                                    # test
curl -X POST https://your-domain.com/api/search -d '{"query":"test"}'     # search
```

### 3. RunPod — Diarization

```bash
./deploy/runpod/deploy.sh myuser/hashomer-diarize
```

### 4. RunPod — Audio Segmentation

```bash
./deploy/audio-processing/deploy.sh myuser/hashomer-audio-segmenter
```

### 5. Pipeline Automation

```bash
./deploy/pipeline/deploy.sh EC2_IP key.pem
```

---

## Environment Variables

### EC2 (`~/app/services/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Project URL |
| `SUPABASE_KEY` | Yes | Service role key |
| `AWS_KEY_ID` | Yes | S3 access key |
| `AWS_SECRET` | Yes | S3 secret key |
| `S3_BUCKET` | Yes | `yoav-radio-recordings` |
| `S3_REGION` | Yes | `us-east-1` |
| `RUNPOD_API_KEY` | Yes | RunPod API key |
| `RUNPOD_DIARIZE_ENDPOINT` | Yes | Diarize endpoint ID |

### RunPod Endpoints

| Variable | Diarize | Audio Seg |
|----------|---------|-----------|
| `AWS_KEY_ID` | Yes | Yes |
| `AWS_SECRET` | Yes | Yes |
| `S3_BUCKET` | Yes | Yes |
| `HF_TOKEN` | Yes | No |

### Frontend (`index.html`)

The frontend calls `/api/search` (relative URL), so no configuration needed — it works automatically behind Caddy.

---

## Monitored Stations

**Radio**: `kan-bet`, `glz`, `galei-israel`, `103fm`
**TV**: `tv/n12`, `tv/knesset`, `tv/kan11`, `tv/reshet13`, `tv/ch14`

---

## Cost Estimate

| Component | Cost/month |
|-----------|-----------|
| Supabase (free tier) | $0 |
| EC2 t3.medium | ~$30 |
| RunPod diarization | ~$5-20 (usage) |
| RunPod audio seg | ~$2-10 (usage) |
| **Total** | **~$37-60** |

---

## Troubleshooting

### EC2 embed OOM
t3.medium has 4GB RAM, models need ~3GB. Check `docker compose logs embed`. Consider t3.large (8GB, ~$60/month).

### Caddy TLS failure
Ports 80+443 must be open. DNS must point directly to EC2 IP (no Cloudflare proxy). Check `docker compose logs caddy`.

### Search returns empty
Check embed service is warm (models loaded): `docker compose logs embed`. Verify data is indexed: `curl /api/search -d '{"query":"test"}'`.

### Pipeline finds no files
Check AWS credentials, S3 bucket name, use `--hours 24` for wider window.

### Splitting services to separate machines
Each service has its own `Dockerfile`. To run separately:
1. Build and run the container on its own machine
2. Update `EMBED_SERVICE_URL` in search/index to point to the embed machine
3. Update Caddy to reverse proxy to the new machine IPs
