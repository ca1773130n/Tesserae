"""Cognee search-type resolution (Cognee 1.0 dropped the V1 INSIGHTS type)
and output suppression (explicit cognee use must never spew logs into CLI output)."""

from __future__ import annotations

import logging
import sys
import types

import pytest


def test_insights_aliases_to_graph_completion_and_passthrough():
    cognee = pytest.importorskip("cognee")
    from tesserae.cognee_query import _search_type

    # Cognee 1.0 removed INSIGHTS -> Tesserae's historical default must still resolve.
    assert _search_type("INSIGHTS") == cognee.SearchType.GRAPH_COMPLETION
    assert _search_type("insights") == cognee.SearchType.GRAPH_COMPLETION   # case-insensitive
    assert _search_type(None) == cognee.SearchType.GRAPH_COMPLETION         # default
    assert _search_type("CHUNKS") == cognee.SearchType.CHUNKS               # valid type passes through


def test_unknown_search_type_still_raises_clearly():
    pytest.importorskip("cognee")
    from tesserae.cognee_query import _search_type

    with pytest.raises(ValueError, match="Unknown Cognee search type"):
        _search_type("DEFINITELY_NOT_A_TYPE")


def _spew_everywhere() -> None:
    """Emit noise through every channel cognee is known to abuse."""
    print("cognee stdout banner")
    sys.stderr.write("cognee stderr banner\n")
    logging.getLogger("cognee.noise").warning("stdlib logging leak")
    try:
        import structlog

        structlog.get_logger("cognee.noise").warning("structlog leak")
    except ModuleNotFoundError:
        pass


def _install_fake_cognee(monkeypatch, *, fail: bool = False):
    """A stand-in `cognee` module that behaves like the real one's worst habits:
    chatty on import-adjacent calls, chatty during search, optionally failing."""
    mod = types.ModuleType("cognee")

    class _SearchType:
        GRAPH_COMPLETION = "GRAPH_COMPLETION"
        CHUNKS = "CHUNKS"

    mod.SearchType = _SearchType

    async def search(*args, **kwargs):
        _spew_everywhere()
        if fail:
            raise RuntimeError("dataset not found")
        return ["graph answer"]

    mod.search = search
    monkeypatch.setitem(sys.modules, "cognee", mod)
    return mod


def test_search_cognee_suppresses_backend_chatter(monkeypatch, capsys):
    _install_fake_cognee(monkeypatch)
    from tesserae.cognee_query import search_cognee

    capsys.readouterr()  # drain anything earlier tests left behind
    results = search_cognee("what renders mermaid?", dataset="demo_memory")

    assert results == ["graph answer"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_explicit_cognee_failure_is_clean_one_line_error(monkeypatch, capsys, tmp_path):
    """Regression (cognee demotion): cognee importable + explicitly requested +
    backend failure must surface as ONE clean RuntimeError — no structlog or
    stdout/stderr leakage in the captured output."""
    _install_fake_cognee(monkeypatch, fail=True)
    from tesserae.project import ProjectWiki
    from tesserae.query import ask_project

    wiki = ProjectWiki.init(tmp_path, name="demo", sources=[])
    capsys.readouterr()  # drain init output; we only judge the ask itself

    with pytest.raises(RuntimeError) as excinfo:
        ask_project(wiki, "what renders mermaid?", backend="cognee")

    message = str(excinfo.value)
    assert message == "cognee ask failed: dataset not found"
    assert "\n" not in message
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
