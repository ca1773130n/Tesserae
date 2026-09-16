"""Commands that answered a mistake with a stack trace.

Seven of them, from the CLI audit. Each takes something a user typed — a path,
a date, a config value — and each turned a perfectly ordinary mistake into an
unhandled exception. A traceback tells the reader that Tesserae broke; the
mistake was theirs, and the answer is a sentence naming it.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tesserae.cli import main
from tesserae.project import ProjectWiki


def _project(tmp_path: Path) -> ProjectWiki:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text("# Alpha\n\nRetrieval grounds answers.\n", encoding="utf-8")
    wiki = ProjectWiki.init(tmp_path, name="inputs")
    wiki.compile()
    return wiki


def test_sessions_import_of_a_non_json_file(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("hello, not json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["sessions", "import", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert "Traceback" not in err


def test_sessions_import_of_json_that_is_not_a_session(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    bad = tmp_path / "list.json"
    bad.write_text(json.dumps(["a string", "another"]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["sessions", "import", str(bad)]) == 2
    assert "not a session object" in capsys.readouterr().err


def test_chunk_backfill_with_an_unparseable_since(tmp_path, monkeypatch, capsys):
    """The code already composed the right message; nothing caught it."""
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["sessions", "chunk-backfill", "--since", "not-a-date"]) == 2
    err = capsys.readouterr().err
    assert "could not parse" in err and "YYYY-MM-DD" in err
    assert "Traceback" not in err


def test_context_output_pointed_at_a_directory(tmp_path, monkeypatch, capsys):
    """`--output` names a file. A directory raised IsADirectoryError — after
    the bundle had been compiled, so the work was done and thrown away."""
    _project(tmp_path)
    target = tmp_path / "adir"
    target.mkdir()
    monkeypatch.chdir(tmp_path)

    assert main(["context", "alpha", "--output", str(target)]) == 2
    err = capsys.readouterr().err
    assert "must be a file" in err
    assert "Traceback" not in err


def test_vault_set_root_pointed_at_a_file(tmp_path, monkeypatch, capsys):
    """A vault root is a directory by definition; mkdir raised FileExistsError."""
    _project(tmp_path)
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["vault", "set-root", str(afile)]) == 2
    err = capsys.readouterr().err
    assert "must be a directory" in err
    assert "Traceback" not in err


def test_an_invalid_provider_in_a_project_config_is_diagnosed_not_fatal(tmp_path, monkeypatch, capsys):
    """`config status` exists to diagnose a bad backend, and died of one.

    The resolve runs BEFORE the liveness try/except, and a project config is
    the path that reaches it — the global-config case was already handled.
    """
    wiki = _project(tmp_path)
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["llm_provider"] = "not-a-real-provider"
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["config", "status"]) == 1
    out = capsys.readouterr().out
    assert "not-a-real-provider" in out
    assert "config llm --llm-provider" in out, "say how to fix it"


def test_agents_tree_survives_an_unreadable_artifact(tmp_path, monkeypatch, capsys):
    """One half-written artifact took the whole tree down — after the header
    had printed, so the operator saw a partial tree and a traceback."""
    from tesserae.agent_distill import agent_artifact_path

    _project(tmp_path)
    registry = tmp_path / ".tesserae" / "agents" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({"version": 1, "agents": {"claude-code:x:default": {"label": "X"}}}, indent=2),
        encoding="utf-8",
    )
    artifact = agent_artifact_path(tmp_path, "claude-code:x:default")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["agents", "tree"]) == 0
    out = capsys.readouterr().out
    assert "unreadable artifact" in out
    assert "claude-code:x:default" in out


def test_a_compile_survives_one_unreadable_document(tmp_path, monkeypatch, capsys):
    """A corpus is thousands of files and any one can be momentarily unreadable.

    A single PermissionError aborted the whole compile with a traceback.
    """
    if os.geteuid() == 0:
        pytest.skip("running as root: a chmod 000 file is still readable")
    wiki = _project(tmp_path)
    blocked = tmp_path / "docs" / "blocked.md"
    blocked.write_text("# Blocked\n\nsecret\n", encoding="utf-8")
    blocked.chmod(0)
    try:
        assert main(["compile", "--project", str(tmp_path), "--extractor", "deterministic"]) == 0
    finally:
        blocked.chmod(stat.S_IRUSR | stat.S_IWUSR)
    err = capsys.readouterr().err
    assert "skipping unreadable file" in err
    assert "blocked.md" in err
    assert wiki.paths.graph.is_file()
