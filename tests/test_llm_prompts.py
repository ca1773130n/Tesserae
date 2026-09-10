"""`setup.llm_prompts` — the one backend conversation, shared by three commands.

`tesserae setup`, `tesserae init` and `tesserae config llm` each used to ask (or
fail to ask) their own version of this. Two of the three had the same hole: a
`custom` provider was asked for a base URL, an API key and a model, and never
for the wire protocol or a bearer token — a combination that cannot answer,
because `llm_api_style` defaults to `anthropic` for `custom`.
"""
from __future__ import annotations

import pytest

from tesserae.setup.llm_prompts import PROVIDERS, prompt_llm_backend


def _scripted(answers: dict, seen: list | None = None):
    """A prompt function that answers by substring, else takes the default."""

    def ask(prompt="", **kwargs):
        text = str(prompt)
        if seen is not None:
            seen.append(text)
        for key, value in answers.items():
            if key.lower() in text.lower():
                return value
        return kwargs.get("default", "")

    return ask


def test_every_provider_the_parsers_accept_is_offered():
    """A wizard must not be able to hide a backend the flags can reach."""
    from tesserae.cli import _build_config_parser

    # Read off the flag itself rather than restated here, so adding a provider
    # to the CLI and forgetting the wizard fails this test.
    parser = _build_config_parser()
    llm = parser._subparsers._group_actions[0].choices["llm"]  # type: ignore[attr-defined]
    flag = next(a for a in llm._actions if "--llm-provider" in a.option_strings)
    assert set(PROVIDERS) == set(flag.choices)


def test_an_openai_compatible_endpoint_is_fully_specified():
    seen: list = []
    answers = prompt_llm_backend(
        {},
        ask=_scripted({
            "LLM provider": "openai",
            "Wire protocol": "openai",
            "Base URL": "https://gw.example/v1",
            "Credential": "bearer",
            "Bearer token": "tok-bearer",
            "Model name": "deepseek-chat",
        }, seen),
        echo=lambda *a, **k: None,
    )
    assert answers.provider == "openai"
    assert answers.api_style == "openai"
    assert answers.base_url == "https://gw.example/v1"
    assert answers.auth_token == "tok-bearer"
    assert answers.api_key is None, "bearer and api-key are alternatives, never both"
    assert answers.model == "deepseek-chat"
    assert any("Wire protocol" in q for q in seen), "the wire must never be decided silently"


def test_the_caller_may_have_asked_for_the_provider_itself():
    """`init` uses a numbered list annotated with which CLIs are logged in."""
    seen: list = []
    answers = prompt_llm_backend(
        {}, provider="codex",
        ask=_scripted({"Codex reasoning effort": "high", "Codex home": "~/.codex-work"}, seen),
        echo=lambda *a, **k: None,
    )
    assert answers.provider == "codex"
    assert answers.reasoning_effort == "high"
    assert answers.codex_home == "~/.codex-work"
    assert not any("LLM provider" in q for q in seen), "asked twice"


def test_claude_is_asked_for_a_rotation_order_not_one_dir():
    answers = prompt_llm_backend(
        {}, provider="claude",
        ask=_scripted({"Claude config dirs": "~/.claude-work, ~/.claude-personal"}),
        echo=lambda *a, **k: None,
    )
    assert answers.claude_config_dirs == ["~/.claude-work", "~/.claude-personal"]


def test_blank_answers_never_clear_what_is_already_configured():
    """Re-running a wizard and pressing Enter must not erase settings."""
    current = {
        "llm_provider": "openai",
        "llm_api_style": "openai",
        "llm_base_url": "https://gw.example/v1",
        "llm_model": "deepseek-chat",
        "llm_auth_token": "tok",
    }
    answers = prompt_llm_backend(
        current, ask=_scripted({"Credential": "none"}), echo=lambda *a, **k: None,
    )
    assert answers.provider == "openai"
    assert answers.api_style == "openai"
    assert answers.base_url == "https://gw.example/v1"
    assert answers.model == "deepseek-chat"
    # Declining to retype the secret leaves it alone rather than blanking it:
    # the caller only copies non-None answers over the existing config.
    assert answers.auth_token is None and answers.api_key is None


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_cli_provider_is_not_dragged_through_endpoint_questions(provider):
    seen: list = []
    prompt_llm_backend(
        {}, provider=provider, ask=_scripted({}, seen), echo=lambda *a, **k: None,
    )
    assert not any("Wire protocol" in q for q in seen)


def test_config_llm_bare_on_a_tty_runs_the_wizard(monkeypatch):
    """The command the user had to type by hand, one flag at a time."""
    import sys as _sys

    from tesserae.cli import _build_config_parser, _config_llm_wants_interactive

    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True, raising=False)

    bare = _build_config_parser().parse_args(["llm"])
    assert _config_llm_wants_interactive(bare) is True
    # Any knob given means the user is scripting: never prompt over a flag.
    for flag, value in (
        ("--llm-provider", "codex"),
        ("--llm-auth-token", "tok"),
        ("--llm-api-style", "openai"),
        ("--claude-config-dir", "~/.claude-work"),
    ):
        args = _build_config_parser().parse_args(["llm", flag, value])
        assert _config_llm_wants_interactive(args) is False, f"{flag} must skip the wizard"


def test_config_llm_never_blocks_without_a_tty(monkeypatch):
    """CI and scripts run this command too."""
    import sys as _sys

    from tesserae.cli import _build_config_parser, _config_llm_wants_interactive

    monkeypatch.setattr(_sys.stdin, "isatty", lambda: False, raising=False)
    assert _config_llm_wants_interactive(_build_config_parser().parse_args(["llm"])) is False
