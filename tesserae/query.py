"""Wiki search and Q&A over the compiled ``.tesserae`` workspace.

Two modes:

* ``WikiQuery.search(question)`` — deterministic BM25 over
  ``.tesserae/site/search-index.json``. No I/O writes, no LLM.
* ``WikiQuery.answer(question, model=...)`` — same search, plus an Anthropic
  call gated behind ``TESSERAE_QUERY_LLM=1`` (or an explicit ``--llm`` flag
  passed by the CLI). On any failure the result degrades to search-only with
  ``used_llm=False`` and a populated ``fallback_reason``.

The LLM gate is intentionally separate from ``TESSERAE_SYNTHESIS_LLM`` so the
two surfaces (compile-time synthesis vs. interactive query) can be enabled
independently. ``TESSERAE_QUERY_DRY_RUN=1`` exercises the prompt builder
without actually calling the SDK — a fixed stub body comes back so tests stay
deterministic.

System message layout (mirrors :mod:`llm_synthesis`):

* one ``cache_control: ephemeral`` text block carrying the wiki overview, the
  ontology recap (built from :class:`ResearchNodeType`), and the citation
  rules. Stable across questions in a single REPL session — prompt caching
  pays for itself after the first turn.
* the user message has the question and the top-K page bodies, each clipped
  to 1000 chars and bracketed with
  ``<source kind="..." title="..." node_id="...">…</source>``.

Determinism contract: ``search()`` is pure given a fixed index. The dry-run
``answer()`` returns the same body for the same question + hits. Production
``answer()`` is naturally subject to the SDK, but we never invoke it in tests
— the ``set_client_factory`` test seam injects a fake client.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .ask_shape import SHAPE_GRAPH, SHAPE_LOOKUP, classify_ask_shape
from .citation_names import NODE_CITATION_RE, rewrite_citations
from .research_graph import ResearchNodeType
from .site.search import bm25_score, bm25_score_tokens, average_doc_len, tokenize


# ----------------------------------------------------------------- data shapes


@dataclass
class QueryHit:
    """One BM25-ranked page returned by :meth:`WikiQuery.search`."""

    title: str
    kind: str
    href: str
    score: float
    excerpt: str
    page_path: Optional[Path]
    node_id: Optional[str]
    arxiv_id: Optional[str] = None
    page_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "kind": self.kind,
            "href": self.href,
            "score": self.score,
            "excerpt": self.excerpt,
            "page_path": str(self.page_path) if self.page_path else None,
            "node_id": self.node_id,
            "arxiv_id": self.arxiv_id,
        }


@dataclass
class QueryResult:
    """The bundle returned by :meth:`WikiQuery.answer` (and CLI ``project query``)."""

    question: str
    hits: List[QueryHit]
    answer: Optional[str]
    model: Optional[str]
    used_llm: bool
    fallback_reason: Optional[str]

    #: Novel Grounded Evidence: the rare, source-attested vocabulary this
    #: answer added beyond what the question already contained, in idf nats.
    #: Higher is more grounded; near zero is the signature of a fluent
    #: restatement of the question, which is what a fabrication looks like once
    #: retrieval has already succeeded. ``None`` when there is no answer.
    #:
    #: **REPORTED, NEVER ENFORCED.** The product keeps answering; the number is
    #: surfaced so a caller can decide. Refusing below a threshold raised
    #: Youden J from +0.505 to +0.588 (honest leave-one-out) on a 352-question
    #: benchmark, but it also newly refused 14 of 284 answerable questions and
    #: cost 0.015 of token F1 — a trade a caller must opt into, not a default.
    #: Turning an eval-only behaviour into the product default is a change this
    #: repo has already reverted once.
    #:
    #: Distinct from ask_planner's older "grounding gate", which only asks
    #: whether a cited answer carries a citation at all.
    grounding: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "hits": [hit.to_dict() for hit in self.hits],
            "answer": self.answer,
            "model": self.model,
            "used_llm": self.used_llm,
            "fallback_reason": self.fallback_reason,
            "grounding": self.grounding,
        }


# ------------------------------------------------------------- prompt assembly


_SYSTEM_PREAMBLE_HEADER = """\
You are the librarian voice of Tesserae, a self-evolving research notebook.
You answer questions strictly from the compiled wiki sources provided in the
user message. You never invent papers, numbers, names, or claims.

# Hard rules

1. RESTATE, DO NOT INVENT. If the answer is not present in the supplied
   <source> blocks, say so plainly and stop. Do not guess.
2. CITE EVERY FACTUAL CLAIM. End sentences (or short clusters of sentences)
   that name a paper, repository, concept, model, dataset, benchmark,
   organization, or person with one or more bracket citations
   ``[<node_id>]`` taken verbatim from the ``node_id`` attribute on the
   relevant <source> tag. Multiple citations are allowed: ``[a] [b]``.
