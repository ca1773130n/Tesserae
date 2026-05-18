"""LLM-backed synthesis prose generation.

Optional, gated upgrade path for `SynthesisProjector`. The deterministic
heuristic templates remain the default ship; this module activates only when
``TESSERAE_SYNTHESIS_LLM=1`` is set, ``ANTHROPIC_API_KEY`` is non-empty, and
the ``anthropic`` package can be imported. Any failure falls back per page so
a flaky network never blocks the compile.

Prompt caching is mandatory — the long stable preamble (style rules + ontology
recap) is wrapped in a ``cache_control: ephemeral`` block so every page after
the first hits the cache and is cheap. Variable inputs (the per-page facts)
sit after the cache breakpoint in the user message.

The module is import-safe even when ``anthropic`` is missing: the SDK import
is lazy, performed inside the constructor and per-call paths so simply
importing this module never raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


_VALID_KINDS = (
    "pulse",
    "daily_digest",
    "weekly",
    "topic",
    "comparison",
    "field_overview",
)


_NODE_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_\-:./]{3,})\]")


# A long, stable preamble. Keep this byte-stable across builds — changes here
# invalidate the prompt cache for every page in this run.
#
# This is the canonical system prompt for every synthesis kind. The block is
# wrapped in ``cache_control: ephemeral`` so every page after the first hits
# the cache. Per-kind shape lives in the user message so the cached prefix
# stays identical across pages.
_SYSTEM_PREAMBLE = """\
You are an Tesserae synthesis writer. Your job is to summarize a controlled
knowledge graph into a single Markdown page. Rules you follow ABSOLUTELY:

  RULE 1 — DO NOT INVENT FACTS. Restate or summarize ONLY material you find
  in the inputs. If a fact would require knowledge outside the inputs, omit
  it. No analogies to outside work. No author opinions. No predictions.

  RULE 2 — CITE EVERY CLAIM. Every paragraph that names a node MUST end
  with one or more citation markers in square brackets, where the bracket
  body is the node's id (e.g. ``[Paper:arxiv-2604.20329:abcd1234]``).
  Multiple citations: ``[id1] [id2]``. A response with zero citations is
  invalid and will be discarded — fall through to the heuristic body.

  RULE 3 — STAY ON TOPIC. The synthesis kind decides the shape:
    * pulse        : project-wide weekly snapshot. 5-9 sentences max.
    * daily_digest : one paragraph per noteworthy paper that day.
    * weekly       : 3 themes from the week, 1 paragraph each.
    * topic        : narrative about a research topic / approach family.
    * comparison   : one paragraph per family with shared task/benchmark.
    * field_overview: 1-2 paragraphs per linked sub-topic.

  RULE 4 — TONE. Direct, terse, technical. No marketing language. No
  "this exciting development" / "groundbreaking" / "paradigm-shift". Use
  past tense for what was claimed; present tense for what the wiki KNOWS.

  RULE 5 — FORMAT. Output is pure Markdown. No frontmatter. Start with
  one ``## <Section>`` heading; subsequent sections under ``## Section``.
  Inline code allowed. No HTML.

  RULE 6 — LANGUAGE. Match the dominant language of the input materials.
  If 80%+ of input titles/descriptions are in Korean, write in Korean.
  Otherwise English.

The current ontology is:
  Paper, Repository, Concept, Algorithm, Model, Dataset, Benchmark, Metric,
  Person, Organization, ResearchTopic, ApproachFamily, Synthesis, ...
A node id has the shape ``Type:slug:hash``.
"""


# Per-kind cap on inputs sent to the model. Above this, sample by degree
# descending so the page sees its highest-signal contributors and the prompt
# stays cheap.
_MAX_INPUTS = 25


# Plain-language shape descriptors for each kind. Purely informational —
# anchors the user message so the model knows which Rule-3 sub-bullet to
# apply to this page. Kept short so it doesn't dilute the inputs.
_KIND_SHAPE: Dict[str, str] = {
    "pulse": "project-wide snapshot, 5-9 sentences",
    "daily_digest": "one paragraph per noteworthy paper that day",
    "weekly": "three themes from the week, one paragraph each",
    "topic": "narrative about the named topic / approach family",
    "comparison": (
        "one paragraph per family with the shared task/benchmark, "
        "or a short two-column table"
    ),
    "field_overview": "1-2 paragraphs per linked sub-topic",
}


@dataclass(frozen=True)
class LlmSynthesisRequest:
    """One synthesis page's worth of structured inputs for the LLM."""

    kind: str
    title: str
    inputs: Sequence[Dict[str, Any]] = field(default_factory=tuple)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmSynthesisResponse:
    body: str
    citations: List[str]
    cache_id: str
    model: str


