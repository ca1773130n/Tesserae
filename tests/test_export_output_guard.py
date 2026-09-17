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
import re
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


# -------------------------------------------- compile / code ingest / query


def test_compile_over_paths_with_no_markdown_refuses_before_writing(tmp_path, monkeypatch, capsys):
    """`compile README.txt` rebuilt graph.json from nothing and exited 0."""
    wiki = _project(tmp_path)
    (tmp_path / "README.txt").write_text("plain text, not markdown", encoding="utf-8")
    before = wiki.paths.graph.read_bytes()
    monkeypatch.chdir(tmp_path)

    assert main(["compile", "README.txt", "--extractor", "deterministic"]) == 2
    err = capsys.readouterr().err
    assert "nothing to compile" in err
    assert "Traceback" not in err
    assert wiki.paths.graph.read_bytes() == before, "the graph was touched anyway"


def test_compile_resolves_its_paths_against_the_project_not_the_cwd(tmp_path, monkeypatch):
    """`compile --project X note.md` names a file inside X."""
    _project(tmp_path)
    (tmp_path / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["compile", "--project", str(tmp_path), "note.md",
                 "--extractor", "deterministic"]) == 0


def test_compile_over_a_missing_path_says_so(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["compile", "nope.md", "--extractor", "deterministic"]) == 2
    assert "no such path" in capsys.readouterr().err


def test_query_on_something_that_is_not_a_project_is_an_error(tmp_path, capsys):
    """A typo'd --project answered "No matches" with exit 0."""
    not_a_project = tmp_path / "elsewhere"
    not_a_project.mkdir()
    assert main(["query", "anything", "--project", str(not_a_project)]) == 2
    assert "No Tesserae project" in capsys.readouterr().err


def test_code_ingest_refuses_a_missing_path_instead_of_emptying_the_graph(tmp_path, monkeypatch, capsys):
    """It exited 0 and wrote an empty code-graph.json over a real one."""
    _project(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["code", "ingest", "src"]) == 0
    graph = tmp_path / ".tesserae" / "code-graph.json"
    before = graph.read_bytes()

    assert main(["code", "ingest", "srcc"]) == 2
    assert "no such path" in capsys.readouterr().err
    assert graph.read_bytes() == before, "the code graph was emptied anyway"


