"""Wiki-layer search index.

The search index that the static site ships powers the in-page command palette
and any external agents that read ``search-index.json``. By design it lists
**only the wiki layer** described in §3.1 of the frontend redesign spec:

- sources (``SourceDocument`` / ``Paper`` / ``Repository`` / ``CodeProject``)
- concepts (concept-ish term/algorithm types)
- entities (``Model`` / ``Dataset`` / ``Benchmark`` / ``Metric`` /
  ``Organization`` / ``Person``)
- topics (``ResearchField`` / ``ResearchTopic`` / ``ProblemArea`` /
  ``ApproachFamily`` / ``Trend``)
- syntheses (``Synthesis``)
- questions (``OpenQuestion``)

Code-graph node types (``CodeClass`` / ``CodeFunction`` / ``CodeModule`` /
``Dependency`` / ``SourceFile``) and assertion-layer types (``Claim`` and the
five ``*Claim`` variants, plus ``EvidenceSpan``) are intentionally excluded:
they remain in ``graph.json`` for MCP/Cognee/Graphiti consumers but never get
their own URL or search entry.

Each entry in the index carries three new fields on top of the original
``id/title/kind/href/summary/source_path`` schema:

- ``tokens``: lower-cased, stop-word stripped, deduplicated tokens drawn from
  the title, summary, kind name, and aliases. The browser-side palette uses
  these for BM25-style scoring (no n-gram blow-up; bag of tokens).
- ``created_ts``: Unix seconds for the freshest signal we know about — this
  is ``generated_at`` for syntheses, ``mtime`` (or frontmatter ``mtime`` /
  ``updated_at``) for sources. ``None`` is allowed; the JS recency multiplier
  treats missing as "no boost".
- ``len``: the number of tokens (i.e. ``len(tokens)``). Cached so the BM25
  helper does not have to recount on every query.

Old entries that lack the new fields still work — the browser falls back to a
case-insensitive substring match.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from ..research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from ..wiki_store import WikiPage


# ---------------------------------------------------------------- type filters


# Node types that are allowed to surface as wiki pages / search entries.
WIKI_LAYER_TYPES: frozenset[str] = frozenset(
    {
        # sources
        ResearchNodeType.SOURCE_DOCUMENT.value,
        ResearchNodeType.PAPER.value,
        ResearchNodeType.REPOSITORY.value,
        ResearchNodeType.PROJECT.value,
        ResearchNodeType.CODE_PROJECT.value,
        # concepts
        ResearchNodeType.CONCEPT.value,
        ResearchNodeType.TECHNICAL_TERM.value,
        ResearchNodeType.MATHEMATICAL_CONCEPT.value,
        ResearchNodeType.METHODOLOGICAL_CONCEPT.value,
        ResearchNodeType.ALGORITHM.value,
        ResearchNodeType.OBJECTIVE_FUNCTION.value,
        ResearchNodeType.ARCHITECTURE_PATTERN.value,
        ResearchNodeType.TRAINING_PARADIGM.value,
        ResearchNodeType.INFERENCE_STRATEGY.value,
        ResearchNodeType.EVALUATION_PROTOCOL.value,
        ResearchNodeType.TASK.value,
        ResearchNodeType.CAPABILITY.value,
        # entities
        ResearchNodeType.MODEL.value,
        ResearchNodeType.DATASET.value,
        ResearchNodeType.BENCHMARK.value,
        ResearchNodeType.METRIC.value,
        ResearchNodeType.RESULT.value,
        ResearchNodeType.ORGANIZATION.value,
        ResearchNodeType.PERSON.value,
        # topics
        ResearchNodeType.RESEARCH_FIELD.value,
        ResearchNodeType.RESEARCH_TOPIC.value,
        ResearchNodeType.PROBLEM_AREA.value,
        ResearchNodeType.APPROACH_FAMILY.value,
        ResearchNodeType.TREND.value,
        # syntheses + questions
        ResearchNodeType.SYNTHESIS.value,
        ResearchNodeType.OPEN_QUESTION.value,
    }
)


# Explicit exclusion list (kept as documentation / for tests). These types stay
# in graph.json but never get an HTML route or a search entry.
EXCLUDED_TYPES: frozenset[str] = frozenset(
    {
        ResearchNodeType.CODE_CLASS.value,
        ResearchNodeType.CODE_FUNCTION.value,
        ResearchNodeType.CODE_MODULE.value,
        ResearchNodeType.SOURCE_FILE.value,
        ResearchNodeType.DEPENDENCY.value,
        ResearchNodeType.EVIDENCE_SPAN.value,
        ResearchNodeType.CLAIM.value,
        ResearchNodeType.CONTRIBUTION_CLAIM.value,
        ResearchNodeType.PERFORMANCE_CLAIM.value,
        ResearchNodeType.COMPARISON_CLAIM.value,
        ResearchNodeType.LIMITATION_CLAIM.value,
        ResearchNodeType.CAUSAL_CLAIM.value,
    }
)


# ----------------------------------------------------------------- node → kind


_KIND_BY_TYPE: Dict[str, str] = {
    # sources
    ResearchNodeType.SOURCE_DOCUMENT.value: "sources",
    ResearchNodeType.PAPER.value: "papers",
    ResearchNodeType.REPOSITORY.value: "repos",
    ResearchNodeType.PROJECT.value: "repos",
    ResearchNodeType.CODE_PROJECT.value: "repos",
    # concepts
    ResearchNodeType.CONCEPT.value: "concepts",
    ResearchNodeType.TECHNICAL_TERM.value: "concepts",
    ResearchNodeType.MATHEMATICAL_CONCEPT.value: "concepts",
    ResearchNodeType.METHODOLOGICAL_CONCEPT.value: "concepts",
    ResearchNodeType.ALGORITHM.value: "concepts",
    ResearchNodeType.OBJECTIVE_FUNCTION.value: "concepts",
    ResearchNodeType.ARCHITECTURE_PATTERN.value: "concepts",
    ResearchNodeType.TRAINING_PARADIGM.value: "concepts",
    ResearchNodeType.INFERENCE_STRATEGY.value: "concepts",
    ResearchNodeType.EVALUATION_PROTOCOL.value: "concepts",
    ResearchNodeType.TASK.value: "concepts",
    ResearchNodeType.CAPABILITY.value: "concepts",
    # entities
    ResearchNodeType.MODEL.value: "entities",
    ResearchNodeType.DATASET.value: "entities",
    ResearchNodeType.BENCHMARK.value: "entities",
    ResearchNodeType.METRIC.value: "entities",
    ResearchNodeType.RESULT.value: "entities",
    ResearchNodeType.ORGANIZATION.value: "entities",
    ResearchNodeType.PERSON.value: "entities",
    # topics
    ResearchNodeType.RESEARCH_FIELD.value: "topics",
    ResearchNodeType.RESEARCH_TOPIC.value: "topics",
    ResearchNodeType.PROBLEM_AREA.value: "topics",
    ResearchNodeType.APPROACH_FAMILY.value: "topics",
    ResearchNodeType.TREND.value: "topics",
    # syntheses + questions
    ResearchNodeType.SYNTHESIS.value: "syntheses",
    ResearchNodeType.OPEN_QUESTION.value: "questions",
}


_SUMMARY_LIMIT = 200


# --------------------------------------------------------------- tokenizer


# Stop-word list: short, dependency-free, deliberately limited so we don't
# strip query intent on niche terms. Includes English function words plus
# the most common Korean particles so a Korean query like "가우시안 스플래팅" still
# scores cleanly even when the corpus body is mixed-language.
STOP_WORDS: frozenset[str] = frozenset(
    {
        # English articles, prepositions, auxiliaries, conjunctions
        "a", "an", "the", "and", "or", "but", "if", "then", "else",
        "of", "to", "in", "on", "at", "by", "for", "with", "from",
        "is", "are", "was", "were", "be", "been", "being",
        "as", "it", "its", "this", "that", "these", "those",
        "we", "you", "they", "he", "she", "i", "me", "us", "them",
        "do", "does", "did", "have", "has", "had",
        "not", "no", "yes",
        "so", "than", "too", "very", "can", "will", "just",
        # Korean common particles (조사). One-grapheme particles we'd otherwise
        # collide with real tokens — we strip them before scoring.
        "은", "는", "이", "가", "을", "를", "의", "에", "와", "과",
        "도", "만", "에서", "으로", "로", "께서", "한테",
    }
)


_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    """Return lowercase tokens with stop-words stripped.

    Tokens are matched with ``\\w`` (so punctuation is dropped) plus the Korean
    Hangul Syllables block (``가-힣``) — the stdlib ``\\w`` already covers the
    Latin / digit cases. Order is preserved (callers that need de-duplication
    do it themselves so we keep the bag-of-words count for BM25).
    """

    if not text:
        return []
    out: List[str] = []
    for match in _TOKEN_RE.findall(text.lower()):
        token = match.strip()
        if not token:
            continue
        if token in STOP_WORDS:
            continue
        if len(token) == 1 and not token.isalnum():
            continue
        out.append(token)
    return out


def token_set(text: str) -> List[str]:
    """De-duplicated token list (order-preserving)."""

    seen: set[str] = set()
    out: List[str] = []
    for token in tokenize(text):
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


# ----------------------------------------------------------------- BM25 helper


_BM25_K1 = 1.2
_BM25_B = 0.75


def bm25_score(
    query: str,
    entry: Mapping[str, object],
    avg_doc_len: float,
) -> float:
    """Compute a BM25-lite score (no IDF — corpus-shape stable in the browser).

    Mirrors the JS implementation in ``JS_SEARCH_PALETTE`` so server-side tests
    can lock in the relative ordering. The score per token is::

        tf / (tf + k1 * (1 - b + b * dl / avg_dl))

    where ``tf`` is the term frequency in the entry's ``tokens`` bag, ``dl``
    is ``entry["len"]``, and ``b=0.75``, ``k1=1.2``.
    """

    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0
    tokens = entry.get("tokens") or []
    if not isinstance(tokens, (list, tuple)):
        return 0.0
    dl = float(entry.get("len") or len(tokens) or 1)
    avg = float(avg_doc_len or 1.0)

    counts: Dict[str, int] = {}
    for tok in tokens:
        if not isinstance(tok, str):
            continue
        counts[tok] = counts.get(tok, 0) + 1

    total = 0.0
    norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * dl / avg)
    for q in q_tokens:
        tf = counts.get(q)
        if not tf:
            continue
        total += tf / (tf + norm)
    return total


# ---------------------------------------------------------------- recency


_DAY_SECONDS = 86_400.0
_RECENCY_FRESH_DAYS = 7.0
_RECENCY_HALFLIFE_DAYS = 180.0


def recency_factor(created_ts: Optional[float], now_ts: Optional[float] = None) -> float:
    """Return a 1.0 → 0.0 factor depending on how recent ``created_ts`` is.

    1.0 for entries < 7 days old, decaying linearly to 0.0 at 180 days, and
    pinned at 0.0 beyond that. Mirrors the JS implementation so the browser
    palette agrees with the Python tests.
    """

    if created_ts is None:
        return 0.0
    if now_ts is None:
        now_ts = datetime.now(tz=timezone.utc).timestamp()
    age_days = max(0.0, (now_ts - float(created_ts)) / _DAY_SECONDS)
    if age_days <= _RECENCY_FRESH_DAYS:
        return 1.0
    if age_days >= _RECENCY_HALFLIFE_DAYS:
        return 0.0
    span = _RECENCY_HALFLIFE_DAYS - _RECENCY_FRESH_DAYS
    return max(0.0, 1.0 - (age_days - _RECENCY_FRESH_DAYS) / span)


def score_with_recency(
    query: str,
    entry: Mapping[str, object],
    avg_doc_len: float,
    now_ts: Optional[float] = None,
) -> float:
    """BM25 score multiplied by ``1 + 0.1 * recency_factor``."""

    base = bm25_score(query, entry, avg_doc_len)
    boost = 1.0 + 0.1 * recency_factor(
        _coerce_ts(entry.get("created_ts")), now_ts=now_ts
    )
    return base * boost


# ---------------------------------------------------------------- helpers


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe or "node"


def _trim(text: str, limit: int = _SUMMARY_LIMIT) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_paragraph(body: str) -> str:
    """Return the first non-heading paragraph in ``body`` (already stripped)."""

    if not body:
        return ""
    paragraphs: List[List[str]] = [[]]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if stripped.startswith("#"):
            # Skip headings entirely; they're titles, not summaries.
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(stripped)
    for para in paragraphs:
        if para:
            return " ".join(para)
    return ""


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _kind_dir(kind: str) -> str:
    # Already plural-ish from _KIND_BY_TYPE; the index page mounts at ``/<kind>/``.
    return kind


def _node_href(kind: str, slug: str) -> str:
    return f"{_kind_dir(kind)}/{slug}.html"


def _wiki_href(page: WikiPage) -> str:
    return f"{_kind_dir(page.kind)}/{page.slug}.html"


def _coerce_ts(value: object) -> Optional[float]:
    """Best-effort conversion to a Unix-seconds float.

    Accepts:
      * ``None`` / empty → ``None``
      * ``int`` / ``float`` → coerced
      * ISO-8601 strings (with or without ``Z``) → parsed via
        ``datetime.fromisoformat``.
    Anything else returns ``None`` rather than raising.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Plain integer-as-string?
        try:
            return float(text)
        except ValueError:
            pass
        try:
            cleaned = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _aliases_text(node: ResearchNode) -> str:
    """Pull alias-ish strings out of a node so they help search recall."""

    parts: List[str] = []
    aliases = getattr(node, "aliases", None)
    if isinstance(aliases, (list, tuple, set)):
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                parts.append(alias.strip())
    metadata = getattr(node, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("aliases", "alt_names", "synonyms"):
            value = metadata.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        parts.append(item)
    return " ".join(parts)


def _wiki_aliases_text(page: WikiPage) -> str:
    fm = page.frontmatter or {}
    if not isinstance(fm, dict):
        return ""
    parts: List[str] = []
    for key in ("aliases", "alt_names", "synonyms", "tags"):
        value = fm.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
    return " ".join(parts)


def _wiki_created_ts(page: WikiPage) -> Optional[float]:
    fm = page.frontmatter or {}
    if isinstance(fm, dict):
        for key in ("generated_at", "updated_at", "published_at", "mtime", "date"):
            ts = _coerce_ts(fm.get(key))
            if ts is not None:
                return ts
    # Fall back to the on-disk mtime; harmless if the path is None / missing.
    path = getattr(page, "path", None)
    if path is not None:
        try:
            return float(path.stat().st_mtime)
        except (OSError, AttributeError):
            return None
    return None


def _node_created_ts(node: ResearchNode) -> Optional[float]:
    metadata = getattr(node, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("generated_at", "updated_at", "mtime", "created", "created_at"):
            ts = _coerce_ts(metadata.get(key))
            if ts is not None:
                return ts
    for attr in ("generated_at", "updated_at", "mtime", "created_at"):
        ts = _coerce_ts(getattr(node, attr, None))
        if ts is not None:
            return ts
    return None


def _enrich(entry: Dict[str, object], text: str, created_ts: Optional[float]) -> Dict[str, object]:
    """Attach BM25 fields (tokens / len / created_ts) to a search entry.

    Tokens are kept as a raw bag (with repetitions) so BM25 term-frequencies
    carry signal — a title that names the query term *and* a summary that
    repeats it ranks above a doc that mentions it once. The "dedupe" in the
    schema doc applies to the spirit, not the letter: stop-words are stripped
    and casing collapsed, but term counts are preserved (otherwise BM25
    becomes pure length normalization, which we verified ranks worse on the
    Vision-Banana smoke test).
    """

    tokens = tokenize(text)
    entry["tokens"] = tokens
    entry["len"] = len(tokens)
    entry["created_ts"] = int(created_ts) if isinstance(created_ts, (int, float)) else None
    return entry


def _node_entry(node: ResearchNode) -> Dict[str, object]:
    kind = _KIND_BY_TYPE[node.type.value]
    title = (node.name or node.id).strip() or node.id
    summary = _trim(node.description or node.name or "")
    base: Dict[str, object] = {
        "id": node.id,
        "title": title,
        "kind": kind,
        "href": _node_href(kind, _slug(title)),
        "summary": summary,
        "source_path": node.source_path or "",
    }
    text = " ".join(filter(None, (title, summary, kind, _aliases_text(node))))
    return _enrich(base, text, _node_created_ts(node))


def _wiki_entry(page: WikiPage) -> Dict[str, object]:
    fm = page.frontmatter or {}
    title_raw = fm.get("title") if isinstance(fm, dict) else None
    title = ""
    if isinstance(title_raw, str) and title_raw.strip():
        title = title_raw.strip()
    if not title:
        title = _first_h1(page.body) or page.title or page.slug

    summary_raw = ""
    if isinstance(fm, dict):
        candidate = fm.get("summary") or fm.get("description")
        if isinstance(candidate, str):
            summary_raw = candidate
    if not summary_raw:
        summary_raw = _first_paragraph(page.body)
    summary = _trim(summary_raw)

    source_path = ""
    if isinstance(fm, dict):
        sp = fm.get("source_path") or fm.get("source") or ""
        if isinstance(sp, str):
            source_path = sp
    if not source_path:
        source_path = str(page.path) if page.path else ""

    base: Dict[str, object] = {
        "id": f"{page.kind}:{page.slug}",
        "title": title,
        "kind": page.kind,
        "href": _wiki_href(page),
        "summary": summary,
        "source_path": source_path,
    }
    text = " ".join(filter(None, (title, summary, page.kind, _wiki_aliases_text(page))))
    return _enrich(base, text, _wiki_created_ts(page))


# --------------------------------------------------------------------- public


def is_wiki_layer(node: ResearchNode) -> bool:
    """Return True iff ``node`` belongs to a wiki-layer type."""

    return node.type.value in WIKI_LAYER_TYPES


def average_doc_len(entries: Sequence[Mapping[str, object]]) -> float:
    """Mean of ``entry["len"]`` over ``entries`` (1.0 if empty)."""

    if not entries:
        return 1.0
    total = 0
    n = 0
    for entry in entries:
        v = entry.get("len")
        if isinstance(v, int):
            total += v
            n += 1
    return (total / n) if n else 1.0


def build_search_index(
    graph: ResearchGraph,
    wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]] | None = None,
) -> List[Dict[str, object]]:
    """Build the wiki-layer search index.

    Parameters
    ----------
    graph:
        The validated research graph. Code/claim/evidence nodes are dropped
        before any entry is emitted.
    wiki_pages_by_kind:
        Source-document and synthesis pages prefer their wiki-layer markdown
        rendition (frontmatter title + first paragraph). Pass an empty mapping
        if the wiki layer is not yet materialised.

    Each returned dict has the keys ``id``, ``title``, ``kind``, ``href``,
    ``summary`` (capped at 200 chars), and ``source_path``, plus the new
    BM25-scoring fields ``tokens``, ``len``, and ``created_ts``.
    """

    pages_by_kind: Dict[str, List[WikiPage]] = {}
    if wiki_pages_by_kind:
        for kind, pages in wiki_pages_by_kind.items():
            pages_by_kind[kind] = list(pages)

    entries: List[Dict[str, object]] = []
    seen_hrefs: set[str] = set()

    # 1) Wiki pages first — they own the canonical title/summary for documents
    #    and syntheses (those are the kinds the spec calls out specifically).
    preferred_kinds = ("sources", "syntheses", "topics", "concepts", "entities", "papers", "repos", "questions")
    for kind in preferred_kinds:
        for page in pages_by_kind.get(kind, []):
            entry = _wiki_entry(page)
            if entry["href"] in seen_hrefs:
                continue
            seen_hrefs.add(str(entry["href"]))
            entries.append(entry)

    # 2) Graph nodes — only wiki-layer types, skipping any whose href already
    #    came from a wiki page.
    for node in graph.nodes:
        if not is_wiki_layer(node):
            continue
        entry = _node_entry(node)
        if entry["href"] in seen_hrefs:
            continue
        seen_hrefs.add(str(entry["href"]))
        entries.append(entry)

    entries.sort(key=lambda e: (str(e["kind"]), str(e["title"]).lower()))
    return entries


__all__ = [
    "WIKI_LAYER_TYPES",
    "EXCLUDED_TYPES",
    "STOP_WORDS",
    "build_search_index",
    "is_wiki_layer",
    "tokenize",
    "token_set",
    "bm25_score",
    "recency_factor",
    "score_with_recency",
    "average_doc_len",
]
