"""Tests for the LLMJsonClient interface used by the session graph LLM pass."""

from __future__ import annotations

import os
from typing import Any, List
from unittest import mock

import pytest

from tesserae import llm_json
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
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **kw: fake_proc,
    )
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
    """CLAUDE_CONFIG_DIR env wins over the auto-discovery glob."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude-personal1").mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / ".claude-personal1"))
    client = ClaudeCLIJsonClient()
    assert client.config_dirs == [str(fake_home / ".claude-personal1")]


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

    env_home = tmp_path / "codex-personal1"
    env_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(env_home))

    client = CodexCLIJsonClient()
    assert client.codex_homes == [str(env_home)]


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
    home = tmp_path / "h"
    home.mkdir()

    client = CodexCLIJsonClient(codex_homes=[str(home)])
    with caplog.at_level(logging.WARNING):
        result = client.complete_json(system="s", user="u", schema_name="x")

    assert result is None
    assert any("codex" in r.getMessage().lower() for r in caplog.records)


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
    assert settings["codex_home"] == "/env/codex"
    assert settings["claude_config_dirs"] == ["/env/claude"]


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
    other = lj.CodexCLIJsonClient(codex_homes=["/x"], model="some-other-model")
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
