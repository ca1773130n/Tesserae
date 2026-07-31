"""Tests for the LLMJsonClient interface used by the session graph LLM pass."""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path
from typing import Any, List
from unittest import mock

import pytest

from tesserae import llm_extractor, llm_json
from tesserae.llm_json import (
    AnthropicLLMJsonClient,
    ClaudeCLIJsonClient,
    build_default_json_client,
    parse_json_tolerant,
    set_client_factory,
)


# ---------------------------------------------------------------------------
# parse_json_tolerant
# ---------------------------------------------------------------------------


def test_parse_well_formed_json():
    assert parse_json_tolerant('{"k": 1}') == {"k": 1}
    assert parse_json_tolerant('[1, 2, 3]') == [1, 2, 3]


def test_parse_strips_markdown_fences():
    text = "```json\n{\"k\": 2}\n```"
    assert parse_json_tolerant(text) == {"k": 2}


def test_parse_drops_trailing_commas():
    text = '{"a": 1, "b": 2,}'
    assert parse_json_tolerant(text) == {"a": 1, "b": 2}
    assert parse_json_tolerant('[1, 2,]') == [1, 2]


def test_parse_recovers_from_leading_prose():
    text = "Sure! Here is the JSON you asked for:\n\n[{\"x\": 5}]"
    assert parse_json_tolerant(text) == [{"x": 5}]


