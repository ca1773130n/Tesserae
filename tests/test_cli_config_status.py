"""`tesserae config status` — resolved LLM backend view + liveness ping.

The whole point is making a dead backend visible: a rate-limited / mis-authed
codex account silently makes extraction produce zero findings, so `status`
pings the backend and reports OK/FAILED with a non-zero exit on failure.
Hermetic: the resolver, global-config loader, and client builder are stubbed —
no real LLM call, no dependence on the machine's ~/.tesserae.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import tesserae.llm_json as lj
from tesserae.cli import _handle_config_status


class _OkClient:
    def complete_json(self, **kwargs):
        return {"ok": True}


class _DeadClient:
    def complete_json(self, **kwargs):
        return None  # rate-limit / auth / wrong model


@pytest.fixture
def _stub_resolution(monkeypatch):
    monkeypatch.setattr(lj, "resolve_llm_client_settings",
                        lambda cfg=None: {"provider": "codex", "codex_home": None, "claude_config_dirs": []})
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})


def test_status_reports_resolved_backend_and_live_ok(_stub_resolution, monkeypatch, capsys):
    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: _OkClient())
    rc = _handle_config_status(SimpleNamespace(project=None, ping=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "provider   : codex" in out
    assert "codex_home : ~/.codex (OS default)" in out
    assert "liveness   : ✓ OK" in out


def test_status_flags_dead_backend_nonzero(_stub_resolution, monkeypatch, capsys):
    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: _DeadClient())
    rc = _handle_config_status(SimpleNamespace(project=None, ping=True))
    out = capsys.readouterr().out
    assert rc == 1
    assert "liveness   : ✗ FAILED" in out
    assert "zero findings" in out


def test_status_no_ping_skips_live_call(_stub_resolution, monkeypatch, capsys):
    called = {"built": False}

    def _builder(**kw):
        called["built"] = True
        return _OkClient()

    monkeypatch.setattr(lj, "build_default_json_client", _builder)
    rc = _handle_config_status(SimpleNamespace(project=None, ping=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "liveness" not in out
    assert called["built"] is False
