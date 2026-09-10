"""Optional-dependency registry: detect + install, and the global config merge."""

from __future__ import annotations

import types

from tesserae import deps
from tesserae.cli import _merge_global_llm_config, _resolve_dep_targets


def test_status_shape_covers_known_deps():
    s = deps.status()
    assert {d["name"] for d in s} >= {"memex", "raganything"}
    assert "understand-anything" not in {d["name"] for d in s}  # backend removed
    assert "cognee" not in {d["name"] for d in s}  # backend removed in 0.19
    assert all(set(d) == {"name", "summary", "installed", "note"} for d in s)


def test_install_unknown_dep():
    r = deps.install("does-not-exist")
    assert r["ok"] is False and "unknown" in r["error"]


def test_install_already_present_is_noop(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: True, ["false"]))
    # subprocess must NOT run when it's already installed.
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    r = deps.install("x")
    assert r["ok"] is True and r["already"] is True


def test_install_runs_then_detects_success(monkeypatch):
    seen = {"n": 0}

    def detect():
        seen["n"] += 1
        return seen["n"] > 1  # absent before install, present after

    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", detect, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    r = deps.install("x")
    assert r["ok"] is True and not r.get("already")


def test_install_failure_surfaces_stderr(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: False, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    r = deps.install("x")
    assert r["ok"] is False and "boom" in r["error"]


def test_install_missing_installer_degrades(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: False, ["nope-cmd"]))

    def boom(*a, **k):
        raise OSError("cargo not found")
    monkeypatch.setattr(deps.subprocess, "run", boom)
    r = deps.install("x")
    assert r["ok"] is False and "could not run" in r["error"]


def test_detect_exception_never_propagates(monkeypatch):
    def boom():
        raise RuntimeError("which blew up")
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", boom, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    # status() and install() must both swallow a detect exception.
    assert deps.status()  # does not raise
    r = deps.install("x")
    assert r["ok"] is False  # detect-after-install raised -> treated as absent


def test_pip_install_falls_back_to_uv_when_pip_absent(monkeypatch):
    # uv tool envs ship without pip -> must not emit a dead `python -m pip` argv.
    monkeypatch.setattr(deps, "_module_present", lambda n: False)
    monkeypatch.setattr(deps, "_binary_present", lambda n: n == "uv")
    argv = deps._pip_install_argv(["some-pkg"])
    assert argv[:3] == ["uv", "pip", "install"] and "--python" in argv and argv[-1] == "some-pkg"
    # when pip IS importable, use it directly
    monkeypatch.setattr(deps, "_module_present", lambda n: n == "pip")
    assert deps._pip_install_argv(["some-pkg"])[1:3] == ["-m", "pip"]


def test_install_uses_uv_argv_for_pip_dep_without_pip(monkeypatch):
    monkeypatch.setattr(deps, "_module_present", lambda n: False)  # nothing importable
    monkeypatch.setattr(deps, "_binary_present", lambda n: n == "uv")
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setitem(
        deps.DEPS_BY_NAME,
        "x",
        deps.Dep("x", "s", lambda: False, ["unused"], pip_specs=["x"]),
    )
    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    deps.install("x")
    assert seen["argv"][:3] == ["uv", "pip", "install"]  # not `python -m pip`


def test_setup_is_top_level_command_interactive_by_default():
    from tesserae.cli import _build_setup_parser, _setup_wants_interactive
    from tesserae.cli_tree import KNOWN_COMMANDS

    assert "setup" in KNOWN_COMMANDS  # `tesserae setup` is the one setup surface
    flagged = _build_setup_parser().parse_args(["--install", "all"])
    assert flagged._handler == "_handle_setup_machine"
    assert _setup_wants_interactive(flagged) is False  # flags given -> skip prompts
    # bare invocation under a non-TTY (CI/scripts) must NOT block on input
    assert _setup_wants_interactive(_build_setup_parser().parse_args([])) is False
    # the old `config setup` alias is gone: it is a moved-command stub now.
    from tesserae.cli import main as _cli_main

    assert _cli_main(["config", "setup"]) == 2


def test_resolve_targets_expands_all_and_dedups():
    targets, unknown = _resolve_dep_targets(["all"], False)
    assert targets == deps.DEP_NAMES and unknown == []
    targets, unknown = _resolve_dep_targets(["memex", "memex", "raganything"], False)
    assert targets == ["memex", "raganything"] and unknown == []
    targets, unknown = _resolve_dep_targets(["bogus"], False)
    assert unknown == ["bogus"]


def test_merge_global_llm_config_only_changes_passed_keys():
    m = _merge_global_llm_config({"keep": 1}, llm_provider="codex", reasoning_effort="medium")
    assert m["keep"] == 1
    assert m["llm_provider"] == "codex"
    assert m["llm_codex_reasoning_effort"] == "medium"
    # An unrelated existing provider survives when not passed.
    m2 = _merge_global_llm_config({"llm_provider": "claude"}, reasoning_effort="high")
    assert m2["llm_provider"] == "claude" and m2["llm_codex_reasoning_effort"] == "high"