def test_parse_returns_none_on_garbage():
    assert parse_json_tolerant("not json at all") is None
    assert parse_json_tolerant("") is None
    assert parse_json_tolerant("   ") is None
    assert parse_json_tolerant(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AnthropicLLMJsonClient via test factory
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    def __init__(self, scripted_responses: List[Any]) -> None:
        self._scripted = list(scripted_responses)
        self.calls: List[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("no scripted response left")
        item = self._scripted.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeAnthropic:
    def __init__(self, scripted_responses: List[Any]) -> None:
        self.messages = _FakeMessages(scripted_responses)


@pytest.fixture
def fake_client_factory():
    """Inject a scripted fake Anthropic client; restore on teardown."""
    container: dict[str, Any] = {}

    def _set(scripted: List[Any]):
        fake = _FakeAnthropic(scripted)
        container["fake"] = fake
        set_client_factory(lambda api_key=None, timeout=None: fake)
        return fake

    yield _set
    set_client_factory(None)


def test_client_well_formed_json_returns_parsed(fake_client_factory):
    """Happy path: well-formed JSON in the response body is returned parsed."""
    # The `{`-prefill means the model's response starts AFTER the `{`.
    # So if we want {"kind": "decision"}, the model returns `"kind": "decision"}`.
    fake_client_factory([_FakeResponse('"kind": "decision"}')])
    client = AnthropicLLMJsonClient()
    result = client.complete_json(
        system="extract decisions",
        user="transcript text",
        schema_name="finding-v1",
    )
    assert result == {"kind": "decision"}


def test_client_fenced_response_unwraps(fake_client_factory):
    """A model that leaks ```json fences despite instructions still parses."""
    # Pre-fill `{` already happened; assistant continues with `"kind": "x"}` and
    # then garbage / fence. Our prepend gives us `{"kind": "x"}` after parse.
    fake_client_factory([_FakeResponse('"kind": "x"}')])
    client = AnthropicLLMJsonClient()
    assert client.complete_json(
        system="x", user="y", schema_name="z"
    ) == {"kind": "x"}


def test_client_garbage_response_returns_none(fake_client_factory):
    fake_client_factory([_FakeResponse("totally not json")])
    client = AnthropicLLMJsonClient()
    assert client.complete_json(
        system="x", user="y", schema_name="z"
    ) is None


def test_client_retries_on_transient_then_succeeds(fake_client_factory):
    """First call raises a fake RateLimitError-like exception; second succeeds."""

    class _RateLimit(Exception):
        retry_after = 0  # don't actually sleep

    fake = fake_client_factory(
        [_RateLimit("slow down"), _FakeResponse('"k": 1}')]
    )

    # Wire the RateLimitError class onto the client so the retry path triggers.
    client = AnthropicLLMJsonClient()
    client._rate_limit_cls = _RateLimit  # type: ignore[attr-defined]

    result = client.complete_json(
        system="x", user="y", schema_name="z", max_retries=2
    )
    assert result == {"k": 1}
    assert len(fake.messages.calls) == 2


def test_client_gives_up_after_max_retries(fake_client_factory):
    class _RateLimit(Exception):
        retry_after = 0

    fake = fake_client_factory(
        [_RateLimit("x"), _RateLimit("x"), _RateLimit("x")]
    )
    client = AnthropicLLMJsonClient()
    client._rate_limit_cls = _RateLimit  # type: ignore[attr-defined]

    result = client.complete_json(
        system="x", user="y", schema_name="z", max_retries=2
    )
    assert result is None
    # max_retries=2 → 3 total attempts (initial + 2 retries).
    assert len(fake.messages.calls) == 3


# ---------------------------------------------------------------------------
# build_default_json_client gating
# ---------------------------------------------------------------------------


def test_build_default_returns_none_without_credentials(monkeypatch):
    """No `claude` CLI + no API key → None. Caller falls back to structural-only."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    set_client_factory(None)
    # Force `which claude` to miss by clearing PATH.
    monkeypatch.setenv("PATH", "/nonexistent-bin-only-dir")
    assert build_default_json_client() is None


def test_build_default_prefers_claude_cli_over_api_key(monkeypatch, tmp_path):
    """When the `claude` CLI is available, it wins over an API key —
    matches the README's "no API keys required for the common path"
    promise."""
    from tesserae.llm_json import ClaudeCLIJsonClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "would-also-work-but-we-prefer-oauth")
    set_client_factory(None)
    # Fake CLAUDE_CONFIG_DIR with a settings.json marker so
    # `_claude_cli_available()` is satisfied for the credential half.
    fake_config = tmp_path / "fake-claude"
    fake_config.mkdir()
    (fake_config / "settings.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_config))
    # Fake a `claude` binary on PATH.
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_claude = fake_bin_dir / "claude"
    fake_claude.write_text("#!/bin/sh\necho '{}'\n")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{os.environ.get('PATH','')}")

    client = build_default_json_client()
    assert isinstance(client, ClaudeCLIJsonClient), (
        "CLI must win over API key when both are available"
    )


def test_build_default_falls_back_to_api_key(monkeypatch):
    """When no CLI is available but ANTHROPIC_API_KEY is set, the API
    client is used (fallback path for headless / CI environments).

    Skips when the ``anthropic`` SDK isn't installed — in that case the
    factory correctly returns None (silent no-op) rather than crashing,
    and there's no fallback client to assert isinstance against.
    """
    try:
        import anthropic  # noqa: F401
    except ImportError:
        pytest.skip("anthropic SDK not installed; fallback path returns None")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-bin-only-dir")
    set_client_factory(None)
    client = build_default_json_client()
    assert isinstance(client, AnthropicLLMJsonClient)


# ---------------------------------------------------------------------------
# ClaudeCLIJsonClient "Not logged in" graceful degradation
# ---------------------------------------------------------------------------


def _make_completed_process(returncode: int, stderr: str = "", stdout: str = ""):
    """Build a minimal CompletedProcess-like stand-in for subprocess.run."""
    import subprocess as _subprocess

    return _subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture
def reset_login_warning():
    """Reset the module-level one-shot login warning flag around each test."""
    llm_json._reset_login_warning_for_tests()
    yield
    llm_json._reset_login_warning_for_tests()


def test_cli_complete_text_returns_prose(monkeypatch):
    """complete_text returns raw stdout prose (no JSON parse)."""
    from tesserae.llm_json import ClaudeCLIJsonClient

    fake_proc = _make_completed_process(
        returncode=0, stdout="We decided to ship. [node:abc]\n"
    )
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"])
    out = client.complete_text(system="cite nodes", user="what did we decide?")
    assert out == "We decided to ship. [node:abc]"


def test_codex_home_is_preferred_not_exclusive(monkeypatch, tmp_path):
    """CODEX_HOME ranks first; the other authenticated homes stay in rotation.

    Codex had the same defect claude did — the env var replaced the discovered
    list, so the rotation loop that exists to survive a rate-limited account
    had nowhere to go.
    """
    from tesserae.llm_json import CodexCLIJsonClient

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    for name in (".codex", ".codex-personal1", ".codex-personal2"):
        (fake_home / name).mkdir()
        (fake_home / name / "auth.json").write_text("{}")
    # Neither is a codex home: a stray log file, and a dir with no auth.json.
    (fake_home / ".codex-review-pr208.log").write_text("noise")
    (fake_home / ".codex-nomcp").mkdir()

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CODEX_HOME", str(fake_home / ".codex-personal2"))

    homes = CodexCLIJsonClient().codex_homes
    assert homes[0] == str(fake_home / ".codex-personal2"), f"env home first, got {homes}"
    assert len(homes) == 3, f"other authenticated homes must stay, got {homes}"
    assert not any(h.endswith(".log") for h in homes), f"log files are not homes: {homes}"
    assert not any(h.endswith(".codex-nomcp") for h in homes), (
        f"a dir without auth.json is not a usable home: {homes}"
    )


def test_codex_homes_list_config(monkeypatch):
    """llm_codex_homes (list) is the modern key; llm_codex_home (str) still works."""
    from tesserae.llm_json import resolve_llm_client_settings

    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(llm_json, "_load_global_llm_config", lambda: {})

    listed = resolve_llm_client_settings({"llm_codex_homes": ["/a", "/b"]})
    assert listed["codex_homes"] == ["/a", "/b"]
    assert listed["codex_home"] == "/a", "scalar back-compat view is the first home"

    legacy = resolve_llm_client_settings({"llm_codex_home": "/only"})
    assert legacy["codex_homes"] == ["/only"]

    # Unconfigured must be None, never [CODEX_HOME] — that would pin the client
    # verbatim and re-disable rotation. Same trap as the claude side.
    monkeypatch.setenv("CODEX_HOME", "/from/env")
    assert resolve_llm_client_settings({})["codex_homes"] is None


def test_codex_model_precedence(monkeypatch):
    """Model choice is supported on codex: caller > configured > default.

    gpt-5.6-luna is the DEFAULT, not a pin — `--llm-model`, `llm_model`, and the
    per-feature community/distill model settings all reach this argument and
    must be honoured. `_configured_default_model` is provider-scoped, which is
    what keeps a claude-shaped name from arriving here through config.
    """
    from tesserae.llm_json import CODEX_DEFAULT_MODEL, CodexCLIJsonClient

    assert CODEX_DEFAULT_MODEL == "gpt-5.6-luna"

    monkeypatch.setattr(llm_json, "_configured_default_model", lambda providers: None)
    assert CodexCLIJsonClient().model == CODEX_DEFAULT_MODEL, "default when nothing is set"

    monkeypatch.setattr(llm_json, "_configured_default_model", lambda providers: "gpt-5.6-mini")
    assert CodexCLIJsonClient().model == "gpt-5.6-mini", "configured llm_model must win"
    assert CodexCLIJsonClient(model="gpt-5.6-max").model == "gpt-5.6-max", (
        "an explicit caller model must beat config"
    )


def test_quota_exhaustion_stops_spawning_children(monkeypatch, reset_login_warning):
    """Quota is an ACCOUNT fact — once every account reports its limit, later
    documents must not each re-spawn the CLI to be told the same thing.

    Regression: one observed compile spawned a child per account per remaining
    document after the quota ran out — 1,531 documents' worth of subprocesses
    that could not have succeeded.
    """
    from tesserae.llm_json import ClaudeCLIJsonClient

    calls: List[str] = []

    def fake_run(cmd, **kw):
        calls.append((kw.get("env") or {}).get("CLAUDE_CONFIG_DIR", "default"))
        return _make_completed_process(
            returncode=1,
            stdout="",
            stderr="You've hit your weekly limit · resets 6am (Asia/Seoul)",
        )

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    client = ClaudeCLIJsonClient(config_dirs=["/acct/a", "/acct/b"])

    assert client.complete_text(system="s", user="doc 1") is None
    assert len(calls) == 2, f"first doc must try every account, got {calls}"

    for n in range(2, 12):
        assert client.complete_text(system="s", user=f"doc {n}") is None
    assert len(calls) == 2, f"exhausted accounts must not be re-spawned, got {len(calls)} calls"


def test_latch_needs_every_account_proven(monkeypatch, reset_login_warning):
    """One timeout among the accounts must prevent the latch.

    Codex review of PR #94: the latch read only `last_error`, so account A
    timing out and account B hitting quota latched the whole run — A was never
    shown to be exhausted, and every later document lost its LLM call for a
    reason that may have been a passing network blip.
    """
    from tesserae.llm_json import ClaudeCLIJsonClient

    calls: List[str] = []

    def fake_run(cmd, **kw):
        calls.append("x")
        if len(calls) % 2:  # account A: transient, proves nothing
            raise TimeoutError("network stall")
        return _make_completed_process(  # account B: genuinely out of quota
            returncode=1, stdout="",
            stderr="You've hit your weekly limit · resets 6am (Asia/Seoul)",
        )

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    client = ClaudeCLIJsonClient(config_dirs=["/acct/a", "/acct/b"])

    client.complete_text(system="s", user="doc 1")
    client.complete_text(system="s", user="doc 2")
    assert len(calls) == 4, f"a mixed verdict must keep trying, got {len(calls)}"
    assert not client._accounts_exhausted, "unproven account must not latch the run"


def test_latch_is_per_client_not_per_process(monkeypatch, reset_login_warning):
    """An exhausted client must not disable an unrelated one.

    Codex review of PR #94: the latch was a module global, so a pinned
    single-account client could switch off every other client in the process —
    and a daemon/MCP process stayed disabled long after the quota reset.
    """
    from tesserae.llm_json import ClaudeCLIJsonClient

    def out_of_quota(cmd, **kw):
        return _make_completed_process(
            returncode=1, stdout="",
            stderr="You've hit your weekly limit · resets 6am (Asia/Seoul)",
        )

    monkeypatch.setattr(llm_json, "_run_cli", out_of_quota)
    spent = ClaudeCLIJsonClient(config_dirs=["/acct/a"])
    spent.complete_text(system="s", user="doc")
    assert spent._accounts_exhausted, "the exhausted client should latch"

    fresh = ClaudeCLIJsonClient(config_dirs=["/acct/b"])
    assert not fresh._accounts_exhausted, "a separate client must start clean"


def test_per_document_failure_does_not_latch(monkeypatch, reset_login_warning):
    """A failure that is NOT account-level quota must keep rotating — latching
    on it would turn one bad document into a whole-run outage."""
    from tesserae.llm_json import ClaudeCLIJsonClient

    calls: List[str] = []

    def fake_run(cmd, **kw):
        calls.append("x")
        return _make_completed_process(returncode=1, stdout="", stderr="network unreachable")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    client = ClaudeCLIJsonClient(config_dirs=["/acct/a", "/acct/b"])

    client.complete_text(system="s", user="doc 1")
    client.complete_text(system="s", user="doc 2")
    assert len(calls) == 4, f"non-quota failures must keep retrying, got {len(calls)}"


def test_cli_env_config_dir_is_preferred_not_exclusive(monkeypatch, tmp_path):
    """CLAUDE_CONFIG_DIR names the account to try FIRST, not the only one.

    Regression: pinning to the single env dir disabled the rotation loop —
    the one mechanism that survives a rate-limited account. Everything
    launched from a Claude Code session inherits the var, so a compile could
    only ever use that session's account; when its quota ran out, 1,531 docs
    fell back to deterministic while two other logged-in accounts sat idle.
    """
    from tesserae.llm_json import ClaudeCLIJsonClient

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    for name in (".claude", ".claude-personal1", ".claude-personal2"):
        (fake_home / name).mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / ".claude-personal2"))

    dirs = ClaudeCLIJsonClient().config_dirs

    assert dirs[0] == str(fake_home / ".claude-personal2"), "env dir must be tried first"
    assert len(dirs) == 3, f"rotation needs every discovered account, got {dirs}"
    assert len(set(dirs)) == len(dirs), f"env dir duplicated into the rotation: {dirs}"

    # An explicit argument still wins absolutely (tests / MCP override / flags).
    pinned = ClaudeCLIJsonClient(config_dirs=["/only/this"]).config_dirs
    assert pinned == ["/only/this"], f"explicit config_dirs must not rotate, got {pinned}"


def test_cli_argv_has_no_turn_cap(monkeypatch):
    """Regression: `--max-turns 1` counted tool calls, so any MCP server in
    the user's config dir burned the only turn and the CLI exited 1 before
    emitting JSON — silently degrading every extraction to deterministic.
    The one-shot guarantee is `--strict-mcp-config` (no tools to call)."""
    from tesserae.llm_json import ClaudeCLIJsonClient

    seen: List[List[str]] = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return _make_completed_process(returncode=0, stdout="ok\n")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"]).complete_text(
        system="s", user="u"
    )

    argv = seen[0]
    assert "--max-turns" not in argv, f"turn cap is back in {argv}"
    assert "--strict-mcp-config" in argv, f"missing MCP isolation in {argv}"
    assert "--max-turns" not in llm_extractor.run_claude_cli.__code__.co_consts, (
        "run_claude_cli still passes --max-turns"
    )


def test_cli_complete_text_rotates_past_rate_limited_account(monkeypatch):
    """A rate-limited account (non-zero exit) is skipped; the next account
    answers — proving multi-account rotation for prose, not just JSON."""
    from tesserae.llm_json import ClaudeCLIJsonClient

    seen = []

    def fake_run(cmd, **kw):
        config_dir = kw.get("env", {}).get("CLAUDE_CONFIG_DIR", "default")
        seen.append(config_dir)
        if "personal1" in config_dir:
            return _make_completed_process(
                returncode=1, stdout="You've hit your weekly limit · resets tomorrow"
            )
        return _make_completed_process(returncode=0, stdout="answer [node:x]")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    client = ClaudeCLIJsonClient(
        config_dirs=["/home/u/.claude-personal1", "/home/u/.claude-personal2"]
    )
    out = client.complete_text(system="s", user="u")
    assert out == "answer [node:x]"
    assert any("personal1" in c for c in seen)
    assert any("personal2" in c for c in seen)


def test_composite_falls_through_providers_on_exhaustion():
    """CompositeCLIClient tries each sub-client until one answers — so a
    fully rate-limited Claude rotates to Codex (and vice-versa)."""
    from tesserae.llm_json import CompositeCLIClient

    class _Dead:
        def complete_text(self, **k):
            return None

        def complete_json(self, **k):
            return None

    class _Alive:
        def complete_text(self, **k):
            return "from the next provider [node:x]"

        def complete_json(self, **k):
            return {"ok": 1}

    comp = CompositeCLIClient([_Dead(), _Alive()])
    assert comp.complete_text(system="s", user="u") == "from the next provider [node:x]"
    assert comp.complete_json(system="s", user="u", schema_name="z") == {"ok": 1}
    # all-dead → None, never raises
    assert CompositeCLIClient([_Dead(), _Dead()]).complete_text(system="s", user="u") is None


def test_build_rotating_client_composes_claude_and_codex(monkeypatch, tmp_path):
    """build_rotating_client returns a composite spanning every available
    provider so a call only gives up once ALL accounts are exhausted."""
    import tesserae.llm_json as lj
    from tesserae.llm_json import CompositeCLIClient

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)

    client = lj.build_rotating_client()
    assert isinstance(client, CompositeCLIClient)
    kinds = [type(c).__name__ for c in client.clients]
    assert "ClaudeCLIJsonClient" in kinds and "CodexCLIJsonClient" in kinds


def test_cli_not_logged_in_returns_none(monkeypatch, caplog, reset_login_warning):
    """A 'Not logged in' stderr → complete_json returns None, does NOT raise."""
    fake_proc = _make_completed_process(
        returncode=1, stderr="Not logged in · Please run /login\n"
    )
    # Patch ``_run_cli``, the seam every other test in this file uses. This one
    # patched ``subprocess.run``, which ``_run_cli`` stopped calling when it
    # moved to ``subprocess.Popen`` (so a timeout could kill the whole process
    # group). The patch quietly became a no-op: the test shelled out to the real
    # ``claude`` binary, passing on machines that have it installed and failing
    # with FileNotFoundError anywhere else. It asserted nothing about the code.
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"])
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        result = client.complete_json(
            system="x", user="y", schema_name="finding-v1",
        )
    assert result is None
    # The fix hint must appear in the logs.
    assert any("claude /login" in rec.getMessage() for rec in caplog.records), (
        f"expected `claude /login` hint in logs, got: {[r.getMessage() for r in caplog.records]}"
    )


def test_cli_not_logged_in_logs_once_across_calls(
    monkeypatch, caplog, reset_login_warning
):
    """Two consecutive 'Not logged in' calls log the hint exactly once."""
    fake_proc = _make_completed_process(
        returncode=1, stderr="Not logged in · Please run /login\n"
    )
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"])
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        assert client.complete_json(system="x", user="y", schema_name="z") is None
        assert client.complete_json(system="x", user="y", schema_name="z") is None
    login_hint_count = sum(
        1 for r in caplog.records if "claude /login" in r.getMessage()
    )
    assert login_hint_count == 1, (
        f"expected exactly one `claude /login` warning across two calls, got {login_hint_count}"
    )


def test_cli_not_logged_in_case_insensitive(
    monkeypatch, caplog, reset_login_warning
):
    """Detection is case-insensitive — robust to phrasing drift."""
    fake_proc = _make_completed_process(
        returncode=2, stderr="ERROR: NOT LOGGED IN. run /login first."
    )
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"])
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        assert client.complete_json(system="x", user="y", schema_name="z") is None
    assert any("claude /login" in r.getMessage() for r in caplog.records)


def test_cli_not_logged_in_falls_through_to_next_config_dir(
    monkeypatch, caplog, reset_login_warning
):
    """Codex PR #17 P2 fix — when one config_dir is logged out but a
    later one is logged in, we should USE the logged-in one instead
    of returning None on first failure.
    """
    call_log = []
    valid_json = '{"ok": true}'

    def fake_run(*args, **kwargs):
        env = kwargs.get("env") or {}
        config_dir = env.get("CLAUDE_CONFIG_DIR", "")
        call_log.append(config_dir)
        if "stale" in config_dir:
            return _make_completed_process(
                returncode=1, stderr="Not logged in · Please run /login\n"
            )
        return _make_completed_process(returncode=0, stdout=valid_json)

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    client = ClaudeCLIJsonClient(
        config_dirs=["/tmp/stale-config", "/tmp/fresh-config"]
    )
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        result = client.complete_json(
            system="x", user="y", schema_name="z", max_retries=0,
        )
    assert result == {"ok": True}, (
        f"expected fresh config to succeed; got {result!r}"
    )
    assert call_log == ["/tmp/stale-config", "/tmp/fresh-config"], (
        f"expected fallback to fresh config_dir; call_log={call_log}"
    )
    # No login warning — overall result was success, not skip.
    assert not any("claude /login" in r.getMessage() for r in caplog.records), (
        "login hint must NOT fire when a later config_dir succeeded"
    )


def test_cli_not_logged_in_logs_only_when_all_dirs_fail(
    monkeypatch, caplog, reset_login_warning
):
    """If EVERY config_dir reports not-logged-in → emit the hint once.
    Mention the count of tried dirs so the user knows it wasn't a
    single-profile glitch."""
    fake_proc = _make_completed_process(
        returncode=1, stderr="Not logged in\n"
    )
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(
        config_dirs=["/tmp/c1", "/tmp/c2", "/tmp/c3"]
    )
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        assert client.complete_json(
            system="x", user="y", schema_name="z", max_retries=0,
        ) is None
    hints = [r.getMessage() for r in caplog.records if "claude /login" in r.getMessage()]
    assert len(hints) == 1, f"expected exactly one login hint; got {len(hints)}"
    # The hint should mention that we tried all 3 dirs.
    assert "3 config dirs" in hints[0], (
        f"expected count of dirs in hint; got: {hints[0]}"
    )


def test_cli_autodiscovers_multiple_claude_config_dirs(monkeypatch, tmp_path):
    """When CLAUDE_CONFIG_DIR is unset, ClaudeCLIJsonClient should glob
    ``~/.claude*`` and probe every matching dir — not just default to
    ``~/.claude``. Mirrors the multi-account setup most Tesserae users
    run (``~/.claude``, ``~/.claude-personal1``, ``~/.claude-personal2``).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude-personal1").mkdir()
    (fake_home / ".claude-personal2").mkdir()
    # Non-matching siblings: should be ignored.
    (fake_home / ".claudefoo.bak").mkdir()  # .bak suffix excluded
    (fake_home / ".claude-old.old").mkdir()  # .old suffix excluded
    (fake_home / "other-dir").mkdir()

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    client = ClaudeCLIJsonClient()
    # Sorted glob result: .claude, .claude-personal1, .claude-personal2
    expected = [
        str(fake_home / ".claude"),
        str(fake_home / ".claude-personal1"),
        str(fake_home / ".claude-personal2"),
    ]
    assert client.config_dirs == expected, (
        f"expected auto-discovery of 3 ~/.claude* dirs; got {client.config_dirs}"
    )


def test_cli_explicit_arg_beats_env_and_autodiscovery(monkeypatch, tmp_path):
    """Explicit ``config_dirs=`` kwarg wins over env and auto-discovery."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/from-env")
    client = ClaudeCLIJsonClient(config_dirs=["/explicit/path"])
    assert client.config_dirs == ["/explicit/path"]


def test_cli_env_beats_autodiscovery(monkeypatch, tmp_path):
    """CLAUDE_CONFIG_DIR env RANKS ahead of the auto-discovery glob.

    It used to replace the glob outright. "Wins" means tried first — making
    it exclusive turned the rotation loop off for every caller that inherits
    the var, which is every caller launched from a Claude Code session.
    See test_cli_env_config_dir_is_preferred_not_exclusive.
    """
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude-personal1").mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / ".claude-personal1"))
    client = ClaudeCLIJsonClient()
    assert client.config_dirs[0] == str(fake_home / ".claude-personal1")
    assert str(fake_home / ".claude") in client.config_dirs


def test_claude_cli_available_uses_autodiscovery(monkeypatch, tmp_path):
    """Codex PR #19 P2 fix — `_claude_cli_available` must use the same
    autodiscovery as the constructor. Pre-fix: only checked the env or
    ~/.claude, so a user with only ~/.claude-personal1 silently got
    None from build_default_json_client.
    """
    import shutil as _shutil
    from tesserae import llm_json

    fake_home = tmp_path / "home"
    profile = fake_home / ".claude-personal1"
    profile.mkdir(parents=True)
    # Marker file proving "logged in" looking.
    (profile / "settings.json").write_text("{}", encoding="utf-8")
    # No ~/.claude at all → pre-fix would have returned False.

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr(_shutil, "which", lambda name: "/fake/bin/claude")
    assert llm_json._claude_cli_available() is True


def test_claude_cli_available_returns_false_when_no_credentialed_dirs(
    monkeypatch, tmp_path
):
    """Empty $HOME with no claude bin marker → False (no auth)."""
    import shutil as _shutil
    from tesserae import llm_json

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)  # exists but no markers
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr(_shutil, "which", lambda name: "/fake/bin/claude")
    assert llm_json._claude_cli_available() is False


def test_cli_autodiscovery_falls_back_when_no_dirs_exist(monkeypatch, tmp_path):
    """No ~/.claude* dirs at all → fall back to [~/.claude] so older
    single-config-dir setups still work (the fallback dir need not exist;
    the auth-check at call time decides)."""
    fake_home = tmp_path / "home-empty"
    fake_home.mkdir()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    client = ClaudeCLIJsonClient()
    assert client.config_dirs == [str(fake_home / ".claude")]


def test_cli_genuine_error_still_logs_failure(
    monkeypatch, caplog, reset_login_warning
):
    """A non-login error must NOT be silently swallowed — the existing
    'ClaudeCLIJsonClient.complete_json failed' warning still fires, and
    the login-specific hint does NOT appear."""
    fake_proc = _make_completed_process(
        returncode=1, stderr="rate limit exceeded; try again in 60s"
    )
    monkeypatch.setattr(llm_json, "_run_cli", lambda *a, **kw: fake_proc)
    client = ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude-config"])
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        result = client.complete_json(
            system="x", user="y", schema_name="z", max_retries=0,
        )
    assert result is None  # ClaudeCLIJsonClient already returns None on errors
    messages = [r.getMessage() for r in caplog.records]
    assert any("complete_json failed" in m for m in messages), (
        f"expected the existing failure warning, got: {messages}"
    )
    assert not any("claude /login" in m for m in messages), (
        "non-login errors must NOT emit the login hint"
    )


def test_build_default_returns_client_when_factory_set_without_credentials(monkeypatch):
    """Test factory wins over everything — keeps tests hermetic."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent-bin-only-dir")
    set_client_factory(lambda api_key=None, timeout=None: _FakeAnthropic([]))
    try:
        assert build_default_json_client() is not None
    finally:
        set_client_factory(None)


# ---------------------------------------------------------------------------
# CodexCLIJsonClient — `codex exec` OAuth backend (mirror of ClaudeCLIJsonClient)
# ---------------------------------------------------------------------------


class _FakeCodexRun:
    """Stand-in for subprocess.run capturing the codex invocation.

    Writes ``payload`` to the ``--output-last-message`` tmp path embedded in
    the command, mimicking the real codex CLI contract.
    """

    def __init__(self, payload: str = '{"ok": true}', returncode: int = 0, stderr: str = ""):
        self.payload = payload
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, **kwargs):
        import types

        self.calls.append({"cmd": list(cmd), "env": kwargs.get("env"), "input": kwargs.get("prompt")})
        if self.returncode == 0 and "--output-last-message" in cmd:
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(self.payload)
        return types.SimpleNamespace(returncode=self.returncode, stdout=self.payload, stderr=self.stderr)


