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

import hashlib
import json
import logging
import os
import sys
import re
import secrets
import signal
import subprocess
import threading
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

#: Default model for the Codex CLI. A caller argument or a provider-scoped
#: ``llm_model`` still wins — model choice is supported on codex. Named rather
#: than inlined so the default has one definition and the tests can move it.
CODEX_DEFAULT_MODEL = "gpt-5.6-luna"

#: Substrings the Claude CLI uses when an account is out of quota, e.g.
#: "You've hit your weekly limit · resets 6am (Asia/Seoul)" and the 5-hour
#: "session limit" variant. Matched case-insensitively against the CLI's own
#: message. Deliberately NOT matching bare "limit" — a document that trips a
#: context or rate limit is a per-document fact and must keep rotating.
_QUOTA_MARKERS = ("hit your weekly limit", "hit your session limit", "hit your usage limit")


def _is_quota_exhaustion(error: BaseException) -> bool:
    """True when the CLI said the ACCOUNT is out of quota, not this call."""
    text = str(error).lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


def _reset_login_warning_for_tests() -> None:
    """Reset the one-shot login warning flag. Test-only helper."""
    global _LOGGED_LOGIN_WARNING
    _LOGGED_LOGIN_WARNING = False


#: Why the most recent ``complete_json`` on THIS thread returned None.
#: ``"unavailable"`` = the provider never produced text (non-zero exit, spawn
#: error, or a clean exit with an empty body) — transport/capacity.
#: ``"timeout"`` = the attempt did not finish inside ``TESSERAE_EXTRACT_TIMEOUT``.
#: That is ALL it establishes. It is deliberately not folded into
#: ``"unavailable"`` — the remedies differ (wait out a capacity window vs. raise
#: the bound / split the document) — but it must not assert the opposite either:
#: the ``TimeoutExpired`` comes from the child process, and a DNS/connect stall
#: that never reached the provider raises exactly the same exception as a
#: genuinely slow generation. The verdict says the bound was hit; it does not
#: say the provider saw the request, in either direction.
#: ``"auth"`` = every account that was tried answered "not logged in". Split out
#: of ``"unavailable"`` for the third time the same way: waiting out a capacity
#: window never refreshes an expired OAuth session, and this verdict is re-read
#: PER DOCUMENT — before it existed, the one actionable ``claude /login`` /
#: ``codex login`` line was the once-per-process login warning and the other 136
#: per-document lines of a 137-doc compile asserted transport/capacity.
#: The verdict is what carries the remedy per doc (``LLMResearchExtractor`` turns
#: it into ``ProviderAuthError``); how loudly each client ALSO logs it is a
#: separate, deliberately unequal choice — :class:`ClaudeCLIJsonClient`
#: de-duplicates its static hint via ``_LOGGED_LOGIN_WARNING``,
#: :class:`CodexCLIJsonClient` logs one line per call.
#: Covers ONLY the credential refusal — a missing CLI binary stays
#: ``"unavailable"``.
#: ponytail: ``"auth"`` is written by the two CLI clients only;
#: :class:`AnthropicLLMJsonClient` still reports a 401 as ``"unavailable"``
#: (its except ladder classifies by rate-limit/status, not by auth). Upgrade
#: path when an API-key deployment needs it: test ``status_code == 401`` in
#: that ladder and note ``"auth"`` there too.
#: ``"unparseable"`` = the provider DID answer and the answer wasn't JSON — a
#: real bad generation. Collapsing these into a bare ``None`` is not free:
#: a provider capacity window (99 "Reconnecting…" lines in the raw log) pushed
#: 35/137 docs to the deterministic baseline and was read three times, with
#: rising confidence, as the MODEL having worse schema compliance. The model
#: never returned anything to validate.
#: Thread-local because BatchIngestRunner shares ONE client across
#: TESSERAE_EXTRACT_CONCURRENCY worker threads, so an instance attribute would
#: report whichever worker wrote last — the same reason
#: SelectiveClaudeResearchExtractor keeps its fallback flag in threading.local.
#: ponytail: four string literals, not an enum or an exception hierarchy —
#: there is exactly one consumer (LLMResearchExtractor). Promote if a second
#: one appears.
_LAST_FAILURE = threading.local()


def _note_failure(kind: Optional[str]) -> None:
    """Record why this thread's in-flight ``complete_json`` is giving up."""
    _LAST_FAILURE.kind = kind


def last_failure_kind() -> Optional[str]:
    """``"unavailable"`` | ``"timeout"`` | ``"auth"`` | ``"unparseable"`` |
    ``None`` for this thread's most recent ``complete_json``. Only meaningful
    immediately after a None return."""
    return getattr(_LAST_FAILURE, "kind", None)


#: The provider's LAST raw reply on this thread, before parsing.
#:
#: ``complete_json`` DESTROYS unparseable text: ``parse_json_tolerant`` returns
#: None, ``_note_failure("unparseable")`` records why, and the caller gets None
#: one frame above with nothing to recover from. Measured on the LoCoMo
#: answering path (24-call raw probe at fan-out prompt size, 2026-08-23): 6 of
#: 24 replies were shape failures and ZERO were transport failures — two were
#: bare prose carrying the CORRECT answer, one was a correct refusal written as
#: a bare JSON string. Every "the backbone returned nothing" row on that run was
#: an answer already paid for and then thrown away inside this module.
#:
#: Thread-local for the same reason ``_LAST_FAILURE`` is: BatchIngestRunner
#: shares ONE client across worker threads, so an instance attribute would
#: report whichever worker wrote last. Only meaningful immediately after the
#: ``complete_json`` whose text it describes — it is cleared on entry to every
#: one, so a stale reply can never be attributed to a call that never answered.
_LAST_RAW = threading.local()


def _note_raw(text: Optional[str]) -> None:
    """Record the raw text this thread's in-flight ``complete_json`` received."""
    _LAST_RAW.text = text


def last_raw_reply() -> Optional[str]:
    """The provider's unparsed reply to this thread's most recent
    ``complete_json``, or ``None`` when nothing was received. Read it only
    immediately after that call: it is per-thread, not per-client."""
    return getattr(_LAST_RAW, "text", None)


#: Monotonic per-thread tally of on-disk cache consultations, so a caller can
#: bracket a unit of work and learn what it COST rather than only that it
#: finished. A compile that is 90% replay from ``~/.tesserae/llm_cache`` and one
#: paying full price for every document look identical from the outside and take
#: wildly different amounts of time; this is the only signal that separates them.
#:
#: Thread-local for the same reason ``_LAST_FAILURE`` is: BatchIngestRunner
#: shares ONE client across TESSERAE_EXTRACT_CONCURRENCY worker threads, and a
#: shared counter would attribute one worker's cache hit to whichever document
#: another worker happened to be holding. Each worker extracts a document
#: start-to-finish on its own thread, so a per-thread delta is exactly that
#: document's cost.
#:
#: Counters only ever increase; callers take DELTAS. Never reset one — a reset
#: races every other bracket open on the same thread.
_CACHE_TALLY = threading.local()


def _note_cache_lookup(*, hit: bool) -> None:
    """Record one cache consultation on this thread."""
    field = "hits" if hit else "misses"
    setattr(_CACHE_TALLY, field, getattr(_CACHE_TALLY, field, 0) + 1)


def cache_tally() -> tuple:
    """``(hits, misses)`` for this thread since the process started.

    Both climb monotonically; the useful quantity is the difference across a
    unit of work. A lookup is counted only when the cache is actually
    CONSULTED, so a run with ``TESSERAE_LLM_CACHE=0`` — or one on a client with
    no on-disk cache at all — reports ``(0, 0)`` and a caller can honestly say
    "unknown" instead of inventing a miss it never observed.
    """
    return (getattr(_CACHE_TALLY, "hits", 0), getattr(_CACHE_TALLY, "misses", 0))


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


#: On-disk response cache for the CLI clients. The Anthropic client gets prompt
#: caching from the SDK; the codex/claude CLI clients had none, so they accepted
#: ``cache_key`` and ignored it — every recompile re-paid full price for
#: byte-identical input.
#: Global rather than per-project on purpose: the identity of an entry is a
#: digest of the ASSEMBLED PROMPT, so two projects sending the same prompt
#: should share the answer.
#: That digest is taken here rather than trusted from the caller, and that is
#: the whole point. The original contract asked every caller to pass a content
#: digest as ``cache_key``; three of them passed a constant-ish label instead
#: (a member COUNT, a schema name, a schema version), so unrelated prompts
#: collided onto one file and were served each other's answers. A contract that
#: depends on every future caller remembering is not a contract — so
#: :func:`_cli_cache_path` now REQUIRES the prompt and hashes it itself, and
#: ``cache_key`` degrades to the namespace/version label those callers were
#: already treating it as.
#: ponytail: no eviction. Entries are small JSON and keyed by content digest;
#: add an LRU sweep if the directory ever actually gets big.
_CLI_CACHE_DIR = Path.home() / ".tesserae" / "llm_cache"


def _cli_cache_enabled() -> bool:
    return (os.environ.get("TESSERAE_LLM_CACHE") or "").strip().lower() not in {"0", "false", "no", "off"}


