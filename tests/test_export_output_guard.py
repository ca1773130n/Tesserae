"""`export site` must not lie about where it wrote, or delete what it was not asked to.

Two defects, both reachable from one command:

* the atomic swap added in v0.40.0 made `build_site` report the STAGING
  directory, which the swap then deletes — so the path printed to the user was
  gone before they could read it;
* `write_site` opens by rmtree-ing its output directory, and `--output` is an
  arbitrary user path, so one mistyped argument destroyed a folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.cli import main
from tesserae.project import ProjectWiki
from tesserae.output_guard import OutputRefused
from tesserae.site import StaticSiteBuilder
from tesserae.research_graph import ResearchGraph


def _project(tmp_path: Path) -> ProjectWiki:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text("# Alpha\n\nRetrieval grounds answers.\n", encoding="utf-8")
    wiki = ProjectWiki.init(tmp_path, name="guard")
    wiki.compile()
    return wiki


# ------------------------------------------------- the path it reports exists


def test_build_site_reports_the_site_not_the_staging_dir(tmp_path):
    wiki = _project(tmp_path)
    result = wiki.build_site()
    assert result["site_path"] == str(wiki.paths.site)
    assert Path(result["site_path"]).is_dir(), "reported a path that does not exist"
    assert ".site.staging" not in result["site_path"]


def test_the_printed_path_is_one_the_user_can_open(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["export", "site"]) == 0
    printed = capsys.readouterr().out.split("path=")[-1].strip()
    assert Path(printed).is_dir(), f"`export site` printed a dead path: {printed}"


def test_an_explicit_output_still_reports_itself(tmp_path):
    wiki = _project(tmp_path)
    out = tmp_path / "exported"
    assert wiki.build_site(output=out)["site_path"] == str(out)


# ------------------------------------------------------ the destructive wipe


def test_writing_into_a_directory_of_someone_elses_files_is_refused(tmp_path):
    wiki = _project(tmp_path)
    mine = tmp_path / "mydocs"
    (mine / "sub").mkdir(parents=True)
    (mine / "notes.txt").write_text("important", encoding="utf-8")
    (mine / "sub" / "more.txt").write_text("also important", encoding="utf-8")

    with pytest.raises(OutputRefused):
        wiki.build_site(output=mine)

    assert (mine / "notes.txt").is_file(), "refusal did not happen before the delete"
    assert (mine / "sub" / "more.txt").is_file()


def test_the_cli_turns_that_refusal_into_one_line_and_exit_2(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    mine = tmp_path / "mydocs"
    mine.mkdir()
    (mine / "notes.txt").write_text("important", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["export", "site", "--output", str(mine)]) == 2
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err
    assert "--overwrite" in err, "the message must name the flag that actually exists"
    assert "Traceback" not in err
    assert (mine / "notes.txt").is_file()


def test_overwrite_is_the_way_through(tmp_path, monkeypatch):
    _project(tmp_path)
    mine = tmp_path / "mydocs"
    mine.mkdir()
    (mine / "notes.txt").write_text("important", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["export", "site", "--output", str(mine), "--overwrite"]) == 0
    assert not (mine / "notes.txt").exists(), "--overwrite must actually overwrite"
    assert (mine / "index.html").is_file()


def test_re_exporting_over_a_previous_site_needs_no_flag(tmp_path):
    """The common case must stay silent, or the guard is just an obstacle."""
    wiki = _project(tmp_path)
    out = tmp_path / "dist"
    wiki.build_site(output=out)
    assert (out / "index.html").is_file()
    wiki.build_site(output=out)  # must not raise
    assert (out / "index.html").is_file()


def test_an_empty_directory_is_not_somebody_elses_data(tmp_path):
    wiki = _project(tmp_path)
    out = tmp_path / "empty"
    out.mkdir()
    wiki.build_site(output=out)  # must not raise
    assert (out / "index.html").is_file()


def test_the_builder_guard_is_independent_of_the_cli(tmp_path):
    """`write_site` is a library entry point; the guard lives there, not in argparse."""
    mine = tmp_path / "data"
    mine.mkdir()
    (mine / "thesis.txt").write_text("years of work", encoding="utf-8")
    builder = StaticSiteBuilder(site_title="T")
    with pytest.raises(OutputRefused):
        builder.write_site(ResearchGraph(), None, mine)
    assert (mine / "thesis.txt").is_file()
    builder.write_site(ResearchGraph(), None, mine, force=True)
    assert not (mine / "thesis.txt").exists()


# ------------------------------------------------------------- flag collision


def test_export_site_has_exactly_one_force_flag(tmp_path, capsys):
    """`--force` on this parser means "deploy a dirty tree".

    A second `--force` for the output guard raised ArgumentError at parser
    construction and took the whole command down, which is how this test
    exists. The two meanings stay on two flags.
    """
    from tesserae.cli import _build_export_parser

    parser = _build_export_parser()
    site = [a for a in parser._subparsers._group_actions[0].choices["site"]._actions]
    force = [a for a in site if "--force" in a.option_strings]
    assert len(force) == 1
    assert "dirty" in (force[0].help or "").lower()
    assert any("--overwrite" in a.option_strings for a in site)


# ------------------------------------------------------------------- kuzu


def _kuzu_or_skip():
    try:
        import kuzu  # noqa: F401
    except ImportError:
        pytest.skip("kuzu not installed")


def test_export_kuzu_refuses_a_directory_of_someone_elses_files(tmp_path, monkeypatch, capsys):
    """Same defect, second exporter: `write_graph` rmtree'd its --output.

    This version of Kuzu writes a single FILE, so a directory at the output
    path is never a database this wrote — it is whatever the user pointed at.
    """
    _kuzu_or_skip()
    _project(tmp_path)
    mine = tmp_path / "precious"
    (mine / "sub").mkdir(parents=True)
    (mine / "thesis.txt").write_text("years of work", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["export", "kuzu", "--output", str(mine)]) == 2
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err and "--overwrite" in err
    assert "Traceback" not in err
    assert (mine / "thesis.txt").is_file()


def test_export_kuzu_refuses_a_plain_file_too(tmp_path, monkeypatch, capsys):
    """The unlink branch would take an ordinary file without looking."""
    _kuzu_or_skip()
    _project(tmp_path)
    notes = tmp_path / "notes.kuzu"  # right suffix, wrong content
    notes.write_text("not a database", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["export", "kuzu", "--output", str(notes)]) == 2
    assert notes.read_text(encoding="utf-8") == "not a database"


def test_re_exporting_over_a_real_kuzu_database_needs_no_flag(tmp_path, monkeypatch):
    _kuzu_or_skip()
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "graph.kuzu"
    assert main(["export", "kuzu", "--output", str(out)]) == 0
    assert out.exists()
    assert main(["export", "kuzu", "--output", str(out)]) == 0  # silent re-export


def test_kuzu_overwrite_is_the_way_through(tmp_path, monkeypatch):
    _kuzu_or_skip()
    _project(tmp_path)
    mine = tmp_path / "precious"
    mine.mkdir()
    (mine / "thesis.txt").write_text("years of work", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["export", "kuzu", "--output", str(mine), "--overwrite"]) == 0
    assert not (mine / "thesis.txt").exists()


def test_a_kuzu_database_is_recognised_by_magic_not_suffix(tmp_path):
    _kuzu_or_skip()
    from tesserae.kuzu_adapter import _looks_like_a_kuzu_database

    real = tmp_path / "nosuffix"
    real.write_bytes(b"KUZU" + b"\x00" * 32)
    assert _looks_like_a_kuzu_database(real) is True

    fake = tmp_path / "impostor.kuzu"
    fake.write_text("plain text", encoding="utf-8")
    assert _looks_like_a_kuzu_database(fake) is False


# ------------------------------------------------------------ shared guard


def test_the_guard_lets_through_what_it_should(tmp_path):
    from tesserae.output_guard import guard_output

    missing = tmp_path / "not-there"
    guard_output(missing, is_ours=lambda p: False, kind="x")  # absent: fine

    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(OutputRefused):
        guard_output(taken, is_ours=lambda p: False, kind="x")
    guard_output(taken, is_ours=lambda p: False, kind="x", force=True)  # force: fine
    guard_output(taken, is_ours=lambda p: True, kind="x")  # recognised: fine


def test_an_unreadable_output_is_refused_not_crashed(tmp_path):
    """`is_ours` raising OSError must mean "not recognisable", not a traceback."""
    from tesserae.output_guard import guard_output

    target = tmp_path / "d"
    target.mkdir()
    (target / "f").write_text("x", encoding="utf-8")

    def explodes(_path):
        raise OSError("permission denied")

    with pytest.raises(OutputRefused):
        guard_output(target, is_ours=explodes, kind="x")


# -------------------------------------------------------------------- okf


def test_export_okf_refuses_a_notes_directory(tmp_path, monkeypatch, capsys):
    """The widest blast radius of the three: this sweep is RECURSIVE.

    `write_okf_bundle` deletes every *.md under --output, at any depth, so a
    notes folder lost documents from subdirectories the bundle never touches.
    """
    _project(tmp_path)
    notes = tmp_path / "notes"
    (notes / "sub").mkdir(parents=True)
    (notes / "thesis.md").write_text("years of work", encoding="utf-8")
    (notes / "sub" / "deep.md").write_text("nested and still mine", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["export", "okf", "--output", str(notes)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert (notes / "thesis.md").is_file()
    assert (notes / "sub" / "deep.md").is_file(), "the recursive sweep reached a subdirectory"


def test_export_okf_re_export_is_silent_and_overwrite_works(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "bundle"
    assert main(["export", "okf", "--output", str(out)]) == 0
    assert (out / "index.md").is_file()
    assert main(["export", "okf", "--output", str(out)]) == 0  # no flag needed

    notes = tmp_path / "notes2"
    notes.mkdir()
    (notes / "keep.md").write_text("x", encoding="utf-8")
    assert main(["export", "okf", "--output", str(notes), "--overwrite"]) == 0
    assert not (notes / "keep.md").exists()


def test_an_okf_bundle_is_recognised_by_the_files_it_really_writes(tmp_path):
    from tesserae.okf import OKF_MARKERS, _looks_like_an_okf_bundle

    assert set(OKF_MARKERS) == {"index.md", "log.md"}
    d = tmp_path / "b"
    d.mkdir()
    assert _looks_like_an_okf_bundle(d) is True  # empty
    (d / "random.md").write_text("x", encoding="utf-8")
    assert _looks_like_an_okf_bundle(d) is False  # a stray markdown file is not a bundle
    (d / "index.md").write_text("x", encoding="utf-8")
    assert _looks_like_an_okf_bundle(d) is True
