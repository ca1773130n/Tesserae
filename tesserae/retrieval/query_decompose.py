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
from typing import List, Mapping, Optional

from ..llm_json import LLMJsonClient, parse_json_tolerant
# `STOPWORDS` and `_tokenize` are reused rather than restated. `grounding`
# already imports `_tokenize` from `hybrid` for exactly this reason ("one
# definition, no drift", rerank.py:41-44), and its STOPWORDS frozenset already
# carries every question word the filter below needs. A sixth copy of a
# stoplist in this repo would be a copy that can drift from the five others.
from .grounding import STOPWORDS
from .hybrid import _tokenize

logger = logging.getLogger(__name__)

# Minimum length (after strip) for a fragment to count as a real sub-query.
# Filters out stray connectives / punctuation left behind by the split.
_MIN_FRAGMENT_LEN = 3

#: A term appearing in this fraction of the corpus or more is UBIQUITOUS and
#: carries no discriminative signal, so it is stripped from the sub-query.
#:
#: The corpus-DEPENDENT half of the filter (``STOPWORDS`` is the
#: corpus-independent half). Measured on LoCoMo conv-26, over the exact lexical
#: strings the BM25/lexical lanes score: the two speakers who appear in all 19
#: sessions are ``caroline`` at DF ratio 0.577 and ``melanie`` at 0.336, while
#: the topic terms that actually separate one session from another sit far
#: below — ``books`` 0.012, ``paint`` 0.012, ``pets`` 0.014, ``hike`` 0.017,
#: ``pottery`` 0.075, ``lgbtq`` 0.157. 0.30 is the gap between those two
#: populations. Both halves of the filter earn their place: the stoplist alone
#: scores 47.2% pooled multi-hop ALL-gold@10, the DF rule alone 48.9%, both
#: together 50.4%.
#:
#: MEDIUM confidence that 0.30 transfers off LoCoMo — mitigated by the fact
#: that a parameter-free "drop the single most frequent content term" rule
#: scored identically (50.4%) on the same sweep, so the constant is not
#: carrying the result on its own.
DEFAULT_UBIQUITY_DF_RATIO = 0.30

#: Shortest token that can be a content word. This is grounding.py:129's
#: ``len(t) > 2`` restated, and it is what covers the function words STOPWORDS
#: deliberately leaves out ("of", "in", "to", …) rather than a second list.
_MIN_CONTENT_LEN = 3

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


def discriminative_subquery(
    query: str,
    *,
    doc_freq: Mapping[str, int],
    n_docs: int,
    ubiquity_df_ratio: float = DEFAULT_UBIQUITY_DF_RATIO,
) -> str:
    """``query`` with its corpus-ubiquitous and function words removed.

    This is NOT :func:`decompose_query` and must not be confused with it. That
    function splits a question into CLAUSES, which is the right move for a
    conjunctive question ("how do I run the seeder *and* why does it fail?")
    and the wrong one here: LoCoMo multi-hop questions are not conjunctive,
    they are aggregation questions with one atomic clause whose ANSWER spans
    sessions. Measured on conv-26, the clause split fires on 2 of 32 multi-hop
    questions and both times wrongly ("What types of pottery have Melanie and
    her kids made?" splits into ``['What types of pottery have Melanie',
    'her kids made']``), and round-robin merging those fragments moves
    multi-hop ALL-gold@10 46.9% -> 43.8%. Reach for that function for a
    genuinely multi-clause query; reach for this one when the query is atomic
    and one of its terms is drowning the rest.

    The mechanism this removes: on a corpus where the same two people speak in
    every session, the person's name is in most documents, so the ranking is
    driven by it rather than by the rare topic term that actually names the
    session the answer is in. Stripping both classes of ubiquitous token leaves
    a sub-query the lanes can only satisfy with the topic.

    ``doc_freq`` maps token -> number of documents containing it, counted over
    the SAME strings the lanes score (see
    :func:`~tesserae.retrieval.hybrid._lexical_texts`), and ``n_docs`` is how
    many documents that was.

    Returns ``""`` — the caller's signal to run ONE pass and not two — when the
    filter kept nothing, or when it kept EVERYTHING (the sub-query would then
    equal the query and the second search would be pure waste). It never
    returns the query itself.

    Pure, deterministic, no clock, no RNG, no LLM; never raises. Duplicate
    content tokens are kept in order, because a repeated term is a repeated
    term to BM25 and dropping the repeat would change the query's term
    statistics.
    """
    stripped = (query or "").strip()
    if not stripped or n_docs <= 0:
        return ""
    tokens = _tokenize(stripped)
    if not tokens:
        return ""
    ceiling = ubiquity_df_ratio * float(n_docs)
    kept = [
        t for t in tokens
        if t not in STOPWORDS
        and len(t) >= _MIN_CONTENT_LEN
        and float(doc_freq.get(t, 0)) <= ceiling
    ]
    if not kept or len(kept) == len(tokens):
        return ""
    return " ".join(kept)