def _cli_cache_path(cache_key: str, *, model: str, prompt: str, extra: str = "") -> Path:
    """Cache file for one (namespace, prompt, model, variant) quadruple.

    ``prompt`` is the exact text that would be sent to the CLI, and it is
    keyword-REQUIRED on purpose: it is the one input guaranteed to distinguish
    two different questions, so making it impossible to address this cache
    without it is what makes a collision impossible rather than merely
    discouraged.

    ``cache_key`` is a namespace/version label (an empty or repeated one is now
    harmless), ``model`` and ``extra`` cover which model and per-client variant
    (e.g. reasoning effort) produced the answer, so switching models re-asks
    instead of serving another model's output.
    """
    digest = hashlib.sha256()
    digest.update(f"{cache_key}\n{model}\n{extra}\n".encode("utf-8"))
    digest.update(prompt.encode("utf-8"))
    hexed = digest.hexdigest()
    return _CLI_CACHE_DIR / hexed[:2] / f"{hexed}.json"


def _cli_cache_get(cache_key: Optional[str], *, model: str, prompt: str, extra: str = "") -> Optional[str]:
    """Cached raw response text, or None. Never raises — a bad cache must not
    break a compile, it just means paying for the call."""
    if not cache_key or not _cli_cache_enabled():
        # Not a miss — the cache was never asked. Counting this would report a
        # run with the cache switched off as one that missed on every document,
        # which is the opposite of the truth (it never had a chance to hit).
        return None
    # Every counted outcome below is "the cache was consulted", which is exactly
    # the funnel every CLI client passes through — one instrumentation point
    # rather than one per client, so a new client cannot forget to report.
    try:
        payload = json.loads(
            _cli_cache_path(cache_key, model=model, prompt=prompt, extra=extra).read_text(encoding="utf-8")
        )
        raw = payload.get("raw")
    except (OSError, json.JSONDecodeError, ValueError):
        # An unreadable or corrupt entry means paying for the call, so it is a
        # miss in the only sense a caller cares about: cost.
        _note_cache_lookup(hit=False)
        return None
    hit = isinstance(raw, str)
    _note_cache_lookup(hit=hit)
    return raw if hit else None