def test_codex_client_invokes_codex_exec_and_parses_json(monkeypatch, tmp_path):
    from tesserae.llm_json import CodexCLIJsonClient

    fake = _FakeCodexRun(payload='{"insights": [1, 2]}')
    monkeypatch.setattr(llm_json, "_run_cli", fake)
    home = tmp_path / "codex-home"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)])
    result = client.complete_json(
        system="You extract insights.",
        user="Session text here.",
        schema_name="insights_v1",
    )

    assert result == {"insights": [1, 2]}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    # codex exec contract — non-interactive, read-only sandbox, stdin prompt.
    assert call["cmd"][:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in call["cmd"]
    assert "read-only" in call["cmd"]
    assert call["cmd"][-1] == "-"
    assert "--model" in call["cmd"]
    # default model
    assert call["cmd"][call["cmd"].index("--model") + 1] == "gpt-5.6-luna"
    # CODEX_HOME routed to the requested home
    assert call["env"]["CODEX_HOME"] == str(home)
    # prompt carries the JSON-only contract pieces
    assert "You extract insights." in call["input"]
    assert "Session text here." in call["input"]
    assert "insights_v1" in call["input"]


def test_codex_client_env_home_used_when_no_arg(monkeypatch, tmp_path):
    from tesserae.llm_json import CodexCLIJsonClient

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    env_home = fake_home / ".codex-personal1"
    env_home.mkdir()
    (env_home / "auth.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(env_home))

    client = CodexCLIJsonClient()
    # Ranked first, not exclusive — see test_codex_home_is_preferred_not_exclusive.
    assert client.codex_homes[0] == str(env_home)


def test_codex_client_explicit_homes_beat_env(monkeypatch, tmp_path):
    from tesserae.llm_json import CodexCLIJsonClient

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env-home"))
    explicit = str(tmp_path / "explicit-home")

    client = CodexCLIJsonClient(codex_homes=[explicit])
    assert client.codex_homes == [explicit]


def test_codex_client_falls_through_homes_on_failure(monkeypatch, tmp_path):
    from tesserae.llm_json import CodexCLIJsonClient

    calls = []

    def fake_run(cmd, **kwargs):
        import types

        calls.append(kwargs.get("env", {}).get("CODEX_HOME"))
        if len(calls) == 1:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="401 Unauthorized; run codex login")
        out_path = cmd[cmd.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write('{"ok": true}')
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home_a), str(home_b)])
    result = client.complete_json(system="s", user="u", schema_name="x")

    assert result == {"ok": True}
    assert calls == [str(home_a), str(home_b)]


