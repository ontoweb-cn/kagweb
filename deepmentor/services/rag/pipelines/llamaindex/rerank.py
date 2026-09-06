"""Optional cross-encoder reranking for the LlamaIndex pipeline.

Two backends are supported, selected by the ``reranker_endpoint`` setting:

* **vLLM HTTP** (endpoint set, e.g. ``http://localhost:8000``) — scores via a
  vLLM ``/v1/rerank`` service (bge-reranker-v2-m3 style pooling deployment).
  Reuses the GPU-resident model instead of loading a second copy in-process.
* **Local** (endpoint empty) — loads a Hugging Face cross-encoder through
  ``sentence_transformers.CrossEncoder`` (requires the ``rag-rerank`` extra).
"""

from __future__ import annotations

from collections import OrderedDict
import logging
import math
from threading import Lock
from typing import Any, Callable

from llama_index.core.schema import MetadataMode, NodeWithScore

logger = logging.getLogger(__name__)

_RERANKER_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_RERANKER_CACHE_LOCK = Lock()
_RERANKER_CACHE_MAXSIZE = 2

# vLLM rerank calls must not stall retrieval: a short timeout keeps the
# first-stage ordering as the fallback when the service is slow or down.
_HTTP_TIMEOUT_SECONDS = 10.0


def clear_reranker_cache() -> None:
    """Drop cached reranker models (used by tests and settings changes)."""
    with _RERANKER_CACHE_LOCK:
        _RERANKER_CACHE.clear()


def _load_cross_encoder(model_name: str) -> Any:
    """Load a SentenceTransformers cross-encoder lazily."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


def _cross_encoder(
    model_name: str,
    loader: Callable[[str], Any] | None = None,
) -> Any | None:
    with _RERANKER_CACHE_LOCK:
        cached = _RERANKER_CACHE.get(model_name)
        if cached is not None:
            _RERANKER_CACHE.move_to_end(model_name)
            return cached

    try:
        model = (loader or _load_cross_encoder)(model_name)
    except ImportError:
        logger.warning(
            "Reranker model %r is configured, but sentence-transformers is not installed; "
            "using embedding retrieval unchanged.",
            model_name,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Failed to load reranker model %r; using embedding retrieval unchanged: %s",
            model_name,
            exc,
        )
        return None

    with _RERANKER_CACHE_LOCK:
        _RERANKER_CACHE[model_name] = model
        _RERANKER_CACHE.move_to_end(model_name)
        while len(_RERANKER_CACHE) > _RERANKER_CACHE_MAXSIZE:
            _RERANKER_CACHE.popitem(last=False)
    return model


def _sigmoid(value: float) -> float:
    """Convert cross-encoder logits to a stable 0..1 source score."""
    try:
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exp_value = math.exp(value)
        return exp_value / (1.0 + exp_value)
    except (OverflowError, ValueError):
        return 1.0 if value > 0 else 0.0


def _identity_logits(scores: Any) -> Any:
    """Request raw cross-encoder logits from SentenceTransformers."""
    return scores


def _vllm_rerank_url(endpoint: str) -> str:
    """Normalize a reranker endpoint base into a ``/v1/rerank`` URL.

    Accepts both bare bases (``http://host:8000``) and ``/v1``-suffixed ones
    (``http://host:8000/v1``), mirroring how embedding base URLs are entered.
    """
    base = endpoint.strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/rerank"


def _rerank_via_vllm(
    endpoint: str,
    model_name: str,
    query: str,
    texts: list[str],
) -> list[tuple[int, float]] | None:
    """Score query/document pairs against a vLLM ``/v1/rerank`` service.

    Returns ``(candidate_index, relevance_score)`` pairs already sorted by the
    service (score descending), or ``None`` when the call fails — reranking is
    an refinement step, so failures fall back to first-stage ordering.
    """
    import httpx

    url = _vllm_rerank_url(endpoint)
    try:
        response = httpx.post(
            url,
            json={"model": model_name, "query": query, "documents": texts},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "Reranker service %r failed for model %r; using embedding "
            "retrieval unchanged: %s",
            url,
            model_name,
            exc,
        )
        return None

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        logger.warning(
            "Reranker service %r returned an unexpected payload; using "
            "embedding retrieval unchanged.",
            url,
        )
        return None

    scored: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score")
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            continue
        value = float(score)
        if math.isfinite(value):
            scored.append((index, value))
    # vLLM already applies the model's sigmoid, so relevance_score arrives as
    # 0..1 — no local activation needed. Sort defensively regardless.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    if not scored:
        # Zero usable entries means the payload shape drifted — treat it like a
        # failed call so retrieval falls back to the first-stage ordering
        # instead of silently returning no results.
        logger.warning(
            "Reranker service %r returned no usable results; using embedding "
            "retrieval unchanged.",
            url,
        )
        return None
    return scored


def rerank_nodes(
    query: str,
    nodes: list[Any],
    *,
    top_k: int,
    model_name: str,
    loader: Callable[[str], Any] | None = None,
    endpoint: str = "",
) -> list[Any]:
    """Rerank LlamaIndex results and return at most ``top_k`` nodes.

    Missing optional dependencies, model-load failures and HTTP failures are
    non-fatal: the first-stage ordering is returned so saved knowledge remains
    searchable.
    """
    requested = max(1, int(top_k))
    if not query or not nodes or not model_name:
        return nodes[:requested]

    if endpoint:
        texts = [
            result.node.get_content(metadata_mode=MetadataMode.LLM) for result in nodes
        ]
        scored = _rerank_via_vllm(endpoint, model_name, query, texts)
        if scored is None:
            return nodes[:requested]
        ranked = [
            (index, score)
            for index, score in scored
            if 0 <= index < len(nodes)
        ][:requested]
        return [
            NodeWithScore(node=nodes[index].node, score=score) for index, score in ranked
        ]

    model = _cross_encoder(model_name, loader)
    if model is None:
        return nodes[:requested]

    pairs = [(query, result.node.get_content(metadata_mode=MetadataMode.LLM)) for result in nodes]
    try:
        raw_scores = model.predict(pairs, activation_fct=_identity_logits)
        ranked = sorted(
            (
                (index, float(score))
                for index, score in enumerate(raw_scores)
                if math.isfinite(float(score))
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:requested]
    except Exception as exc:
        logger.warning(
            "Reranker model %r failed while scoring %d candidates; "
            "using embedding retrieval unchanged: %s",
            model_name,
            len(nodes),
            exc,
        )
        return nodes[:requested]

    return [NodeWithScore(node=nodes[index].node, score=_sigmoid(score)) for index, score in ranked]


__all__ = ["clear_reranker_cache", "rerank_nodes"]
