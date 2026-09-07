"""`tesserae test` — does the configured LLM backend actually answer?

Every test here drives the real ``run_llm_selftest`` and fakes only the CLIENT,
because the point of the command is that it builds the client the same way
compile does. Faking the resolver instead would test nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tesserae.llm_json as lj
import tesserae.llm_selftest as st


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No global config, no ambient env: the resolver must see only the test's."""
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", tmp_path / "no-global.json")
    for var in (
        "TESSERAE_LLM_PROVIDER", "TESSERAE_LLM_MODEL", "TESSERAE_CLAUDE_CONFIG_DIRS",
        "TESSERAE_CODEX_HOMES", "TESSERAE_LLM_BASE_URL", "TESSERAE_LLM_API_KEY",
        "TESSERAE_LLM_AUTH_TOKEN", "TESSERAE_LLM_API_STYLE",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR", "CODEX_HOME",
    ):
        monkeypatch.delenv(var, raising=False)


class _Client:
    """Answers both round trips; records what it was asked."""

    identity = "acct-a"

    def __init__(self, json_payload=None, text="backend ok", json_exc=None, text_exc=None):
        self._json = {"ok": True} if json_payload is None else json_payload
        self._text = text
        self._json_exc = json_exc
        self._text_exc = text_exc
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema_name, cache_key="unset", **kw):
        self.calls.append({"kind": "json", "system": system, "cache_key": cache_key})
        if self._json_exc:
            raise self._json_exc
        return self._json

    def complete_text(self, *, system, user, **kw):
        self.calls.append({"kind": "prose", "system": system})
        if self._text_exc:
            raise self._text_exc
        return self._text


def _install(monkeypatch, client, capture=None):
    def _build(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return client

    monkeypatch.setattr(lj, "build_default_json_client", _build)
    return client


def _project(tmp_path: Path, cfg: dict) -> Path:
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tesserae" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


def test_all_four_steps_pass_and_report_ok(monkeypatch, tmp_path):
    client = _install(monkeypatch, _Client())
    root = _project(tmp_path, {"llm_provider": "claude", "llm_claude_config_dirs": ["/acct/a"]})

    result = st.run_llm_selftest(root)

    assert result["ok"] is True
    assert [s["step"] for s in result["steps"]] == ["resolve", "build", "json", "prose"]
    assert all(s["ok"] for s in result["steps"])
    assert result["provider"] == "claude"
    assert result["settings"]["claude_config_dirs"] == ["/acct/a"]
    assert result["settings"]["provider_source"] == "project .tesserae/config.json"
    # Both round trips really happened — a self-test that skips the call is a lie.
    assert [c["kind"] for c in client.calls] == ["json", "prose"]


def test_the_json_probe_is_never_cached(monkeypatch, tmp_path):
    """A cached yes to "is the backend answering right now" is a wrong yes."""
    client = _install(monkeypatch, _Client())
    st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}))
    assert client.calls[0]["cache_key"] is None


def test_the_client_is_built_from_the_whole_resolved_settings(monkeypatch, tmp_path):
    """Rebuilding from a hand-picked subset is how a probe vouches for a
    different client than the one compile would use."""
    seen: dict = {}
    _install(monkeypatch, _Client(), capture=seen)
    root = _project(tmp_path, {
        "llm_provider": "claude",
        "llm_claude_config_dirs": ["/acct/a", "/acct/b"],
        "llm_base_url": "https://gw.example",
        "llm_auth_token": "tok-secret",
    })

    st.run_llm_selftest(root)

    assert seen["settings"]["base_url"] == "https://gw.example"
    assert seen["settings"]["auth_token"] == "tok-secret"
    assert seen["claude_config_dirs"] == ["/acct/a", "/acct/b"]


def test_provider_override_reaches_the_builder_without_touching_config(monkeypatch, tmp_path):
    seen: dict = {}
    _install(monkeypatch, _Client(), capture=seen)
    root = _project(tmp_path, {"llm_provider": "claude"})

    result = st.run_llm_selftest(root, provider="codex")

    assert seen["provider"] == "codex"
    assert result["provider"] == "codex"
    # Nothing is written: the override is for this run only.
    assert json.loads((root / ".tesserae" / "config.json").read_text())["llm_provider"] == "claude"


def test_no_prose_makes_exactly_one_call(monkeypatch, tmp_path):
    client = _install(monkeypatch, _Client())
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}), prose=False)
    assert [s["step"] for s in result["steps"]] == ["resolve", "build", "json"]
    assert [c["kind"] for c in client.calls] == ["json"]
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# the failures this command exists to surface
# ---------------------------------------------------------------------------