def test_codex_client_returns_none_when_all_homes_fail(monkeypatch, tmp_path, caplog):
    import logging

    from tesserae.llm_json import CodexCLIJsonClient

    def fake_run(cmd, **kwargs):
        import types

        return types.SimpleNamespace(returncode=1, stdout="", stderr="run codex login first")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)  # skip transport backoff
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)])
    with caplog.at_level(logging.WARNING):
        result = client.complete_json(system="s", user="u", schema_name="x")

    assert result is None
    assert any("codex" in r.getMessage().lower() for r in caplog.records)
    # ONE record for the whole call, not one per transport attempt: three
    # attempts across 137 docs would drown the compile output.
    assert sum("complete_json failed" in r.getMessage() for r in caplog.records) == 1


def test_codex_client_timeout_returns_none(monkeypatch, tmp_path):
    import subprocess as _subprocess

    from tesserae.llm_json import CodexCLIJsonClient

    def fake_run(cmd, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1)
    assert client.complete_json(system="s", user="u", schema_name="x") is None


# ---------------------------------------------------------------------------
# Transport retry + failure-kind classification
#
# A provider capacity window (99 "Reconnecting…" lines in the raw log) pushed
# 35/137 docs of one compile to the deterministic baseline, and was read three
# times as the MODEL having worse schema compliance. Two defects: the single
# CODEX_HOME made the rotation a single attempt, and the None return carried
# no provenance so transport and bad-generation rendered identically.
# ---------------------------------------------------------------------------


def _codex_fake_run(script, calls):
    """Build a _run_cli fake driven by ``script`` (one entry per call).

    Each entry is ``(returncode, last_message)``; the message is written to the
    --output-last-message path so the client reads it the way codex writes it.
    """

    def fake_run(cmd, **kwargs):
        import types

        calls.append(kwargs.get("env", {}).get("CODEX_HOME"))
        rc, message = script[min(len(calls) - 1, len(script) - 1)]
        if rc == 0:
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(message)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=rc, stdout="", stderr=message)

    return fake_run


def test_codex_client_retries_transport_failure_against_same_home(monkeypatch, tmp_path):
    """A momentary 5xx must NOT permanently condemn the doc to deterministic.

    Rotation is not the remedy here — this machine has ONE paid account — so
    the retry must re-run against the SAME CODEX_HOME.
    """
    from tesserae.llm_json import CodexCLIJsonClient

    calls: list = []
    script = [
        (1, "stream error: We're currently experiencing high demand"),
        (1, "stream disconnected before completion; Reconnecting..."),
        (0, '{"ok": true}'),
    ]
    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run(script, calls))
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "only-home"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)])
    result = client.complete_json(system="s", user="u", schema_name="x")

    assert result == {"ok": True}
    assert calls == [str(home), str(home), str(home)]  # retried, never rotated
    assert llm_json.last_failure_kind() is None


def test_codex_client_does_not_retry_a_timeout(monkeypatch, tmp_path):
    """The wedge guard bounds ONE attempt; retrying a 1800s timeout would turn
    a bounded 30 min into 90 min per document."""
    import subprocess as _subprocess

    from tesserae.llm_json import CodexCLIJsonClient

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs.get("env", {}).get("CODEX_HOME"))
        raise _subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == 1  # one attempt, no retry stacking