3. NEUTRAL VOICE. No marketing copy, no exclamation marks, no first-person
   plural. Plain markdown. No code fences or HTML.
4. STAY SHORT. 60-220 words is the target. Lead with the direct answer,
   then a single follow-up paragraph at most. A bulleted list is allowed
   when it improves clarity.
5. NO FRONTMATTER. Do not emit a YAML frontmatter block or a leading H1.

# Wiki overview
"""


#: The short-span preamble. Rules 1-3 and 5 are the house rules and stay; only
#: rule 4, the length and shape target, differs.
#:
#: This exists because Tesserae could not be benchmarked at all without it.
#: HotpotQA, LongMemEval and MemoryAgentBench all score exact match and token F1
#: over the WHOLE answer string against a one-phrase gold answer, so 60-220 words
#: of cited prose scores near zero however correct it is —
#: `evals/qa/scorer.py::ANSWER_SHAPES` says exactly that, and
#: `fairness_blockers` correctly refuses to publish a comparison across two
#: shapes. The default house style was therefore not a style preference; it was
#: an unstated decision that this system is unmeasurable next to any competitor.
#:
#: Citations are dropped here, and that is the point of the shape rather than an
#: oversight: a bracket citation inside a one-phrase answer is scored as answer
#: tokens and penalises the very metric this mode exists to be scored on. Callers
#: that need provenance want the default mode, which still carries it.
_SHORT_SPAN_PREAMBLE_HEADER = """\
You answer questions from the compiled wiki sources in the user message.