def _stable_json(value: Any) -> str:
    """Deterministic JSON dump — sorted keys, no whitespace surprises."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_prompt(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return "sha256-" + digest.hexdigest()


def _format_inputs_section(req: LlmSynthesisRequest) -> str:
    """Render the per-page INPUTS block as a deterministic JSON document.

    JSON keeps the structure obvious to the model and trivially diffable;
    `sort_keys=True` keeps the prompt-bytes hash stable across runs.
    """

    payload = {
        "kind": req.kind,
        "title": req.title,
        "context": req.context,
        "inputs": list(req.inputs),
    }
    return "INPUTS\n```json\n" + _stable_json(payload) + "\n```"


def _format_input_node_yaml(node: Dict[str, Any]) -> str:
    """One ``- id: ... / name: ... / type: ...`` block for the user message.

    Renders deterministically (keys in fixed order) so the user-message bytes
    stay stable across runs for the same INPUTS — useful for spot-checking
    diffs even though the user message is intentionally NOT prompt-cached.
    """

    lines: List[str] = []
    lines.append(f"  - id: {node.get('id', '')}")
    name = node.get("name")
    if name:
        lines.append(f"    name: {name}")
    ntype = node.get("type")
    if ntype:
        lines.append(f"    type: {ntype}")
    description = node.get("description")
    if description:
        lines.append(f"    description: {description}")
    metadata = node.get("metadata")
    if metadata:
        # Stable JSON keeps key order deterministic. Inline so a single line
        # fits per node and the YAML stays grep-friendly.
        lines.append(f"    metadata: {_stable_json(metadata)}")
    return "\n".join(lines)


def _build_user_message(req: LlmSynthesisRequest) -> str:
    """Per-kind, NOT cached. The model sees title, kind, inputs, and context.

    The structure mirrors the spec verbatim: a labelled header, an INPUTS
    block of YAML-ish entries, a CONTEXT block of graph-wide counts, and the
    EDITORIAL ANGLE — the heuristic body the model can use as a starting
    point so it never has to invent material to fill space. Then a closing
    instruction reiterating Rule 2.
    """

    ctx = dict(req.context or {})
    inputs_list = list(req.inputs or ())[:_MAX_INPUTS]

    shape = _KIND_SHAPE.get(req.kind, "")
    source_files = list(ctx.get("source_paths", []) or [])

    lines: List[str] = []
    lines.append(f"SYNTHESIS_KIND: {req.kind}")
    if shape:
        lines.append(f"SHAPE: {shape}")
    lines.append(f"TITLE: {req.title}")
    if source_files:
        lines.append(f"SOURCE_FILES: {_stable_json(source_files)}")
    else:
        lines.append("SOURCE_FILES: []")

    lines.append("")
    lines.append("INPUTS:")
    if inputs_list:
        for node in inputs_list:
            lines.append(_format_input_node_yaml(node))
    else:
        lines.append("  (no contributing nodes — produce a one-line "
                     "placeholder and stop)")

    lines.append("")
    lines.append("CONTEXT:")
    total_nodes = ctx.get("total_nodes")
    if total_nodes is not None:
        lines.append(f"  total nodes in graph: {total_nodes}")
    total_edges = ctx.get("total_edges")
    if total_edges is not None:
        lines.append(f"  total edges: {total_edges}")
    field_name = ctx.get("field")
    if field_name:
        lines.append(f"  field name: {field_name}")
    days = ctx.get("days") or []
    if days:
        lines.append(f"  contributing days/weeks: {', '.join(str(d) for d in days)}")
    site_title = ctx.get("site_title")
    if site_title:
        lines.append(f"  site title: {site_title}")
    summary = ctx.get("summary")
    if summary:
        lines.append(f"  page summary: {summary}")

    heuristic_body = ctx.get("heuristic_body")
    lines.append("")
    lines.append("EDITORIAL ANGLE (HEURISTIC FALLBACK BODY for the model to consult):")
    if heuristic_body:
        # Indent the heuristic body so it's clearly a quoted block in the
        # prompt. The model sees the same facts the deterministic projector
        # would have written — so it can rephrase / re-organize without
        # introducing new facts.
        body = str(heuristic_body).strip("\n")
        for ln in body.splitlines() or [""]:
            lines.append(f"  | {ln}")
    else:
        lines.append("  | (no heuristic body available)")

    lines.append("")
    lines.append(
        "Write the synthesis page now. Remember Rule 2 — every claim must "
        "be cited with the relevant node id in square brackets at the end "
        "of the sentence or paragraph."
    )
    return "\n".join(lines) + "\n"


def _build_prompt(req: LlmSynthesisRequest) -> Dict[str, Any]:
    """Assemble the full prompt payload for ``messages.create``.

    Returns a dict the call-site can hand straight to the SDK:

    - ``system``: list with the cached preamble block.
    - ``messages``: list with one user message carrying the per-kind shape.
    - ``user_text``: the user-message string, exposed for hashing/tests.

    Kept side-effect-free so tests can shape-check the prompt without
    spinning up a fake client.
    """

    user_text = _build_user_message(req)
    system_blocks = [
        {
            "type": "text",
            "text": _SYSTEM_PREAMBLE,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": user_text}]
    return {
        "system": system_blocks,
        "messages": messages,
        "user_text": user_text,
    }


def _strip_frontmatter(text: str) -> str:
    """Defence-in-depth: drop a leading ``---`` block if the model emits one."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :]).lstrip("\n")
    return text