def _fake_clock(monkeypatch, elapsed_per_call: float):
    """Replace ``llm_json.time`` with a clock the test advances itself.

    Swapping the whole module attribute (not ``time.monotonic``) keeps the real
    ``time`` module untouched — several tests here patch ``llm_json.time.sleep``
    on the genuine module.
    """
    import types

    clock = {"t": 0.0}

    def tick():
        clock["t"] += elapsed_per_call
        return clock["t"]

    monkeypatch.setattr(
        llm_json, "time",
        types.SimpleNamespace(monotonic=lambda: clock["t"], sleep=lambda _s: None),
    )
    return clock, tick


def test_codex_transport_retry_is_bounded_by_cumulative_elapsed(monkeypatch, tmp_path):
    """A SLOW non-zero exit must not triple the per-document wedge bound.

    A capacity window presents as `codex exec` sitting in network wait at 0% CPU
    for ~TESSERAE_EXTRACT_TIMEOUT and then exiting non-zero — the returncode
    path, which the TimeoutExpired guard does not cover. Unbounded, that doc
    costs 3 x 1800s while .tesserae/compile.lock is held.
    """
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    calls: list = []
    clock, tick = _fake_clock(monkeypatch, elapsed_per_call=1800.0)

    def slow_failure(cmd, **kwargs):
        calls.append(1)
        tick()
        return types.SimpleNamespace(returncode=1, stdout="", stderr="stream error")

    monkeypatch.setattr(llm_json, "_run_cli", slow_failure)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1800)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == 1  # budget spent by the first attempt
    assert clock["t"] < 2 * 1800  # the honest bound: "< 2x", not "== 1x"


def test_codex_transport_retry_still_fires_on_a_fast_failure(monkeypatch, tmp_path):
    """...and the cumulative bound must not silently disable the retry itself:
    a fast 5xx still gets the full rotation + backoff."""
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    calls: list = []
    _fake_clock(monkeypatch, elapsed_per_call=0.0)

    def fast_failure(cmd, **kwargs):
        calls.append(1)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="stream error")

    monkeypatch.setattr(llm_json, "_run_cli", fast_failure)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1800)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == llm_json._TRANSPORT_RETRIES + 1


def test_a_fast_failure_does_not_sleep_past_the_budget_and_retry(monkeypatch, tmp_path):
    """The bound is on when the NEXT rotation STARTS, not on when we last looked.

    Checking elapsed alone left the backoff sleep uncharged, so a failure fast
    enough to leave budget on the clock could sleep straight past the bound and
    start another rotation anyway. Measured against the real clock at
    timeout=1s / backoff=2s: attempt 2 began at t=2.01s, i.e. the true bound was
    timeout + backoff + timeout, not timeout.

    The clock here advances on ``sleep`` — that is the whole point of the test,
    so it cannot use ``_fake_clock`` (whose sleep is free).
    """
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    calls: list = []
    sleeps: list = []
    clock = {"t": 0.0}

    def advancing_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(
        llm_json, "time",
        types.SimpleNamespace(monotonic=lambda: clock["t"], sleep=advancing_sleep),
    )
    monkeypatch.setattr(llm_json, "_TRANSPORT_BACKOFF", 2.0)

    def fast_failure(cmd, **kwargs):
        calls.append(clock["t"])
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(llm_json, "_run_cli", fast_failure)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == 1  # a 2s backoff cannot fit inside a 1s budget
    assert sleeps == []  # ...and the doomed sleep is not burned either
    assert all(t < client.timeout for t in calls)  # nothing STARTS past the bound


def test_codex_does_not_retry_a_permanent_failure(monkeypatch, tmp_path):
    """An expired token / missing binary is not transient.

    Retrying one costs 3 spawns + [2.0, 4.0] sleeps PER DOCUMENT — 411 spawns
    and ~14 minutes of pure time.sleep on a 137-doc corpus, added to a compile
    that is guaranteed to fail.
    """
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    slept: list = []
    monkeypatch.setattr(llm_json.time, "sleep", lambda s: slept.append(s))
    home = tmp_path / "h"
    home.mkdir()
    client = CodexCLIJsonClient(codex_homes=[str(home)])

    # (a) expired OAuth — the same substring test ClaudeCLIJsonClient uses.
    calls: list = []

    def not_logged_in(cmd, **kwargs):
        calls.append(1)
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="Not logged in. Run `codex login`."
        )

    monkeypatch.setattr(llm_json, "_run_cli", not_logged_in)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == 1 and slept == []

    # (b) no `codex` on PATH — no amount of backoff installs it.
    calls.clear()

    def missing_binary(cmd, **kwargs):
        calls.append(1)
        raise FileNotFoundError(2, "No such file or directory: 'codex'")

    monkeypatch.setattr(llm_json, "_run_cli", missing_binary)
    assert client.complete_json(system="s", user="u", schema_name="y") is None
    assert len(calls) == 1 and slept == []


def test_stale_home_does_not_kill_transport_retry(monkeypatch, tmp_path):
    """One stale ``~/.codex*`` directory must not cancel the retry for the good one.

    Homes are not accounts. With CODEX_HOME absent from the environment — a
    daemon, a launchd job, a scrubbed subprocess env — ``__init__`` discovers
    every ``~/.codex*`` DIRECTORY, and on the target machine four of the five
    are stale. A logged-out verdict that was sticky per CALL made the whole
    transport retry inert in exactly that (default) shape.
    """
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    good = tmp_path / "codex"
    stale = tmp_path / "codex-stale"
    good.mkdir()
    stale.mkdir()

    calls: list = []

    def fake_run(cmd, **kwargs):
        home = kwargs.get("env", {}).get("CODEX_HOME")
        calls.append(home)
        if home == str(stale):
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="Not logged in. Run `codex login`."
            )
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="stream error: We're currently experiencing high demand"
        )

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)

    client = CodexCLIJsonClient(codex_homes=[str(good), str(stale)])
    assert client.complete_json(system="s", user="u", schema_name="x") is None

    assert calls.count(str(good)) == llm_json._TRANSPORT_RETRIES + 1
    assert calls.count(str(stale)) == 1  # asked once; it will answer the same
    # A capacity blip on the healthy home is NOT an auth failure just because a
    # sibling directory is logged out.
    assert llm_json.last_failure_kind() == "unavailable"


def test_all_homes_logged_out_aborts_without_retry(monkeypatch, tmp_path):
    """...and the property the per-home scoping must not break: an expired
    session still costs ONE rotation, not 3 spawns + 6s of sleep per document."""
    import types

    from tesserae.llm_json import CodexCLIJsonClient

    home_a = tmp_path / "codex"
    home_b = tmp_path / "codex-personal1"
    home_a.mkdir()
    home_b.mkdir()

    calls: list = []
    slept: list = []

    def not_logged_in(cmd, **kwargs):
        calls.append(kwargs.get("env", {}).get("CODEX_HOME"))
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="Not logged in. Run `codex login`."
        )

    monkeypatch.setattr(llm_json, "_run_cli", not_logged_in)
    monkeypatch.setattr(llm_json.time, "sleep", lambda s: slept.append(s))

    client = CodexCLIJsonClient(codex_homes=[str(home_a), str(home_b)])
    assert client.complete_json(system="s", user="u", schema_name="x") is None

    assert calls == [str(home_a), str(home_b)]
    assert slept == []
    assert llm_json.last_failure_kind() == "auth"


def test_codex_timeout_is_not_reported_as_a_capacity_outage(monkeypatch, tmp_path, caplog):
    """The timeout log line must name the real cause and the real remedy.

    Classifying it "unavailable" and telling the operator to run `codex login`
    sends them to wait out a capacity window that does not exist.
    """
    import logging
    import subprocess as _subprocess

    from tesserae.llm_json import CodexCLIJsonClient

    def timing_out(cmd, **kwargs):
        raise _subprocess.TimeoutExpired(cmd=cmd, timeout=1800)

    monkeypatch.setattr(llm_json, "_run_cli", timing_out)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1800)
    with caplog.at_level(logging.WARNING):
        assert client.complete_json(system="s", user="u", schema_name="x") is None

    assert llm_json.last_failure_kind() == "timeout"  # NOT "unavailable"
    message = "\n".join(r.getMessage() for r in caplog.records)
    assert "TESSERAE_EXTRACT_TIMEOUT" in message
    # The line must steer AWAY from re-auth — that remedy belongs to the auth
    # verdict. It may not do so absolutely: a hung token refresh reaches this
    # same branch, so "will not help" was itself an overclaim.
    assert "codex login" in message and "unlikely to help" in message


def test_claude_client_tries_each_config_dir_exactly_once(monkeypatch, tmp_path):
    """Rotation IS this client's retry — it never hammers one account.

    ``_run_prompt`` used to take a ``max_retries`` and wrap its body in
    ``for attempt in range(max_retries + 1)`` whose second iteration was
    unreachable (every path returns or breaks), so the parameter documented a
    retry that never happened. Deleting it is only safe if this stays true.
    """
    import inspect
    import types

    from tesserae.llm_json import ClaudeCLIJsonClient

    seen: list = []

    def failing(cmd, **kwargs):
        seen.append(kwargs["env"].get("CLAUDE_CONFIG_DIR"))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="429 rate limited")

    monkeypatch.setattr(llm_json, "_run_cli", failing)
    dirs = [str(tmp_path / "a"), str(tmp_path / "b")]
    client = ClaudeCLIJsonClient(config_dirs=dirs)

    assert client.complete_json(system="s", user="u", schema_name="x", max_retries=5) is None
    assert seen == dirs  # one attempt per account, even at max_retries=5
    assert "max_retries" not in inspect.signature(client._run_prompt).parameters