def test_lint_reports_an_unreadable_graph_instead_of_calling_it_clean(tmp_path, monkeypatch, capsys):
    """It swallowed the JSONDecodeError and printed "Wiki is clean." with exit 0."""
    wiki = _project(tmp_path)
    wiki.paths.graph.write_text("{ not json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["lint"]) != 0
    out = capsys.readouterr().out + capsys.readouterr().err
    report = json.loads((tmp_path / ".tesserae" / "lint-report.json").read_text())
    codes = {f["code"] for f in report["findings"]}
    assert "GRAPH_UNREADABLE" in codes


def test_an_unparseable_global_config_is_not_overwritten(tmp_path, monkeypatch, capsys):
    """One stray comma used to cost the stored clip token and every API key."""
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    cfg = home / ".tesserae" / "config.json"
    cfg.write_text('{"clip_token": "SECRET", "llm_api_key": "KEY",\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    import tesserae.llm_json as lj

    # `cli` imports the module as `_lj`, so patching the attribute on the module
    # object reaches both names — there is no `tesserae.cli._lj` import path.
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", cfg)

    assert main(["config", "clip-token", "--generate"]) == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err and "Traceback" not in err
    assert "SECRET" in cfg.read_text(encoding="utf-8")


# ------------------------------------------------ registry / sessions scope


def test_register_does_not_reinitialise_an_uncompiled_project(tmp_path, monkeypatch, capsys):
    """The convenience branch tested for graph.json and re-ran init over a real
    project, replacing config.json and losing sources, name and provider.

    The comment above it already promised "an already-initialized project is
    left untouched (no config overwrite)". The condition just did not match.
    """
    project = tmp_path / "proj"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="myname")
    cfg_path = project / ".tesserae" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["sources"] = ["docs"]
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    (project / ".tesserae" / "graph.json").unlink()  # initialised, never compiled

    monkeypatch.setenv("TESSERAE_REGISTRY", str(tmp_path / "registry.json"))
    assert main(["projects", "register", str(project)]) == 1
    err = capsys.readouterr().err
    assert "no compiled graph yet" in err

    after = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert after.get("name") == "myname"
    assert after.get("sources") == ["docs"]


def test_register_still_initialises_a_plain_directory(tmp_path, monkeypatch, capsys):
    """The convenience the guard must not remove."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("TESSERAE_REGISTRY", str(tmp_path / "registry.json"))
    assert main(["projects", "register", str(fresh)]) == 0
    assert (fresh / ".tesserae" / "config.json").is_file()


def test_discover_does_not_prune_records_from_a_root_it_never_scanned(tmp_path, monkeypatch, capsys):
    """`--import` deleted records harvested from a --root that is not on disk.

    Discovery skipped the root entirely and learned nothing about it; absence of
    a directory is not evidence its sessions are gone. An unmounted disk was
    enough to lose the records.
    """
    project = tmp_path / "proj"
    project.mkdir()
    ProjectWiki.init(project, name="p")
    monkeypatch.chdir(project)

    assert main(["sessions", "discover", "--root", str(tmp_path / "absent")]) == 0
    out = capsys.readouterr().out
    assert "not scanned (missing)" in out
    assert "kept, not pruned" in out


def test_vault_sync_persists_the_override_it_says_it_applied(tmp_path):
    """`vault sync` reported "applied: N override(s)" and wrote only the vault.

    The overlay was applied to the in-memory graph, re-projected, and dropped:
    graph.json still held the old value, so the next run — which re-projects
    from disk — put the user's edit back the way it was. Reporting an edit as
    applied and then reverting it is worse than refusing it.

    The vault is pinned INSIDE tmp_path: `effective_obsidian_vault()` otherwise
    resolves to the machine's real Obsidian vault, and a test must not write
    there.
    """
    wiki = _project(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    wiki.set_vault_override(vault)
    wiki.export_obsidian()
    wiki.reproject_after_vault_change()  # settle the snapshot baseline
    before = wiki.paths.graph.read_text(encoding="utf-8")

    pages = [p for p in vault.rglob("*.md") if "node_id:" in p.read_text(encoding="utf-8")]
    assert pages, "fixture produced no node-bearing vault pages"
    page = sorted(pages)[0]
    body = page.read_text(encoding="utf-8")
    assert "\ntitle: " in body, f"no title field to override in {page.name}"
    page.write_text(
        re.sub(r"\ntitle: .*", "\ntitle: EDITED BY HAND", body, count=1), encoding="utf-8"
    )

    assert wiki.reproject_after_vault_change().graph_changed is True
    assert wiki.paths.graph.read_text(encoding="utf-8") != before, (
        "the overlay was reported as applied but never reached graph.json"
    )


def test_a_vault_sync_that_changes_nothing_does_not_rewrite_the_graph(tmp_path):
    """Byte-idempotence: an empty overlay must leave graph.json untouched."""
    wiki = _project(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    wiki.set_vault_override(vault)
    wiki.export_obsidian()
    wiki.reproject_after_vault_change()
    before = wiki.paths.graph.read_bytes()
    # The engine reads this flag to decide whether a recompile is owed.
    assert wiki.reproject_after_vault_change().graph_changed is False
    assert wiki.paths.graph.read_bytes() == before


def test_vault_export_keeps_an_orphan_that_holds_user_notes(tmp_path):
    """`vault export` deleted exactly what `vault prune` refuses to delete.

    A projected page can hold hand-written content — the
    `<!-- user-notes -->` block is the whole point of a bidirectional vault.
    `prune_orphan_pages` keeps those and surfaces them for review;
    `_prune_orphaned_vault_pages`, which runs on every export, unlinked them
    without looking. A stale page is recoverable; a deleted note is not.
    """
    wiki = _project(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    wiki.set_vault_override(vault)
    wiki.export_obsidian()

    # An orphan: projector-shaped (node_id frontmatter) for a node that no
    # longer projects here, carrying the user's own notes.
    orphan = vault / "concepts" / "renamed-away.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text(
        "---\nnode_id: Concept:gone:deadbeef\ntitle: Gone\n---\n\n"
        "<!-- user-notes:start -->\nmy hand-written analysis, months of it\n"
        "<!-- user-notes:end -->\n",
        encoding="utf-8",
    )
    bare_orphan = vault / "concepts" / "also-gone.md"
    bare_orphan.write_text(
        "---\nnode_id: Concept:alsogone:cafe\ntitle: Also Gone\n---\n\n"
        "<!-- user-notes:start -->\n\n<!-- user-notes:end -->\n",
        encoding="utf-8",
    )

    wiki.export_obsidian()

    assert orphan.is_file(), "an orphan carrying user notes was deleted"
    assert "months of it" in orphan.read_text(encoding="utf-8")
    assert not bare_orphan.exists(), "an orphan with an EMPTY notes block should still go"
