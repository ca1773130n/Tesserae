"""LLM JSON-completion client used by the session graph extractor.

The existing :mod:`tesserae.llm_synthesis` module is markdown-prose
oriented — it validates citations, enforces a citation-density floor,
and returns a `LlmSynthesisResponse` whose `body` is a markdown string.
Calling it directly from a JSON-extracting consumer (the session
graph LLM pass) would fail because the response contract is
incompatible.

This module carves out a small, JSON-specific interface that:

* lazy-imports ``anthropic`` (same pattern as ``llm_synthesis``);
* mirrors the retry-on-rate-limit logic;
* asks Claude for JSON-only output via a sharply worded system message
  + an optional `{`-prefill on the assistant turn so the model commits
  to JSON;
* parses the response with tolerance — strips ```json``` fences,
  drops trailing-comma artefacts, returns `None` on unrecoverable
  parse error;
* exposes a `set_client_factory()` test hook so unit tests can inject
  canned responses without monkey-patching the SDK.

It does not try to do "JSON mode" — Anthropic doesn't have a native
toggle for that. Prompt + prefill + tolerant parse is the standard
recipe.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, List, Optional, Protocol, Union

logger = logging.getLogger(__name__)

# Test-only client factory hook. Mirrors the pattern in
# :mod:`tesserae.llm_synthesis`. When set, ``AnthropicLLMJsonClient``
# calls this instead of importing the real Anthropic SDK.
_CLIENT_FACTORY: Optional[Callable[..., Any]] = None


def set_client_factory(factory: Optional[Callable[..., Any]]) -> None:
    """Inject a fake Anthropic client for tests."""
    global _CLIENT_FACTORY
    _CLIENT_FACTORY = factory


class LLMJsonClient(Protocol):
    """Returns parsed JSON from an LLM call, or None on any failure.

    Implementations must be safe to call concurrently across threads
    only if the underlying SDK is — :class:`AnthropicLLMJsonClient`
    uses a single ``anthropic.Anthropic`` instance which is documented
    as thread-safe.
    """

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        ...


# ---------------------------------------------------------------------------
# Anthropic implementation
# ---------------------------------------------------------------------------


class AnthropicLLMJsonClient:
    """LLMJsonClient backed by ``anthropic.Anthropic``."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.max_tokens = int(max_tokens)
        self._client: Any = None
        self._rate_limit_cls: Any = None
        self._status_cls: Any = None

        if _CLIENT_FACTORY is not None:
            # Test seam — used by unit tests to inject canned responses.
            self._client = _CLIENT_FACTORY(api_key=api_key, timeout=timeout)
            return

        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — exercised via env-gate
            raise RuntimeError(
                "anthropic SDK not installed; install tesserae[synthesis-llm]"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        try:
            self._rate_limit_cls = anthropic.RateLimitError
            self._status_cls = anthropic.APIStatusError
        except AttributeError:  # pragma: no cover
            self._rate_limit_cls = None
            self._status_cls = None

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        """Call the model and return parsed JSON; None on any unrecoverable error."""
        # Add a JSON-mode reminder to whatever system prompt the caller
        # supplied. Belt-and-suspenders even though the prompt should
        # already say "respond with JSON only".
        system_with_guard = (
            f"{system.strip()}\n\n"
            f"Respond with valid JSON only — no Markdown fences, no prose, "
            f"no trailing commas, no commentary. The response body must be "
            f"parseable by ``json.loads``. Schema name: {schema_name}."
        )

        messages: List[dict] = [
            {"role": "user", "content": user},
            # `{`-prefill commits Claude to a JSON object/array opener. The
            # model continues the assistant message starting from `{` (or
            # `[`) which dramatically reduces "Here's the JSON you asked
            # for:" preambles.
            {"role": "assistant", "content": "{"},
        ]

        # Anthropic prompt caching: a stable cache_key on the system block
        # lets second-and-subsequent calls reuse the cached prefix.
        if cache_key:
            system_block: Union[str, list] = [
                {
                    "type": "text",
                    "text": system_with_guard,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_block = system_with_guard

        attempt = 0
        while True:
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_block,
                    messages=messages,
                )
                break
            except Exception as exc:  # noqa: BLE001
                transient = False
                if self._rate_limit_cls is not None and isinstance(exc, self._rate_limit_cls):
                    transient = True
                elif self._status_cls is not None and isinstance(exc, self._status_cls):
                    transient = getattr(exc, "status_code", None) in {429, 529}
                if transient and attempt < max_retries:
                    delay = getattr(exc, "retry_after", None) or (2 ** attempt)
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.warning(
                    "AnthropicLLMJsonClient.complete_json failed (schema=%s): %s",
                    schema_name,
                    exc,
                )
                return None

        text = _extract_text(response)
        if not text:
            return None
        # The model started the assistant turn from `{`. Re-prepend so we
        # parse what the model "thinks" it wrote.
        if not text.lstrip().startswith(("{", "[")):
            text = "{" + text
        return parse_json_tolerant(text)


# ---------------------------------------------------------------------------
# Tolerant JSON parser
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def parse_json_tolerant(text: str) -> Optional[Union[dict, list]]:
    """Parse JSON allowing for common LLM-output quirks.

    Tries, in order:
      1. Raw ``json.loads`` on the input.
      2. Stripping markdown ```json…``` fences.
      3. Dropping trailing commas (``[1, 2,]`` → ``[1, 2]``).
      4. Walking forward from the first ``{`` or ``[`` and finding the
         matching closer — used when the model leaks prose around its
         JSON despite the prompt.

    Returns ``None`` when none of those parse paths recover a value.
    """
    if text is None:
        return None
    candidates: List[str] = []
    raw = text.strip()
    if not raw:
        return None
    candidates.append(raw)

    fenced = _FENCE_RE.match(raw)
    if fenced:
        candidates.append(fenced.group(1).strip())

    candidates.append(_TRAILING_COMMA_RE.sub(r"\1", raw))

    # Walk forward to find the first top-level brace/bracket and try to
    # parse from there. We only attempt this if the raw input has prose
    # before the opener — otherwise we'd re-parse what we already tried.
    first_brace = min(
        [i for i in (raw.find("{"), raw.find("[")) if i >= 0],
        default=-1,
    )
    if first_brace > 0:
        candidates.append(raw[first_brace:])

    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _extract_text(response: Any) -> str:
    """Pull the plain-text content out of an Anthropic Messages response."""
    blocks = getattr(response, "content", None) or []
    parts: List[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Claude CLI implementation (OAuth — preferred default, no API key needed)
# ---------------------------------------------------------------------------


class ClaudeCLIJsonClient:
    """LLMJsonClient backed by the ``claude`` CLI subprocess over OAuth.

    Mirrors the pattern in :mod:`tesserae.llm_extractor.run_claude_cli` so
    we reuse the same auth path the existing extractor uses: spawn
    ``claude -p`` with ``CLAUDE_CONFIG_DIR`` pointing at one of the
    configured multi-account dirs, write the prompt to stdin, read the
    response from stdout. No API key required — this is the canonical
    Tesserae default per README ("LLM-calling features default to the
    `codex` CLI over OAuth, so no API keys are required for the common
    path"; the same pattern applies to the Claude CLI).
    """

    def __init__(
        self,
        model: str = "sonnet",
        config_dirs: Optional[List[str]] = None,
        timeout: int = 180,
    ) -> None:
        import os as _os
        from pathlib import Path as _Path

        self.model = model
        # Honor CLAUDE_CONFIG_DIR if set; otherwise default to ~/.claude.
        if config_dirs:
            self.config_dirs = list(config_dirs)
        elif _os.environ.get("CLAUDE_CONFIG_DIR"):
            self.config_dirs = [_os.environ["CLAUDE_CONFIG_DIR"]]
        else:
            self.config_dirs = [str(_Path.home() / ".claude")]
        self.timeout = int(timeout)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        import os as _os
        import subprocess as _subprocess

        # Stitch system + user into a single prompt for the CLI's -p flag.
        # The CLI doesn't expose a separate system slot, so we prefix the
        # JSON-only contract to the user message.
        prompt = (
            f"{system.strip()}\n\n"
            f"Respond with valid JSON only — no Markdown fences, no prose, "
            f"no trailing commas. Schema name: {schema_name}.\n\n"
            f"{user}"
        )

        last_error: Optional[Exception] = None
        for config_dir in self.config_dirs:
            for attempt in range(max_retries + 1):
                try:
                    env = _os.environ.copy()
                    env["CLAUDE_CONFIG_DIR"] = config_dir
                    cmd = [
                        "claude",
                        "-p",
                        "--output-format", "text",
                        "--max-turns", "1",
                    ]
                    if self.model:
                        cmd.extend(["--model", self.model])
                    proc = _subprocess.run(
                        cmd,
                        input=prompt,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=self.timeout,
                        check=False,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"claude exited {proc.returncode}: "
                            f"{proc.stderr.strip() or proc.stdout.strip()}"
                        )
                    return parse_json_tolerant(proc.stdout)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    # Don't retry on the same config_dir; fall through to
                    # the next one. Auth/network issues are best handled
                    # by switching accounts, not by hammering one.
                    break
        if last_error is not None:
            logger.warning(
                "ClaudeCLIJsonClient.complete_json failed (schema=%s): %s",
                schema_name,
                last_error,
            )
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _claude_cli_available() -> bool:
    """Return True when the ``claude`` binary is on PATH AND at least one
    config dir looks credentialed (has ``settings.json`` or ``projects/``)."""
    import os as _os
    import shutil as _shutil
    from pathlib import Path as _Path

    if not _shutil.which("claude"):
        return False
    candidate = _os.environ.get("CLAUDE_CONFIG_DIR") or str(_Path.home() / ".claude")
    cdir = _Path(candidate)
    if not cdir.exists():
        return False
    return any(
        (cdir / marker).exists()
        for marker in ("settings.json", "settings.local.json", "projects", "history.jsonl")
    )


def build_default_json_client(model: Optional[str] = None) -> Optional[LLMJsonClient]:
    """Return the best-available JSON-completion client.

    Resolution order matches the README's "common path uses no API keys"
    promise:

    1. **Test factory** (``set_client_factory``) — for hermetic tests.
    2. **Claude CLI over OAuth** — preferred default. Requires only the
       ``claude`` binary on PATH plus a credentialed
       ``CLAUDE_CONFIG_DIR`` (defaults to ``~/.claude``). Zero API keys.
    3. **Anthropic SDK** with ``ANTHROPIC_API_KEY`` — fallback for
       environments where the CLI isn't available (CI runners, headless
       servers). Opt-in via the env var.
    4. ``None`` — caller falls back to the structural-only path.
    """
    import os

    # Test seam wins.
    if _CLIENT_FACTORY is not None:
        return AnthropicLLMJsonClient(model=model or "claude-sonnet-4-6")

    # Canonical: claude CLI over OAuth.
    if _claude_cli_available():
        return ClaudeCLIJsonClient(model=model or "sonnet")

    # Fallback: API key. Returns None if the anthropic SDK isn't installed
    # (e.g. base install without `tesserae[synthesis-llm]`) — that's a
    # silent no-op rather than a crash because the structural-only path
    # remains useful with zero LLM access.
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicLLMJsonClient(model=model or "claude-sonnet-4-6")
        except RuntimeError:
            return None

    return None
