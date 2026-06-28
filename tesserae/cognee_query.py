"""Cognee-backed project question answering helpers."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional


# Cognee 1.0 removed several V1 search types (notably INSIGHTS, the entity/edge
# triplet retriever Tesserae defaulted to). Map the ones Tesserae historically
# used to their 1.x successor so existing configs keep working. GRAPH_COMPLETION
# answers a query over the knowledge graph — the closest fit for `ask`.
_SEARCH_TYPE_ALIASES = {"INSIGHTS": "GRAPH_COMPLETION"}


def _search_type(name: str):
    import cognee

    normalized = (name or "INSIGHTS").upper()
    normalized = _SEARCH_TYPE_ALIASES.get(normalized, normalized)
    try:
        return getattr(cognee.SearchType, normalized)
    except AttributeError as exc:
        available = ", ".join(item for item in dir(cognee.SearchType) if item.isupper())
        raise ValueError(f"Unknown Cognee search type: {name}. Available: {available}") from exc


def _coerce_results(results: Any) -> List[str]:
    if results is None:
        return []
    if not isinstance(results, list):
        results = [results]
    rendered: List[str] = []
    for item in results:
        if isinstance(item, str):
            rendered.append(item)
        elif isinstance(item, dict):
            rendered.append(str(item.get("text") or item.get("content") or item.get("answer") or item))
        else:
            rendered.append(str(item))
    return rendered


async def asearch_cognee(question: str, *, dataset: Optional[str] = None, search_type: str = "INSIGHTS", top_k: int = 8) -> List[str]:
    import cognee

    query_type = _search_type(search_type)
    kwargs = {}
    # Cognee's Python API changed across releases. Prefer the newer keyword
    # shape when possible; fall back to the older positional shape installed in
    # some local environments.
    try:
        results = await cognee.search(
            query_text=question,
            query_type=query_type,
            datasets=[dataset] if dataset else None,
            top_k=top_k,
        )
    except TypeError:
        results = await cognee.search(query_type, question)
    return _coerce_results(results)


def search_cognee(question: str, *, dataset: Optional[str] = None, search_type: str = "INSIGHTS", top_k: int = 8) -> List[str]:
    return asyncio.run(asearch_cognee(question, dataset=dataset, search_type=search_type, top_k=top_k))