Answer with the shortest exact answer — a name, a date, a number, or a mechanism
phrase. No explanation, no full sentence, no citations, no markdown.
If the sources do not contain the answer, reply with exactly: I don't know
"""


_DEFAULT_OVERVIEW = """\
Tesserae ingests markdown notes (papers, repositories, daily research
digests, source documents) and projects them into a typed research
graph. The compiled ``.tesserae/`` workspace exposes the graph as a
static site, an MCP server, and a search index. Pages are organized by
kind: ``sources``, ``papers``, ``repos``, ``concepts``, ``entities``,
``topics``, ``syntheses``, and ``questions``.
"""


def _ontology_recap() -> str:
    """Render a short ontology paragraph from :class:`ResearchNodeType`.

    Grouped by layer so the model sees structure rather than a flat dump.
    Stable across runs (enum order is fixed) so the system block stays
    cache-friendly.
    """

    layers: Dict[str, List[str]] = {
        "Field / taxonomy": [
            ResearchNodeType.RESEARCH_FIELD.value,
            ResearchNodeType.RESEARCH_TOPIC.value,
            ResearchNodeType.PROBLEM_AREA.value,
            ResearchNodeType.APPROACH_FAMILY.value,
            ResearchNodeType.TREND.value,
        ],
        "Sources": [
            ResearchNodeType.SOURCE_DOCUMENT.value,
            ResearchNodeType.PAPER.value,
            ResearchNodeType.REPOSITORY.value,
            ResearchNodeType.CODE_PROJECT.value,
        ],
        "Entities": [
            ResearchNodeType.MODEL.value,
            ResearchNodeType.DATASET.value,
            ResearchNodeType.BENCHMARK.value,
            ResearchNodeType.METRIC.value,
            ResearchNodeType.RESULT.value,
            ResearchNodeType.ORGANIZATION.value,
            ResearchNodeType.PERSON.value,
        ],
        "Concepts": [
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
        ],
        "Synthesis / questions": [
            ResearchNodeType.SYNTHESIS.value,
            ResearchNodeType.OPEN_QUESTION.value,
        ],
    }
    lines = ["# Ontology recap", ""]
    for layer, names in layers.items():
        lines.append(f"- **{layer}**: " + ", ".join(names))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- log helpers


_LOGGED_FAILURE_KINDS: set[str] = set()


def _readable_label_from_id(node_id: str, raw: Mapping[str, object]) -> str:
    """Human label for an untitled node. '<Kind>: <slug>' from 'Kind:slug:hash'."""
    parts = node_id.split(":")
    if len(parts) >= 2 and parts[0]:
        return f"{parts[0]}: {parts[1].replace('-', ' ').strip()}".strip(": ")
    return node_id or "source"


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED_FAILURE_KINDS:
        return
    _LOGGED_FAILURE_KINDS.add(key)
    print(f"[tesserae] {message}", file=sys.stderr)


def reset_failure_log_for_tests() -> None:
    """Clear the dedupe set. Tests use this so each case sees a fresh log."""

    _LOGGED_FAILURE_KINDS.clear()


# ----------------------------------------------------------------- LLM gate


def llm_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_enabled() -> bool:
    """``True`` if ``TESSERAE_QUERY_LLM`` is set to a truthy value."""

    return llm_truthy(os.environ.get("TESSERAE_QUERY_LLM"))


def env_dry_run() -> bool:
    """``True`` if ``TESSERAE_QUERY_DRY_RUN`` is set to a truthy value."""

    return llm_truthy(os.environ.get("TESSERAE_QUERY_DRY_RUN"))


# Optional injection seam: tests stub a fake Anthropic client by setting this
# module-level factory. Production never sets it; production builds the
# client by importing ``anthropic`` and calling ``anthropic.Anthropic(...)``.
_CLIENT_FACTORY: Optional[Callable[..., Any]] = None


def set_client_factory(factory: Optional[Callable[..., Any]]) -> None:
    """Inject a client constructor (``factory(api_key=..., timeout=...)``).

    Used by tests only — production leaves this ``None``.
    """

    global _CLIENT_FACTORY
    _CLIENT_FACTORY = factory


# ----------------------------------------------------------------- WikiQuery


@dataclass(frozen=True)
class _IndexEntry:
    raw: Mapping[str, Any]
    tokens: List[str]
    length: int
    counts: Dict[str, int] = field(default_factory=dict)


class WikiQuery:
    """Search the compiled wiki and optionally call an LLM for a synthesized answer.

    ``search()`` reads only ``.tesserae/site/search-index.json`` and the
    ``wiki/<kind>/<slug>.md`` page bodies (lazily, for excerpts). It never
    writes to disk. ``answer()`` may emit one stderr log line on API failure.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        top_k: int = 8,
        kind_filter: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.wiki_root = self.project_root / ".tesserae"
        self.site_dir = self.wiki_root / "site"
        self.wiki_dir = self.wiki_root / "wiki"
        self.search_index_path = self.site_dir / "search-index.json"
        self.overview_path = self.wiki_dir / "overview.md"
        self.top_k = max(1, int(top_k))
        self.kind_filter = kind_filter or None
        self._entries: Optional[List[_IndexEntry]] = None
        self._index_mtime: Optional[float] = None
        self._avg_len: float = 1.0
        self._system_blocks_cache: Optional[List[Dict]] = None
        self._idf_cache: Optional[Tuple[Dict[str, float], int]] = None
        self._idf_mtime: Optional[float] = None
        self._client: Any = None
        self._client_api_key: Optional[str] = None

    # ------------------------------------------------------------------ search

    def _load_index(self) -> List[_IndexEntry]:
        if self._entries is not None:
            try:
                current_mtime = self.search_index_path.stat().st_mtime
            except OSError:
                current_mtime = None
            if current_mtime == self._index_mtime:
                return self._entries
            self._entries = None
        if not self.search_index_path.exists():
            self._entries = []
            self._avg_len = 1.0
            return self._entries
        try:
            raw = json.loads(self.search_index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            self._entries = []
            self._avg_len = 1.0
            return self._entries
        if not isinstance(raw, list):
            self._entries = []
            self._avg_len = 1.0
            return self._entries
        entries: List[_IndexEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tokens = item.get("tokens") or []
            if not isinstance(tokens, (list, tuple)):
                tokens = []
            length = item.get("len")
            if not isinstance(length, int):
                length = len(tokens)
            clean_tokens = [str(t) for t in tokens if isinstance(t, str)]
            entries.append(
                _IndexEntry(
                    raw=item,
                    tokens=clean_tokens,
                    length=int(length),
                    counts=dict(Counter(clean_tokens)),
                )
            )
        self._entries = entries
        self._avg_len = average_doc_len([e.raw for e in entries])
        try:
            self._index_mtime = self.search_index_path.stat().st_mtime
        except OSError:
            self._index_mtime = None
        return entries

    def _grounding_for(
        self, question: str, answer: Optional[str], hits: Sequence[QueryHit]
    ) -> Optional[float]:
        """:func:`grounding_score` with rarity measured over the whole index.

        Attestation is checked against the pages actually shown to the model;
        rarity is not, and must not be. A term's idf inside a five-page bundle
        says nothing about whether it is rare, so the corpus-wide document
        frequency comes from the search index — which already holds every
        page's token list, so this costs one pass at first use and nothing
        after. Cached against the index mtime, like the index itself.
        """
        if not answer:
            return None
        from .retrieval.grounding import idf_from_document_frequency

        entries = self._load_index()
        if self._idf_cache is None or self._idf_mtime != self._index_mtime:
            df: Counter = Counter()
            for entry in entries:
                df.update(set(entry.tokens))
            self._idf_cache = (
                idf_from_document_frequency(df, len(entries)),
                len(entries),
            )
            self._idf_mtime = self._index_mtime
        idf, n_docs = self._idf_cache
        return grounding_score(question, answer, hits, idf, n_docs)

    def search(self, question: str) -> List[QueryHit]:
        """BM25 over the search index, ``top_k`` highest-scoring entries.

        Deterministic for a fixed index. Returns an empty list when the index
        is missing or empty. Optional ``kind_filter`` narrows the result set
        (e.g. ``"papers"``, ``"concepts"``).
        """

        entries = self._load_index()
        if not entries:
            return []
        q_tokens = tokenize(question)
        scored: List[tuple[float, _IndexEntry]] = []
        for entry in entries:
            kind = str(entry.raw.get("kind") or "")
            if self.kind_filter and kind != self.kind_filter:
                continue
            score = bm25_score_tokens(q_tokens, entry.raw, self._avg_len, entry.counts)
            if score <= 0:
                continue
            scored.append((score, entry))
        # Sort by score desc, then by title asc as a stable tie-breaker so
        # repeated calls with the same question return the same ordering.
        scored.sort(key=lambda item: (-item[0], str(item[1].raw.get("title", ""))))
        hits: List[QueryHit] = []
        for score, entry in scored[: self.top_k]:
            hits.append(self._hit_for(entry, score))
        return hits

    def _hit_for(self, entry: _IndexEntry, score: float) -> QueryHit:
        raw = entry.raw
        kind = str(raw.get("kind") or "")
        raw_title = raw.get("title")
        if raw_title:
            title = str(raw_title)
        else:
            # Never surface the raw id as a display name. Prefer a readable
            # "<Kind>: <slug>" label derived from the node id.
            title = _readable_label_from_id(str(raw.get("id") or ""), raw)
        href = str(raw.get("href") or "")
        node_id_raw = raw.get("id")
        node_id = str(node_id_raw) if node_id_raw is not None else None

        page_path = self._page_path_for(raw)
        page_text: Optional[str] = None
        if page_path is not None:
            try:
                page_text = page_path.read_text(encoding="utf-8")
            except OSError:
                page_text = None
        excerpt = self._excerpt_for(page_path, fallback=str(raw.get("summary") or ""), text=page_text)
        arxiv = self._arxiv_for(raw, page_path, text=page_text)
        return QueryHit(
            title=title,
            kind=kind,
            href=href,
            score=float(score),
            excerpt=excerpt,
            page_path=page_path,
            node_id=node_id,
            arxiv_id=arxiv,
            page_text=page_text,
        )

    def _page_path_for(self, raw: Mapping[str, Any]) -> Optional[Path]:
        href = str(raw.get("href") or "")
        if not href.endswith(".html"):
            return None
        # ``href`` is ``<kind>/<slug>.html``. The corresponding markdown lives
        # at ``.tesserae/wiki/<kind>/<slug>.md`` — the same partition the
        # ``WikiLayerProjector`` writes into.
        rel = Path(href).with_suffix(".md")
        candidate = self.wiki_dir / rel
        if candidate.exists():
            return candidate
        return None

    def _excerpt_for(self, page_path: Optional[Path], *, fallback: str, text: Optional[str] = None) -> str:
        if page_path is None:
            return _trim(fallback, 200)
        if text is None:
            try:
                text = page_path.read_text(encoding="utf-8")
            except OSError:
                return _trim(fallback, 200)
        body = _strip_frontmatter(text)
        para = _first_paragraph(body)
        if not para:
            para = fallback
        return _trim(para, 200)

    def _arxiv_for(self, raw: Mapping[str, Any], page_path: Optional[Path], *, text: Optional[str] = None) -> Optional[str]:
        # Prefer a frontmatter ``arxiv_id`` if the page has one; otherwise
        # try the heuristic of ``papers:<id>`` slugs.
        if page_path is not None:
            if text is None:
                try:
                    text = page_path.read_text(encoding="utf-8")
                except OSError:
                    text = ""
            fm = _parse_frontmatter(text)
            arxiv = fm.get("arxiv_id") or fm.get("arxiv") or fm.get("arxiv_url")
            if isinstance(arxiv, str) and arxiv.strip():
                return arxiv.strip()
        # Heuristic: a Paper id often looks like ``Paper:2604.00538``.
        ident = str(raw.get("id") or "")
        if ident.startswith("Paper:"):
            tail = ident.split(":", 1)[1]
            if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", tail):
                return tail
        return None

    # ------------------------------------------------------------------ answer

    def answer(
        self,
        question: str,
        *,
        model: str = "claude-sonnet-4-6",
        force_llm: bool = False,
        force_no_llm: bool = False,
        api_key: Optional[str] = None,
        history: Optional[Sequence[Mapping[str, str]]] = None,
    ) -> QueryResult:
        """Run :meth:`search` and optionally synthesize an LLM answer.

        Gating mirrors :mod:`llm_synthesis`:

        * ``force_no_llm=True`` short-circuits to search-only.
        * Otherwise the LLM path requires either ``force_llm=True`` (CLI
          ``--llm``) or ``TESSERAE_QUERY_LLM=1``.
        * ``TESSERAE_QUERY_DRY_RUN=1`` returns a deterministic stub body
          without invoking the SDK — useful for tests and prompt review.

        Any failure (missing SDK, missing key, empty response, no citations,
        API exception) returns a ``QueryResult`` with ``used_llm=False`` and
        a populated ``fallback_reason``.
        """

        hits = self.search(question)

        if force_no_llm:
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="LLM disabled",
            )

        gate_ok = force_llm or env_enabled()
        if not gate_ok:
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="LLM disabled",
            )

        # Dry-run: build the prompt for shape-checking but never call the SDK.
        if env_dry_run():
            answer_body = _dry_run_body(question, hits)
            return QueryResult(
                question=question,
                hits=hits,
                answer=answer_body,
                model=model,
                used_llm=True,
                fallback_reason=None,
                # Scored here too, so the dry run exercises the same code the
                # real path does. The stub body is not a real answer, so the
                # NUMBER is meaningless — but a field that is only ever
                # populated on the path nobody can run in a test is a field
                # that silently rots.
                grounding=self._grounding_for(question, answer_body, hits),
            )

        # Resolve API key + client.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key and _CLIENT_FACTORY is None:
            # No API key: take the OAuth CLI path (claude/codex), rotating
            # across EVERY account on the machine so a rate-limited account
            # never blocks synthesis. This is Tesserae's common path — no
            # API key required.
            cli_result = self._answer_via_cli(question, hits, history)
            if cli_result is not None:
                return cli_result
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason=(
                    "no LLM backend (set ANTHROPIC_API_KEY, or log in to the "
                    "claude/codex CLI)"
                ),
            )

        client: Any
        if _CLIENT_FACTORY is not None:
            client = _CLIENT_FACTORY(api_key=key, timeout=30.0)
        elif self._client is not None and self._client_api_key == key:
            client = self._client
        else:
            try:
                import anthropic  # type: ignore[import-not-found]
            except ImportError:
                return QueryResult(
                    question=question,
                    hits=hits,
                    answer=None,
                    model=None,
                    used_llm=False,
                    fallback_reason="anthropic SDK not installed",
                )
            try:
                client = anthropic.Anthropic(api_key=key, timeout=30.0)
                self._client = client
                self._client_api_key = key
            except Exception as exc:  # noqa: BLE001 — we want a safety net
                _log_once(
                    f"client-init:{type(exc).__name__}",
                    f"LLM query client init failed ({type(exc).__name__}); "
                    "returning search-only result.",
                )
                return QueryResult(
                    question=question,
                    hits=hits,
                    answer=None,
                    model=None,
                    used_llm=False,
                    fallback_reason=f"client init failed: {type(exc).__name__}",
                )

        system_blocks = self._system_blocks()
        user_message = _build_user_message(question, hits)
        messages: List[Dict[str, Any]] = []
        if history:
            for turn in history:
                role = str(turn.get("role") or "")
                content = str(turn.get("content") or "")
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_blocks,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — we want a safety net
            cls = type(exc).__name__
            _log_once(
                f"api-error:{cls}",
                f"LLM query failed ({cls}); returning search-only result.",
            )
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason=f"API error: {cls}",
            )

        body_text = _extract_text(response)
        if not body_text or not body_text.strip():
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="model produced empty response",
            )

        if not NODE_CITATION_RE.search(body_text):
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="model produced no citations",
            )

        model_id = getattr(response, "model", None) or model
        # Rewrite [node_id] citations to the hit title AFTER the grounding
        # check above (names contain spaces and no longer match the regex).
        id_to_name = {h.node_id: h.title for h in hits if h.node_id and h.title}
        body_text = rewrite_citations(body_text, id_to_name)
        answer_text = body_text.strip() + "\n"
        return QueryResult(
            question=question,
            hits=hits,
            answer=answer_text,
            model=str(model_id),
            used_llm=True,
            fallback_reason=None,
            grounding=self._grounding_for(question, answer_text, hits),
        )

    def _answer_via_cli(
        self,
        question: str,
        hits: List[Any],
        history: Optional[List[Dict[str, Any]]],
    ) -> Optional["QueryResult"]:
        """Synthesize an answer via the claude/codex CLI over OAuth.

        Reuses the same rotating, no-API-key client the JSON extractors use
        (``build_rotating_client``), so synthesis works without
        ``ANTHROPIC_API_KEY`` and survives a rate-limited account by rotating
        to the next one on the machine. Returns None when no CLI backend is
        usable (caller then reports search-only); returns a search-only
        ``QueryResult`` when the model answered but without citations.
        """
        from .llm_json import build_rotating_client, project_llm_settings

        # Resolve against THIS project, not just env + the global config: a
        # project-level custom endpoint used to be visible to compile and
        # invisible here, so `query --llm` answered from a different backend.
        client = build_rotating_client(
            settings=project_llm_settings(getattr(self, "project_root", None)))
        if client is None:
            return None

        # Flatten the cache-control system blocks into one prompt string —
        # the CLI has no separate system slot.
        system_text = "\n\n".join(
            str(block.get("text", ""))
            for block in self._system_blocks()
            if isinstance(block, dict) and block.get("text")
        )
        user_message = _build_user_message(question, hits)
        if history:
            prior = "\n\n".join(
                f"{turn.get('role', '')}: {turn.get('content', '')}"
                for turn in history
                if turn.get("role") in {"user", "assistant"} and turn.get("content")
            )
            if prior:
                user_message = (
                    f"Earlier in this conversation:\n{prior}\n\n{user_message}"
                )

        try:
            body_text = client.complete_text(system=system_text, user=user_message)
        except Exception as exc:  # noqa: BLE001 — search-only is the safety net
            _log_once(
                f"cli-synth:{type(exc).__name__}",
                f"CLI synthesis failed ({type(exc).__name__}); search-only result.",
            )
            return None

        if not body_text or not body_text.strip():
            return None
        if not NODE_CITATION_RE.search(body_text):
            # Ungrounded prose — drop to search-only, same as the SDK path.
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="model produced no citations",
            )
        # Rewrite [node_id] citations to the hit title AFTER the grounding
        # check above (names contain spaces and no longer match the regex).
        id_to_name = {h.node_id: h.title for h in hits if h.node_id and h.title}
        body_text = rewrite_citations(body_text, id_to_name)
        answer_text = body_text.strip() + "\n"
        return QueryResult(
            question=question,
            hits=hits,
            answer=answer_text,
            model="cli-oauth",
            used_llm=True,
            fallback_reason=None,
            grounding=self._grounding_for(question, answer_text, hits),
        )

    # --------------------------------------------------------- prompt helpers

    def _system_blocks(self) -> List[Dict[str, Any]]:
        # Keyed on the style: the cache is per-instance, and an instance whose
        # style is switched between calls would otherwise serve the previous
        # style's preamble and silently answer in the wrong shape.
        style = getattr(self, "answer_style", "prose-cited")
        if (self._system_blocks_cache is not None
                and getattr(self, "_system_blocks_style", None) == style):
            return self._system_blocks_cache
        self._system_blocks_style = style
        overview = _DEFAULT_OVERVIEW
        if self.overview_path.exists():
            try:
                text = self.overview_path.read_text(encoding="utf-8")
                if text.strip():
                    overview = text.strip() + "\n"
            except OSError:
                pass
        if getattr(self, "answer_style", "prose-cited") == "short-span":
            # LEAN ON PURPOSE. The overview and the 40-type ontology recap exist
            # to help the model write grounded CITED PROSE; a one-phrase answer
            # needs neither, and they cost 1.4k characters of framing.
            #
            # Measured, and this is the reason the mode exists at all: with the
            # full packaging the short-span prompt ran 1,688 chars against the
            # retrieval baseline's 391, and Tesserae REFUSED 59.9% of answerable
            # questions where that baseline refused 6.3% — while 93% of those
            # refusals had a gold document sitting in the bundle. The bare model
            # with NO documents refused only 18.3%, so this was never a
            # retrieval failure: the instruction was converting present evidence
            # into "I don't know".
            text = _SHORT_SPAN_PREAMBLE_HEADER
        else:
            text = _SYSTEM_PREAMBLE_HEADER + overview + "\n" + _ontology_recap()
        self._system_blocks_cache = [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return self._system_blocks_cache


# ---------------------------------------------------------- prompt formatting


_SOURCE_BODY_LIMIT = 1000


def _source_bodies(hits: Sequence[QueryHit]) -> List[str]:
    """The source text, per hit, exactly as it is pasted into the prompt.

    Factored out of :func:`_build_user_message` so the grounding score can be
    computed against the bundle the model actually READ. Scoring against a
    reconstruction — a BM25 re-retrieval, the excerpts alone — measures a
    different thing than the model saw, which is the whole failure mode the
    number exists to detect.
    """
    bodies: List[str] = []
    for hit in hits:
        body = ""
        if hit.page_text is not None:
            body = _strip_frontmatter(hit.page_text).strip()
        elif hit.page_path is not None:
            try:
                raw = hit.page_path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            body = _strip_frontmatter(raw).strip()
        if not body:
            body = hit.excerpt
        bodies.append(body[:_SOURCE_BODY_LIMIT])
    return bodies


def grounding_score(
    question: str,
    answer: Optional[str],
    hits: Sequence[QueryHit],
    idf: Optional[Dict[str, float]] = None,
    n_docs: int = 0,
) -> Optional[float]:
    """Novel Grounded Evidence for an answer, over the bundle it was read from.

    Rare, source-attested vocabulary the answer adds that the question did not
    already carry. See :mod:`tesserae.retrieval.grounding` for why the naive
    version of this (extractive support, no question subtraction) measures
    nothing: retrieval selected the sources BY the question, so restating the
    question scores as fully supported, and fabrications restate the question
    more than correct answers do.

    ``idf``/``n_docs`` should come from the WHOLE corpus — see
    :func:`~tesserae.retrieval.grounding.idf_from_document_frequency`. Omitting
    them falls back to the shown bundle, which is defensible only when the
    bundle IS the corpus; a ten-page bundle caps idf near 1.9 and flattens the
    score. :meth:`WikiQuery._grounding_for` supplies the index-wide table.

    ``None`` when there is no answer to score. **Reported, never enforced** —
    see :attr:`QueryResult.grounding`.
    """
    if not answer:
        return None
    from .retrieval.grounding import corpus_idf, novel_grounded_evidence

    bodies = _source_bodies(hits)
    if idf is None or n_docs <= 0:
        idf, n_docs = corpus_idf(bodies)
    return novel_grounded_evidence(answer, question, bodies, idf, n_docs)


def _build_user_message(question: str, hits: Sequence[QueryHit]) -> str:
    """Assemble the per-question user message.

    The question goes first so the model sees the task before the corpus.
    Each <source> block carries the page kind, title, and node_id (which the
    model is expected to echo back as ``[node_id]`` citations) plus the page
    body clipped to 1000 chars.
    """

    parts: List[str] = [
        "Answer the following question strictly from the supplied wiki "
        "sources. Cite every factual claim with [<node_id>] using the "
        "node_id attribute on each <source>.",
        "",
        f"QUESTION: {question.strip()}",
        "",
    ]
    if not hits:
        parts.append("(no matching sources)")
        return "\n".join(parts)
    for hit, body in zip(hits, _source_bodies(hits)):
        node_id = hit.node_id or ""
        parts.append(
            f'<source kind="{_xml_escape(hit.kind)}" title="{_xml_escape(hit.title)}" node_id="{_xml_escape(node_id)}">'
        )
        parts.append(body)
        parts.append("</source>")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _dry_run_body(question: str, hits: Sequence[QueryHit]) -> str:
    """Stable stub for ``TESSERAE_QUERY_DRY_RUN=1`` mode.

    Echos the question and emits one [node_id] citation per hit so the
    citation-required gate is satisfied during shape-tests.
    """

    if not hits:
        return (
            "(dry-run preview, no API call)\n\n"
            f"No matching sources for '{question.strip()}'.\n"
        )
    citations = " ".join(f"[{hit.node_id or 'unknown'}]" for hit in hits[:8])
    return (
        "(dry-run preview, no API call)\n\n"
        f"Stub answer for '{question.strip()}'. Top sources: {citations}.\n"
    )


def _extract_text(response: Any) -> str:
    """Pull text out of an Anthropic Messages API response, defensively.

    Mirrors the helper in :mod:`llm_synthesis` so dict-shaped fakes (used by
    tests) and the real SDK both work.
    """

    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        return ""
    parts: List[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(block, dict):
            block_type = block.get("type")
        if block_type and block_type != "text":
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


# ------------------------------------------------------------- text utilities


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _strip_frontmatter(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :]).lstrip("\n")
    return text


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Tiny YAML-frontmatter parser for the keys we care about.

    Only supports ``key: value`` pairs in the leading ``---`` block — enough
    for ``arxiv_id`` lookups. Anything fancier we just ignore.
    """

    if not text:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: Dict[str, Any] = {}
    for idx in range(1, len(lines)):
        stripped = lines[idx].strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        out[key.strip()] = value.strip().strip("\"'")
    return out


def _first_paragraph(body: str) -> str:
    """First non-heading paragraph in ``body`` (already frontmatter-stripped)."""

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
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(stripped)
    for para in paragraphs:
        if para:
            return " ".join(para)
    return ""


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ----------------------------------------------------------------- ask dispatcher


ANSWER_STYLES = ("prose-cited", "short-span")


def ask_project(
    wiki: Any,
    question: str,
    *,
    backend: str = "auto",
    top_k: int = 5,
    use_llm: bool = True,
    no_llm: bool = False,
    route: str = "auto",
    answer_style: str = "prose-cited",
) -> Dict[str, Any]:
    """Run a question against the configured memory backends and return a JSON-serializable envelope.

    Shared by ``tesserae ask``, the new top-level ``tesserae ask``,
    and the MCP ``ask`` tool so all three call sites stay in lockstep.

    Dispatch:

    * ``backend="auto"`` (and ``"wiki"``) go straight to the compiled-wiki
      path — the KG planner when LLM synthesis is enabled, BM25 search
      otherwise. Auto never enters raganything.
    * Explicit ``backend="raganything"`` short-circuits to that
      backend and surfaces its errors instead of silently falling through.

    ``route`` picks HOW the wiki path answers: ``"auto"`` classifies the
    question's SHAPE (:func:`tesserae.ask_shape.classify_ask_shape`) and sends
    definitional lookups to BM25 instead of the KG planner; ``"graph"`` /
    ``"lookup"`` force one side. The decision is reported back on
    ``envelope["route"]`` on BOTH branches so a cheap answer is auditable.

    ``use_llm`` defaults to **True**: ask is the LLM-answer surface (the
    planner + synthesis run unless disabled). ``no_llm=True`` forces
    synthesis off on the wiki path — it beats both ``use_llm`` and
    ``TESSERAE_QUERY_LLM``, skips the planner, and pins ``wiki.query`` to
    search-only.

    Returns one of:

    * ``{"backend": "raganything", "question", "answer"}``
      (or ``{"backend": "raganything", "answer": None, "note": ...}`` when
      explicit raganything was requested but returned nothing)
    * ``{"backend": "wiki", "question", ...}`` (carries the full
      ``QueryResult.to_dict()`` payload merged with ``backend`` and ``question``)
    """

    if backend == "cognee":
        raise ValueError(
            "ask_project: the cognee backend was removed in 0.19 "
            "(demoted in 0.18, never fed the graph); use backend='auto', "
            "'wiki', or 'raganything'"
        )
    if backend not in {"auto", "raganything", "wiki"}:
        raise ValueError(f"ask_project: unknown backend {backend!r}")
    if route not in {"auto", "lookup", "graph"}:
        raise ValueError(f"ask_project: unknown route {route!r}")
    cleaned_question = (question or "").strip()
    if not cleaned_question:
        raise ValueError("ask_project: question is required")

    cfg = wiki.config()

    # ---- raganything path (explicit backend only; auto never enters) ----
    if backend == "raganything":
        raganything_cfg = (cfg.get("memory_backends") or {}).get("raganything") or {}
        # Resolve working_dir relative to the project root for portability.
        wd = raganything_cfg.get("working_dir")
        if wd and not Path(wd).is_absolute():
            raganything_cfg = {**raganything_cfg, "working_dir": str(wiki.project_root / wd)}
        if not raganything_cfg.get("enabled"):
            raganything_cfg = {**raganything_cfg, "enabled": True}
        from .raganything_query import query as _raganything_query

        try:
            answer = _raganything_query(cleaned_question, backend_config=raganything_cfg)
        except Exception as exc:
            raise RuntimeError(f"raganything ask failed: {exc}") from exc
        if answer is not None:
            return {
                "backend": "raganything",
                "question": cleaned_question,
                "answer": answer,
            }
        return {
            "backend": "raganything",
            "question": cleaned_question,
            "answer": None,
            "note": "no answer (likely missing API keys or empty index)",
        }

    # ---- wiki path ----
    # LLM gate: synthesize when the caller asked (use_llm) or the
    # TESSERAE_QUERY_LLM env is set; no_llm force-disables and beats both.
    want_llm = (use_llm or env_enabled()) and not no_llm
    # LLM path: the model PLANS retrieval over the KG (timeline, sessions,
    # activity, facts, wiki) before answering — plain BM25 cannot see dated
    # evidence, so "what happened recently?" is unanswerable without this.
    # Requires a compiled graph; dry-run keeps the deterministic classic path.
    graph_path = getattr(getattr(wiki, "paths", None), "graph", None)
    planner_available = want_llm and not env_dry_run() and graph_path is not None and graph_path.exists()

    # SHAPE routing. Resolved once, reported on both branches. ``forced`` means
    # the planner was already off (no LLM / no compiled graph / dry-run) and the
    # router never got a say — the envelope must not claim a decision it did not
    # make.
    if not planner_available:
        route_info = {
            "shape": SHAPE_LOOKUP,
            "reason": "planner unavailable (no-llm, dry-run, or no compiled graph)",
            "source": "forced",
        }
    elif route == "auto":
        shape = classify_ask_shape(cleaned_question)
        route_info = {"shape": shape.shape, "reason": shape.reason, "source": "heuristic"}
    else:
        route_info = {"shape": route, "reason": f"--route {route}", "source": "flag"}

    if planner_available and route_info["shape"] == SHAPE_GRAPH:
        from .ask_planner import plan_and_answer

        planned = plan_and_answer(wiki, cleaned_question, top_k=top_k,
                                  answer_style=answer_style)
        if planned is not None:
            planned["backend"] = "wiki"
            planned["question"] = cleaned_question
            planned["route"] = route_info
            return planned

    result = wiki.query(cleaned_question, top_k=top_k, use_llm=want_llm,
                        force_no_llm=no_llm, answer_style=answer_style)
    payload = result.to_dict()
    payload["backend"] = "wiki"
    payload["question"] = cleaned_question
    payload["route"] = route_info
    return payload


__all__ = [
    "QueryHit",
    "QueryResult",
    "WikiQuery",
    "ask_project",
    "env_enabled",
    "env_dry_run",
    "llm_truthy",
    "reset_failure_log_for_tests",
    "set_client_factory",
]