def test_codex_failure_kind_unavailable_vs_unparseable(monkeypatch, tmp_path):
    """The three None paths must be distinguishable at the point of failure."""
    from tesserae.llm_json import CodexCLIJsonClient

    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "h"
    home.mkdir()
    client = CodexCLIJsonClient(codex_homes=[str(home)])

    # (a) every attempt exits non-zero: transport.
    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(1, "Reconnecting...")], []))
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert llm_json.last_failure_kind() == "unavailable"

    # (c) exit 0 with prose: the model DID answer, and answered badly.
    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(0, "I could not do that.")], []))
    assert client.complete_json(system="s", user="u", schema_name="y") is None
    assert llm_json.last_failure_kind() == "unparseable"

    # (b) exit 0 with an EMPTY last-message: transport wearing a clean exit code.
    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(0, "")], []))
    assert client.complete_json(system="s", user="u", schema_name="z") is None
    assert llm_json.last_failure_kind() == "unavailable"


def test_failure_kind_cleared_on_success(monkeypatch, tmp_path):
    """A stale note from an earlier failure must not misattribute a later call."""
    from tesserae.llm_json import CodexCLIJsonClient

    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "h"
    home.mkdir()
    client = CodexCLIJsonClient(codex_homes=[str(home)])

    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(1, "boom")], []))
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert llm_json.last_failure_kind() == "unavailable"

    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(0, '{"ok": 1}')], []))
    assert client.complete_json(system="s", user="u", schema_name="x2") == {"ok": 1}
    assert llm_json.last_failure_kind() is None


def test_claude_client_failure_kind_unavailable_vs_unparseable(
    monkeypatch, tmp_path, caplog, reset_login_warning
):
    """Same failure-kind treatment on the Claude CLI client (bug-class sweep).

    Takes ``reset_login_warning`` so the "no /login hint" assertion below can't
    pass just because the one-shot guard already fired in an earlier test.
    """
    import logging
    import subprocess
    import types

    from tesserae.llm_json import ClaudeCLIJsonClient

    client = ClaudeCLIJsonClient(config_dirs=[str(tmp_path / "cfg")])

    monkeypatch.setattr(
        llm_json, "_run_cli",
        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="429 rate limited"),
    )
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert llm_json.last_failure_kind() == "unavailable"

    monkeypatch.setattr(
        llm_json, "_run_cli",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="sorry, no.", stderr=""),
    )
    assert client.complete_json(system="s", user="u", schema_name="y") is None
    assert llm_json.last_failure_kind() == "unparseable"

    monkeypatch.setattr(
        llm_json, "_run_cli",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="   ", stderr=""),
    )
    assert client.complete_json(system="s", user="u", schema_name="z") is None
    assert llm_json.last_failure_kind() == "unavailable"

    # ...and a timeout is its own verdict here too: all we saw is the bound
    # being hit, so "unavailable" would send the operator after an outage we
    # never observed.
    def _timing_out(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1800)

    monkeypatch.setattr(llm_json, "_run_cli", _timing_out)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert client.complete_json(system="s", user="u", schema_name="t") is None
    assert llm_json.last_failure_kind() == "timeout"
    # ...and it must not be reported as an auth problem either: a timeout never
    # cleared the all-profiles-not-logged-in tracker, so it used to surface as
    # "Claude CLI not logged in — run `claude /login`".
    message = "\n".join(r.getMessage() for r in caplog.records)
    assert "/login" not in message
    assert "TESSERAE_EXTRACT_TIMEOUT" in message


# ---------------------------------------------------------------------------
# Factory: provider selection (claude | codex) + availability gates
# ---------------------------------------------------------------------------


def test_codex_cli_available_false_without_binary(monkeypatch):
    from tesserae.llm_json import _codex_cli_available

    monkeypatch.setenv("PATH", "/nonexistent-bin-only-dir")
    assert _codex_cli_available() is False


def test_codex_cli_available_with_binary_and_credentialed_home(monkeypatch, tmp_path):
    from tesserae.llm_json import _codex_cli_available

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_bin = bin_dir / "codex"
    codex_bin.write_text("#!/bin/sh\nexit 0\n")
    codex_bin.chmod(0o755)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text("{}")

    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert _codex_cli_available() is True


