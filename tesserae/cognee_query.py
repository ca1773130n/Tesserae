"""Cognee-backed project question answering helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, Iterator, List, Optional


# Cognee 1.0 removed several V1 search types (notably INSIGHTS, the entity/edge
# triplet retriever Tesserae defaulted to). Map the ones Tesserae historically
# used to their 1.x successor so existing configs keep working. GRAPH_COMPLETION
# answers a query over the knowledge graph — the closest fit for `ask`.
_SEARCH_TYPE_ALIASES = {"INSIGHTS": "GRAPH_COMPLETION"}

# One process-lifetime sink for suppressed backend output. Never closed:
# handlers/loggers created inside a suppression window capture the stream
# object, and a closed file would make their later emits raise.
_DEVNULL = None


def _devnull():
    global _DEVNULL
    if _DEVNULL is None or _DEVNULL.closed:
        _DEVNULL = open(os.devnull, "w", encoding="utf-8")
    return _DEVNULL


@contextlib.contextmanager
def _silenced_cognee() -> Iterator[None]:
    """Suppress cognee's import/search chatter (stdout, stderr, logging, structlog).

    ``import cognee`` configures structlog and prints auth/telemetry banners;
    searches log per-step progress. An explicit ``--backend cognee`` ask must
    surface only its answer (or a clean one-line error), so every touchpoint
    wraps itself in this: stdout/stderr are redirected to ``os.devnull``,
    stdlib logging is disabled for the window, and structlog (when installed)
    is parked on a discard-everything factory, then restored. Exceptions
    propagate untouched.
    """
    devnull = _devnull()
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        previous_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        structlog_mod = None
        saved_config = None
        try:
            import structlog

            structlog_mod = structlog
            saved_config = structlog.get_config()
            structlog.configure(
                processors=[],
                logger_factory=structlog.ReturnLoggerFactory(),
                cache_logger_on_first_use=False,
            )
        except Exception:
            structlog_mod = None
        try:
            yield
        finally:
            logging.disable(previous_disable)
            if structlog_mod is not None and saved_config is not None:
                try:
                    structlog_mod.configure(**saved_config)
                except Exception:
                    pass


def _search_type(name: str):
    with _silenced_cognee():
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
    with _silenced_cognee():
        import cognee

        query_type = _search_type(search_type)
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
    with _silenced_cognee():
        return asyncio.run(asearch_cognee(question, dataset=dataset, search_type=search_type, top_k=top_k))
