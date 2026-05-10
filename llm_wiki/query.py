"""Wiki search and Q&A over the compiled ``.llm-wiki`` workspace.

Two modes:

* ``WikiQuery.search(question)`` — deterministic BM25 over
  ``.llm-wiki/site/search-index.json``. No I/O writes, no LLM.
* ``WikiQuery.answer(question, model=...)`` — same search, plus an Anthropic
  call gated behind ``LLM_WIKI_QUERY_LLM=1`` (or an explicit ``--llm`` flag
  passed by the CLI). On any failure the result degrades to search-only with
  ``used_llm=False`` and a populated ``fallback_reason``.

The LLM gate is intentionally separate from ``LLM_WIKI_SYNTHESIS_LLM`` so the
two surfaces (compile-time synthesis vs. interactive query) can be enabled
independently. ``LLM_WIKI_QUERY_DRY_RUN=1`` exercises the prompt builder
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .research_graph import ResearchNodeType
from .site.search import bm25_score, average_doc_len


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "hits": [hit.to_dict() for hit in self.hits],
            "answer": self.answer,
            "model": self.model,
            "used_llm": self.used_llm,
            "fallback_reason": self.fallback_reason,
        }


# ------------------------------------------------------------- prompt assembly


_SYSTEM_PREAMBLE_HEADER = """\
You are the librarian voice of LLM-Wiki, a self-evolving research notebook.
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


