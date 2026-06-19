"""memex transcript-search wrapper: degrades gracefully, never raises."""

from __future__ import annotations

import subprocess

from tesserae import memex_search


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_missing_binary_is_reported(monkeypatch):
    monkeypatch.setattr(memex_search, "memex_path", lambda: None)
    r = memex_search.search_transcripts("anything")
    assert r["available"] is False and r["results"] == []
    assert "not installed" in r["error"]


def test_empty_query_short_circuits(monkeypatch):
    monkeypatch.setattr(memex_search, "memex_path", lambda: "/bin/memex")
    r = memex_search.search_transcripts("   ")
    assert r == {"available": True, "results": [], "total": 0}


def test_success_parses_json_array(monkeypatch):
    monkeypatch.setattr(memex_search, "memex_path", lambda: "/bin/memex")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Proc(stdout='[{"project":"P","snippet":"hit"}]'))
    r = memex_search.search_transcripts("q", limit=5)
    assert r["available"] and r["total"] == 1 and r["results"][0]["project"] == "P"


def test_no_index_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(memex_search, "memex_path", lambda: "/bin/memex")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Proc(returncode=1, stderr="index does not exist"))
    r = memex_search.search_transcripts("q")
    assert r["available"] and r["results"] == [] and "memex index" in r["error"]


def test_timeout_and_oserror_degrade(monkeypatch):
    monkeypatch.setattr(memex_search, "memex_path", lambda: "/bin/memex")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="memex", timeout=20)
    monkeypatch.setattr(subprocess, "run", boom)
    assert "timed out" in memex_search.search_transcripts("q")["error"]

    def oserr(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(subprocess, "run", oserr)
    assert "could not run" in memex_search.search_transcripts("q")["error"]
