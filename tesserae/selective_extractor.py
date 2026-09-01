"""Selective Claude enrichment for cost-aware extraction."""

from __future__ import annotations

import sys
import threading
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional, Protocol, Sequence

from .guidance_filter import apply_guidance_filter
from .research_graph import ResearchGraph


class ExtractorLike(Protocol):
    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph: ...
    def extract_text(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument") -> ResearchGraph: ...


class SelectiveClaudeResearchExtractor:
    """Route only selected documents through Claude, fallback to deterministic extraction.

    This keeps the whole corpus process cheap while allowing manual/targeted
    Claude enrichment for important papers or path subsets.
    """

    def __init__(
        self,
        deterministic: ExtractorLike,
        claude: ExtractorLike,
        include_patterns: Sequence[str],
        claude_limit: Optional[int] = None,
    ) -> None:
        self.deterministic = deterministic
        self.claude = claude
        self.include_patterns = list(include_patterns)
        self.claude_limit = claude_limit
        self.claude_calls = 0
        #: The LLM half's endpoint, so the batch runner can size its worker
        #: pool to it — the deterministic half makes no requests.
        self.llm_base_url: Optional[str] = getattr(claude, "llm_base_url", None)
        #: Guards the claude_limit check-and-claim. Concurrent extraction made
        #: the old read-then-increment lose the cap entirely: every worker read
        #: ``claude_calls`` before any of them wrote it, so a limit of 1 issued
        #: one call PER WORKER. This is a spend cap, so it fails closed.
        #: ponytail: one lock around a counter, not a semaphore — the critical
        #: section is an int compare, and the LLM call itself stays outside it.
        self._limit_lock = threading.Lock()
        #: Per-thread, because BatchIngestRunner may extract concurrently: the
        #: runner reads ``last_was_fallback`` right after its own extract_text
        #: call, so the flag must belong to THAT call and not to whichever
        #: worker happened to write it last. Sequential callers see identical
        #: behaviour (one thread, one slot).
        self._tls = threading.local()
        #: True when the LAST extract_* call was routed to the LLM and the LLM
        #: failed, so the result is the deterministic baseline rather than the
        #: typed extraction the caller asked for. ``BatchIngestRunner`` reads it
        #: (duck-typed) to mark the manifest entry ``fallback: true``, which
        #: ``compile --retry-fallbacks`` re-attempts. A doc that was never
        #: routed to the LLM is NOT a fallback — it got exactly what it asked
        #: for. Neither is an LLM call that legitimately returns nothing (an
        #: i18n duplicate extracts empty by design): only a raise counts, which
        #: covers all four observed failures — a killed wedged child
        #: (ExtractionTimeoutError), the provider never answering
        #: (ProviderUnavailableError), every account refusing the credentials
        #: (ProviderAuthError) and the model returning unusable JSON
        #: (GraphJSONValidationError). A provider outage MUST still land here
        #: so ``--retry-fallbacks`` can recover it.
        self._guidance = ""

    @property
    def last_was_fallback(self) -> bool:
        return bool(getattr(self._tls, "fallback", False))

    @last_was_fallback.setter
    def last_was_fallback(self, value: bool) -> None:
        self._tls.fallback = bool(value)

    @property
    def guidance(self) -> str:
        return getattr(self.claude, "guidance", "") or self._guidance

    @guidance.setter
    def guidance(self, value: str) -> None:
        # Forward extraction-feedback guidance to the Claude sub-extractor for
        # prompt-shaping. The deterministic baseline can't re-prompt, so we also
        # retain the guidance text here and apply it as a STRUCTURAL post-filter
        # (apply_guidance_filter) to the deterministic extract output.
        self._guidance = value or ""
        if hasattr(self.claude, "guidance"):
            self.claude.guidance = value

    def _guidance_bullets(self) -> list[str]:
        """Split the forwarded guidance string into individual bullet lines."""
        if not self._guidance:
            return []
        return [ln for ln in self._guidance.splitlines() if ln.strip()]

    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph:
        file_path = Path(path)
        self.last_was_fallback = False
        if self._claim_claude_slot(file_path):
            try:
                return self.claude.extract_file(file_path, source_kind=source_kind)
            except Exception as exc:
                self.last_was_fallback = True
                # Name the CAUSE, not just the class: ProviderUnavailableError
                # ("wait and re-run") and GraphJSONValidationError ("fix the
                # prompt/schema") used to render identically here. Also drop
                # the "claude" noun — this routes whatever backend is
                # configured, and reading a codex outage as a claude failure
                # was half of the original misdiagnosis.
                print(f"  selective: LLM extraction failed on {file_path} "
                      f"({type(exc).__name__}: {exc}); used deterministic", file=sys.stderr)
        result = self.deterministic.extract_file(file_path, source_kind=source_kind)
        # The deterministic baseline can't be re-prompted, so honor structural
        # guidance via a pure post-filter. No guidance => byte-identical no-op.
        return apply_guidance_filter(result, self._guidance_bullets())

    def extract_text(
        self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument"
    ) -> ResearchGraph:
        """Text counterpart to :meth:`extract_file` — the form the compile/ingest
        pipeline (``BatchIngestRunner``) actually calls. Routes by ``source_path``."""
        path = Path(source_path) if source_path else None
        self.last_was_fallback = False
        if path is not None and self._claim_claude_slot(path):
            try:
                return self.claude.extract_text(text, source_path, source_kind)
            except Exception as exc:
                # The LLM timed out / errored on this doc — fall back to the
                # deterministic baseline so one slow doc can't abort the whole
                # compile (the big design docs occasionally exceed the timeout).
                # The message is included so a provider outage
                # (ProviderUnavailableError) is distinguishable from a genuine
                # schema violation (GraphJSONValidationError) without grepping
                # the raw log for "Reconnecting…".
                self.last_was_fallback = True
                print(f"  selective: LLM extraction failed on {source_path or 'doc'} "
                      f"({type(exc).__name__}: {exc}); used deterministic", file=sys.stderr)
        result = self.deterministic.extract_text(text, source_path, source_kind)
        return apply_guidance_filter(result, self._guidance_bullets())

    def _claim_claude_slot(self, path: Path) -> bool:
        """Decide AND claim a ``claude_limit`` slot in one atomic step.

        Callers used to ask ``_should_use_claude`` and increment afterwards.
        That is a check-then-act race: under ``TESSERAE_EXTRACT_CONCURRENCY``
        every worker read the counter before any of them wrote it, so
        ``--llm-limit 1`` bought one call per worker instead of one call. The
        routing decision itself stays in ``_should_use_claude`` (pure, and
        still directly unit-tested); only the claim is serialized.
        """
        with self._limit_lock:
            if not self._should_use_claude(path):
                return False
            self.claude_calls += 1
            return True

    def _should_use_claude(self, path: Path) -> bool:
        if not self.include_patterns:
            return False
        if self.claude_limit is not None and self.claude_calls >= self.claude_limit:
            return False
        path_text = str(path)
        name = path.name
        # Sources reach us as ABSOLUTE paths but include patterns are usually
        # written relative ("docs/superpowers/**/*.md"), so also try the pattern
        # anchored anywhere in the path ("*/" + pattern) and the bare filename.
        return any(
            fnmatch(path_text, pat) or fnmatch(path_text, "*/" + pat) or fnmatch(name, pat)
            for pat in self.include_patterns
        )