def _strip_leading_h1(text: str, title: str) -> str:
    """If the model emitted a leading H1 echoing the title, drop it.

    Synthesis pages are rendered with a title shell already; an embedded H1
    duplicates the headline and inflates the body hash without adding info.
    """

    lines = text.lstrip().splitlines()
    if not lines:
        return text
    head = lines[0].strip()
    if head.startswith("# "):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _extract_citations(body: str) -> List[str]:
    """Pull bracket-citations out of the body, preserving order, deduped."""

    out: List[str] = []
    seen = set()
    for match in _NODE_CITATION_RE.finditer(body):
        token = match.group(1)
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


# Minimum body length below which we treat the response as malformed and
# fall back to the heuristic. The smallest legitimate body is a short pulse
# paragraph — well above this floor — so the threshold only catches
# pathological output (a single sentence, an apology, a stray newline).
_MIN_BODY_LENGTH = 80


def _validate_response(body: str) -> Optional[List[str]]:
    """Return parsed citations on success, ``None`` if the body is invalid.

    A valid body must:
    - be at least ``_MIN_BODY_LENGTH`` characters once stripped
    - contain at least one ``[node_id]`` citation marker

    On failure the caller logs once per kind/reason and falls back to the
    heuristic body for that page.
    """

    stripped = (body or "").strip()
    if len(stripped) < _MIN_BODY_LENGTH:
        return None
    citations = _extract_citations(body)
    if not citations:
        return None
    return citations


# Logged-failure dedupe: one line per (kind, kind-of-error) pair per process.
_LOGGED_FAILURE_KINDS: set = set()


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED_FAILURE_KINDS:
        return
    _LOGGED_FAILURE_KINDS.add(key)
    print(f"[tesserae] {message}", file=sys.stderr)


def reset_failure_log_for_tests() -> None:
    """Clear the dedupe set. Tests use this so each case sees a fresh log."""

    _LOGGED_FAILURE_KINDS.clear()


# Optional injection seam: tests stub a fake Anthropic client by setting this
# module-level factory. Production never sets it; the constructor falls back
# to importing and instantiating ``anthropic.Anthropic`` directly.
_CLIENT_FACTORY: Optional[Callable[..., Any]] = None


def set_client_factory(factory: Optional[Callable[..., Any]]) -> None:
    """Inject a client constructor (``factory(api_key=..., timeout=...)``).

    Used only by tests — production leaves this ``None``.
    """

    global _CLIENT_FACTORY
    _CLIENT_FACTORY = factory


