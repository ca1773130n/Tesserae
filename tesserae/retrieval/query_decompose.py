"""Query decomposition for multi-pool retrieval (design component 3).

A retrieval question often bundles several distinct sub-questions ("how do
I run the seeder *and* why does it fail on a fresh DB?"). Splitting it lets
``compile_context(multi_pool=True)`` run hybrid search per sub-query, union
the seeds, and surface distilled ``Runbook`` / ``Gotcha`` / ``Event`` memory
that a single embedding of the whole sentence would dilute.

This module follows the project's two load-bearing conventions:

* **LLM-optional, degrade-never-raise** (mirrors :mod:`tesserae.memory.supersede`
  ``_ask_llm``): an :class:`~tesserae.llm_json.LLMJsonClient` only *enriches*
  the split. With no client — or on ANY client failure, empty output, or
  invalid JSON — we fall back to a deterministic clause split. The function
  never raises.
* **Pure & deterministic** given the same inputs: no ``datetime.now()``, no
  RNG, stable ordering. The original query always leads the result, the list
  is deduped order-stably, and length is capped at ``max_subqueries``.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from ..llm_json import LLMJsonClient, parse_json_tolerant

logger = logging.getLogger(__name__)

# Minimum length (after strip) for a fragment to count as a real sub-query.
# Filters out stray connectives / punctuation left behind by the split.
_MIN_FRAGMENT_LEN = 3

# Clause boundaries, longest-token-first so " and " / " then " win over a
# bare comma. Sentence terminators, ``?``/``;``, commas, and the two common
# coordinating words. Case-insensitive on the word boundaries.
_SPLIT_RE = re.compile(
    r"(?:[.?;,]+)"          # sentence terminators / ? ; , (one or more)
    r"|(?:\s+and\s+)"        # " and "
    r"|(?:\s+then\s+)",      # " then "
    re.IGNORECASE,
)

_DECOMPOSE_SYSTEM = (
    "You split a single retrieval question into a short list of focused, "
    "self-contained sub-questions. Each sub-question must stand alone and "
    "target one distinct piece of information. Do not invent topics the "
    "original question does not raise."
)

_DECOMPOSE_USER_TEMPLATE = (
    "Original question:\n{query}\n\n"
    "Return a JSON array of {max_subqueries} or fewer short sub-question "
    "strings (most focused first). If the question is already atomic, return "
    "a single-element array containing it. Return ONLY the JSON array."
)


def _clause_fragments(query: str) -> List[str]:
    """Deterministic clause split — the no-LLM fallback core.

    Splits on sentence / ``?`` / ``;`` / ``,`` / ``" and "`` / ``" then "``
    boundaries, keeps non-trivial unique fragments (>= 3 chars after strip),
    preserving first-seen order. Pure; no side effects.
    """
    fragments: List[str] = []
    seen: set[str] = set()
    for raw in _SPLIT_RE.split(query):
        frag = (raw or "").strip()
        if len(frag) < _MIN_FRAGMENT_LEN:
            continue
        key = frag.lower()
        if key in seen:
            continue
        seen.add(key)
        fragments.append(frag)
    return fragments


def _dedupe_capped(items: List[str], max_subqueries: int) -> List[str]:
    """Order-stable dedupe (case-insensitive) capped at ``max_subqueries``."""
    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = (item or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= max_subqueries:
            break
    return result


def _fallback(query: str, max_subqueries: int) -> List[str]:
    """Deterministic decomposition: original query first, then clause splits.

    If nothing splits, returns ``[query.strip()]`` (or ``[]`` for blank
    input). Never raises.
    """
    stripped = query.strip()
    if not stripped:
        return []
    # Original always leads; clause fragments follow. _dedupe_capped drops
    # any fragment equal to the full query (common when nothing really split)
    # and enforces the cap.
    return _dedupe_capped([stripped, *_clause_fragments(query)], max_subqueries)


def _llm_subqueries(
    query: str,
    json_client: LLMJsonClient,
    max_subqueries: int,
) -> Optional[List[str]]:
    """Ask the client for a JSON array of sub-questions. ``None`` on any
    failure / empty / invalid output. Never raises."""
    try:
        response = json_client.complete_json(
            system=_DECOMPOSE_SYSTEM,
            user=_DECOMPOSE_USER_TEMPLATE.format(
                query=query.strip(), max_subqueries=max_subqueries
            ),
            schema_name="query_decomposition",
            cache_key="query-decompose-v1",
            max_retries=1,
        )
    except Exception:  # noqa: BLE001 — degrade-never-raise
        logger.warning("query_decompose: LLM call raised; using fallback")
        return None

    # complete_json may already return a parsed list; tolerate a raw string
    # too (some clients hand back the text for the caller to parse).
    if isinstance(response, str):
        response = parse_json_tolerant(response)
    if not isinstance(response, list):
        return None
    subqueries = [str(item).strip() for item in response if str(item).strip()]
    return subqueries or None


def decompose_query(
    query: str,
    *,
    json_client: Optional[LLMJsonClient] = None,
    max_subqueries: int = 5,
) -> List[str]:
    """Decompose ``query`` into a deduped, order-stable list of sub-queries.

    The original query always leads the result. With a ``json_client`` the
    LLM's focused sub-questions are merged in after the original; with no
    client — or on ANY failure / empty / invalid response — a deterministic
    clause split is used instead. The list is deduped (case-insensitive,
    first-seen order) and capped at ``max_subqueries``.

    Blank input returns ``[]``. A query with no internal clause boundaries
    and no LLM returns ``[query.strip()]``. Pure & deterministic for the same
    inputs; never raises.
    """
    stripped = query.strip()
    if not stripped:
        return []
    cap = max(1, int(max_subqueries))

    if json_client is not None:
        subqueries = _llm_subqueries(query, json_client, cap)
        if subqueries:
            # Original leads, LLM sub-questions follow; dedupe + cap.
            return _dedupe_capped([stripped, *subqueries], cap)
        # Empty / invalid / exception → deterministic fallback.

    return _fallback(query, cap)