_DEFAULT_OVERVIEW = """\
LLM-Wiki ingests markdown notes (papers, repositories, daily research
digests, source documents) and projects them into a typed research
graph. The compiled ``.llm-wiki/`` workspace exposes the graph as a
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


_NODE_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_\-:./]{3,})\]")


# ---------------------------------------------------------------- log helpers


_LOGGED_FAILURE_KINDS: set[str] = set()


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED_FAILURE_KINDS:
        return
    _LOGGED_FAILURE_KINDS.add(key)
    print(f"[llm-wiki] {message}", file=sys.stderr)


def reset_failure_log_for_tests() -> None:
    """Clear the dedupe set. Tests use this so each case sees a fresh log."""

    _LOGGED_FAILURE_KINDS.clear()


# ----------------------------------------------------------------- LLM gate


def llm_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_enabled() -> bool:
    """``True`` if ``LLM_WIKI_QUERY_LLM`` is set to a truthy value."""

    return llm_truthy(os.environ.get("LLM_WIKI_QUERY_LLM"))


def env_dry_run() -> bool:
    """``True`` if ``LLM_WIKI_QUERY_DRY_RUN`` is set to a truthy value."""

    return llm_truthy(os.environ.get("LLM_WIKI_QUERY_DRY_RUN"))


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


class WikiQuery:
    """Search the compiled wiki and optionally call an LLM for a synthesized answer.

    ``search()`` reads only ``.llm-wiki/site/search-index.json`` and the
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
        self.wiki_root = self.project_root / ".llm-wiki"
        self.site_dir = self.wiki_root / "site"
        self.wiki_dir = self.wiki_root / "wiki"
        self.search_index_path = self.site_dir / "search-index.json"
        self.overview_path = self.wiki_dir / "overview.md"
        self.top_k = max(1, int(top_k))
        self.kind_filter = kind_filter or None
        self._entries: Optional[List[_IndexEntry]] = None
        self._avg_len: float = 1.0

    # ------------------------------------------------------------------ search

    def _load_index(self) -> List[_IndexEntry]:
        if self._entries is not None:
            return self._entries
        if not self.search_index_path.exists():
            self._entries = []
            self._avg_len = 1.0
            return self._entries
        raw = json.loads(self.search_index_path.read_text(encoding="utf-8"))
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
            entries.append(
                _IndexEntry(
                    raw=item,
                    tokens=[str(t) for t in tokens if isinstance(t, str)],
                    length=int(length),
                )
            )
        self._entries = entries
        self._avg_len = average_doc_len([e.raw for e in entries])
        return entries

    def search(self, question: str) -> List[QueryHit]:
        """BM25 over the search index, ``top_k`` highest-scoring entries.

        Deterministic for a fixed index. Returns an empty list when the index
        is missing or empty. Optional ``kind_filter`` narrows the result set
        (e.g. ``"papers"``, ``"concepts"``).
        """

        entries = self._load_index()
        if not entries:
            return []
        scored: List[tuple[float, _IndexEntry]] = []
        for entry in entries:
            kind = str(entry.raw.get("kind") or "")
            if self.kind_filter and kind != self.kind_filter:
                continue
            score = bm25_score(question, entry.raw, self._avg_len)
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
        title = str(raw.get("title") or raw.get("id") or "")
        href = str(raw.get("href") or "")
        node_id_raw = raw.get("id")
        node_id = str(node_id_raw) if node_id_raw is not None else None

        page_path = self._page_path_for(raw)
        excerpt = self._excerpt_for(page_path, fallback=str(raw.get("summary") or ""))
        arxiv = self._arxiv_for(raw, page_path)
        return QueryHit(
            title=title,
            kind=kind,
            href=href,
            score=float(score),
            excerpt=excerpt,
            page_path=page_path,
            node_id=node_id,
            arxiv_id=arxiv,
        )

    def _page_path_for(self, raw: Mapping[str, Any]) -> Optional[Path]:
        href = str(raw.get("href") or "")
        if not href.endswith(".html"):
            return None
        # ``href`` is ``<kind>/<slug>.html``. The corresponding markdown lives
        # at ``.llm-wiki/wiki/<kind>/<slug>.md`` — the same partition the
        # ``WikiLayerProjector`` writes into.
        rel = Path(href).with_suffix(".md")
        candidate = self.wiki_dir / rel
        if candidate.exists():
            return candidate
        return None

    def _excerpt_for(self, page_path: Optional[Path], *, fallback: str) -> str:
        if page_path is None:
            return _trim(fallback, 200)
        try:
            text = page_path.read_text(encoding="utf-8")
        except OSError:
            return _trim(fallback, 200)
        body = _strip_frontmatter(text)
        para = _first_paragraph(body)
        if not para:
            para = fallback
        return _trim(para, 200)

    def _arxiv_for(self, raw: Mapping[str, Any], page_path: Optional[Path]) -> Optional[str]:
        # Prefer a frontmatter ``arxiv_id`` if the page has one; otherwise
        # try the heuristic of ``papers:<id>`` slugs.
        if page_path is not None:
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
          ``--llm``) or ``LLM_WIKI_QUERY_LLM=1``.
        * ``LLM_WIKI_QUERY_DRY_RUN=1`` returns a deterministic stub body
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
            )

        # Resolve API key + client.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key and _CLIENT_FACTORY is None:
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="ANTHROPIC_API_KEY not set",
            )

        client: Any
        if _CLIENT_FACTORY is not None:
            client = _CLIENT_FACTORY(api_key=key, timeout=30.0)
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

        if not _NODE_CITATION_RE.search(body_text):
            return QueryResult(
                question=question,
                hits=hits,
                answer=None,
                model=None,
                used_llm=False,
                fallback_reason="model produced no citations",
            )

        model_id = getattr(response, "model", None) or model
        return QueryResult(
            question=question,
            hits=hits,
            answer=body_text.strip() + "\n",
            model=str(model_id),
            used_llm=True,
            fallback_reason=None,
        )

    # --------------------------------------------------------- prompt helpers

    def _system_blocks(self) -> List[Dict[str, Any]]:
        overview = _DEFAULT_OVERVIEW
        if self.overview_path.exists():
            try:
                text = self.overview_path.read_text(encoding="utf-8")
                if text.strip():
                    overview = text.strip() + "\n"
            except OSError:
                pass
        text = _SYSTEM_PREAMBLE_HEADER + overview + "\n" + _ontology_recap()
        return [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]


# ---------------------------------------------------------- prompt formatting