class LlmSynthesizer:
    """Generates synthesis bodies via the Anthropic SDK with prompt caching.

    Construction does NOT make a network call — only client setup. The
    ``synthesize`` method handles per-page calls and returns ``None`` on any
    failure so the caller can fall back to the heuristic body.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        timeout: float = 20.0,
        dry_run: bool = False,
        *,
        max_tokens: int = 1200,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.dry_run = bool(dry_run)
        self.max_tokens = int(max_tokens)
        self._client: Any = None
        self._rate_limit_cls: Any = None
        self._status_cls: Any = None

        if self.dry_run:
            return

        # Lazy import — keeps this module safe to load when anthropic isn't
        # installed (e.g. base install with the heuristic-only path).
        if _CLIENT_FACTORY is not None:
            self._client = _CLIENT_FACTORY(api_key=api_key, timeout=timeout)
            return

        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised via env-gate
            raise RuntimeError(
                "anthropic SDK not installed; install tesserae[synthesis-llm]"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        try:
            self._rate_limit_cls = anthropic.RateLimitError
            self._status_cls = anthropic.APIStatusError
        except AttributeError:
            self._rate_limit_cls = None
            self._status_cls = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(self, req: LlmSynthesisRequest) -> Optional[LlmSynthesisResponse]:
        """Return an LLM-generated body, or ``None`` on any failure."""

        if req.kind not in _VALID_KINDS:
            self._log_failure(req.kind, ValueError(f"unknown kind {req.kind!r}"))
            return None

        # Build prompt up front so dry-run shape-checks match the real path.
        prompt = _build_prompt(req)
        cache_id = _hash_prompt(
            self.model,
            _SYSTEM_PREAMBLE,
            prompt["user_text"],
        )

        if self.dry_run:
            return self._dry_run_response(req, cache_id)

        return self._call_api(req, prompt, cache_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dry_run_response(
        self, req: LlmSynthesisRequest, cache_id: str
    ) -> LlmSynthesisResponse:
        # Shape-check the prompt: we still constructed inputs_block above, so
        # the assertion is implicit. Emit a stub body that names every input
        # node id with a citation marker so downstream parsing is exercised.
        ids = [str(item.get("id")) for item in req.inputs if item.get("id")]
        if not ids:
            ids = ["dry-run"]
        citation_run = " ".join(f"[{node_id}]" for node_id in ids[:8])
        body = (
            f"(dry-run preview, no API call)\n\n"
            f"Stub synthesis for *{req.title}*. Contributing nodes: {citation_run}.\n"
        )
        return LlmSynthesisResponse(
            body=body,
            citations=ids[:8],
            cache_id=cache_id,
            model=f"{self.model}-dry-run",
        )

    def _call_api(
        self,
        req: LlmSynthesisRequest,
        prompt: Dict[str, Any],
        cache_id: str,
    ) -> Optional[LlmSynthesisResponse]:
        # ``_build_prompt`` already wrapped the system block with
        # ``cache_control: ephemeral`` — second and subsequent pages in a
        # run hit the cache. The user message is per-kind and NOT cached.
        for attempt in range(3):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=prompt["system"],
                    messages=prompt["messages"],
                )
                break
            except Exception as exc:  # noqa: BLE001
                transient = False
                if self._rate_limit_cls is not None and isinstance(exc, self._rate_limit_cls):
                    transient = True
                elif self._status_cls is not None and isinstance(exc, self._status_cls):
                    transient = getattr(exc, "status_code", None) in {429, 529}
                if transient and attempt < 2:
                    delay = getattr(exc, "retry_after", None) or (2 ** attempt)
                    time.sleep(delay)
                    continue
                self._log_failure(req.kind, exc)
                return None
        else:
            return None

        body_text = self._extract_text(response)
        if not body_text or not body_text.strip():
            _log_once(
                f"empty-response:{req.kind}",
                f"LLM synthesis returned empty body for kind={req.kind}; "
                "falling back to heuristic.",
            )
            return None

        body = _strip_frontmatter(body_text).strip("\n")
        body = _strip_leading_h1(body, req.title)
        body = body.rstrip() + "\n"

        citations = _validate_response(body)
        if citations is None:
            stripped = body.strip()
            if len(stripped) < _MIN_BODY_LENGTH:
                _log_once(
                    f"short-response:{req.kind}",
                    f"LLM synthesis returned a body shorter than "
                    f"{_MIN_BODY_LENGTH} chars for kind={req.kind}; "
                    "falling back to heuristic.",
                )
            else:
                _log_once(
                    f"no-citations:{req.kind}",
                    f"LLM synthesis produced no [node_id] citations for kind="
                    f"{req.kind}; falling back to heuristic.",
                )
            return None

        model_id = getattr(response, "model", None) or self.model
        return LlmSynthesisResponse(
            body=body,
            citations=citations,
            cache_id=cache_id,
            model=str(model_id),
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Pull text out of a Messages API response, defensively.

        Real SDK responses expose ``response.content`` as a list of typed
        blocks with ``.type`` and ``.text``. Tests pass simpler stand-ins, so
        accept dicts as well.
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

    def _log_failure(self, kind: str, exc: BaseException) -> None:
        # Map known anthropic exception classes by name so we don't have to
        # import them eagerly (the SDK might be uninstalled in some tests).
        cls = type(exc).__name__
        _log_once(
            f"api-error:{kind}:{cls}",
            f"LLM synthesis failed for kind={kind} ({cls}); "
            "falling back to heuristic for this page.",
        )


def llm_truthy(value: Optional[str]) -> bool:
    """Match ``1``, ``true``, ``yes``, ``on`` (case-insensitive)."""

    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_enabled() -> bool:
    """Convenience: cheap pre-check for ``TESSERAE_SYNTHESIS_LLM``."""

    return llm_truthy(os.environ.get("TESSERAE_SYNTHESIS_LLM"))


def env_dry_run() -> bool:
    return llm_truthy(os.environ.get("TESSERAE_SYNTHESIS_DRY_RUN"))


__all__ = [
    "LlmSynthesisRequest",
    "LlmSynthesisResponse",
    "LlmSynthesizer",
    "env_dry_run",
    "env_enabled",
    "llm_truthy",
    "reset_failure_log_for_tests",
    "set_client_factory",
]


# Re-exported for tests; not part of the long-term API surface.
_BUILD_PROMPT = _build_prompt
_BUILD_USER_MESSAGE = _build_user_message
_VALIDATE_RESPONSE = _validate_response