# ---------------------------------------------------------------------------
# `tesserae setup` (the wizard) must reach every knob `tesserae config llm` does
# ---------------------------------------------------------------------------


def _wizard_flags() -> set:
    """The LLM knobs `tesserae config llm` accepts, as attribute names."""
    from tesserae.cli import _build_config_parser

    parsed = _build_config_parser().parse_args(["llm"])
    return {k for k in vars(parsed) if k.startswith(("llm_", "claude_", "codex_", "reasoning_"))}


def test_setup_parser_accepts_every_knob_config_llm_does():
    """A flag on one and not the other is a command the user cannot finish."""
    from tesserae.cli import _build_setup_parser

    setup = set(vars(_build_setup_parser().parse_args([])))
    missing = _wizard_flags() - setup
    assert not missing, f"`tesserae setup` cannot set: {sorted(missing)}"


def _run_wizard(monkeypatch, answers, current=None):
    """Drive the real `_setup_interactive_fill` with scripted prompt answers."""
    import tesserae.cli as cli
    import tesserae.deps as _deps
    import tesserae.llm_json as lj
    from rich.prompt import Confirm, Prompt

    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: dict(current or {}))
    asked: list = []

    def fake_ask(prompt, *a, **kw):
        asked.append(str(prompt))
        for key, value in answers.items():
            if key.lower() in str(prompt).lower():
                return value
        return kw.get("default", "")

    monkeypatch.setattr(Prompt, "ask", staticmethod(fake_ask))
    monkeypatch.setattr(Confirm, "ask", staticmethod(lambda *a, **kw: kw.get("default", True)))
    # `deps` is imported inside the function, so patch the module itself.
    monkeypatch.setattr(_deps, "status", lambda: [])
    monkeypatch.setattr(_deps, "DEP_NAMES", [])

    args = cli._build_setup_parser().parse_args([])
    assert cli._setup_interactive_fill(args) is True
    return args, asked


def test_wizard_configures_an_openai_compatible_endpoint_end_to_end(monkeypatch):
    """The gap that sent a user to `tesserae config llm <long arg list>`.

    The wizard asked only base_url / api_key / model for `custom`, so an
    OpenAI-compatible gateway got the Anthropic wire (the default for `custom`)
    and its bearer token written into the api-key header. Nothing the wizard
    could produce would answer, and the only way through was the flag form.
    """
    args, asked = _run_wizard(monkeypatch, {
        "LLM provider": "openai",
        "Wire protocol": "openai",
        "Base URL": "https://gw.example/v1",
        "Credential": "bearer",
        "Bearer token": "tok-bearer",
        "Model name": "deepseek-chat",
    })
    assert args.llm_provider == "openai"
    assert args.llm_api_style == "openai"
    assert args.llm_base_url == "https://gw.example/v1"
    assert args.llm_auth_token == "tok-bearer"
    assert args.llm_api_key is None, "bearer and api-key are alternatives, never both"
    assert args.llm_model == "deepseek-chat"


def test_wizard_asks_for_the_claude_rotation_order(monkeypatch):
    """One dir is a single-account pin that dies with that account's quota."""
    args, _ = _run_wizard(monkeypatch, {
        "LLM provider": "claude",
        "Claude config dirs": "~/.claude-work, ~/.claude-personal",
    })
    assert args.claude_config_dir == ["~/.claude-work", "~/.claude-personal"]


def test_wizard_offers_the_existing_config_as_the_default(monkeypatch):
    """Re-running setup must not silently drop what is already configured."""
    args, _ = _run_wizard(
        monkeypatch,
        {"LLM provider": "claude"},
        current={"llm_claude_config_dir": "~/.claude-work"},  # legacy singular key
    )
    assert args.claude_config_dir == ["~/.claude-work"]


def test_a_passed_flag_is_never_overwritten_by_the_wizard(monkeypatch):
    """`setup --llm-auth-token X` on a TTY opened a wizard that cannot ask for
    a bearer token, and then wrote its own answers over the flag.

    The TTY is faked deliberately: under pytest ``isatty()`` is False, so
    without this the assertion passes for the wrong reason on any code at all.
    """
    import sys as _sys

    from tesserae.cli import _build_setup_parser, _setup_wants_interactive

    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True, raising=False)

    bare = _build_setup_parser().parse_args([])
    bare._interactive_default = True
    assert _setup_wants_interactive(bare) is True, "the fake TTY must reach the wizard"

    for flag, value in (
        ("--llm-auth-token", "tok"),
        ("--llm-api-style", "openai"),
        ("--llm-base-url", "https://gw.example"),
    ):
        args = _build_setup_parser().parse_args([flag, value])
        args._interactive_default = True
        assert _setup_wants_interactive(args) is False, f"{flag} must skip the wizard"
