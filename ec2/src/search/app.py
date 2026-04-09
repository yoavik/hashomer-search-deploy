import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.config import EMBED_SERVICE_URL, SUPABASE_URL, SUPABASE_KEY

app = FastAPI(title="HaShomer Search Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS", "GET"],
    allow_headers=["*"],
)

MAX_LIMIT = 50
K = 60

SIGNAL_WEIGHTS = {
    "sent_kw": 1.5, "seg_kw": 1.5, "phrase": 1.2,
    "seg_he": 0.8, "sent_he": 0.7,
    "sent_trgm": 0.4, "seg_trgm": 0.3, "topic": 0.15,
}


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"
    station: str | None = None
    mediaType: str | None = None
    limit: int = 20
    rrf_k: int | None = None
    weights: dict[str, float] | None = None
    threshold_he: float | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "search"}


def get_query_embedding(query: str) -> dict:
    res = httpx.post(
        f"{EMBED_SERVICE_URL}/embed-query",
        json={"query": query},
        timeout=60,
    )
    res.raise_for_status()
    data = res.json()
    return {
        "he": data["embedding_he"],
        "tsquery": data.get("tsquery"),
        "has_operators": data.get("has_operators", False),
        "excluded_terms": data.get("excluded_terms", []),
        "phrases": data.get("phrases", []),
    }


def supabase_rpc(function_name: str, params: dict):
    res = httpx.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{function_name}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json=params,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


COSINE_SIM_GATE = 0.25
MIN_HYBRID_SCORE = 0.008


def merge_results(signals: dict[str, list], limit: int, weights: dict | None = None, rrf_k: int | None = None) -> list:
    from collections import Counter

    merged: dict[str, dict] = {}
    w = weights if weights else SIGNAL_WEIGHTS
    k = rrf_k if rrf_k is not None else K

    for signal_name, results in signals.items():
        seen_segments: set[str] = set()
        for i, r in enumerate(results or []):
            sid = str(r.get("segment_id", ""))
            if not sid or sid in seen_segments:
                continue
            seen_segments.add(sid)
            # Score-weighted RRF
            sim = r.get("similarity", r.get("rank", 0.5))
            sim_factor = min(max(float(sim) / 0.5, 0.3), 1.0) if isinstance(sim, (int, float)) else 0.5
            rrf = w.get(signal_name, 0.3) * sim_factor / (k + i + 1)
            if sid in merged:
                merged[sid]["score"] += rrf
                merged[sid]["signals"].add(signal_name)
            else:
                merged[sid] = {**r, "score": rrf, "signals": {signal_name}, "match_type": signal_name}

    all_results = list(merged.values())

    # Quality penalty: short/repetitive content
    for r in all_results:
        text = r.get("segment_text") or r.get("sentence_text") or ""
        words = text.split()
        wc = len(words)
        if wc < 5:
            r["score"] *= 0.3
        elif wc < 10:
            r["score"] *= 0.7
        if wc >= 8:
            unique_ratio = len(set(words)) / wc
            if unique_ratio < 0.3:
                r["score"] *= 0.2
            elif unique_ratio < 0.4:
                r["score"] *= 0.5
        if wc >= 12:
            ngrams = [" ".join(words[j:j+4]) for j in range(wc - 3)]
            if max(Counter(ngrams).values()) >= 3:
                r["score"] *= 0.4

    for r in all_results:
        r["signal_count"] = len(r["signals"])
        r["signal_list"] = "+".join(sorted(r["signals"]))
        r["match_type"] = "hybrid" if len(r["signals"]) > 1 else r["match_type"]
        r["signals"] = list(r["signals"])

    all_results.sort(key=lambda x: x["score"], reverse=True)

    # Score floor
    if all_results and all_results[0]["score"] < MIN_HYBRID_SCORE:
        all_results = []
    elif all_results:
        best = all_results[0]["score"]
        floor = max(MIN_HYBRID_SCORE, best * 0.15)
        all_results = [r for r in all_results if r["score"] >= floor]

    return all_results[:limit]


