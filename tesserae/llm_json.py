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
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol, Union

logger = logging.getLogger(__name__)

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
                        # Non-auth failure on this profile resets the
                        # "all_not_logged_in" tracker so we surface the
                        # generic warning at the end instead of the
                        # login-specific one.
                        all_not_logged_in = False
                        raise RuntimeError(
                            f"claude exited {proc.returncode}: "
                            f"{stderr_text or stdout_text}"
                        )
                    return parse_json_tolerant(proc.stdout)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    # Don't retry on the same config_dir; fall through to
                    # the next one. Auth/network issues are best handled
                    # by switching accounts, not by hammering one.
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
            logger.warning(
                "ClaudeCLIJsonClient.complete_json failed (schema=%s): %s",
                schema_name,
                last_error,
            )
        return None


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
        model: str = "gpt-5.4",
        codex_homes: Optional[List[str]] = None,
        timeout: int = 180,
    ) -> None:
        import os as _os
        from pathlib import Path as _Path

        self.model = model
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
        import tempfile as _tempfile
        from pathlib import Path as _Path

        # Same prompt stitching as the Claude CLI client: codex exec has no
        # separate system slot either, so prefix the JSON-only contract.
        prompt = (
            f"{system.strip()}\n\n"
            f"Respond with valid JSON only — no Markdown fences, no prose, "
            f"no trailing commas. Schema name: {schema_name}.\n\n"
            f"{user}"
        )

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
                    "--output-last-message", str(output_path),
                    "-",
                ]
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
                    # Auth or transport failure on this home — try the next
                    # one. Switching accounts beats hammering one (same
                    # policy as the Claude CLI client).
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
                return parse_json_tolerant(final or proc.stdout or "")
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
                "CodexCLIJsonClient.complete_json failed (schema=%s, tried %d "
                "CODEX_HOME %s): %s — run `codex login` to re-auth.",
                schema_name,
                len(self.codex_homes),
                "dir" if len(self.codex_homes) == 1 else "dirs",
                last_error,
            )
        return None


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
# ``tesserae project llm-defaults``.
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
    ``"codex"``), ``llm_claude_config_dirs`` (list or str),
    ``llm_codex_home`` (str).
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

    return {
        "provider": provider,
        "claude_config_dirs": claude_config_dirs,
        "codex_home": codex_home,
    }


def build_default_json_client(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    claude_config_dirs: Optional[List[str]] = None,
    codex_home: Optional[str] = None,
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
    """
    import os

    # Test seam wins.
    if _CLIENT_FACTORY is not None:
        return AnthropicLLMJsonClient(model=model or "claude-sonnet-4-6")

    resolved_provider = (
        provider or os.environ.get("TESSERAE_LLM_PROVIDER") or "claude"
    ).strip().lower()

    def _codex() -> Optional[LLMJsonClient]:
        if _codex_cli_available():
            return CodexCLIJsonClient(
                model=model or "gpt-5.4",
                codex_homes=[codex_home] if codex_home else None,
            )
        return None

    def _claude() -> Optional[LLMJsonClient]:
        if _claude_cli_available():
            return ClaudeCLIJsonClient(
                model=model or "sonnet",
                config_dirs=claude_config_dirs,
            )
        return None

    def _api_key() -> Optional[LLMJsonClient]:
        # Returns None if the anthropic SDK isn't installed (e.g. base
        # install without `tesserae[synthesis-llm]`) — that's a silent
        # no-op rather than a crash because the structural-only path
        # remains useful with zero LLM access.
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                return AnthropicLLMJsonClient(model=model or "claude-sonnet-4-6")
            except RuntimeError:
                return None
        return None

    if resolved_provider == "codex":
        chain = (_codex, _claude, _api_key)
    else:
        chain = (_claude, _api_key, _codex)
    for builder in chain:
        client = builder()
        if client is not None:
            return client
    return None
