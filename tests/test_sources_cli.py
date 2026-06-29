"""`tesserae sources add|list|remove` — manage a project's compile scope, local & global."""

from __future__ import annotations

import json

from tesserae.cli import main
from tesserae.project import ProjectWiki


def _sources(proj):
    return json.loads((proj / ".tesserae" / "config.json").read_text(encoding="utf-8"))["sources"]


def test_sources_add_local_is_stored_relative(tmp_path):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "notes").mkdir()
    ProjectWiki.init(proj, sources=["docs"])

    assert main(["sources", "add", "notes", "--project", str(proj)]) == 0
    assert _sources(proj) == ["docs", "notes"]  # inside the project -> relative (local)


def test_sources_add_global_is_stored_absolute_and_dedupes(tmp_path):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ext = tmp_path / "external"
    ext.mkdir()
    ProjectWiki.init(proj, sources=["docs"])

    # absolute path outside the project -> global (stored absolute)
    assert main(["sources", "add", str(ext), "--project", str(proj)]) == 0
    assert str(ext.resolve()) in _sources(proj)

    # a relative path that ESCAPES the root resolves to the same place -> deduped
    assert main(["sources", "add", "../external", "--project", str(proj)]) == 0
    assert _sources(proj).count(str(ext.resolve())) == 1


def test_sources_dedupe_and_remove(tmp_path):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "notes").mkdir()
    ProjectWiki.init(proj, sources=["docs"])

    main(["sources", "add", "notes", "--project", str(proj)])
    main(["sources", "add", "notes", "--project", str(proj)])  # dedupe
    assert _sources(proj) == ["docs", "notes"]

    assert main(["sources", "remove", "notes", "--project", str(proj)]) == 0
    assert _sources(proj) == ["docs"]
    assert main(["sources", "remove", "nope", "--project", str(proj)]) == 1  # not a source


def test_sources_list_marks_local_and_global(tmp_path, capsys):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ext = tmp_path / "ext"
    ext.mkdir()
    ProjectWiki.init(proj, sources=["docs"])
    main(["sources", "add", str(ext), "--project", str(proj)])

    capsys.readouterr()
    assert main(["sources", "list", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "local" in out and "docs" in out
    assert "global" in out and str(ext.resolve()) in out