def _isolate_factory(monkeypatch):
    """Common env isolation for build_default_json_client tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)


def test_build_default_provider_codex_prefers_codex(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    client = lj.build_default_json_client(provider="codex")
    assert isinstance(client, lj.CodexCLIJsonClient)


def test_build_default_env_provider_codex(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setenv("TESSERAE_LLM_PROVIDER", "codex")
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    client = lj.build_default_json_client()
    assert isinstance(client, lj.CodexCLIJsonClient)


def test_build_default_order_unchanged_for_claude_default(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    client = lj.build_default_json_client()
    assert isinstance(client, lj.ClaudeCLIJsonClient)


def test_build_default_codex_fills_former_none_gap(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    client = lj.build_default_json_client()
    assert isinstance(client, lj.CodexCLIJsonClient)


def test_build_default_provider_codex_falls_back_to_claude(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: False)

    client = lj.build_default_json_client(provider="codex")
    assert isinstance(client, lj.ClaudeCLIJsonClient)


def test_build_default_passes_codex_home_and_claude_dirs(monkeypatch, tmp_path):
    import tesserae.llm_json as lj

    _isolate_factory(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    codex_home = str(tmp_path / "codex-personal1")
    codex = lj.build_default_json_client(provider="codex", codex_home=codex_home)
    assert isinstance(codex, lj.CodexCLIJsonClient)
    assert codex.codex_homes == [codex_home]

    claude_dir = str(tmp_path / "claude-personal2")
    claude = lj.build_default_json_client(claude_config_dirs=[claude_dir])
    assert isinstance(claude, lj.ClaudeCLIJsonClient)
    assert claude.config_dirs == [claude_dir]


def test_resolve_llm_client_settings_precedence(monkeypatch):
    from tesserae.llm_json import resolve_llm_client_settings

    _isolate_factory(monkeypatch)
    cfg = {
        "llm_provider": "codex",
        "llm_codex_home": "/cfg/codex",
        "llm_claude_config_dirs": ["/cfg/claude"],
    }
    # config-only: cfg values flow through
    settings = resolve_llm_client_settings(cfg)
    assert settings["provider"] == "codex"
    assert settings["codex_home"] == "/cfg/codex"
    assert settings["claude_config_dirs"] == ["/cfg/claude"]

    # env beats config (CLI flags are surfaced as env by the handlers)
    monkeypatch.setenv("TESSERAE_LLM_PROVIDER", "claude")
    monkeypatch.setenv("CODEX_HOME", "/env/codex")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/env/claude")
    settings = resolve_llm_client_settings(cfg)
    assert settings["provider"] == "claude"
    # CODEX_HOME no longer wins here for the same reason CLAUDE_CONFIG_DIR does
    # not: whatever this returns is passed down as an explicit pin, which would
    # collapse rotation to one account. The configured home stands.
    assert settings["codex_home"] == "/cfg/codex"
    # ...EXCEPT CLAUDE_CONFIG_DIR, which is the one ambient var here: every
    # process a Claude Code session spawns inherits it, so it is not evidence
    # of intent the way TESSERAE_* / CODEX_HOME are. Letting it win meant an
    # accidental value overrode the accounts the user configured and pinned
    # the run to one account's quota.
    assert settings["claude_config_dirs"] == ["/cfg/claude"], (
        "configured accounts must beat the inherited CLAUDE_CONFIG_DIR"
    )

    # With nothing configured, this must return None — NOT [env_claude].
    # Whatever it returns is handed to the client as an explicit config_dirs=,
    # which pins verbatim; returning the env dir here collapsed rotation to one
    # account regardless of the constructor's own env handling. None delegates
    # to ClaudeCLIJsonClient, which ranks the env dir first and keeps the rest.
    settings = resolve_llm_client_settings({})
    assert settings["claude_config_dirs"] is None, (
        "an unconfigured env var must not become an explicit single-account pin"
    )


def test_built_client_rotates_when_only_env_is_set(monkeypatch, tmp_path):
    """End-to-end through the REAL construction path, not the constructor alone.

    Regression: the constructor ranked CLAUDE_CONFIG_DIR first and kept the
    other accounts, but resolve_llm_client_settings passed [env_dir] down as an
    explicit config_dirs=, which pinned the client verbatim and re-disabled
    rotation. Testing the constructor in isolation missed it entirely — a live
    compile then lost 1,590 documents to one account's expired OAuth while a
    healthy account sat next to it.
    """
    from tesserae.llm_json import ClaudeCLIJsonClient, build_default_json_client

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    for name in (".claude", ".claude-personal1", ".claude-personal2"):
        (fake_home / name / "settings.json").parent.mkdir()
        (fake_home / name / "settings.json").write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / ".claude-personal2"))
    monkeypatch.setattr(llm_json, "_load_global_llm_config", lambda: {})
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(llm_json, "_codex_cli_available", lambda: False)
    set_client_factory(None)

    client = build_default_json_client(provider="claude")
    assert isinstance(client, ClaudeCLIJsonClient), f"expected the CLI client, got {client!r}"
    dirs = client.config_dirs
    assert dirs[0] == str(fake_home / ".claude-personal2"), f"env dir first, got {dirs}"
    assert len(dirs) == 3, f"the other accounts must stay in rotation, got {dirs}"


def test_run_cli_kills_grandchildren_on_timeout():
    """subprocess.run(capture_output=True, timeout=...) wedges forever when the
    child spawns grandchildren that inherit the output pipes: on timeout Python
    kills only the direct child, then drains the pipes with NO timeout, and the
    drain blocks until the grandchild exits. _run_cli must kill the whole
    process group and return promptly."""
    import subprocess
    import time

    # The backgrounded sleep inherits the stdout pipe and would keep it open
    # for 30s after the direct child dies.
    cmd = ["sh", "-c", "sleep 30 & sleep 30"]
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        llm_json._run_cli(cmd, prompt="", env=dict(os.environ), timeout=0.5)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"timeout drain blocked for {elapsed:.1f}s — group not killed"


def test_run_cli_returns_completed_process_output():
    result = llm_json._run_cli(
        ["sh", "-c", "printf out; printf err >&2; exit 3"],
        prompt="",
        env=dict(os.environ),
        timeout=10,
    )
    assert result.returncode == 3
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_build_default_json_client_seam_forwards_timeout():
    """The test seam must observe the timeout the production path would have used.

    It previously returned AnthropicLLMJsonClient(model=...) with no timeout, so the
    injected fake was handed the 30s Anthropic default regardless of what the caller
    asked for — a test pinning a timeout silently proved nothing. _KEEP_TIMEOUT
    semantics match the real builders: sentinel -> client default, explicit value
    (including None = no cutoff) -> forwarded verbatim.
    """
    from tesserae.llm_json import build_default_json_client, set_client_factory

    seen: dict = {}
    try:
        set_client_factory(lambda api_key=None, timeout=None: seen.update(timeout=timeout))

        build_default_json_client(timeout=600)
        assert seen["timeout"] == 600

        build_default_json_client(timeout=None)   # 0 -> None: no cutoff
        assert seen["timeout"] is None

        seen.clear()
        build_default_json_client()               # sentinel -> Anthropic default
        assert seen["timeout"] == 30.0
    finally:
        set_client_factory(None)


def test_cli_clients_honour_cache_key(tmp_path, monkeypatch):
    """cache_key was accepted and ignored by both CLI clients, so every recompile
    re-paid full price for byte-identical input. A second identical call must now
    hit disk instead of the CLI — and a different model must NOT reuse the answer."""
    import tesserae.llm_json as lj

    monkeypatch.setattr(lj, "_CLI_CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("TESSERAE_LLM_CACHE", raising=False)

    calls = []

    def _fake_run(self, prompt, **kw):
        calls.append(prompt)
        return '{"nodes": [], "edges": []}'

    monkeypatch.setattr(lj.CodexCLIJsonClient, "_run_prompt", _fake_run)
    client = lj.CodexCLIJsonClient(codex_homes=["/x"], model="gpt-5.6-luna")

    a = client.complete_json(system="s", user="u", schema_name="g", cache_key="k1")
    b = client.complete_json(system="s", user="u", schema_name="g", cache_key="k1")
    assert a == b == {"nodes": [], "edges": []}
    assert len(calls) == 1, "second identical call should have hit the cache"

    # A different model must re-ask: the caller's cache_key covers content only.
    # Codex clients are pinned to one model (CODEX_DEFAULT_MODEL), so two of them
    # can no longer differ by constructor argument — move the pin itself to get
    # a genuinely different model rather than asserting a state the pin forbids.
    monkeypatch.setattr(lj, "CODEX_DEFAULT_MODEL", "some-other-model")
    other = lj.CodexCLIJsonClient(codex_homes=["/x"])
    assert other.model == "some-other-model"
    other.complete_json(system="s", user="u", schema_name="g", cache_key="k1")
    assert len(calls) == 2, "a different model must not reuse another model's answer"

    # No cache_key => no caching (callers that don't opt in are unaffected).
    client.complete_json(system="s", user="u", schema_name="g")
    client.complete_json(system="s", user="u", schema_name="g")
    assert len(calls) == 4

    # Kill switch.
    monkeypatch.setenv("TESSERAE_LLM_CACHE", "0")
    client.complete_json(system="s", user="u", schema_name="g", cache_key="k1")
    assert len(calls) == 5


def test_cli_cache_never_stores_unparseable_output(tmp_path, monkeypatch):
    """Caching a malformed generation would make one bad roll permanent."""
    import tesserae.llm_json as lj

    monkeypatch.setattr(lj, "_CLI_CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("TESSERAE_LLM_CACHE", raising=False)
    calls = []

    def _fake_run(self, prompt, **kw):
        calls.append(prompt)
        return "not json at all"

    monkeypatch.setattr(lj.CodexCLIJsonClient, "_run_prompt", _fake_run)
    client = lj.CodexCLIJsonClient(codex_homes=["/x"], model="gpt-5.6-luna")
    client.complete_json(system="s", user="u", schema_name="g", cache_key="bad")
    client.complete_json(system="s", user="u", schema_name="g", cache_key="bad")
    assert len(calls) == 2, "a malformed answer must not be cached"


# ---------------------------------------------------------------------------
# Failure-shape parity + the auth verdict
# ---------------------------------------------------------------------------


def test_claude_empty_answer_rotates_to_the_next_profile(monkeypatch, tmp_path):
    """`claude -p` exiting 0 with an EMPTY body is transport, not an answer.

    Returning "" ended the rotation on the FIRST profile, so a blip on account A
    was reported as "the backend produced nothing" while account B — the whole
    point of keeping a rotation — was never asked.
    """
    import types

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs["env"].get("CLAUDE_CONFIG_DIR"))
        if len(calls) == 1:
            return types.SimpleNamespace(returncode=0, stdout="   \n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout='{"ok": 1}', stderr="")

    monkeypatch.setattr(llm_json, "_run_cli", fake_run)
    dirs = [str(tmp_path / "a"), str(tmp_path / "b")]
    client = ClaudeCLIJsonClient(config_dirs=dirs)

    assert client.complete_json(system="s", user="u", schema_name="x") == {"ok": 1}
    assert calls == dirs  # the empty answer did NOT end the rotation


def test_claude_auth_failure_is_its_own_verdict(monkeypatch, tmp_path, reset_login_warning):
    """Every profile answering "not logged in" is an AUTH verdict, not capacity.

    It used to flatten to "unavailable", which the extractor renders per document
    as "(transport/capacity)" — a remedy ("wait and re-run") that can never fix
    an expired session, on every line but the one scrolled-away login hint.
    """
    import types

    monkeypatch.setattr(
        llm_json, "_run_cli",
        lambda cmd, **kw: types.SimpleNamespace(
            returncode=1, stdout="", stderr="Not logged in · Please run /login"
        ),
    )
    client = ClaudeCLIJsonClient(config_dirs=[str(tmp_path / "cfg")])
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert llm_json.last_failure_kind() == "auth"


def test_codex_auth_failure_is_its_own_verdict_only_when_every_home_agrees(
    monkeypatch, tmp_path
):
    """Same verdict on the codex client — but all-or-nothing.

    A capacity failure on one home must not be reported as an auth problem just
    because a different home happens to be logged out; that would swap one
    confidently-wrong remedy for another.
    """
    from tesserae.llm_json import CodexCLIJsonClient

    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    homes = [str(tmp_path / "h1"), str(tmp_path / "h2")]

    calls: list = []
    monkeypatch.setattr(
        llm_json, "_run_cli", _codex_fake_run([(1, "stream error: Not logged in")], calls)
    )
    client = CodexCLIJsonClient(codex_homes=homes)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert llm_json.last_failure_kind() == "auth"

    # ...but mixed causes stay "unavailable": h1 is logged out, h2 is a capacity blip.
    mixed: list = []
    monkeypatch.setattr(
        llm_json, "_run_cli",
        _codex_fake_run([(1, "Not logged in"), (1, "stream error: high demand")], mixed),
    )
    assert client.complete_json(system="s", user="u", schema_name="y") is None
    assert llm_json.last_failure_kind() == "unavailable"


def test_codex_empty_answer_reaches_the_transport_retry(monkeypatch, tmp_path):
    """Exit 0 with an EMPTY last message must get the same rolls as any other
    transport failure.

    It returned "" straight out of the rotation, so it was the ONE shape that
    never entered the retry loop — while the extractor above declines to re-ask
    on the grounds that the transport layer already retried.
    """
    from tesserae.llm_json import CodexCLIJsonClient

    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    calls: list = []
    monkeypatch.setattr(llm_json, "_run_cli", _codex_fake_run([(0, "")], calls))
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)], timeout=1800)
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    assert len(calls) == llm_json._TRANSPORT_RETRIES + 1
    assert llm_json.last_failure_kind() == "unavailable"  # nothing was generated


def test_transport_retry_prose_states_the_bound_it_actually_delivers():
    """The retry is a CEILING, not a promise — the prose must say so.

    ``_run_prompt`` refuses to start a new rotation once cumulative elapsed time
    reaches ``self.timeout``, and the observed capacity shape spends exactly that
    budget in network wait, so with the default 1800s the retry never fires for
    it (pinned behaviourally by
    ``test_codex_transport_retry_is_bounded_by_cumulative_elapsed``). Prose that
    promises an unconditional "the WHOLE rotation is re-run with backoff" sends
    an operator looking for two retries that the code will not perform.
    """
    import inspect

    source = inspect.getsource(llm_json)
    banner = source[source.index("#: A transport failure"): source.index("_TRANSPORT_RETRIES = 2")]
    docstring = inspect.getdoc(llm_json.CodexCLIJsonClient._run_prompt) or ""

    for text, label in ((banner, "_TRANSPORT_RETRIES comment"), (docstring, "_run_prompt docstring")):
        lowered = text.lower()
        assert "cumulative" in lowered, f"{label} does not name the cumulative-elapsed bound"
        assert "once" in lowered or "zero" in lowered, (
            f"{label} does not say that a rotation which spends the whole budget "
            "gets no retry"
        )


# ---------------------------------------------------------------------------
# What a timeout establishes — and what it does not
#
# The timeout verdict exists so an operator is not sent to wait out a capacity
# window that does not exist. Round 3 then overcorrected into the mirror-image
# claim: "The provider was reachable". Nothing in this layer can see that. The
# ``TimeoutExpired`` is raised on a killed CHILD PROCESS, so a DNS/connect stall
# that never sent a byte arrives here identically to a slow generation, and
# telling that operator to raise the bound or split the document buys them
# another full ``TESSERAE_EXTRACT_TIMEOUT`` of nothing.
# ---------------------------------------------------------------------------

# Past tense ONLY: "was reachable" is the claim about a round trip we never
# observed. "check the provider is reachable from this host" is the remedy, and
# has to survive.
_ASSERTS_REACHABILITY = re.compile(r"\b(?:was|were)\s+reachable\b", re.IGNORECASE)


def _timeout_before_the_provider_saw_it(monkeypatch, tmp_path, client_factory):
    """Drive a client through a timeout raised BEFORE the request left the host."""
    import subprocess as _subprocess

    def stalled(cmd, **kwargs):
        # The DNS/connect-stall shape: the CLI never reached the provider, and
        # the wedge guard killed it. Indistinguishable, from here, from a slow
        # generation — which is the whole point.
        raise _subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(llm_json, "_run_cli", stalled)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    home = tmp_path / "h"
    home.mkdir()
    client = client_factory(str(home))
    assert client.complete_json(system="s", user="u", schema_name="x") is None
    return client


@pytest.mark.parametrize(
    "client_factory",
    [
        pytest.param(lambda h: llm_json.CodexCLIJsonClient(codex_homes=[h], timeout=1), id="codex"),
        pytest.param(lambda h: ClaudeCLIJsonClient(config_dirs=[h], timeout=1), id="claude"),
    ],
)
def test_timeout_line_states_the_bound_without_asserting_reachability(
    monkeypatch, tmp_path, caplog, client_factory
):
    """The timeout line may claim the bound was hit. It may not claim a round trip."""
    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        _timeout_before_the_provider_saw_it(monkeypatch, tmp_path, client_factory)

    # Behaviour (c) is untouched: timeout stays its own verdict, not "unavailable".
    assert llm_json.last_failure_kind() == "timeout"

    lines = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert lines, "the timeout produced no operator-facing line"
    for line in lines:
        assert not _ASSERTS_REACHABILITY.search(line), (
            f"the timeout line asserts a round trip this layer cannot observe: {line}"
        )
        # ...and it still has to be actionable in BOTH directions, or it is just
        # a shrug. Large doc -> raise/split; small doc -> check connectivity.
        assert "TESSERAE_EXTRACT_TIMEOUT" in line
        assert "split" in line
        assert "reachable from this host" in line


def test_extraction_timeout_error_prose_matches_what_the_timeout_proves():
    """Both the class docstring and the raised message; both used to overclaim."""
    from tesserae.llm_extractor import ExtractionTimeoutError

    doc = inspect.getdoc(ExtractionTimeoutError) or ""
    assert not _ASSERTS_REACHABILITY.search(doc), (
        "ExtractionTimeoutError's docstring still asserts the provider was reachable"
    )
    assert "does not" in doc.lower() or "not establish" in doc.lower(), (
        "the docstring does not say what the timeout fails to establish"
    )

    source = inspect.getsource(llm_extractor.LLMResearchExtractor)
    start = source.index("raise ExtractionTimeoutError(")
    message = source[start : source.index('if kind == "auth":', start)]
    assert not _ASSERTS_REACHABILITY.search(message), (
        f"the ExtractionTimeoutError message still asserts reachability: {message}"
    )


# The claim was retired in seven places at once and immediately reappeared in an
# eighth (cli.py's router comment), because the guards above only inspect the two
# modules they import. Sweep the package instead, so the NEXT copy fails here
# rather than in a review.
_REACHABILITY_SWEEP_ALLOWED = {
    # Semantically different: describes a backend that answered and then failed,
    # which is precisely the case this layer CAN observe.
    ("session_graph.py", "reachable-but-failing"),
}


def test_no_module_claims_the_provider_was_reachable_on_a_timeout():
    package = Path(llm_json.__file__).parent
    offenders = []
    for path in sorted(package.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _ASSERTS_REACHABILITY.search(line):
                continue
            if any(
                path.name == name and token in line
                for name, token in _REACHABILITY_SWEEP_ALLOWED
            ):
                continue
            offenders.append(f"{path.relative_to(package)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "a timeout cannot establish that the provider was reached — these assert it did:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Auth-hint volume: the two clients are deliberately unequal, so say so
#
# The ``auth`` verdict is re-read PER DOCUMENT — that is what makes every failed
# doc name `claude /login` / `codex login` instead of "transport/capacity". The
# LOG lines on top of it are not symmetric: Claude de-duplicates its static hint
# to once per process, codex logs one line per call. Prose that promises
# "logged ONCE per process" flatly is false for half the code it describes.
# ---------------------------------------------------------------------------


def _logged_out_run(cmd, **kwargs):
    return mock.Mock(returncode=1, stdout="", stderr="Not logged in · Please run /login")


def test_auth_hint_volume_is_per_call_on_codex_and_once_per_process_on_claude(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(llm_json, "_run_cli", _logged_out_run)
    monkeypatch.setattr(llm_json.time, "sleep", lambda _s: None)
    llm_json._reset_login_warning_for_tests()
    home = tmp_path / "h"
    home.mkdir()

    with caplog.at_level("WARNING", logger="tesserae.llm_json"):
        codex = llm_json.CodexCLIJsonClient(codex_homes=[str(home)], timeout=1)
        for i in range(5):
            assert codex.complete_json(system="s", user=f"doc{i}", schema_name="x") is None
        codex_lines = [r.getMessage() for r in caplog.records if "codex login" in r.getMessage()]

        caplog.clear()
        claude = ClaudeCLIJsonClient(config_dirs=[str(home)], timeout=1)
        for i in range(5):
            assert claude.complete_json(system="s", user=f"doc{i}", schema_name="x") is None
        claude_lines = [r.getMessage() for r in caplog.records if "/login" in r.getMessage()]

    assert len(codex_lines) == 5, "codex must keep naming the real remedy per document"
    assert len(claude_lines) == 1, "the Claude hint is still de-duplicated per process"
    # Either way the per-document DIAGNOSIS is the verdict, not the log line.
    assert llm_json.last_failure_kind() == "auth"


def test_auth_prose_does_not_promise_a_once_per_process_hint_for_both_clients():
    """The claim has to name which client it is about, because they differ."""
    from tesserae.llm_extractor import ProviderAuthError

    banner = inspect.getsource(llm_json)
    banner = banner[banner.index('#: ``"auth"``'): banner.index("_LAST_FAILURE = threading.local()")]

    for text, label in (
        (inspect.getdoc(ProviderAuthError) or "", "ProviderAuthError docstring"),
        (banner, "_LAST_FAILURE auth banner"),
    ):
        if "once per process" not in text.lower():
            continue
        assert "ClaudeCLIJsonClient" in text, (
            f"{label} promises a once-per-process hint without saying it is the "
            "Claude client's alone"
        )
        assert "CodexCLIJsonClient" in text and "per call" in text.lower(), (
            f"{label} does not record that the codex client logs one line per call"
        )