def _cli_cache_put(cache_key: Optional[str], raw: str, *, model: str, prompt: str, extra: str = "") -> None:
    """Store a SUCCESSFUL response. Atomic tmp+replace with a pid/random suffix,
    matching the manifest/sidecar idiom, so concurrent extractions can't collide."""
    if not cache_key or not _cli_cache_enabled() or not raw:
        return
    path = _cli_cache_path(cache_key, model=model, prompt=prompt, extra=extra)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps({"model": model, "raw": raw}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a cache that can't be written is a slow compile, not a failed one


def _cli_cache_drop(cache_key: Optional[str], *, model: str, prompt: str, extra: str = "") -> None:
    """Forget an answer the CALLER rejected. Never raises.

    ``_cli_cache_put`` stores every PARSEABLE answer, but parseable is not the
    same as accepted — schema validation lives one layer up, in
    :class:`tesserae.llm_extractor.LLMResearchExtractor`. Without this, JSON
    that parses and then violates the node-type vocabulary is stored as if it
    were a success, which is worse than not caching at all: the extractor's
    retry loop re-reads its OWN bad answer (zero extra LLM calls while stderr
    says "retrying"), and because ``_CLI_CACHE_DIR`` has no eviction the doc
    then fails identically forever — ``--retry-fallbacks`` can never recover
    it until the document's bytes change.

    ``prompt`` must be the SAME assembled prompt the ``_cli_cache_put`` used,
    or this unlinks a path nothing ever wrote and the rejected answer survives
    on disk — a silent no-op is the exact regression this cache exists to
    prevent. That is why both sides go through one ``_stitch_json_prompt`` +
    ``_cache_coords`` pair per client rather than re-deriving the bytes.

    ponytail: ceiling — the drop is BY PATH, and the path is content-derived, so
    two byte-identical documents extracted concurrently share it: one worker
    can unlink the good answer another just stored. The cost is one cache miss
    (the next run re-asks and re-validates), never a wrong graph, so this stays
    a plain unlink rather than a compare-and-delete. Upgrade path if it ever
    matters: thread the rejected raw text down and unlink only when the stored
    ``raw`` matches it.
    """
    if not cache_key or not _cli_cache_enabled():
        return
    try:
        _cli_cache_path(cache_key, model=model, prompt=prompt, extra=extra).unlink()
    except (OSError, ValueError):
        pass  # already gone, or unreadable — either way there is nothing to serve


def _stitch_json_prompt(*, system: str, user: str, schema_name: str) -> str:
    """The single prompt the CLI clients actually send for ``complete_json``.

    Neither ``claude -p`` nor ``codex exec`` exposes a separate system slot, so
    system + JSON-only contract + user are stitched into one string. Defined
    ONCE, module level, because it is now also the cache identity: a
    ``complete_json`` that stores an answer and a ``forget_cached_answer`` that
    drops it must produce byte-identical text or the drop silently misses.
    """
    return (
        f"{system.strip()}\n\n"
        f"Respond with valid JSON only — no Markdown fences, no prose, "
        f"no trailing commas. Schema name: {schema_name}.\n\n"
        f"{user}"
    )


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


#: The providers a user may name. Anything else is a typo, and a typo used to be
#: silently treated as "claude" — so a config saying ``openrouter`` ran against
#: Anthropic and reported a model error about a model the user never chose.
_VALID_PROVIDERS = ("claude", "codex", "anthropic", "openai", "custom")

#: The HTTP dialect, which is NOT the same question as which backend. Anthropic
#: speaks ``POST {base}/v1/messages``; OpenAI-compatible servers (vLLM, LiteLLM,
#: OpenRouter, Together, Ollama, LM Studio) speak ``POST {base}/chat/completions``.
#: Conflating the two is why ``provider=custom`` could only ever reach an
#: Anthropic-shaped endpoint.
_VALID_API_STYLES = ("anthropic", "openai")

#: Providers that carry a USER-SPECIFIED endpoint: a base URL, a model name and a
#: credential the user chose. Falling through from one of these to another
#: provider is what produced the reported bug — the user's model name reached a
#: backend they never configured, which rejected it. Falling back between the two
#: OAuth CLIs (claude <-> codex) carries no such identity and stays allowed: they
#: take no base_url, and their model is scoped per provider.
_ENDPOINT_PROVIDERS = ("anthropic", "openai", "custom")


class LLMProviderConfigError(RuntimeError):
    """The configured LLM provider cannot be built as asked.

    Raised instead of quietly falling through to a different provider. The
    message names provider, style, base_url, model and credential kind, because
    the failure this replaces was a model error from a backend the user never
    configured.
    """


def _normalize_base_url(raw: Optional[str], style: str) -> Optional[str]:
    """Trim a base URL to what each SDK actually wants appended.

    The Anthropic SDK appends ``/v1/messages`` itself, so the OpenAI-convention
    ``https://gw/v1`` that every gateway's README shows becomes
    ``https://gw/v1/v1/messages`` — a 404 that reads like a wrong model. Strip a
    single trailing ``/v1`` there. The OpenAI wire is the mirror image: this
    code appends ``/chat/completions``, so exactly one ``/v1`` must be present.

    Only ONE trailing segment is touched, and the rewrite is logged, because a
    proxy legitimately serving ``/anthropic/v1`` must not be silently rerouted.
    """
    if not raw:
        return raw
    url = raw.strip().rstrip("/")
    if not url:
        return None
    if style == "anthropic":
        if url.endswith("/v1"):
            trimmed = url[: -len("/v1")]
            logger.info("base_url %s ends in /v1 and the Anthropic SDK appends "
                        "/v1/messages itself; using %s", url, trimmed)
            return trimmed
        return url
    # openai wire: this client appends /chat/completions, so /v1 must be there
    if not url.endswith("/v1"):
        logger.info("base_url %s has no /v1; using %s/v1 for the OpenAI wire", url, url)
        return url + "/v1"
    return url


class AnthropicLLMJsonClient:
    """LLMJsonClient backed by ``anthropic.Anthropic``."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_tokens: int = 4096,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> None:
        # Hardcoded literal is a last-resort fallback behind the configured
        # llm_model (env TESSERAE_LLM_MODEL → global config).
        self.model = (
            model
            or _configured_default_model(("anthropic", "custom"))
            or "claude-sonnet-4-6"
        )
        self.base_url = _normalize_base_url(base_url, "anthropic")
        self.auth_token = auth_token
        self.api_key = api_key
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

        # A bearer token and an api key are DIFFERENT headers, and which one a
        # gateway wants is not guessable — so it is configured, not inferred.
        # Passing ``api_key=`` also suppresses the SDK's own ANTHROPIC_AUTH_TOKEN
        # resolution, so the two are mutually exclusive here rather than both set.
        client_kwargs: dict = {"timeout": timeout}
        if auth_token:
            client_kwargs["auth_token"] = auth_token
        else:
            client_kwargs["api_key"] = api_key
        if self.base_url:
            # Only pass when set so the SDK's own default/env resolution
            # (ANTHROPIC_BASE_URL) still applies otherwise.
            client_kwargs["base_url"] = self.base_url
        self._client = anthropic.Anthropic(**client_kwargs)
        try:
            self._rate_limit_cls = anthropic.RateLimitError
            self._status_cls = anthropic.APIStatusError
        except AttributeError:  # pragma: no cover
            self._rate_limit_cls = None
            self._status_cls = None


    @property
    def identity(self) -> str:
        """What this client actually talks to — for error messages.

        Every custom-endpoint failure used to be reported without naming the
        provider, the URL or the model, which is what made a fallback provider's
        model error look like the user's own misconfiguration.
        """
        cred = "auth_token" if self.auth_token else ("api_key" if self.api_key else "none")
        return (f"style=anthropic base_url={self.base_url or 'https://api.anthropic.com'} "
                f"model={self.model} auth={cred}")

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
        # Clear first: a caller reads last_failure_kind() only after a None
        # return, and a stale note from an earlier call would misattribute it.
        _note_failure(None)
        _note_raw(None)  # ...and the same for the raw reply, for the same reason
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
                # 401/403 is a credential, 404 is the wrong URL or wire, and
                # a 400 naming the model is the wrong model. Reporting all
                # three as "unavailable" made a misconfigured endpoint look
                # exactly like having no LLM configured at all — and the
                # message never said which endpoint or model was tried.
                _code = getattr(exc, "status_code", None)
                _detail = str(exc)
                if _code in (401, 403):
                    _kind = "auth"
                elif _code == 404 or (_code == 400 and "model" in _detail.lower()):
                    _kind = "endpoint"
                else:
                    _kind = "unavailable"
                logger.warning(
                    "AnthropicLLMJsonClient.complete_json failed (%s, schema=%s) [%s]: %s",
                    _kind, schema_name, self.identity, exc,
                )
                _note_failure(_kind)
                return None

        text = _extract_text(response)
        _note_raw(text)
        if not text:
            # A 200 with an empty body is a transport failure wearing a clean
            # status code, not a bad generation.
            _note_failure("unavailable")
            return None
        # The model started the assistant turn from `{`. Re-prepend so we
        # parse what the model "thinks" it wrote.
        if not text.lstrip().startswith(("{", "[")):
            text = "{" + text
        parsed = parse_json_tolerant(text)
        if parsed is None:
            _note_failure("unparseable")  # the model answered; it wasn't JSON
        return parsed

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
        home = _Path.home()
        discovered = sorted(
            str(p)
            for p in home.glob(".claude*")
            if p.is_dir() and not p.name.endswith((".bak", ".old"))
        )
        if config_dirs:
            self.config_dirs = list(config_dirs)
        elif _os.environ.get("CLAUDE_CONFIG_DIR"):
            # PREFERRED-first, not exclusive. This used to pin the client to
            # the single env dir, which silently disabled the rotation loop
            # below — the one mechanism that exists to survive a rate-limited
            # account. Anything launched from a Claude Code session inherits
            # CLAUDE_CONFIG_DIR, so in practice a compile could only ever use
            # that one account: when its quota ran out, 1,531 documents fell
            # back to deterministic extraction while two other logged-in
            # accounts sat idle. The env var says which account to try FIRST,
            # not which is the only one that may be tried.
            env_dir = _os.environ["CLAUDE_CONFIG_DIR"]
            self.config_dirs = [env_dir] + [d for d in discovered if d != env_dir]
        else:
            self.config_dirs = discovered or [str(home / ".claude")]
        self.timeout = int(timeout) if timeout is not None else None
        #: Set once every account in :attr:`config_dirs` is a proven dead end
        #: (quota exhausted, or not logged in). Per-instance on purpose — see
        #: the guard in :meth:`_run_prompt`.
        self._accounts_exhausted = False

    def _run_prompt(
        self,
        prompt: str,
        *,
        error_label: str = "ClaudeCLIJsonClient call failed",
    ) -> Optional[str]:
        """Run ``claude -p`` over the rotating ``config_dirs`` and return the
        raw stdout from the first account that succeeds, else None.

        This is the single rotation loop shared by :meth:`complete_json`
        (parses the stdout as JSON) and :meth:`complete_text` (returns the
        prose as-is). Each config_dir is one account; a rate-limited or
        failing account falls through to the next, so synthesis never gets
        stuck on a single exhausted account while another has headroom.

        Takes no ``max_retries``: rotation IS this client's retry, and the
        policy below is deliberately "never hammer the same account" — every
        outcome either returns or moves to the next config_dir. It used to
        accept one and wrap the body in ``for attempt in range(max_retries+1)``
        whose second iteration was unreachable, so the parameter documented a
        retry that never happened. Callers' ``max_retries`` (part of the
        :class:`LLMJsonClient` protocol, honored by the Anthropic SDK client
        for rate-limit backoff) is accepted and ignored one level up.
        """
        import os as _os
        import subprocess as _subprocess
        from pathlib import Path as _Path

        # Quota exhaustion is an ACCOUNT fact, not a document fact. Once every
        # configured account has answered "you've hit your … limit", the next
        # document cannot possibly succeed either — but this used to spawn a
        # `claude` child per account per remaining document to rediscover that,
        # 1,531 times in one observed run. Latch it and answer instantly; the
        # caller still marks each doc `fallback: true`, so
        # `compile --changed-only --retry-fallbacks` recovers them all once the
        # limit resets.
        # Scoped to THIS client, not the process: a pinned single-account
        # client must not disable an unrelated one, and a daemon/MCP process
        # that outlives a quota reset must not stay disabled forever. Each
        # compile builds its own client, so the verdict expires naturally.
        if self._accounts_exhausted:
            _note_failure("exhausted")
            return None

        last_error: Optional[Exception] = None
        all_not_logged_in = True  # only True if EVERY tried config_dir was Not-logged-in
        any_attempted = False
        # Latching needs proof about EVERY account, not just the last one. With
        # only last_error to go on, account A timing out and account B hitting
        # quota latched the whole run — A was never shown to be exhausted.
        attempted = 0
        quota_hits = 0
        dead_ends = 0  # exhausted OR not-logged-in: cannot serve this run either way
        timed_out = False  # same bug class as the codex client — see below
        default_claude_dir = str(_Path.home() / ".claude")
        for config_dir in self.config_dirs:
            any_attempted = True
            attempted += 1
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
                    # ponytail: --strict-mcp-config, NOT --max-turns 1. The
                    # turn cap counted tool calls, so any MCP server in the
                    # user's config dir burned the only turn and the CLI
                    # exited 1 ("Reached max turns (1)") before emitting JSON
                    # — every extraction then fell back to deterministic.
                    # Loading no MCP servers is what "one-shot" actually meant.
                    "--strict-mcp-config",
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
                        dead_ends += 1
                        continue  # skip to next config_dir
                    # Non-auth failure on this profile (incl. a rate
                    # limit — `claude -p` exits non-zero with "You've
                    # hit your … limit"); the except below clears the
                    # all_not_logged_in tracker and rotates to the next
                    # account.
                    raise RuntimeError(
                        f"claude exited {proc.returncode}: "
                        f"{stderr_text or stdout_text}"
                    )
                if not (proc.stdout or "").strip():
                    # Exit 0 with an EMPTY body is transport wearing a clean
                    # exit code, not an answer (the same shape complete_json
                    # already classifies "unavailable" one layer up). Returning
                    # it ended the rotation on the first account; rotating
                    # instead gives every remaining profile its turn, which is
                    # this client's whole retry policy.
                    all_not_logged_in = False
                    last_error = RuntimeError(
                        f"claude exited 0 but printed nothing (config dir {config_dir})"
                    )
                    continue
                return proc.stdout
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if _is_quota_exhaustion(exc):
                    quota_hits += 1
                    dead_ends += 1
                # Reaching here means this profile did NOT answer "Not logged
                # in" (that branch continues above without raising), so the
                # auth verdict is off the table. It used to be cleared only on
                # the non-auth returncode path, which left a timeout or a spawn
                # error reported as "Claude CLI not logged in — run
                # `claude /login`" — the same confidently-wrong remedy this
                # change exists to remove.
                all_not_logged_in = False
                # And a timeout is NOT "the backend is unavailable" either: all
                # we know is that this attempt hit the bound. Same defect the
                # codex client had; fixed in both — including the overcorrection
                # of then asserting the CLI *was* reachable, which this layer
                # cannot see (a connect stall raises the same TimeoutExpired).
                if isinstance(exc, _subprocess.TimeoutExpired):
                    timed_out = True
                # Don't retry on the same config_dir; fall through to
                # the next one. Auth/network/rate-limit issues are best
                # handled by switching accounts, not hammering one.
                continue
        # All profiles exhausted. If every one failed with "Not logged in",
        # emit the actionable once-per-process auth warning. Otherwise emit
        # the generic warning so genuine errors stay visible.
        if any_attempted and all_not_logged_in and last_error is not None:
            # Per-CALL verdict, deliberately outside the once-per-process log
            # guard below: the caller re-reads it for every document, and
            # flattening it to "unavailable" is what made 136 of 137 per-doc
            # lines assert transport/capacity for an expired session.
            _note_failure("auth")
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
        if quota_hits and attempted and dead_ends == attempted:
            # EVERY account tried was out of quota (or not logged in, which
            # cannot serve this run either) and at least one said so outright.
            # A timeout or a network blip among them leaves dead_ends short of
            # attempted, so the run keeps trying — transient != exhausted.
            self._accounts_exhausted = True
            _note_failure("exhausted")
            logger.warning(
                "[tesserae] every configured account is out of quota (%d tried); "
                "remaining documents will use deterministic extraction without "
                "further LLM calls. Re-run `compile --changed-only "
                "--retry-fallbacks` after the limit resets. Last: %s",
                len(self.config_dirs),
                last_error,
            )
            return None
        if last_error is not None:
            if timed_out:
                _note_failure("timeout")
                logger.warning(
                    "%s — timed out after %ss: %s. All this establishes is that the "
                    "attempt did not finish inside the per-attempt bound — a slow "
                    "generation and a stall that never reached the provider look the "
                    "same from here. If the document is large, raise "
                    "TESSERAE_EXTRACT_TIMEOUT (0 = no bound) or split it; if it is not, "
                    "check that the provider is reachable from this host.",
                    error_label,
                    self.timeout,
                    last_error,
                )
            else:
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
        # Clear first so a cache hit can't leave a stale note behind.
        _note_failure(None)
        _note_raw(None)
        # Stitch system + user into a single prompt for the CLI's -p flag.
        prompt = _stitch_json_prompt(system=system, user=user, schema_name=schema_name)
        model, extra = self._cache_coords(schema_name)
        cached = _cli_cache_get(cache_key, model=model, prompt=prompt, extra=extra)
        if cached is not None:
            # A replayed answer is still what the provider said; note it so a
            # caller recovering an odd SHAPE behaves the same on a cache hit as
            # on a live call, rather than only on the run that paid.
            _note_raw(cached)
            return parse_json_tolerant(cached)
        raw = self._run_prompt(
            prompt,
            error_label=f"ClaudeCLIJsonClient.complete_json failed (schema={schema_name})",
        )
        _note_raw(raw)
        if raw is None:
            # _run_prompt records "timeout" when that's what it was; every
            # other exhausted-all-config-dirs path means nothing was answered.
            if last_failure_kind() is None:
                _note_failure("unavailable")
            return None
        parsed = parse_json_tolerant(raw)
        if parsed is None:
            # `claude -p` can exit 0 and print nothing; that is transport, not
            # a bad generation. Only a non-empty answer can be a bad one.
            _note_failure("unavailable" if not raw.strip() else "unparseable")
        if parsed is not None:
            # Only a parseable answer is worth keeping — caching a malformed
            # generation would make one bad roll permanent.
            _cli_cache_put(cache_key, raw, model=model, prompt=prompt, extra=extra)
        return parsed

    def _cache_coords(self, schema_name: str) -> tuple:
        """``(model, extra)`` for the on-disk cache. One definition so a write
        and a later drop can never disagree about which file they mean."""
        return (self.model or "claude-cli-default", schema_name)

    def forget_cached_answer(
        self, cache_key: Optional[str], *, schema_name: str, system: str, user: str
    ) -> None:
        """Drop the cached answer for this call — the CALLER rejected it.

        Parseable is not accepted; see :func:`_cli_cache_drop`. ``system`` and
        ``user`` are required (not optional-with-a-default) because the cache
        entry is addressed by the assembled prompt: a caller that could omit
        them would get a drop that unlinks nothing and reports success, which
        is exactly the stale-answer failure this method exists to prevent. Pass
        the SAME pair that was passed to :meth:`complete_json`.
        """
        model, extra = self._cache_coords(schema_name)
        prompt = _stitch_json_prompt(system=system, user=user, schema_name=schema_name)
        _cli_cache_drop(cache_key, model=model, prompt=prompt, extra=extra)

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
        # See CodexCLIJsonClient.complete_text: clear the thread-local verdict
        # so a recycled pool worker cannot read the prior document's.
        _note_failure(None)
        prompt = f"{system.strip()}\n\n{user}"
        raw = self._run_prompt(
            prompt,
            error_label="ClaudeCLIJsonClient.complete_text failed",
        )
        text = (raw or "").strip()
        return text or None


# ---------------------------------------------------------------------------
# Codex CLI implementation (OAuth — `codex exec`, no API key needed)
# ---------------------------------------------------------------------------


#: A transport failure (dropped stream, 5xx, provider capacity window) is
#: transient, but the ``codex_homes`` rotation is ONE attempt on a
#: single-account machine — so a momentary blip permanently condemns that
#: document to the deterministic baseline until a human re-runs. Re-run the
#: WHOLE rotation with backoff against the SAME homes.
#: What that actually buys, honestly: only blips that fail FAST relative to
#: ``self.timeout``. ``_run_prompt`` refuses to START a new rotation once the
#: cumulative elapsed time reaches that bound, so with the default 1800s a
#: rotation that spent the whole budget in network wait gets ZERO retries — the
#: retry count is a ceiling, not a promise. That is deliberate: 3x1800s per
#: document while ``.tesserae/compile.lock`` is held is worse than the
#: deterministic baseline this exists to avoid.
#: Second ceiling: homes are NOT accounts. When ``CODEX_HOME`` is absent from
#: the environment — a daemon, a launchd job, a subprocess with a scrubbed env —
#: ``__init__`` discovers every ``~/.codex*`` DIRECTORY, and on the machine this
#: was measured on that is five (``.codex``, ``.codex-nomcp``,
#: ``.codex-nomcp-pr208``, ``.codex-personal1``, ``.codex-personal2``), four of
#: them stale. A single stale directory answering "not logged in" must not
#: disable the retry for the healthy one, so the logged-out verdict is tracked
#: PER HOME: such a home is skipped on later attempts (it will answer the same),
#: and only an all-homes-logged-out rotation aborts — which is what keeps an
#: expired session from costing 3 spawns + 6s of sleep per document.
#: ponytail: small constants, not config knobs — nobody tunes them, and
#: account rotation is NOT available on this machine so it must not be
#: assumed as the remedy.
_TRANSPORT_RETRIES = 2
_TRANSPORT_BACKOFF = 2.0  # seconds; doubled per attempt



#: Retried status codes and the bound on retries for the OpenAI API client.
#: 429 is the rate limit; 500/502/503 are the API's own transient failures.
_OPENAI_RETRY_CODES = frozenset({429, 500, 502, 503})
_OPENAI_RETRIES = 6
_OPENAI_RETRY_CAP_S = 30.0


def _openai_retry_delay(headers, detail: str, attempt: int) -> float:
    """How long the API asked us to wait, else a capped exponential backoff.

    Prefers ``Retry-After`` (seconds), then the "try again in 1.898s" phrase
    the rate limiter puts in the body, then ``2 ** attempt`` — always capped,
    never zero, so a misparsed header cannot become a hot loop.
    """
    import re as _re

    wait = None
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        if raw:
            wait = float(raw)
    except (TypeError, ValueError):
        wait = None
    if wait is None:
        m = _re.search(r"try again in ([0-9.]+)\s*(ms|s)", detail or "")
        if m:
            wait = float(m.group(1)) / (1000.0 if m.group(2) == "ms" else 1.0)
    if wait is None:
        wait = float(2 ** attempt)
    return max(0.25, min(_OPENAI_RETRY_CAP_S, wait + 0.25))


class OpenAIAPIJsonClient:
    """The OpenAI HTTP API, for models the Codex CLI cannot serve.

    Exists because the PUBLISHED LoCoMo grader is ``gpt-4o-mini`` and this
    codebase could not reach it. Every OpenAI-family model routed through
    :class:`CodexCLIJsonClient`, and Codex on a ChatGPT account answers
    ``The 'gpt-4o-mini' model is not supported when using Codex with a ChatGPT
    account`` — so every run reported ``judge: UNMET`` and no number this
    project produced was comparable to a published one. That is a missing
    capability, not a configuration choice.

    Stdlib ``urllib`` rather than the ``openai`` package, following
    :class:`~tesserae.retrieval.hybrid.OpenAIEmbeddingBackend`, which posts to
    the same API the same way. A grader is a few hundred short calls; it does
    not justify a dependency the base install would carry forever.

    ``None`` on any failure, like every client here, so a caller that cannot
    grade stops rather than scoring an ungraded answer WRONG.
    """

    name = "openai-api"

    #: Where the OpenAI wire lives when nobody says otherwise.
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(self, model: str, *, api_key: Optional[str] = None,
                 timeout: float = 120.0, base_url: Optional[str] = None,
                 auth_token: Optional[str] = None) -> None:
        self.model = model
        # An explicit credential beats the ambient one; OPENAI_API_KEY is only a
        # fallback for the default host, not for a user's own gateway, where an
        # unrelated key would be leaked to a third party.
        self._token = auth_token or ""
        self._key = api_key or ""
        self.base_url = _normalize_base_url(base_url, "openai") or self.DEFAULT_BASE_URL
        if not self._key and not self._token and self.base_url == self.DEFAULT_BASE_URL:
            self._key = os.environ.get("OPENAI_API_KEY") or ""
        self._timeout = timeout

    @property
    def available(self) -> bool:
        # A local endpoint (Ollama, LM Studio, a keyless vLLM) needs no
        # credential at all, so requiring one made every such server unusable.
        return bool(self._key or self._token or self.base_url != self.DEFAULT_BASE_URL)

    @property
    def identity(self) -> str:
        cred = "auth_token" if self._token else ("api_key" if self._key else "none")
        return f"style=openai base_url={self.base_url} model={self.model} auth={cred}"

    def _post(self, *, system: str, user: str, json_mode: bool) -> Optional[str]:
        import json as _json
        import urllib.error
        import urllib.request

        if not self.available:
            return None
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # The published grader runs at temperature 0. A judge that varies
            # run to run is a judge whose disagreements cannot be told from the
            # arms it is grading.
            "temperature": 0,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        # Both credential kinds ride the same header on this wire — there is only
        # one scheme here, so nothing is being guessed. A keyless local server
        # gets no Authorization header rather than an empty bearer.
        _cred = self._token or self._key
        if _cred:
            headers["Authorization"] = f"Bearer {_cred}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=_json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        # A 429 is not a failure, it is a wait. The API says how long — a
        # Retry-After header, or "try again in 1.9s" in the body — and a
        # benchmark run that treats it as an error scores the question zero:
        # measured 2026-08-29, 390 rate-limit replies in one conversation's
        # answering turned 46 gradeable rows into errors and a 90% arm into
        # 68%. Retried with the server's own delay, capped, a bounded number
        # of times; anything else 4xx is still returned at once.
        payload = None
        for attempt in range(_OPENAI_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = _json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:400]
                except Exception:  # pragma: no cover - best effort
                    pass
                if exc.code in _OPENAI_RETRY_CODES and attempt < _OPENAI_RETRIES:
                    time.sleep(_openai_retry_delay(exc.headers, detail, attempt))
                    continue
                # 401/403 is a credential, 404 is the wrong URL, and a 400
                # naming the model is the wrong model. Reporting all three as
                # one "unavailable" is what made a misconfigured endpoint
                # indistinguishable from having no LLM at all.
                if exc.code in (401, 403):
                    kind = "auth"
                elif exc.code == 404 or (exc.code == 400 and "model" in detail.lower()):
                    kind = "endpoint"
                else:
                    kind = "openai-api"
                print(f"[openai-api] HTTP {exc.code} ({kind}) {self.identity}: {detail}",
                      file=sys.stderr)
                _note_failure(kind)
                return None
            except Exception as exc:  # pragma: no cover - network shapes vary
                print(f"[openai-api] {type(exc).__name__} for {self.model}: {exc}",
                      file=sys.stderr)
                _note_failure("openai-api")
                return None
        if payload is None:
            return None
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
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
        _note_raw(None)  # cleared on entry so a stale reply cannot be misread
        cached = _cli_cache_get(cache_key, model=self.model, prompt=f"{system}\n{user}",
                                extra=schema_name)
        if cached is not None:
            _note_raw(cached)
            return parse_json_tolerant(cached)
        for _ in range(max(1, max_retries)):
            raw = self._post(system=system, user=user, json_mode=True)
            _note_raw(raw)
            if raw is None:
                continue
            parsed = parse_json_tolerant(raw)
            if parsed is not None:
                _cli_cache_put(cache_key, raw, model=self.model,
                               prompt=f"{system}\n{user}", extra=schema_name)
                return parsed
        return None

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        for _ in range(max(1, max_retries)):
            raw = self._post(system=system, user=user, json_mode=False)
            if raw is not None:
                return raw
        return None


class CodexCLIJsonClient:
    """LLMJsonClient backed by the ``codex`` CLI subprocess over OAuth.

    Mirrors :class:`ClaudeCLIJsonClient` but shells out to
    ``codex exec --skip-git-repo-check --sandbox read-only`` with the prompt
    on stdin and the final message captured via ``--output-last-message``.
    No API key required — auth comes from the credentialed ``CODEX_HOME``.
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

        # Normal precedence: an explicit caller model, else the configured
        # llm_model (provider-scoped, so a claude-shaped name cannot land here
        # via config), else CODEX_DEFAULT_MODEL as the default. Choosing a codex
        # model is supported — `--llm-model`, `llm_model`, and the per-feature
        # community/distill model settings all reach this argument.
        self.model = model or _configured_default_model(("codex",)) or CODEX_DEFAULT_MODEL
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
        home = _Path.home()
        discovered = sorted(
            str(p)
            for p in home.glob(".codex*")
            # A codex home is a DIRECTORY holding auth.json. The glob otherwise
            # sweeps up siblings like ~/.codex-review-pr208.log — 24 of them on
            # one real machine — and every one costs a doomed `codex exec` per
            # document before rotation moves on.
            if p.is_dir()
            and not p.name.endswith((".bak", ".old"))
            and (p / "auth.json").exists()
        )
        if codex_homes:
            self.codex_homes = list(codex_homes)
        elif _os.environ.get("CODEX_HOME"):
            # Preferred-first, not exclusive — same reasoning as
            # ClaudeCLIJsonClient.config_dirs: pinning to the env var disabled
            # the rotation loop that exists to survive a rate-limited account.
            env_home = _os.environ["CODEX_HOME"]
            self.codex_homes = [env_home] + [d for d in discovered if d != env_home]
        else:
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
        rate-limited or failing CODEX_HOME falls through to the next, and the
        whole rotation may be re-run with backoff (see ``_TRANSPORT_RETRIES``)
        because on a single-account machine the rotation is one attempt.
        "May", not "is": a new rotation only STARTS while the cumulative
        elapsed time is under ``self.timeout``, so a rotation that consumed the
        whole budget — the observed capacity shape, ``codex exec`` in network
        wait for ~``self.timeout`` then a non-zero exit — is tried exactly once.
        The retries are reached by failures that are FAST relative to that
        bound.

        On failure this records the verdict for the calling thread via
        :func:`_note_failure` — ``"timeout"`` (the attempt didn't finish inside
        the bound; that says nothing about whether the provider saw it),
        ``"auth"`` (every home tried answered "not logged in") or
        ``"unavailable"`` (nothing was generated).
        ``complete_json`` must not overwrite it.
        """
        import os as _os
        import subprocess as _subprocess
        import tempfile as _tempfile
        from pathlib import Path as _Path

        last_error: Optional[Exception] = None
        timed_out = False
        # An auth/binary failure is NOT transient: with an expired OAuth token
        # the retry loop costs 3 spawns + 6s of sleep PER DOCUMENT, so a
        # 137-doc corpus pays 411 codex spawns and ~14 minutes of pure
        # time.sleep for a compile that is guaranteed to fail. Detected with
        # exactly the signals the module already trusts — the Claude client's
        # "not logged in" substring test, and FileNotFoundError for a missing
        # binary. Anything subtler stays a transport failure and gets retried.
        # Logged-out is tracked PER HOME because homes are not accounts: an
        # env without CODEX_HOME rotates over every ``~/.codex*`` directory,
        # and one stale directory must not cancel the retry for a healthy one.
        logged_out_homes: set[str] = set()
        binary_missing = False
        # True only while EVERY home tried has answered "not logged in" — the
        # same all-or-nothing rule the Claude client uses, so a capacity failure
        # on one home can't get reported as an auth problem just because
        # another home was logged out. Narrower than ``logged_out_homes``, which
        # a still-healthy sibling home leaves non-empty without making the whole
        # call an auth failure.
        auth_only = True
        started = time.monotonic()
        # OUTER loop = the retry; INNER loop = the account rotation. Keeping
        # the retry outside preserves the "never get stuck on a rate limit
        # while another account has headroom" policy: every home gets a turn
        # before we sleep and start over on the same set.
        for attempt in range(_TRANSPORT_RETRIES + 1):
            for codex_home in self.codex_homes:
                if codex_home in logged_out_homes:
                    # It answered "not logged in" on an earlier attempt and will
                    # answer the same now. Don't pay another spawn for it.
                    continue
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
                        stderr_text = (proc.stderr or "").strip()
                        stdout_text = (proc.stdout or "").strip()
                        last_error = RuntimeError(
                            f"codex exited {proc.returncode}: "
                            f"{stderr_text or stdout_text}"
                        )
                        # Same substring test ClaudeCLIJsonClient already uses.
                        # Keep rotating (a later home may be logged in); THIS
                        # home is then skipped for the rest of the call, and
                        # only an all-homes-logged-out rotation stops the retry.
                        if "not logged in" in f"{stderr_text}\n{stdout_text}".lower():
                            logged_out_homes.add(codex_home)
                        else:
                            auth_only = False
                        continue
                    final = (
                        output_path.read_text(encoding="utf-8", errors="replace")
                        if output_path.exists()
                        else ""
                    )
                    answer = final or proc.stdout or ""
                    if not answer.strip():
                        # Exit 0 with an EMPTY last message is a capacity blip
                        # wearing a clean exit code, not an answer. Returning it
                        # made this the ONE failure shape that never entered the
                        # retry loop — while the extractor above declines to
                        # re-ask precisely because "the transport layer already
                        # retried". A non-zero exit got three rolls; this got
                        # one. Fall through to the same retry as every other
                        # transport failure.
                        auth_only = False
                        last_error = RuntimeError(
                            f"codex exited 0 but produced an empty last message "
                            f"(CODEX_HOME {codex_home})"
                        )
                        continue
                    return answer
                except _subprocess.TimeoutExpired as exc:
                    # The wedge guard already bounded THIS attempt
                    # (TESSERAE_EXTRACT_TIMEOUT, default 1800s). Retrying would
                    # multiply that bound by _TRANSPORT_RETRIES — resurrecting
                    # the multi-day hang _run_cli's process-group kill exists
                    # to prevent. Rotate the homes, then give up.
                    last_error = exc
                    timed_out = True
                    auth_only = False
                    continue
                except FileNotFoundError as exc:
                    # No ``codex`` on PATH. No amount of backoff installs it,
                    # and the next home would fail the same way. Permanent, but
                    # NOT an auth failure — `codex login` needs a binary first.
                    last_error = exc
                    binary_missing = True
                    auth_only = False
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    auth_only = False
                    continue
                finally:
                    try:
                        output_path.unlink()
                    except FileNotFoundError:
                        pass
            # Bound the retry by CUMULATIVE elapsed time, not just per attempt:
            # a capacity window presents as `codex exec` sitting in network wait
            # at 0% CPU for ~self.timeout and then exiting NON-ZERO, which is
            # the returncode path above, not the TimeoutExpired guard — so
            # without this a doc costs 3x the wedge bound while
            # .tesserae/compile.lock is held. Falsy self.timeout (None / 0 =
            # "run to completion") means no cumulative bound either.
            # ponytail: this stops a NEW rotation from STARTING once the budget
            # is spent; it does not interrupt one in flight. Real guarantee for
            # a single home is "< 2x self.timeout", not "== self.timeout".
            # A hard deadline would mean shrinking the timeout passed to
            # _run_cli, which would change the documented per-ATTEMPT bound.
            backoff = _TRANSPORT_BACKOFF * (2 ** attempt)
            # Charge the SLEEP to the budget: the next rotation starts at
            # ``elapsed + backoff``, so checking elapsed ALONE let a fast
            # failure sleep past the bound and start anyway (measured:
            # timeout=1s, backoff=2s, attempt 2 began at t=2.01s).
            budget_spent = (
                bool(self.timeout)
                and (time.monotonic() - started + backoff) >= self.timeout
            )
            if (
                timed_out
                or binary_missing
                or budget_spent
                or attempt >= _TRANSPORT_RETRIES
                or len(logged_out_homes) == len(self.codex_homes)
            ):
                break
            time.sleep(backoff)
        if last_error is not None:
            # ONE record per call, not one per attempt: three attempts across
            # 137 docs would drown the compile output. Say plainly which
            # failure mode this was — the operator-facing per-doc line used to
            # name only the exception class, so a capacity window read exactly
            # like a schema violation.
            if timed_out:
                # A timeout is NOT a capacity outage. Claiming otherwise sends
                # the operator to wait out a window that does not exist and
                # re-run --retry-fallbacks forever — the same
                # confident-wrong-diagnosis this whole classification exists to
                # kill. But the opposite claim is the same mistake mirrored: we
                # see a killed child, not a completed round trip, so a DNS or
                # connect stall reaches this branch too. State the bound, then
                # BOTH remedies.
                _note_failure("timeout")
                logger.warning(
                    "%s — timed out after %ss on %d CODEX_HOME %s: %s. All this "
                    "establishes is that the attempt did not finish inside the "
                    "per-attempt bound — a slow generation and a stall that never "
                    "reached the provider look the same from here. If the document is "
                    "large, raise TESSERAE_EXTRACT_TIMEOUT (0 = no bound) or split it; "
                    "if it is not, check that the provider is reachable from this host. "
                    "`codex login` is unlikely to help — a logged-out home exits fast "
                    "and lands on the auth line instead.",
                    error_label,
                    self.timeout,
                    len(self.codex_homes),
                    "dir" if len(self.codex_homes) == 1 else "dirs",
                    last_error,
                )
            elif auth_only:
                # An expired session is not a capacity window: it will still be
                # expired after any amount of waiting, and the caller renders
                # this verdict once PER DOCUMENT, so it must carry the real
                # remedy rather than "transport/capacity".
                # ponytail: this WARNING is per call, unlike the Claude client's
                # one-shot ``_LOGGED_LOGIN_WARNING``, so a 137-doc compile logs
                # 137 identical lines here on top of the 137 the selective
                # router prints. Not re-gated, because the router's line is the
                # one that names the document and this one is the only place the
                # home count and the raw CLI text appear. Upgrade path if the
                # volume ever bites: log the full line once and a one-line
                # `(auth, see above)` thereafter — the ``auth`` verdict, not the
                # log, is what the per-document diagnosis rides on.
                _note_failure("auth")
                logger.warning(
                    "%s — every CODEX_HOME tried (%d) reported `not logged in`: %s. "
                    "This is NOT a capacity window; run `codex login` and re-compile.",
                    error_label,
                    len(self.codex_homes),
                    last_error,
                )
            else:
                _note_failure("unavailable")
                logger.warning(
                    "%s — provider returned nothing after %d attempt(s) over %d CODEX_HOME "
                    "%s: %s. Nothing was generated, so this is NOT a bad model generation "
                    "— and NOT an auth failure either: a logged-out home reports itself "
                    "and lands on the auth line. If it persists, run `tesserae doctor`.",
                    error_label,
                    attempt + 1,
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
        # Clear first so a cache hit can't leave a stale note behind.
        _note_failure(None)
        _note_raw(None)
        # Same prompt stitching as the Claude CLI client: codex exec has no
        # separate system slot either, so prefix the JSON-only contract.
        prompt = _stitch_json_prompt(system=system, user=user, schema_name=schema_name)
        model, extra = self._cache_coords(schema_name)
        cached = _cli_cache_get(cache_key, model=model, prompt=prompt, extra=extra)
        if cached is not None:
            # A replayed answer is still what the provider said; note it so a
            # caller recovering an odd SHAPE behaves the same on a cache hit as
            # on a live call, rather than only on the run that paid.
            _note_raw(cached)
            return parse_json_tolerant(cached)
        raw = self._run_prompt(
            prompt,
            error_label=f"CodexCLIJsonClient.complete_json failed (schema={schema_name})",
        )
        _note_raw(raw)
        if raw is None:
            # _run_prompt already recorded WHY (timeout vs unavailable) —
            # don't flatten a timeout back into a capacity outage. Only the
            # degenerate "no homes configured, nothing was even tried" path
            # leaves the verdict unset.
            if last_failure_kind() is None:
                _note_failure("unavailable")
            return None
        parsed = parse_json_tolerant(raw or "")
        if parsed is None:
            # `codex exec` can exit 0 and write an EMPTY last-message; that is
            # a transport failure wearing a clean exit code, not a bad
            # generation. Only a non-empty answer can be a bad generation.
            _note_failure("unavailable" if not raw.strip() else "unparseable")
        if parsed is not None:
            # Only a parseable answer is worth keeping — caching a malformed
            # generation would make one bad roll permanent.
            _cli_cache_put(cache_key, raw, model=model, prompt=prompt, extra=extra)
        return parsed

    def _cache_coords(self, schema_name: str) -> tuple:
        """``(model, extra)`` for the on-disk cache. One definition so a write
        and a later drop can never disagree about which file they mean —
        ``extra`` folds in reasoning effort, which is easy to forget twice."""
        return (self.model or "codex-cli-default", f"{schema_name}\n{self.reasoning_effort or ''}")

    def forget_cached_answer(
        self, cache_key: Optional[str], *, schema_name: str, system: str, user: str
    ) -> None:
        """Drop the cached answer for this call — the CALLER rejected it.

        Parseable is not accepted; see :func:`_cli_cache_drop`. ``system`` and
        ``user`` are required for the same reason as on
        :meth:`ClaudeCLIJsonClient.forget_cached_answer`: the entry is
        addressed by the assembled prompt, so a drop without it would silently
        miss.
        """
        model, extra = self._cache_coords(schema_name)
        prompt = _stitch_json_prompt(system=system, user=user, schema_name=schema_name)
        _cli_cache_drop(cache_key, model=model, prompt=prompt, extra=extra)

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
    ) -> Optional[str]:
        """Prose completion over the same rotating CODEX_HOMEs as complete_json."""
        # The verdict is thread-local and pool workers are recycled, so a
        # success here must not leave the PREVIOUS document's verdict readable.
        _note_failure(None)
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

    # Recorded like every other key: reporting the provider's source as
    # "default" when it came from a project config is exactly the kind of
    # mislabel `config status` exists to stop.
    _provider_sources = {}
    if os.environ.get("TESSERAE_LLM_PROVIDER"):
        _provider_sources["provider"] = "env TESSERAE_LLM_PROVIDER"
    elif cfg.get("llm_provider"):
        _provider_sources["provider"] = "project .tesserae/config.json"
    elif global_cfg.get("llm_provider"):
        _provider_sources["provider"] = "~/.tesserae/config.json"
    else:
        _provider_sources["provider"] = "default"
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

    # Deliberate config beats the ambient env var, NOT the other way round.
    # CLAUDE_CONFIG_DIR is inherited by every process a Claude Code session
    # spawns, so honouring it first meant an accidental value silently
    # overrode the accounts the user had actually chosen — and pinned the
    # whole run to one account's quota. Set ``llm_claude_config_dirs`` (a
    # list) in .tesserae/config.json or ~/.tesserae/config.json to say
    # exactly which accounts may be spent, in rotation order; that list is
    # then authoritative and nothing else is tried.
    # NOTE the deliberate absence of a CLAUDE_CONFIG_DIR fallback here. Whatever
    # this returns is passed to the client as an EXPLICIT ``config_dirs=``, which
    # pins it verbatim — so returning ``[env_claude]`` collapsed the rotation to
    # one account no matter what the constructor's own env handling did. Leaving
    # it None hands the decision to ClaudeCLIJsonClient, which puts
    # CLAUDE_CONFIG_DIR first and keeps the other discovered accounts behind it.
    # Only a CONFIGURED list is authoritative, because only that is deliberate.
    # TESSERAE_CLAUDE_CONFIG_DIRS is the CLI's channel for a REPEATED
    # --claude-config-dir: deliberate, ordered, and explicitly Tesserae's, so
    # unlike the ambient CLAUDE_CONFIG_DIR it is safe to treat as
    # authoritative. Scalar CLAUDE_CONFIG_DIR is still deliberately absent —
    # see the note below.
    _env_dirs = os.environ.get("TESSERAE_CLAUDE_CONFIG_DIRS") or ""
    claude_config_dirs = (
        _as_dirs([d for d in _env_dirs.split(os.pathsep) if d])
        or _as_dirs(cfg.get("llm_claude_config_dirs"))
        or _as_dirs(global_cfg.get("llm_claude_config_dirs"))
    )

    # Codex gets the same treatment as claude above, for the same reasons.
    # ``llm_codex_homes`` (a LIST, rotation order) is the modern key; the older
    # singular ``llm_codex_home`` still works and means a one-account list.
    # CODEX_HOME is deliberately NOT a fallback here — see the claude note: a
    # value returned from this function is passed down as an explicit pin, so
    # honouring the env var here would collapse rotation to one account. Left
    # None, CodexCLIJsonClient ranks CODEX_HOME first and keeps the rest.
    _env_codex = os.environ.get("TESSERAE_CODEX_HOMES") or ""
    codex_homes = (
        _as_dirs([d for d in _env_codex.split(os.pathsep) if d])
        or _as_dirs(cfg.get("llm_codex_homes"))
        or _as_dirs(cfg.get("llm_codex_home"))
        or _as_dirs(global_cfg.get("llm_codex_homes"))
        or _as_dirs(global_cfg.get("llm_codex_home"))
    )
    # Back-compat scalar for the callers/CLI that still display a single home.
    codex_home = codex_homes[0] if codex_homes else (os.environ.get("CODEX_HOME") or None)

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
    # One precedence order for every endpoint knob, and a record of which layer
    # won it. ``config status`` used to GUESS the source and credited env vars
    # the resolver deliberately ignores; a resolver that knows the answer should
    # just say it.
    sources: dict = dict(_provider_sources)

    def _pick(key: str, *envs: str, default=None):
        """Tesserae env → project config → global config → default."""
        for env in envs:
            val = os.environ.get(env)
            if val:
                sources[key] = f"env {env}"
                return val
        if cfg.get(f"llm_{key}"):
            sources[key] = "project .tesserae/config.json"
            return cfg.get(f"llm_{key}")
        if global_cfg.get(f"llm_{key}"):
            sources[key] = "~/.tesserae/config.json"
            return global_cfg.get(f"llm_{key}")
        sources[key] = "default"
        return default

    # ANTHROPIC_* stay in the chain one rung below the Tesserae-owned names:
    # they are ambient (any Claude session exports them) and must not outrank a
    # value the user set for Tesserae specifically.
    model = _pick("model", "TESSERAE_LLM_MODEL")
    base_url = _pick("base_url", "TESSERAE_LLM_BASE_URL", "ANTHROPIC_BASE_URL")
    api_key = _pick("api_key", "TESSERAE_LLM_API_KEY", "ANTHROPIC_API_KEY")
    # The bearer credential, which had no channel at all: the Anthropic SDK sends
    # ``api_key`` as X-Api-Key, so a gateway wanting Authorization: Bearer was
    # unreachable no matter what the user configured.
    auth_token = _pick("auth_token", "TESSERAE_LLM_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")

    # The WIRE, which is a different question from the backend. Left unset for
    # ``custom`` it stays ``anthropic`` — that is what custom has always meant
    # here, and inferring it from the URL would repeat the original mistake of
    # guessing the protocol.
    api_style = _pick("api_style", "TESSERAE_LLM_API_STYLE")
    if api_style:
        api_style = str(api_style).strip().lower()
        if api_style not in _VALID_API_STYLES:
            raise LLMProviderConfigError(
                f"llm_api_style={api_style!r} is not one of {list(_VALID_API_STYLES)}")
    else:
        api_style = "openai" if (provider or "").strip().lower() == "openai" else "anthropic"
        sources["api_style"] = f"default for provider={provider or 'claude'}"

    # Parsed, not merely present: an env var read with bool() is ON for "0" and
    # "false", which is the opposite of what anyone setting those means.
    _fb_env = os.environ.get("TESSERAE_LLM_ALLOW_FALLBACK")
    if _fb_env is not None and _fb_env.strip():
        allow_fallback = _fb_env.strip().lower() not in ("0", "false", "no", "off")
    else:
        _fb_cfg = cfg.get("llm_allow_fallback", global_cfg.get("llm_allow_fallback"))
        allow_fallback = bool(_fb_cfg) if not isinstance(_fb_cfg, str) else \
            _fb_cfg.strip().lower() not in ("", "0", "false", "no", "off")

    if provider is not None:
        _p = str(provider).strip().lower()
        if _p and _p not in _VALID_PROVIDERS:
            # Silently becoming "claude" is how a typo turned into a model error
            # about a backend the user never named.
            raise LLMProviderConfigError(
                f"llm_provider={provider!r} is not one of {list(_VALID_PROVIDERS)}")
        provider = _p or None

    return {
        "provider": provider,
        "claude_config_dirs": claude_config_dirs,
        "codex_homes": codex_homes,
        "codex_home": codex_home,
        "codex_reasoning_effort": codex_reasoning_effort,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "auth_token": auth_token,
        "api_style": api_style,
        "allow_fallback": allow_fallback,
        "sources": sources,
    }


_KEEP_TIMEOUT = object()  # sentinel: "use each client's own default timeout"


def project_llm_settings(project_root: Optional[Any] = None) -> dict:
    """Resolve LLM settings against a PROJECT root, not just env + global.

    ``resolve_llm_client_settings()`` with no argument sees only the environment
    and ``~/.tesserae/config.json``. Every caller that had a project in hand but
    called it bare therefore ignored that project's own ``llm_provider`` /
    ``llm_base_url`` / ``llm_model`` — so a custom endpoint configured for one
    project was honoured by ``compile`` and silently skipped by ``ask``, lint,
    the MCP summaries and the daemon, which answered from a different backend.

    Degrades rather than raises: an unreadable or absent project config just
    means the env + global answer, which is what those callers used to get.
    """
    cfg: dict = {}
    if project_root:
        try:
            path = Path(project_root) / ".tesserae" / "config.json"
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    cfg = payload
        except Exception:  # noqa: BLE001 — a corrupt project config must not crash a read path
            logger.warning("ignoring unreadable project config under %s", project_root)
    return resolve_llm_client_settings(cfg)


def build_default_json_client(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    claude_config_dirs: Optional[List[str]] = None,
    codex_home: Optional[str] = None,
    codex_homes: Optional[List[str]] = None,
    codex_reasoning_effort: Optional[str] = "medium",
    timeout: Any = _KEEP_TIMEOUT,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    settings: Optional[dict] = None,
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
    key → None); the model defaults to ``gpt-5.6-luna`` on the codex path.

    ``provider="custom"`` targets a claude-compatible endpoint through the
    Anthropic SDK with the resolved ``llm_model`` / ``llm_base_url`` /
    ``llm_api_key`` (env → project config → global config) — no
    ``ANTHROPIC_API_KEY`` env var required when ``llm_api_key`` is
    configured.
    """
    # Test seam wins — but it must still honour the caller's timeout. Dropping
    # it here handed the injected fake ``AnthropicLLMJsonClient``'s own 30s
    # default no matter what the caller passed, so a test could not observe the
    # timeout the production path would have used. Same _KEEP_TIMEOUT semantics
    # as the real builders below: sentinel = the client's own default, an
    # explicit value (including None = no cutoff) is forwarded verbatim.
    if _CLIENT_FACTORY is not None:
        return AnthropicLLMJsonClient(
            model=model or "claude-sonnet-4-6",
            **({} if timeout is _KEEP_TIMEOUT else {"timeout": timeout}),
        )

    # Explicit args (threaded from a project config by the caller) beat the
    # env → global-config resolution. ``settings["provider"]`` already folds
    # in TESSERAE_LLM_PROVIDER.
    #
    # ``settings=`` exists because re-resolving here sees only env + the
    # GLOBAL config: a caller that resolved against a PROJECT config.json
    # (project.py:_build_json_client) had its llm_model / llm_base_url /
    # llm_api_key silently discarded on the primary compile path. Passing the
    # already-resolved dict is what makes project-level LLM config real.
    settings = settings if settings is not None else resolve_llm_client_settings()
    resolved_provider = (
        provider or settings["provider"] or "claude"
    ).strip().lower()
    if resolved_provider not in _VALID_PROVIDERS:
        raise LLMProviderConfigError(
            f"llm_provider={resolved_provider!r} is not one of {list(_VALID_PROVIDERS)}")

    # Provider-scope the configured model exactly as ``_configured_default_model``
    # does inside each client: a claude-shaped ``llm_model`` must not land on the
    # Codex CLI when the availability chain falls through providers. An explicit
    # ``model=`` argument always wins and is never scoped.
    _cfg_model = settings.get("model")
    _cfg_model_provider = (settings.get("provider") or "claude").strip().lower()
    # A deliberately chosen ENDPOINT provider owns the configured model, whichever
    # config layer each came from. Scoping the model against the other layer's
    # provider string is what dropped it and sent the hardcoded
    # "claude-sonnet-4-6" to custom endpoints — the unsupported-model error.
    # The CLI providers keep the scoping: a claude-shaped model must still never
    # land on the Codex CLI when the availability chain falls through.
    _explicit = bool(provider or settings.get("provider"))
    _endpoint_contract = _explicit and resolved_provider in _ENDPOINT_PROVIDERS

    def _model_for(providers: Sequence[str]) -> Optional[str]:
        if model:
            return model
        if _endpoint_contract:
            return _cfg_model or None
        return _cfg_model if _cfg_model and _cfg_model_provider in providers else None
    # Explicit list > legacy scalar > configured list. Left None, the client
    # ranks CODEX_HOME first and keeps the other discovered homes behind it.
    _resolved_codex_homes = (
        codex_homes or ([codex_home] if codex_home else None) or settings["codex_homes"]
    )
    resolved_base_url = base_url or settings["base_url"]
    resolved_api_key = api_key or settings["api_key"]
    resolved_auth_token = settings.get("auth_token")
    resolved_style = (settings.get("api_style") or "anthropic").strip().lower()

    # timeout=None (from the doc extractor) means "no cutoff — run to completion";
    # the sentinel leaves each client on its own default (180s CLI / 30s API).
    _tkw = {} if timeout is _KEEP_TIMEOUT else {"timeout": timeout}

    def _codex() -> Optional[LLMJsonClient]:
        if _codex_cli_available():
            return CodexCLIJsonClient(
                model=_model_for(("codex",)),
                codex_homes=_resolved_codex_homes,
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
                model=_model_for(("claude",)),
                config_dirs=claude_config_dirs or settings.get("claude_config_dirs"),
                **_ekw,
                **_tkw,
            )
        return None

    def _api_key() -> Optional[LLMJsonClient]:
        # Returns None if the anthropic SDK isn't installed (e.g. base
        # install without `tesserae[synthesis-llm]`) — that's a silent
        # no-op rather than a crash because the structural-only path
        # remains useful with zero LLM access.
        if resolved_style == "openai":
            return _openai_wire()
        if resolved_api_key or resolved_auth_token:
            try:
                return AnthropicLLMJsonClient(
                    model=_model_for(("anthropic", "custom")),
                    api_key=resolved_api_key,
                    auth_token=resolved_auth_token,
                    base_url=resolved_base_url,
                    **_tkw,
                )
            except RuntimeError:
                return None
        return None

    def _openai_wire() -> Optional[LLMJsonClient]:
        # Any OpenAI-compatible server: vLLM, LiteLLM, OpenRouter, Together,
        # Ollama, LM Studio. Previously unreachable — the only OpenAI-protocol
        # client hardcoded api.openai.com and no builder ever constructed it.
        client = OpenAIAPIJsonClient(
            _model_for(("openai", "custom")) or "gpt-4o-mini",
            api_key=resolved_api_key,
            auth_token=resolved_auth_token,
            base_url=resolved_base_url,
            **({} if timeout is _KEEP_TIMEOUT else {"timeout": timeout}),
        )
        return client if client.available else None

    def _custom() -> Optional[LLMJsonClient]:
        # Explicit custom endpoint: build unconditionally (some local endpoints
        # are keyless), resolved knobs applied. The WIRE decides the class —
        # that is the whole point of api_style.
        if resolved_style == "openai":
            return _openai_wire()
        try:
            return AnthropicLLMJsonClient(
                model=_model_for(("anthropic", "custom")),
                api_key=resolved_api_key,
                auth_token=resolved_auth_token,
                base_url=resolved_base_url,
                **_tkw,
            )
        except RuntimeError as exc:
            raise LLMProviderConfigError(
                f"provider=custom style=anthropic base_url={resolved_base_url} "
                f"model={_model_for(('anthropic', 'custom'))}: {exc}") from exc

    _primary = {"codex": _codex, "anthropic": _api_key, "openai": _openai_wire,
                "custom": _custom, "claude": _claude}
    _fallbacks = {"codex": (_claude, _api_key), "anthropic": (_claude, _codex),
                  "openai": (_claude, _codex), "custom": (_claude, _codex),
                  "claude": (_api_key, _codex)}

    # An explicitly configured provider is a CONTRACT, not a preference. It used
    # to be a preference: a custom endpoint that could not be built fell through
    # to the Claude CLI, which was spawned with `--model sonnet` against the
    # user's own base URL and reported an unsupported model they never
    # configured, with nothing naming the real cause. Now the chosen provider is
    # built alone and a failure says which provider, wire, URL and model were
    # used. ``llm_allow_fallback: true`` restores the old chaining.
    chain = (_primary[resolved_provider],)
    if not _endpoint_contract or settings.get("allow_fallback"):
        chain = chain + _fallbacks[resolved_provider]

    for builder in chain:
        client = builder()
        if client is not None:
            return client
    if _endpoint_contract:
        raise LLMProviderConfigError(
            f"provider={resolved_provider} style={resolved_style} "
            f"base_url={resolved_base_url or '(default)'} "
            f"model={_model_for((resolved_provider,)) or '(default)'} "
            f"auth={'auth_token' if resolved_auth_token else ('api_key' if resolved_api_key else 'none')}"
            ": could not be built. Check the credential and, for a custom endpoint, "
            "that llm_base_url and llm_api_style match the server"
            + ("; the anthropic wire needs `pip install 'tesserae[synthesis-llm]'`"
               if resolved_style == "anthropic" else "")
            + ". "
            "Set llm_allow_fallback=true to fall back to another provider instead.")
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
        # No failure bookkeeping here on purpose: each sub-client writes its
        # own `_note_failure`, so the LAST provider tried wins — which is the
        # right verdict for "why did the whole chain give up". This must keep
        # RETURNING None rather than raising; 20+ call sites read None as
        # "degrade gracefully", and the new exception lives strictly above the
        # client layer, in LLMResearchExtractor.
        for client in self.clients:
            result = client.complete_json(**kwargs)
            if result is not None:
                return result
        return None

    def forget_cached_answer(
        self, cache_key: Optional[str], *, schema_name: str, system: str, user: str
    ) -> None:
        """Forward a caller's rejection to every sub-client.

        We don't track which provider answered, and dropping an entry that was
        never cached is a no-op — so fan out rather than guess. ``system`` and
        ``user`` ride along because each sub-client addresses its entry by the
        assembled prompt.
        """
        for client in self.clients:
            forget = getattr(client, "forget_cached_answer", None)
            if callable(forget):
                forget(cache_key, schema_name=schema_name, system=system, user=user)

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
    codex_homes: Optional[List[str]] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    settings: Optional[dict] = None,
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

    # ``settings=`` is how a PROJECT config reaches this path. Without it the
    # resolution saw only env + the global file, so `tesserae ask`, query,
    # activity summaries and the daemon could not see a project-level custom
    # provider at all — they answered from whatever backend happened to be
    # installed while the user believed their own endpoint served the request.
    settings = settings if settings is not None else resolve_llm_client_settings()
    resolved_provider = (
        provider or settings["provider"] or "claude"
    ).strip().lower()
    # Explicit list > legacy scalar > configured list. Left None, the client
    # ranks CODEX_HOME first and keeps the other discovered homes behind it.
    _resolved_codex_homes = (
        codex_homes or ([codex_home] if codex_home else None) or settings["codex_homes"]
    )
    resolved_base_url = base_url or settings["base_url"]
    resolved_api_key = api_key or settings["api_key"]
    resolved_auth_token = settings.get("auth_token")
    resolved_style = (settings.get("api_style") or "anthropic").strip().lower()
    # The configured model, which this builder never passed to the SDK client —
    # so a project-level llm_model silently became the hardcoded
    # "claude-sonnet-4-6" on every `tesserae ask`.
    resolved_model = settings.get("model")

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
            codex_homes=_resolved_codex_homes,
        )
        if _codex_cli_available()
        else None
    )
    api_client = None
    if resolved_style == "openai" and resolved_provider in ("custom", "openai"):
        _oa = OpenAIAPIJsonClient(
            resolved_model or "gpt-4o-mini",
            api_key=resolved_api_key,
            auth_token=resolved_auth_token,
            base_url=resolved_base_url,
        )
        api_client = _oa if _oa.available else None
    elif resolved_api_key or resolved_auth_token or resolved_provider == "custom":
        try:
            api_client = AnthropicLLMJsonClient(
                model=resolved_model,
                api_key=resolved_api_key,
                auth_token=resolved_auth_token,
                base_url=resolved_base_url,
            )
        except RuntimeError:
            api_client = None

    if resolved_provider == "codex":
        ordered = [codex_client, claude_client, api_client]
    elif resolved_provider in ("anthropic", "custom", "openai"):
        ordered = [api_client, claude_client, codex_client]
    else:
        ordered = [claude_client, api_client, codex_client]
    # Composing every backend means a custom endpoint that returns None at RUN
    # time falls through to the Claude CLI mid-answer, so the user's gateway is
    # bypassed silently and the answer comes from somewhere they did not choose.
    # An explicitly chosen provider is not composed with the others.
    if ((provider or settings.get("provider"))
            and resolved_provider in _ENDPOINT_PROVIDERS
            and not settings.get("allow_fallback")):
        ordered = ordered[:1]
    available = [c for c in ordered if c is not None]
    if not available:
        return None
    if len(available) == 1:
        return available[0]
    return CompositeCLIClient(available)