_SOURCE_BODY_LIMIT = 1000


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
    for hit in hits:
        body = ""
        if hit.page_path is not None:
            try:
                raw = hit.page_path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            body = _strip_frontmatter(raw).strip()
        if not body:
            body = hit.excerpt
        body = body[:_SOURCE_BODY_LIMIT]
        node_id = hit.node_id or ""
        parts.append(
            f'<source kind="{_xml_escape(hit.kind)}" title="{_xml_escape(hit.title)}" node_id="{_xml_escape(node_id)}">'
        )
        parts.append(body)
        parts.append("</source>")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _dry_run_body(question: str, hits: Sequence[QueryHit]) -> str:
    """Stable stub for ``LLM_WIKI_QUERY_DRY_RUN=1`` mode.

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


def ask_project(
    wiki: Any,
    question: str,
    *,
    backend: str = "auto",
    top_k: int = 5,
    cognee_search_type: Optional[str] = None,
    cognee_dataset: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a question against the configured memory backends and return a JSON-serializable envelope.

    Shared by ``llm_wiki project ask``, the new top-level ``llm_wiki ask``,
    and the MCP ``ask`` tool so all three call sites stay in lockstep.

    Dispatch order under ``backend="auto"``:

    1. raganything (when ``memory_backends.raganything.enabled``)
    2. cognee (when ``memory_backends.cognee.enabled`` per ``cognee_backend_config``)
    3. compiled-wiki BM25 search (always available)

    Explicit ``backend="raganything"|"cognee"|"wiki"`` short-circuits the
    selector and surfaces backend errors instead of silently falling through.

    Returns one of:

    * ``{"backend": "raganything", "question", "answer"}``
      (or ``{"backend": "raganything", "answer": None, "note": ...}`` when
      explicit raganything was requested but returned nothing)
    * ``{"backend": "cognee", "question", "dataset", "results"}``
    * ``{"backend": "wiki", "question", ...}`` (carries the full
      ``QueryResult.to_dict()`` payload merged with ``backend`` and ``question``)
    """

    from .project import cognee_backend_config

    if backend not in {"auto", "raganything", "cognee", "wiki"}:
        raise ValueError(f"ask_project: unknown backend {backend!r}")
    cleaned_question = (question or "").strip()
    if not cleaned_question:
        raise ValueError("ask_project: question is required")

    cfg = wiki.config()

    # ---- raganything path ----
    raganything_cfg = (cfg.get("memory_backends") or {}).get("raganything") or {}
    raganything_enabled = bool(raganything_cfg.get("enabled"))
    use_raganything = backend == "raganything" or (backend == "auto" and raganything_enabled)
    if use_raganything:
        # Resolve working_dir relative to the project root for portability.
        wd = raganything_cfg.get("working_dir")
        if wd and not Path(wd).is_absolute():
            raganything_cfg = {**raganything_cfg, "working_dir": str(wiki.project_root / wd)}
        if backend == "raganything" and not raganything_cfg.get("enabled"):
            raganything_cfg = {**raganything_cfg, "enabled": True}
        from .raganything_query import query as _raganything_query

        try:
            answer = _raganything_query(cleaned_question, backend_config=raganything_cfg)
        except Exception as exc:
            if backend == "raganything":
                raise RuntimeError(f"raganything ask failed: {exc}") from exc
            answer = None
        if answer is not None:
            return {
                "backend": "raganything",
                "question": cleaned_question,
                "answer": answer,
            }
        if backend == "raganything":
            return {
                "backend": "raganything",
                "question": cleaned_question,
                "answer": None,
                "note": "no answer (likely missing API keys or empty index)",
            }
        # auto: fall through

    # ---- cognee path ----
    cognee_cfg = cognee_backend_config(cfg)
    use_cognee = backend == "cognee" or (backend == "auto" and cognee_cfg.get("enabled", False))
    if use_cognee:
        from .cognee_query import search_cognee

        dataset = cognee_dataset or cognee_cfg.get("dataset")
        cognee_kwargs: Dict[str, Any] = {"dataset": dataset, "top_k": top_k}
        if cognee_search_type:
            cognee_kwargs["search_type"] = cognee_search_type
        try:
            results = search_cognee(cleaned_question, **cognee_kwargs)
        except Exception as exc:
            if backend == "cognee":
                raise RuntimeError(f"cognee ask failed: {exc}") from exc
            results = None
        if results is not None:
            return {
                "backend": "cognee",
                "dataset": dataset,
                "question": cleaned_question,
                "results": results,
            }

    # ---- wiki search fallback ----
    result = wiki.query(cleaned_question, top_k=top_k, use_llm=False)
    payload = result.to_dict()
    payload["backend"] = "wiki"
    payload["question"] = cleaned_question
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
