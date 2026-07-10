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
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Protocol, Sequence, Union

logger = logging.getLogger(__name__)


def _run_cli(
    cmd: Sequence[str], prompt: str, env: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess:
    """Run a CLI agent with a timeout that cannot wedge.

    ``subprocess.run(capture_output=True, timeout=...)`` kills only the direct
    child on timeout, then drains the output pipes with NO timeout. CLI agents
    like ``codex exec`` / ``claude -p`` spawn their own children which inherit
    those pipes, so the drain blocks until the orphaned grandchildren exit —
    observed as compiles sitting at 0% CPU for days. Run the child in its own
    process group and kill the whole group on timeout instead.
    """
    proc = subprocess.Popen(
        list(cmd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    return subprocess.CompletedProcess(list(cmd), proc.returncode, stdout, stderr)

# Test-only client factory hook. Mirrors the pattern in
# :mod:`tesserae.llm_synthesis`. When set, ``AnthropicLLMJsonClient``
# calls this instead of importing the real Anthropic SDK.
_CLIENT_FACTORY: Optional[Callable[..., Any]] = None

# One-shot guard so that a Claude-CLI "Not logged in" failure only
# emits a single user-facing warning per process. A compile typically
# issues many ``complete_json`` calls; without this guard every one of
# them would re-log the same "run `claude /login`" hint and drown the
# SessionEnd hook output.
_LOGGED_LOGIN_WARNING: bool = False


def _reset_login_warning_for_tests() -> None:
    """Reset the one-shot login warning flag. Test-only helper."""
    global _LOGGED_LOGIN_WARNING
    _LOGGED_LOGIN_WARNING = False


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

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        """Return free-text (prose) from an LLM call, or None on any failure.

        The prose counterpart to :meth:`complete_json`; used by synthesis
        callers (e.g. ``tesserae ask``) so they share the same OAuth /
        account-rotation path as the JSON extractors.
        """
        ...


def _configured_default_model(for_providers: Sequence[str]) -> Optional[str]:
    """The configured ``llm_model`` (env → global config), scoped by provider.

    Returns the resolved model only when the resolved provider is one of
    ``for_providers`` — so a claude-shaped ``llm_model`` configured for
    provider ``custom`` never lands on the Codex CLI when the availability
    chain falls through providers. Project-level ``llm_model`` reaches the
    clients through callers threading ``resolve_llm_client_settings(cfg)``
    into the explicit ``model`` argument, which always wins.
    """
    settings = resolve_llm_client_settings()
    if not settings.get("model"):
        return None
    provider = (settings.get("provider") or "claude").strip().lower()
    return settings["model"] if provider in for_providers else None


# ---------------------------------------------------------------------------
# Anthropic implementation
# ---------------------------------------------------------------------------


class AnthropicLLMJsonClient:
    """LLMJsonClient backed by ``anthropic.Anthropic``."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_tokens: int = 4096,
        base_url: Optional[str] = None,
    ) -> None:
        # Hardcoded literal is a last-resort fallback behind the configured
        # llm_model (env TESSERAE_LLM_MODEL → global config).
        self.model = (
            model
            or _configured_default_model(("anthropic", "custom"))
            or "claude-sonnet-4-6"
        )
        self.base_url = base_url
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

        client_kwargs: dict = {"api_key": api_key, "timeout": timeout}
        if base_url:
            # Only pass when set so the SDK's own default/env resolution
            # (ANTHROPIC_BASE_URL) still applies otherwise.
            client_kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**client_kwargs)
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

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        """Prose completion via the Anthropic SDK; None on unrecoverable error."""
        attempt = 0
        while True:
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system.strip(),
                    messages=[{"role": "user", "content": user}],
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
                logger.warning("AnthropicLLMJsonClient.complete_text failed: %s", exc)
                return None
        text = _extract_text(response)
        text = (text or "").strip()
        return text or None


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
        model: Optional[str] = None,
        config_dirs: Optional[List[str]] = None,
        timeout: int = 180,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        import os as _os
        from pathlib import Path as _Path

        # Hardcoded literal is a last-resort fallback behind the configured
        # llm_model (env TESSERAE_LLM_MODEL → global config).
        self.model = model or _configured_default_model(("claude",)) or "sonnet"
        # Custom claude-compatible endpoint routing: when set, these are
        # surfaced to the CLI child process as ANTHROPIC_BASE_URL /
        # ANTHROPIC_AUTH_TOKEN in _run_prompt.
        self.base_url = base_url
        self.api_key = api_key
        # Resolution order:
        #   1. Explicit ``config_dirs`` argument wins (tests, MCP override,
        #      CLI flags like --claude-config-dir).
        #   2. ``CLAUDE_CONFIG_DIR`` env var (Claude Code-managed sessions
        #      set this; multi-account shell aliases set it too).
        #   3. Auto-discover every ``~/.claude*`` directory at $HOME.
        #      Common multi-account setups have ``~/.claude``,
        #      ``~/.claude-personal1``, ``~/.claude-personal2`` etc. The
        #      existing multi-config fallback loop in ``complete_json``
        #      tries each in order and uses the first that's logged in.
        #   4. Final fallback: ``[~/.claude]`` — preserves the pre-fix
        #      default for users with a single config dir.
        if config_dirs:
            self.config_dirs = list(config_dirs)
        elif _os.environ.get("CLAUDE_CONFIG_DIR"):
            self.config_dirs = [_os.environ["CLAUDE_CONFIG_DIR"]]
        else:
            home = _Path.home()
            discovered = sorted(
                str(p)
                for p in home.glob(".claude*")
                if p.is_dir() and not p.name.endswith((".bak", ".old"))
            )
            self.config_dirs = discovered or [str(home / ".claude")]
        self.timeout = int(timeout) if timeout is not None else None

    def _run_prompt(
        self,
        prompt: str,
        *,
        max_retries: int = 2,
        error_label: str = "ClaudeCLIJsonClient call failed",
    ) -> Optional[str]:
        """Run ``claude -p`` over the rotating ``config_dirs`` and return the
        raw stdout from the first account that succeeds, else None.

        This is the single rotation loop shared by :meth:`complete_json`
        (parses the stdout as JSON) and :meth:`complete_text` (returns the
        prose as-is). Each config_dir is one account; a rate-limited or
        failing account falls through to the next, so synthesis never gets
        stuck on a single exhausted account while another has headroom.
        """
        import os as _os
        import subprocess as _subprocess
        from pathlib import Path as _Path

        last_error: Optional[Exception] = None
        all_not_logged_in = True  # only True if EVERY tried config_dir was Not-logged-in
        any_attempted = False
        default_claude_dir = str(_Path.home() / ".claude")
        for config_dir in self.config_dirs:
            for attempt in range(max_retries + 1):
                any_attempted = True
                try:
                    env = _os.environ.copy()
                    # WORKAROUND for Claude CLI quirk: setting
                    # CLAUDE_CONFIG_DIR explicitly (even to the same
                    # value the user is implicitly using) causes the
                    # CLI to lose its auth lookup chain — `Not logged
                    # in` even when the user IS logged into that exact
                    # dir. So when our target config_dir IS the
                    # canonical default ``~/.claude``, leave the env
                    # alone and let the CLI's native discovery work.
                    if config_dir == default_claude_dir:
                        env.pop("CLAUDE_CONFIG_DIR", None)
                    else:
                        env["CLAUDE_CONFIG_DIR"] = config_dir
                    # Route the CLI at a custom claude-compatible endpoint
                    # when one was resolved from config. ANTHROPIC_AUTH_TOKEN
                    # (not ANTHROPIC_API_KEY) so the CLI treats the key as a
                    # bearer token for that endpoint.
                    if self.base_url:
                        env["ANTHROPIC_BASE_URL"] = self.base_url
                    if self.api_key:
                        env["ANTHROPIC_AUTH_TOKEN"] = self.api_key
                    cmd = [
                        "claude",
                        "-p",
                        "--output-format", "text",
                        "--max-turns", "1",
                    ]
                    if self.model:
                        cmd.extend(["--model", self.model])
                    proc = _run_cli(cmd, prompt=prompt, env=env, timeout=self.timeout)
                    if proc.returncode != 0:
                        stderr_text = (proc.stderr or "").strip()
                        stdout_text = (proc.stdout or "").strip()
                        # Detect the canonical "Not logged in" message from
                        # the Claude CLI. Substring + case-insensitive so
                        # we're robust to minor phrasing drift (e.g.
                        # "Not logged in · Please run /login").
                        combined = f"{stderr_text}\n{stdout_text}".lower()
                        if "not logged in" in combined:
                            # Continue to the next config_dir — a later
                            # configured profile may be logged in. Only
                            # emit the actionable warning AFTER every
                            # profile has been tried (codex PR #17 P2 fix).
                            last_error = RuntimeError(
                                f"claude exited {proc.returncode}: {stderr_text or stdout_text}"
                            )
                            break  # skip to next config_dir
                        # Non-auth failure on this profile (incl. a rate
                        # limit — `claude -p` exits non-zero with "You've
                        # hit your … limit") resets the all_not_logged_in
                        # tracker; the except below rotates to the next
                        # account.
                        all_not_logged_in = False
                        raise RuntimeError(
                            f"claude exited {proc.returncode}: "
                            f"{stderr_text or stdout_text}"
                        )
                    return proc.stdout
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    # Don't retry on the same config_dir; fall through to
                    # the next one. Auth/network/rate-limit issues are best
                    # handled by switching accounts, not hammering one.
                    break
        # All profiles exhausted. If every one failed with "Not logged in",
        # emit the actionable once-per-process auth warning. Otherwise emit
        # the generic warning so genuine errors stay visible.
        if any_attempted and all_not_logged_in and last_error is not None:
            global _LOGGED_LOGIN_WARNING
            if not _LOGGED_LOGIN_WARNING:
                _LOGGED_LOGIN_WARNING = True
                logger.warning(
                    "[tesserae] LLM-backed extraction skipped: "
                    "Claude CLI not logged in (tried %d config %s). "
                    "Run `claude /login` to re-auth, then re-compile.",
                    len(self.config_dirs),
                    "dir" if len(self.config_dirs) == 1 else "dirs",
                )
            return None
        if last_error is not None:
            logger.warning("%s: %s", error_label, last_error)
        return None

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        # Stitch system + user into a single prompt for the CLI's -p flag.
        # The CLI doesn't expose a separate system slot, so we prefix the
        # JSON-only contract to the user message.
        prompt = (
            f"{system.strip()}\n\n"
            f"Respond with valid JSON only — no Markdown fences, no prose, "
            f"no trailing commas. Schema name: {schema_name}.\n\n"
            f"{user}"
        )
        raw = self._run_prompt(
            prompt,
            max_retries=max_retries,
            error_label=f"ClaudeCLIJsonClient.complete_json failed (schema={schema_name})",
        )
        return parse_json_tolerant(raw) if raw is not None else None

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        """Prose completion over the same rotating accounts as complete_json.

        Used by the prose-synthesis callers (``tesserae ask``) so they get
        the no-API-key OAuth path + multi-account rotation for free.
        """
        prompt = f"{system.strip()}\n\n{user}"
        raw = self._run_prompt(
            prompt,
            max_retries=max_retries,
            error_label="ClaudeCLIJsonClient.complete_text failed",
        )
        text = (raw or "").strip()
        return text or None


# ---------------------------------------------------------------------------
# Codex CLI implementation (OAuth — `codex exec`, no API key needed)
# ---------------------------------------------------------------------------


class CodexCLIJsonClient:
    """LLMJsonClient backed by the ``codex`` CLI subprocess over OAuth.

    Mirrors :class:`ClaudeCLIJsonClient` but shells out to
    ``codex exec --skip-git-repo-check --sandbox read-only`` with the prompt
    on stdin and the final message captured via ``--output-last-message``
    (the same contract :mod:`tesserae.cognee_codex` uses). No API key
    required — auth comes from the credentialed ``CODEX_HOME``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        codex_homes: Optional[List[str]] = None,
        timeout: int = 180,
        reasoning_effort: Optional[str] = "medium",
    ) -> None:
        import os as _os
        from pathlib import Path as _Path

        # Hardcoded literal is a last-resort fallback behind the configured
        # llm_model (env TESSERAE_LLM_MODEL → global config).
        self.model = model or _configured_default_model(("codex",)) or "gpt-5.4"
        # Reasoning effort for Tesserae's own codex calls. Defaults to
        # ``medium`` — structured graph/finding extraction does NOT need the
        # ``xhigh`` a user may set globally in ``~/.codex/config.toml`` for
        # interactive work, and xhigh makes a multi-chunk compile many times
        # slower. Passed as ``-c model_reasoning_effort=<effort>`` so it
        # overrides config.toml for THIS process only. ``None`` inherits
        # config.toml.
        self.reasoning_effort = (str(reasoning_effort).strip() or None) if reasoning_effort else None
        # Resolution order mirrors ClaudeCLIJsonClient.config_dirs:
        #   1. Explicit ``codex_homes`` argument (tests, CLI flags).
        #   2. ``CODEX_HOME`` env var (multi-account setups point it at
        #      e.g. ``~/.codex-personal1``).
        #   3. Auto-discover every ``~/.codex*`` directory at $HOME.
        #   4. Final fallback: ``[~/.codex]``.
        if codex_homes:
            self.codex_homes = list(codex_homes)
        elif _os.environ.get("CODEX_HOME"):
            self.codex_homes = [_os.environ["CODEX_HOME"]]
        else:
            home = _Path.home()
            discovered = sorted(
                str(p)
                for p in home.glob(".codex*")
                if p.is_dir() and not p.name.endswith((".bak", ".old"))
            )
            self.codex_homes = discovered or [str(home / ".codex")]
        self.timeout = int(timeout) if timeout is not None else None

    def _run_prompt(
        self,
        prompt: str,
        *,
        error_label: str = "CodexCLIJsonClient call failed",
    ) -> Optional[str]:
        """Run ``codex exec`` over the rotating ``codex_homes`` and return the
        final message text from the first account that succeeds, else None.

        Shared by :meth:`complete_json` and :meth:`complete_text`; a
        rate-limited or failing CODEX_HOME falls through to the next.
        """
        import os as _os
        import subprocess as _subprocess
        import tempfile as _tempfile
        from pathlib import Path as _Path

        last_error: Optional[Exception] = None
        for codex_home in self.codex_homes:
            with _tempfile.NamedTemporaryFile(
                "w+", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                output_path = _Path(handle.name)
            try:
                env = _os.environ.copy()
                env["CODEX_HOME"] = codex_home
                cmd = [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--sandbox", "read-only",
                    "--model", self.model,
                    *(
                        ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
                        if self.reasoning_effort
                        else []
                    ),
                    "--output-last-message", str(output_path),
                    "-",
                ]
                proc = _run_cli(cmd, prompt=prompt, env=env, timeout=self.timeout)
                if proc.returncode != 0:
                    # Auth / rate-limit / transport failure on this home —
                    # try the next one. Switching accounts beats hammering
                    # one (same policy as the Claude CLI client).
                    last_error = RuntimeError(
                        f"codex exited {proc.returncode}: "
                        f"{(proc.stderr or '').strip() or (proc.stdout or '').strip()}"
                    )
                    continue
                final = (
                    output_path.read_text(encoding="utf-8", errors="replace")
                    if output_path.exists()
                    else ""
                )
                return final or proc.stdout or ""
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            finally:
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass
        if last_error is not None:
            logger.warning(
                "%s (tried %d CODEX_HOME %s): %s — run `codex login` to re-auth.",
                error_label,
                len(self.codex_homes),
                "dir" if len(self.codex_homes) == 1 else "dirs",
                last_error,
            )
        return None

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        # Same prompt stitching as the Claude CLI client: codex exec has no
        # separate system slot either, so prefix the JSON-only contract.
        prompt = (
            f"{system.strip()}\n\n"
            f"Respond with valid JSON only — no Markdown fences, no prose, "
            f"no trailing commas. Schema name: {schema_name}.\n\n"
            f"{user}"
        )
        raw = self._run_prompt(
            prompt,
            error_label=f"CodexCLIJsonClient.complete_json failed (schema={schema_name})",
        )
        return parse_json_tolerant(raw or "") if raw is not None else None

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        """Prose completion over the same rotating CODEX_HOMEs as complete_json."""
        prompt = f"{system.strip()}\n\n{user}"
        raw = self._run_prompt(
            prompt,
            error_label="CodexCLIJsonClient.complete_text failed",
        )
        text = (raw or "").strip()
        return text or None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _claude_cli_available() -> bool:
    """Return True when the ``claude`` binary is on PATH AND at least one
    candidate config dir looks credentialed.

    Candidate dirs mirror ClaudeCLIJsonClient.__init__ resolution:
    CLAUDE_CONFIG_DIR if set, else every ``~/.claude*`` dir at $HOME,
    falling back to ``~/.claude``. This way a user who only has
    ``~/.claude-personal1`` (no ``~/.claude``) still gets the CLI
    client built — pre-fix, the gate said unavailable and the caller
    silently dropped to no-LLM mode.
    """
    import os as _os
    import shutil as _shutil
    from pathlib import Path as _Path

    if not _shutil.which("claude"):
        return False
    env_dir = _os.environ.get("CLAUDE_CONFIG_DIR")
    if env_dir:
        candidates = [_Path(env_dir)]
    else:
        home = _Path.home()
        discovered = sorted(
            p for p in home.glob(".claude*")
            if p.is_dir() and not p.name.endswith((".bak", ".old"))
        )
        candidates = discovered or [home / ".claude"]
    markers = ("settings.json", "settings.local.json", "projects", "history.jsonl")
    return any(
        cdir.exists() and any((cdir / m).exists() for m in markers)
        for cdir in candidates
    )


def _codex_cli_available() -> bool:
    """Return True when the ``codex`` binary is on PATH AND at least one
    candidate ``CODEX_HOME`` looks credentialed.

    Candidate homes mirror CodexCLIJsonClient.__init__ resolution:
    ``CODEX_HOME`` if set, else every ``~/.codex*`` dir at $HOME, falling
    back to ``~/.codex``. Markers match the codex root detection in
    :mod:`tesserae.harness_sessions`.
    """
    import os as _os
    import shutil as _shutil
    from pathlib import Path as _Path

    if not _shutil.which("codex"):
        return False
    env_home = _os.environ.get("CODEX_HOME")
    if env_home:
        candidates = [_Path(env_home)]
    else:
        home = _Path.home()
        discovered = sorted(
            p for p in home.glob(".codex*")
            if p.is_dir() and not p.name.endswith((".bak", ".old"))
        )
        candidates = discovered or [home / ".codex"]
    markers = ("auth.json", "config.toml", "sessions")
    return any(
        cdir.exists() and any((cdir / m).exists() for m in markers)
        for cdir in candidates
    )


# Machine-wide LLM client defaults, shared with the project registry dir.
# Lets a multi-account user pin e.g. ``llm_codex_home`` for ALL projects
# without exporting the shared ``CODEX_HOME`` env var (which their other
# codex account workflows contend over). Written by
# ``tesserae config llm``.
GLOBAL_CONFIG_PATH = Path.home() / ".tesserae" / "config.json"


def _load_global_llm_config() -> dict:
    """Best-effort read of the machine-wide config; {} on missing/corrupt."""
    try:
        if GLOBAL_CONFIG_PATH.is_file():
            payload = json.loads(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt global file must never crash
        logger.warning("ignoring unreadable global config at %s", GLOBAL_CONFIG_PATH)
    return {}


def resolve_llm_client_settings(cfg: Optional[dict] = None) -> dict:
    """Resolve provider + config-dir settings for the JSON client.

    Precedence per knob: **env var → project config → global config →
    None** (built-in default). CLI flags are surfaced as env vars by the
    handlers, so the effective order is CLI flag > project ``config.json``
    > ``~/.tesserae/config.json`` > default.

    Keys read from both config layers: ``llm_provider`` (``"claude"`` |
    ``"codex"`` | ``"anthropic"`` | ``"custom"``), ``llm_claude_config_dirs``
    (list or str), ``llm_codex_home`` (str), ``llm_model`` (str),
    ``llm_base_url`` (str), ``llm_api_key`` (str). Env overrides:
    ``TESSERAE_LLM_MODEL`` → model, ``ANTHROPIC_BASE_URL`` → base_url,
    ``ANTHROPIC_API_KEY`` → api_key.
    """
    import os

    cfg = cfg or {}
    global_cfg = _load_global_llm_config()

    provider = (
        os.environ.get("TESSERAE_LLM_PROVIDER")
        or cfg.get("llm_provider")
        or global_cfg.get("llm_provider")
        or None
    )

    def _as_dirs(raw: object) -> Optional[List[str]]:
        if isinstance(raw, str) and raw:
            return [raw]
        if isinstance(raw, list) and raw:
            return [str(d) for d in raw]
        return None

    env_claude = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_config_dirs = (
        [env_claude]
        if env_claude
        else _as_dirs(cfg.get("llm_claude_config_dirs"))
        or _as_dirs(global_cfg.get("llm_claude_config_dirs"))
    )

    codex_home = (
        os.environ.get("CODEX_HOME")
        or cfg.get("llm_codex_home")
        or global_cfg.get("llm_codex_home")
        or None
    )

    # Reasoning effort for Tesserae's own codex calls. Default ``medium`` —
    # extraction does not need the ``xhigh`` a user may set globally for
    # interactive codex, and xhigh makes compiles many times slower.
    codex_reasoning_effort = (
        os.environ.get("TESSERAE_CODEX_REASONING_EFFORT")
        or cfg.get("llm_codex_reasoning_effort")
        or global_cfg.get("llm_codex_reasoning_effort")
        or "medium"
    )

    # Custom claude-compatible endpoint knobs (also used to override the
    # model/base_url/key on the anthropic provider). Same precedence:
    # env → project config → global config → None.
    model = (
        os.environ.get("TESSERAE_LLM_MODEL")
        or cfg.get("llm_model")
        or global_cfg.get("llm_model")
        or None
    )
    base_url = (
        os.environ.get("ANTHROPIC_BASE_URL")
        or cfg.get("llm_base_url")
        or global_cfg.get("llm_base_url")
        or None
    )
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or cfg.get("llm_api_key")
        or global_cfg.get("llm_api_key")
        or None
    )

    return {
        "provider": provider,
        "claude_config_dirs": claude_config_dirs,
        "codex_home": codex_home,
        "codex_reasoning_effort": codex_reasoning_effort,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


_KEEP_TIMEOUT = object()  # sentinel: "use each client's own default timeout"


def build_default_json_client(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    claude_config_dirs: Optional[List[str]] = None,
    codex_home: Optional[str] = None,
    codex_reasoning_effort: Optional[str] = "medium",
    timeout: Any = _KEEP_TIMEOUT,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[LLMJsonClient]:
    """Return the best-available JSON-completion client.

    ``provider`` selects the preferred CLI backend: explicit argument →
    ``TESSERAE_LLM_PROVIDER`` env var → ``"claude"``. Resolution order
    matches the README's "common path uses no API keys" promise:

    Default (claude-first):

    1. **Test factory** (``set_client_factory``) — for hermetic tests.
    2. **Claude CLI over OAuth** — preferred default. Requires only the
       ``claude`` binary on PATH plus a credentialed
       ``CLAUDE_CONFIG_DIR`` (defaults to ``~/.claude``). Zero API keys.
    3. **Anthropic SDK** with ``ANTHROPIC_API_KEY`` — fallback for
       environments where the CLI isn't available (CI runners, headless
       servers). Opt-in via the env var.
    4. **Codex CLI over OAuth** — final fallback so a codex-only machine
       still gets LLM passes instead of dropping to structural-only.
    5. ``None`` — caller falls back to the structural-only path.

    ``provider="codex"`` puts the Codex CLI first (codex → claude → API
    key → None); the model defaults to ``gpt-5.4`` on the codex path.

    ``provider="custom"`` targets a claude-compatible endpoint through the
    Anthropic SDK with the resolved ``llm_model`` / ``llm_base_url`` /
    ``llm_api_key`` (env → project config → global config) — no
    ``ANTHROPIC_API_KEY`` env var required when ``llm_api_key`` is
    configured.
    """
    # Test seam wins.
    if _CLIENT_FACTORY is not None:
        return AnthropicLLMJsonClient(model=model or "claude-sonnet-4-6")

    # Explicit args (threaded from a project config by the caller) beat the
    # env → global-config resolution. ``settings["provider"]`` already folds
    # in TESSERAE_LLM_PROVIDER.
    settings = resolve_llm_client_settings()
    resolved_provider = (
        provider or settings["provider"] or "claude"
    ).strip().lower()
    resolved_base_url = base_url or settings["base_url"]
    resolved_api_key = api_key or settings["api_key"]

    # timeout=None (from the doc extractor) means "no cutoff — run to completion";
    # the sentinel leaves each client on its own default (180s CLI / 30s API).
    _tkw = {} if timeout is _KEEP_TIMEOUT else {"timeout": timeout}

    def _codex() -> Optional[LLMJsonClient]:
        if _codex_cli_available():
            return CodexCLIJsonClient(
                model=model,
                codex_homes=[codex_home] if codex_home else None,
                reasoning_effort=codex_reasoning_effort,
                **_tkw,
            )
        return None

    def _claude() -> Optional[LLMJsonClient]:
        if _claude_cli_available():
            # Thread the endpoint pair only when a custom base_url is in
            # play — a stray ANTHROPIC_API_KEY env var must not flip the
            # CLI from OAuth to bearer-token auth.
            _ekw = (
                {"base_url": resolved_base_url, "api_key": resolved_api_key}
                if resolved_base_url
                else {}
            )
            return ClaudeCLIJsonClient(
                model=model,
                config_dirs=claude_config_dirs,
                **_ekw,
                **_tkw,
            )
        return None

    def _api_key() -> Optional[LLMJsonClient]:
        # Returns None if the anthropic SDK isn't installed (e.g. base
        # install without `tesserae[synthesis-llm]`) — that's a silent
        # no-op rather than a crash because the structural-only path
        # remains useful with zero LLM access.
        if resolved_api_key:
            try:
                return AnthropicLLMJsonClient(
                    model=model,
                    api_key=resolved_api_key,
                    base_url=resolved_base_url,
                    **_tkw,
                )
            except RuntimeError:
                return None
        return None

    def _custom() -> Optional[LLMJsonClient]:
        # Explicit custom endpoint: build unconditionally (some local
        # claude-compatible endpoints are keyless), resolved knobs applied.
        try:
            return AnthropicLLMJsonClient(
                model=model,
                api_key=resolved_api_key,
                base_url=resolved_base_url,
                **_tkw,
            )
        except RuntimeError:
            return None

    if resolved_provider == "codex":
        chain = (_codex, _claude, _api_key)
    elif resolved_provider == "anthropic":
        # Honour an explicit anthropic choice: the SDK first, not the Claude CLI.
        chain = (_api_key, _claude, _codex)
    elif resolved_provider == "custom":
        chain = (_custom, _claude, _codex)
    else:
        chain = (_claude, _api_key, _codex)
    for builder in chain:
        client = builder()
        if client is not None:
            return client
    return None


class CompositeCLIClient:
    """Tries an ordered list of clients until one returns a non-None result.

    Each sub-client already rotates across ITS OWN accounts (all
    ``~/.claude*`` dirs, all ``~/.codex*`` homes); this composite chains
    across PROVIDERS so a call only gives up once EVERY account on the
    machine — Claude and Codex — is exhausted. That is the "never get stuck
    on a rate limit while another account has headroom" guarantee.
    """

    def __init__(self, clients: Sequence[Any]) -> None:
        self.clients: List[Any] = [c for c in clients if c is not None]

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        for client in self.clients:
            result = client.complete_json(**kwargs)
            if result is not None:
                return result
        return None

    def complete_text(self, **kwargs: Any) -> Optional[str]:
        for client in self.clients:
            result = client.complete_text(**kwargs)
            if result is not None:
                return result
        return None


def build_rotating_client(
    model_claude: Optional[str] = None,
    model_codex: Optional[str] = None,
    provider: Optional[str] = None,
    claude_config_dirs: Optional[List[str]] = None,
    codex_home: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[Any]:
    """Build a client that rotates across EVERY available account/provider.

    Unlike :func:`build_default_json_client` (which returns the single
    best-available client), this composes ALL available backends — Claude
    CLI (rotating its config dirs), Codex CLI (rotating its homes), and the
    Anthropic SDK if a key is set or provider is ``custom``/``anthropic`` —
    in ``provider`` preference order, and falls through provider-to-provider
    on exhaustion. Returns None only when no backend is usable at all. Used
    by prose synthesis (``tesserae ask``).
    """
    if _CLIENT_FACTORY is not None:
        return AnthropicLLMJsonClient(model="claude-sonnet-4-6")

    # Callers here (ask/query/summary) don't thread a project config, so
    # resolve env → global config directly; explicit args still win.
    settings = resolve_llm_client_settings()
    resolved_provider = (
        provider or settings["provider"] or "claude"
    ).strip().lower()
    resolved_base_url = base_url or settings["base_url"]
    resolved_api_key = api_key or settings["api_key"]

    # Same gate as build_default_json_client: route the CLI at the custom
    # endpoint only when a base_url is in play, so a stray ANTHROPIC_API_KEY
    # env var never flips the CLI from OAuth to bearer-token auth.
    _claude_ekw = (
        {"base_url": resolved_base_url, "api_key": resolved_api_key}
        if resolved_base_url
        else {}
    )
    claude_client = (
        ClaudeCLIJsonClient(
            model=model_claude, config_dirs=claude_config_dirs, **_claude_ekw
        )
        if _claude_cli_available()
        else None
    )
    codex_client = (
        CodexCLIJsonClient(
            model=model_codex,
            codex_homes=[codex_home] if codex_home else None,
        )
        if _codex_cli_available()
        else None
    )
    api_client = None
    if resolved_api_key or resolved_provider == "custom":
        try:
            api_client = AnthropicLLMJsonClient(
                api_key=resolved_api_key, base_url=resolved_base_url
            )
        except RuntimeError:
            api_client = None

    if resolved_provider == "codex":
        ordered = [codex_client, claude_client, api_client]
    elif resolved_provider in ("anthropic", "custom"):
        ordered = [api_client, claude_client, codex_client]
    else:
        ordered = [claude_client, api_client, codex_client]
    available = [c for c in ordered if c is not None]
    if not available:
        return None
    if len(available) == 1:
        return available[0]
    return CompositeCLIClient(available)
