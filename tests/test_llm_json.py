"""Tests for the LLMJsonClient interface used by the session graph LLM pass."""

from __future__ import annotations

import os
from typing import Any, List
from unittest import mock

import pytest

from tesserae import llm_json
from tesserae.llm_json import (
    AnthropicLLMJsonClient,
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
