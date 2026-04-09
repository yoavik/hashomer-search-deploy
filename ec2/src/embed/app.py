import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))

from fastapi import FastAPI
from pydantic import BaseModel

from embeddings import (
    get_model,
    get_reranker,
    generate_embeddings_he,
    rerank,
    sanitize_embedding,
    expand_query,
    expand_hebrew_morphology,
)
from shared.text_processing import parse_query_operators, build_tsquery_from_parsed

app = FastAPI(title="HaShomer Embedding Service")


class EmbedQueryRequest(BaseModel):
    query: str
    expand: bool = True


class EmbedQueryResponse(BaseModel):
    embedding_he: list[float] | None
    expanded_query: str
    keyword_additions: list[str]
    morphological_variants: list[str]
    tsquery: str | None = None
    has_operators: bool = False
    excluded_terms: list[str] = []
    phrases: list[str] = []


class EmbedTextsRequest(BaseModel):
    texts: list[str]


class EmbedTextsResponse(BaseModel):
    embeddings_he: list[list[float] | None]


@app.get("/health")
def health():
    return {"status": "ok", "service": "embed"}


class RerankRequest(BaseModel):
    query: str
    texts: list[str]


class RerankResponse(BaseModel):
    scores: list[float]


@app.on_event("startup")
def startup():
    print("Pre-loading e5-large embedding model...")
    get_model()
    print("Pre-loading HeCross reranker...")
    get_reranker()
    print("All models loaded.")


@app.post("/embed-query", response_model=EmbedQueryResponse)
def embed_query(request: EmbedQueryRequest):
    query = request.query
    keyword_additions = []
    morphological_variants = []
    parsed = parse_query_operators(query)

    if request.expand:
        expanded, keyword_additions = expand_query(query)
        phrase_words = set()
        for phrase in parsed["phrases"]:
            phrase_words.update(phrase.split())
        morphological_variants = list(expand_hebrew_morphology(expanded, skip_words=phrase_words))
    else:
        expanded = parsed["raw_for_embedding"]

    morph_set = set(morphological_variants) if morphological_variants else None
    tsquery_str = build_tsquery_from_parsed(parsed, morph_variants=morph_set)

    he_embs = generate_embeddings_he([expanded], mode="query")

    return EmbedQueryResponse(
        embedding_he=he_embs[0],
        expanded_query=expanded,
        keyword_additions=keyword_additions,
        morphological_variants=morphological_variants,
        tsquery=tsquery_str or None,
        has_operators=parsed["has_operators"],
        excluded_terms=parsed["excluded"],
        phrases=parsed["phrases"],
    )


@app.post("/embed-texts", response_model=EmbedTextsResponse)
def embed_texts(request: EmbedTextsRequest):
    if not request.texts:
        return EmbedTextsResponse(embeddings_he=[])

    he_embs = generate_embeddings_he(request.texts)

    return EmbedTextsResponse(
        embeddings_he=he_embs,
    )


@app.post("/rerank", response_model=RerankResponse)
def rerank_endpoint(request: RerankRequest):
    if not request.texts:
        return RerankResponse(scores=[])
    scores = rerank(request.query, request.texts)
    return RerankResponse(scores=scores)
