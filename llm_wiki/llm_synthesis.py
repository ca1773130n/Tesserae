"""LLM-backed synthesis prose generation.

Optional, gated upgrade path for `SynthesisProjector`. The deterministic
heuristic templates remain the default ship; this module activates only when
``LLM_WIKI_SYNTHESIS_LLM=1`` is set, ``ANTHROPIC_API_KEY`` is non-empty, and
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
_SYSTEM_PREAMBLE = """\
You are the editorial synthesis voice of LLM-Wiki, a self-evolving research
notebook. Your job is to turn a structured set of graph facts into short,
high-signal markdown prose that reads like a senior research editor's digest.

# Hard rules (do not violate)

1. RESTATE, DO NOT INVENT. You may only summarize, group, contrast, and
   restate the facts present in the structured INPUTS section of the user
   message. You must not introduce claims, numbers, names, papers, results,
   organizations, or quotes that are not explicitly in INPUTS. If something
   is not in INPUTS, it does not exist.
2. CITATIONS REQUIRED. Every paragraph that names a node (a paper, repo,
   concept, topic, family, field, task, benchmark) MUST end with at least
   one bracket-citation pointing to that node's id, exactly as it appears in
   INPUTS. Format: ``[<node_id>]``. Multiple citations are fine: ``[a] [b]``.
   Lists/tables that simply enumerate node names do not each need a citation;
   one citation per paragraph that mentions them is enough.
3. STAY SHORT. The body should be 120-280 words for pulse, 80-200 for daily
   and weekly digests, 150-300 for topic / comparison / field overviews. Use
   2-4 paragraphs and at most one bulleted list when it adds clarity. Do not
   pad.
4. NO FRONTMATTER. Do not emit a YAML frontmatter block. Do not emit a
   leading H1 with the page title — the title is already rendered by the
   wiki shell.
5. NEUTRAL, EDITORIAL VOICE. No marketing copy, no "exciting", no exclamation
   marks, no first-person plural ("we") referring to the model.

# Output shape

Plain markdown body. Allowed: paragraphs, bold/italic for emphasis (sparing),
bullet lists, simple two-column tables for comparisons. Disallowed: code
fences, HTML, frontmatter, embedded JSON, footnotes.

# Page kinds and what they emphasize

- pulse: a global snapshot. Lead with what the wiki currently knows; then a
  paragraph on what is most active right now; close with one sentence of
  forward-looking observation drawn ONLY from INPUTS counts.
- daily_digest: what landed on a single ingest day. Group by source type
  (papers, repos, source documents). Mention the dominant concept threads
  that emerged.
- weekly: zoom out from the day-by-day. Surface the dominant approach
  families and the through-line across the week.
- topic: a research topic / approach family page. Lead with what the topic
  IS (drawn from its name + linked concepts), then the contributing papers
  and how they relate.
- comparison: contrast two approach families against a shared task /
  benchmark. Use a short two-column table OR two parallel paragraphs.
- field_overview: highest-level. Name the topics that anchor the field, then
  what concepts cut across them.

If INPUTS is sparse, write a shorter body — never invent material to fill
space. If INPUTS is empty for a section, say "No contributing nodes yet for
this section." and stop.
"""


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


# Logged-failure dedupe: one line per (kind, kind-of-error) pair per process.
_LOGGED_FAILURE_KINDS: set = set()


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED_FAILURE_KINDS:
        return
    _LOGGED_FAILURE_KINDS.add(key)
    print(f"[llm-wiki] {message}", file=sys.stderr)


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
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.dry_run = bool(dry_run)
        self._client: Any = None

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
                "anthropic SDK not installed; install llm-wiki[synthesis-llm]"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(self, req: LlmSynthesisRequest) -> Optional[LlmSynthesisResponse]:
        """Return an LLM-generated body, or ``None`` on any failure."""

        if req.kind not in _VALID_KINDS:
            _log_once(
                f"invalid-kind:{req.kind}",
                f"LLM synthesis skipped (unknown kind {req.kind!r})",
            )
            return None

        inputs_block = _format_inputs_section(req)
        cache_id = _hash_prompt(self.model, _SYSTEM_PREAMBLE, inputs_block)

        if self.dry_run:
            return self._dry_run_response(req, cache_id)

        return self._call_api(req, inputs_block, cache_id)

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
        inputs_block: str,
        cache_id: str,
    ) -> Optional[LlmSynthesisResponse]:
        # Build the request. The system block carries the long preamble with
        # ``cache_control: ephemeral`` — the second and subsequent pages in
        # this run hit the cache. The user message contains the page-specific
        # INPUTS block and is NOT cached (it changes every page).
        system_blocks = [
            {
                "type": "text",
                "text": _SYSTEM_PREAMBLE,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        user_message = (
            "Produce the markdown body for this synthesis page. Follow every "
            "rule in the system prompt. End paragraphs that name nodes with "
            "[<node_id>] citations exactly as they appear in INPUTS.\n\n"
            f"{inputs_block}"
        )

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_blocks,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:  # noqa: BLE001 — we want the safety net
            self._log_failure(req.kind, exc)
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

        citations = _extract_citations(body)
        if not citations:
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
    """Convenience: cheap pre-check for ``LLM_WIKI_SYNTHESIS_LLM``."""

    return llm_truthy(os.environ.get("LLM_WIKI_SYNTHESIS_LLM"))


def env_dry_run() -> bool:
    return llm_truthy(os.environ.get("LLM_WIKI_SYNTHESIS_DRY_RUN"))


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