@app.post("/api/search")
def search(request: SearchRequest):
    query = request.query.strip()
    if not query:
        return {"error": "Missing search query"}

    match_count = min(request.limit, MAX_LIMIT)
    fetch_count = match_count * 2

    filters = {}
    if request.station:
        filters["filter_station"] = request.station
    if request.mediaType:
        filters["filter_media_type"] = request.mediaType

    th_he = request.threshold_he if request.threshold_he is not None else 0.18

    results = []

    if request.mode == "keyword":
        embs = get_query_embedding(query)
        tsquery_str = embs.get("tsquery")
        if tsquery_str and embs.get("has_operators"):
            seg_kw = supabase_rpc("search_segments_keyword_tsquery", {
                "tsquery_str": tsquery_str, "match_count": match_count, **filters,
            })
        else:
            seg_kw = supabase_rpc("search_segments_keyword", {
                "search_query": query, "match_count": match_count, **filters,
            })
        results = [
            {**r, "score": r.get("rank", 0), "match_type": "keyword", "signals": ["seg_kw"]}
            for r in seg_kw
        ]
    else:
        embs = get_query_embedding(query)
        emb_he = embs["he"]
        tsquery_str = embs.get("tsquery")
        has_operators = embs.get("has_operators", False)

        if request.mode == "semantic":
            seg_he = supabase_rpc("search_segments_semantic_he", {
                "query_embedding": emb_he, "match_threshold": th_he,
                "match_count": match_count, **filters,
            })
            results = [
                {**r, "score": r.get("similarity", 0), "match_type": "semantic_he"}
                for r in seg_he
            ]
            results.sort(key=lambda x: x["score"], reverse=True)
            results = results[:match_count]
        else:
            # hybrid: HE semantic + keyword + trigram + topic
            sent_he = supabase_rpc("search_sentences_semantic_he", {
                "query_embedding": emb_he, "match_threshold": th_he,
                "match_count": fetch_count, **filters,
            })
            seg_he = supabase_rpc("search_segments_semantic_he", {
                "query_embedding": emb_he, "match_threshold": th_he,
                "match_count": fetch_count, **filters,
            })

            # Cosine similarity gate: drop semantic signals if best match too distant
            if sent_he and sent_he[0].get("similarity", 0) < COSINE_SIM_GATE:
                sent_he = []
            if seg_he and seg_he[0].get("similarity", 0) < COSINE_SIM_GATE:
                seg_he = []

            if tsquery_str and has_operators:
                sent_kw = supabase_rpc("search_sentences_keyword_tsquery", {
                    "tsquery_str": tsquery_str, "match_count": fetch_count, **filters,
                })
                seg_kw = supabase_rpc("search_segments_keyword_tsquery", {
                    "tsquery_str": tsquery_str, "match_count": fetch_count, **filters,
                })
            else:
                sent_kw = supabase_rpc("search_sentences_keyword", {
                    "search_query": query, "match_count": fetch_count, **filters,
                })
                seg_kw = supabase_rpc("search_segments_keyword", {
                    "search_query": query, "match_count": fetch_count, **filters,
                })

            # Topic expansion
            topic_ids = list({
                r.get("topic_id") for r in seg_he if r.get("topic_id")
            })[:10]
            topic_results = []
            if topic_ids:
                topic_results = supabase_rpc("search_segments_by_topic", {
                    "p_topic_ids": topic_ids, "match_count": fetch_count, **filters,
                })

            results = merge_results({
                "sent_he": sent_he, "seg_he": seg_he,
                "sent_kw": sent_kw, "seg_kw": seg_kw,
                "topic": topic_results,
            }, match_count, weights=request.weights, rrf_k=request.rrf_k)

    # Post-filter: exclude NOT terms
    excluded_terms = embs.get("excluded_terms", [])
    if excluded_terms:
        def _contains_excluded(r):
            text = (r.get("segment_text") or r.get("sentence_text") or "").lower()
            return any(exc.lower() in text for exc in excluded_terms)
        results = [r for r in results if not _contains_excluded(r)]

    # Cross-encoder re-ranking via embed service
    if results and len(results) > 1:
        candidates = results[:20]
        texts = [(r.get("segment_text") or r.get("sentence_text") or "")[:512] for r in candidates]
        if any(texts):
            try:
                rerank_resp = httpx.post(
                    f"{EMBED_SERVICE_URL}/rerank",
                    json={"query": query, "texts": texts},
                    timeout=30,
                )
                rerank_resp.raise_for_status()
                scores = rerank_resp.json()["scores"]
                for r, s in zip(candidates, scores):
                    r["rerank_score"] = float(s)
                candidates.sort(key=lambda x: -x.get("rerank_score", 0))
                results = candidates[:match_count] + results[20:]
            except Exception:
                pass  # fallback to RRF order

    # Enrich with sentences
    seg_ids = [r["segment_id"] for r in results if r.get("segment_id")]
    sentences_by_segment: dict[str, list] = {}
    if seg_ids:
        sentences = supabase_rpc("get_segment_sentences", {"p_segment_ids": seg_ids})
        for s in sentences:
            sid = str(s["segment_id"])
            sentences_by_segment.setdefault(sid, []).append(s)
    for r in results:
        r["sentences"] = sentences_by_segment.get(str(r.get("segment_id", "")), [])

    return {"results": results, "query": query, "mode": request.mode}