def test_no_client_is_a_failure_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: None)
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}))
    assert result["ok"] is False
    build = [s for s in result["steps"] if s["step"] == "build"][0]
    assert build["ok"] is False
    assert "no backend could be built" in build["detail"]
    assert not [s for s in result["steps"] if s["step"] in ("json", "prose")]


def test_a_backend_that_raises_reports_its_own_error(monkeypatch, tmp_path):
    _install(monkeypatch, _Client(json_exc=RuntimeError("claude exited 1: Not logged in")))
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}))
    assert result["ok"] is False
    step = [s for s in result["steps"] if s["step"] == "json"][0]
    assert "Not logged in" in step["detail"]
    assert "RuntimeError" in step["detail"]


def test_a_backend_that_answers_json_but_not_prose_fails(monkeypatch, tmp_path):
    """`ask` goes through complete_text; a gateway can serve one and not the other."""
    _install(monkeypatch, _Client(text=""))
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}))
    assert result["ok"] is False
    assert [s for s in result["steps"] if s["step"] == "json"][0]["ok"] is True
    prose = [s for s in result["steps"] if s["step"] == "prose"][0]
    assert prose["ok"] is False
    assert "empty prose" in prose["detail"]


def test_unusable_json_shape_is_a_failure(monkeypatch, tmp_path):
    _install(monkeypatch, _Client(json_payload=None if False else {}))
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "claude"}))
    assert result["ok"] is False
    assert "no usable JSON" in [s for s in result["steps"] if s["step"] == "json"][0]["detail"]


def test_a_bad_provider_name_is_reported_not_raised(monkeypatch, tmp_path):
    result = st.run_llm_selftest(_project(tmp_path, {"llm_provider": "clod"}))
    assert result["ok"] is False
    assert result["steps"][0]["step"] == "resolve"
    assert "clod" in result["steps"][0]["detail"]


def test_an_unreadable_project_config_still_tests_the_global_backend(monkeypatch, tmp_path):
    """A corrupt project config is doctor's finding, not a reason to refuse."""
    _install(monkeypatch, _Client())
    (tmp_path / ".tesserae").mkdir()
    (tmp_path / ".tesserae" / "config.json").write_text("{not json", encoding="utf-8")
    result = st.run_llm_selftest(tmp_path)
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# rendering + the CLI verb
# ---------------------------------------------------------------------------


def test_render_never_echoes_the_credential(monkeypatch, tmp_path):
    _install(monkeypatch, _Client())
    root = _project(tmp_path, {
        "llm_provider": "claude",
        "llm_base_url": "https://gw.example",
        "llm_auth_token": "tok-secret",
    })
    text = st.render_selftest(st.run_llm_selftest(root))
    assert "tok-secret" not in text
    assert "auth_token (Authorization: Bearer)" in text
    assert "https://gw.example" in text


def test_cli_exits_zero_when_the_backend_answers(monkeypatch, tmp_path, capsys):
    import tesserae.cli as cli

    _install(monkeypatch, _Client())
    root = _project(tmp_path, {"llm_provider": "claude"})
    rc = cli.main(["test", "--project", str(root)])
    assert rc == 0
    assert "Backend is answering" in capsys.readouterr().out


def test_cli_exits_two_when_it_does_not(monkeypatch, tmp_path, capsys):
    import tesserae.cli as cli

    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: None)
    root = _project(tmp_path, {"llm_provider": "claude"})
    rc = cli.main(["test", "--project", str(root)])
    assert rc == 2
    assert "NOT usable" in capsys.readouterr().out


def test_cli_json_output_is_machine_readable(monkeypatch, tmp_path, capsys):
    import tesserae.cli as cli

    _install(monkeypatch, _Client())
    root = _project(tmp_path, {"llm_provider": "claude"})
    rc = cli.main(["test", "--project", str(root), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["settings"]["provider"] == "claude"


def test_the_prose_prompt_is_a_registered_self_capture_signature():
    """Every system prompt Tesserae issues must be filterable out of the session
    store, or the engine files its own self-test as the user's work."""
    from tesserae.harness_sessions import _TESSERAE_PROMPT_SIGNATURES

    assert any(sig in st.PROSE_SYSTEM for sig in _TESSERAE_PROMPT_SIGNATURES)
    assert any(sig in st.JSON_SYSTEM for sig in _TESSERAE_PROMPT_SIGNATURES)
