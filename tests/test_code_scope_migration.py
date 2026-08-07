"""`tesserae doctor migrate-code-scope` — the one-shot code-layer cleanup.

Most of this file is about the PREDICATE, because that is where a bug is
both likely and unrecoverable. The projection directory it sweeps measured
218,796 code-derived pages against 949 genuine ``Concept`` pages: at a
99.45%-to-0.43% ratio, a predicate that matched everything and a predicate
that matched only the code layer produce deletion counts you cannot tell
apart. So the tests assert on what SURVIVES, and the ones that matter most
feed the sweep a page that must not be touched.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tesserae import doctor
from tesserae.project import ProjectWiki
from tesserae.research_graph import CODE_GRAPH_TYPES


def _page(path: Path, node_type: str, *, node_id: str = "n1", body: str = "") -> Path:
    """A page shaped like ``markdown_projection.render_node_page`` writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"node_id: {node_type}:{node_id}\n"
        f"title: {node_id}\n"
        f"type: {node_type}\n"
        "---\n"
        "\n"
        f"# {node_id}\n{body}",
        encoding="utf-8",
    )
    return path


def _project(root: Path) -> ProjectWiki:
    root.mkdir(parents=True, exist_ok=True)
    return ProjectWiki.init(root, name="scopeproj", sources=["README.md"])


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------


def test_declared_type_reads_the_pages_own_frontmatter(tmp_path):
    assert (
        doctor._declared_node_type(_page(tmp_path / "a.md", "CodeFunction"))
        == "CodeFunction"
    )
    assert doctor._declared_node_type(_page(tmp_path / "b.md", "Concept")) == "Concept"


@pytest.mark.parametrize("document_type", ["Repository", "Project", "SourceDocument"])
def test_document_types_that_look_like_code_are_not_code(tmp_path, document_type):
    """The trap this migration must not fall into.

    ``Repository`` and ``Project`` read like code types and are not: they are
    document entities with markdown provenance, and the 320 Repository nodes
    in the compiled graph anchor 1,663 Repository->Session edges. Any
    name-shaped predicate ("Code|Source|Dependency|Repository|Project") would
    delete them. Keying off ``CODE_GRAPH_TYPES`` is what makes that
    impossible, so assert the two facts together.
    """
    page = _page(tmp_path / "doc.md", document_type)
    declared = doctor._declared_node_type(page)
    assert declared == document_type
    assert declared not in doctor._retired_type_values()


def test_retired_values_are_exactly_the_code_graph_types():
    assert doctor._retired_type_values() == {item.value for item in CODE_GRAPH_TYPES}
    assert "Repository" not in doctor._retired_type_values()
    assert "Project" not in doctor._retired_type_values()


def test_a_page_with_no_frontmatter_is_unclassified(tmp_path):
    path = tmp_path / "hand-written.md"
    path.write_text("# notes\n\ntype: CodeFunction\n", encoding="utf-8")
    assert doctor._declared_node_type(path) is None


def test_an_unterminated_block_is_unclassified(tmp_path):
    """No closing fence means we cannot trust the parse — so keep the file."""
    path = tmp_path / "truncated.md"
    path.write_text("---\ntype: CodeFunction\ntitle: half a file\n", encoding="utf-8")
    assert doctor._declared_node_type(path) is None


def test_a_type_line_in_the_body_is_ignored(tmp_path):
    """Only the frontmatter block decides; the body is prose."""
    page = _page(
        tmp_path / "c.md",
        "Concept",
        body="\nSome prose about a page whose\ntype: CodeFunction\nis discussed.\n",
    )
    assert doctor._declared_node_type(page) == "Concept"


def test_a_metadata_key_named_type_cannot_shadow_the_real_one(tmp_path):
    """First unindented ``type:`` wins.

    ``render_node_page`` writes ``type:`` third and then appends the node's
    metadata keys at the same indent, so a node with a metadata key named
    ``type`` would win a last-write-wins parse — and shadowing a survivor
    into a code type deletes it.
    """
    path = tmp_path / "shadowed.md"
    path.write_text(
        "---\nnode_id: Concept:x\ntitle: x\ntype: Concept\ntype: CodeFunction\n---\n\n# x\n",
        encoding="utf-8",
    )
    assert doctor._declared_node_type(path) == "Concept"


def test_a_frontmatter_longer_than_the_line_cap_is_unclassified(tmp_path):
    """Bounded read, and the unbounded case fails toward keeping the file."""
    path = tmp_path / "huge.md"
    filler = "\n".join(f"key{i}: v" for i in range(doctor._FRONTMATTER_LINE_CAP + 10))
    path.write_text(f"---\ntype: CodeFunction\n{filler}\n---\n\n# x\n", encoding="utf-8")
    assert doctor._declared_node_type(path) is None


# ---------------------------------------------------------------------------
# the dry run
# ---------------------------------------------------------------------------


def _seed_projection(wiki: ProjectWiki) -> Path:
    projection = wiki.paths.markdown_projection
    _page(projection / "concepts" / "attention.md", "Concept", node_id="attention")
    _page(projection / "concepts" / "beam-search.md", "Concept", node_id="beam")
    _page(projection / "concepts" / "run-compile.md", "CodeFunction", node_id="run")
    _page(projection / "concepts" / "wiki-py.md", "SourceFile", node_id="wiki")
    _page(projection / "concepts" / "pydantic.md", "Dependency", node_id="pyd")
    _page(projection / "papers" / "transformer.md", "Paper", node_id="tfm")
    _page(projection / "concepts" / "tesserae-repo.md", "Repository", node_id="repo")
    return projection


def test_dry_run_reports_without_deleting_anything(tmp_path):
    wiki = _project(tmp_path)
    projection = _seed_projection(wiki)
    before = sorted(p.name for p in projection.rglob("*.md"))

    result = doctor.migrate_code_scope(tmp_path)

    assert result.applied is False
    assert sorted(p.name for p in projection.rglob("*.md")) == before
    sweep = next(s for s in result.sweeps if s.directory == str(projection))
    assert sweep.scanned == 7
    assert sweep.retired == 3
    # The check: Concept x2, Paper, Repository.
    assert sweep.survivors == 4
    assert sweep.by_type == {"CodeFunction": 1, "Dependency": 1, "SourceFile": 1}
    assert any("dry run" in note for note in result.notes)


def test_apply_removes_the_code_pages_and_only_those(tmp_path):
    wiki = _project(tmp_path)
    projection = _seed_projection(wiki)

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert result.applied is True
    assert sorted(p.name for p in projection.rglob("*.md")) == [
        "attention.md",
        "beam-search.md",
        "tesserae-repo.md",
        "transformer.md",
    ]
    sweep = next(s for s in result.sweeps if s.directory == str(projection))
    assert (sweep.retired, sweep.survivors) == (3, 4)


def test_a_second_apply_is_a_no_op(tmp_path):
    wiki = _project(tmp_path)
    _seed_projection(wiki)
    doctor.migrate_code_scope(tmp_path, apply=True)

    again = doctor.migrate_code_scope(tmp_path, apply=True)

    projection_sweep = next(
        s for s in again.sweeps if s.directory == str(wiki.paths.markdown_projection)
    )
    assert projection_sweep.retired == 0
    assert projection_sweep.survivors == 4


def test_pages_without_frontmatter_are_never_touched(tmp_path):
    wiki = _project(tmp_path)
    projection = wiki.paths.markdown_projection
    _page(projection / "concepts" / "code.md", "CodeClass", node_id="cls")
    stray = projection / "concepts" / "operator-notes.md"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("just some notes, no frontmatter\n", encoding="utf-8")

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert stray.exists()
    sweep = next(s for s in result.sweeps if s.directory == str(projection))
    assert (sweep.retired, sweep.unclassified) == (1, 1)


# ---------------------------------------------------------------------------
# the vault
# ---------------------------------------------------------------------------


def test_vault_code_pages_go_even_though_they_carry_no_node_id(tmp_path):
    """The reason a second sweep exists at all.

    ``vault_pull.prune_orphan_pages`` keys off ``node_id:`` frontmatter and
    skips anything without it as user-authored. The 1,370 code-typed vault
    pages have no ``node_id`` key, so the orphan pruner cannot see them however
    many times it runs. This sweep keys off ``type:`` instead.
    """
    wiki = _project(tmp_path)
    vault = Path(wiki.effective_obsidian_vault())
    code_page = vault / "concepts" / "relevance-from-graph.md"
    code_page.parent.mkdir(parents=True, exist_ok=True)
    code_page.write_text(
        "---\ntitle: RelevanceContext.from_graph\ntype: CodeFunction\n"
        "source_path: /repo/tesserae/site/relevance.py\n---\n\n# x\n",
        encoding="utf-8",
    )
    kept = _page(vault / "concepts" / "attention.md", "Concept", node_id="attention")

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert not code_page.exists()
    assert kept.exists()
    sweep = next(s for s in result.sweeps if s.directory == str(vault))
    assert (sweep.retired, sweep.survivors) == (1, 1)


def test_a_code_page_with_user_notes_is_kept_and_counted(tmp_path):
    """User writing outlives the layer that framed it.

    Nothing will ever regenerate this page to carry the notes forward, so it
    is reported rather than deleted — the same bargain
    ``prune_orphan_pages`` strikes by default.
    """
    from tesserae.markdown_projection import USER_NOTES_END, USER_NOTES_START

    wiki = _project(tmp_path)
    vault = Path(wiki.effective_obsidian_vault())
    page = vault / "concepts" / "annotated.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: parse_frontmatter\ntype: CodeFunction\n---\n\n"
        f"{USER_NOTES_START}\nthis one bit me twice\n{USER_NOTES_END}\n",
        encoding="utf-8",
    )

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert page.exists()
    sweep = next(s for s in result.sweeps if s.directory == str(vault))
    assert (sweep.retired, sweep.kept_with_user_notes) == (0, 1)


def test_an_abandoned_in_project_vault_is_swept_too(tmp_path):
    """Two vault locations, and the stale one is where the pages actually are.

    Measured on this repository: the configured vault held 0 code-typed
    pages and the abandoned in-project default held 1,370. Sweeping only
    ``effective_obsidian_vault()`` would have reported a clean run and left
    every one of them on disk.
    """
    wiki = _project(tmp_path)
    external = tmp_path / "elsewhere" / "vault"
    _page(external / "concepts" / "attention.md", "Concept", node_id="attention")
    config = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    config["obsidian"] = {"vault_path": str(external)}
    wiki.paths.config.write_text(json.dumps(config), encoding="utf-8")

    abandoned = wiki.paths.obsidian_vault
    stale = _page(abandoned / "concepts" / "run.md", "CodeFunction", node_id="run")

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert not stale.exists()
    directories = [s.directory for s in result.sweeps]
    assert str(external) in directories and str(abandoned) in directories


def test_the_same_vault_is_only_swept_once(tmp_path):
    wiki = _project(tmp_path)
    _page(
        wiki.paths.obsidian_vault / "concepts" / "a.md", "CodeFunction", node_id="a"
    )

    result = doctor.migrate_code_scope(tmp_path)

    vault_sweeps = [
        s for s in result.sweeps if s.directory == str(wiki.paths.obsidian_vault)
    ]
    assert len(vault_sweeps) == 1
    assert vault_sweeps[0].retired == 1


def test_dot_directories_in_the_vault_are_left_alone(tmp_path):
    """``.obsidian/`` is the user's tooling, not our projection."""
    wiki = _project(tmp_path)
    vault = Path(wiki.effective_obsidian_vault())
    hidden = vault / ".obsidian" / "workspace.md"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text("---\ntype: CodeFunction\n---\n\n# ignore me\n", encoding="utf-8")

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert hidden.exists()
    sweep = next(s for s in result.sweeps if s.directory == str(vault))
    assert sweep.scanned == 0


# ---------------------------------------------------------------------------
# artifacts and sqlite
# ---------------------------------------------------------------------------


def test_retired_artifacts_are_reported_then_unlinked(tmp_path):
    wiki = _project(tmp_path)
    for name in ("code-graph.json", "code-graph-cache.json"):
        (wiki.root / name).write_text('{"nodes": []}', encoding="utf-8")

    dry = doctor.migrate_code_scope(tmp_path)
    assert sorted(Path(a["path"]).name for a in dry.artifacts) == [
        "code-graph-cache.json",
        "code-graph.json",
    ]
    assert (wiki.root / "code-graph.json").exists()

    doctor.migrate_code_scope(tmp_path, apply=True)
    assert not (wiki.root / "code-graph.json").exists()
    assert not (wiki.root / "code-graph-cache.json").exists()


def _seed_sqlite(db_path: Path) -> None:
    """A store shaped like a post-compile one: live nodes, orphaned sidecars."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute("create table nodes (id text primary key, type text)")
        con.execute("create table edges (source text, type text, target text)")
        con.execute(
            "create table node_provenance (node_id text, source_path text,"
            " first_seen_at text, last_updated_at text)"
        )
        con.execute(
            "create table node_memory (node_id text primary key, decay_score real)"
        )
        con.execute(
            "create table edge_provenance (source text, type text, target text,"
            " source_path text, first_seen_at text, last_updated_at text)"
        )
        con.execute("insert into nodes values ('Concept:a', 'Concept')")
        con.execute("insert into edges values ('Concept:a', 'relates_to', 'Concept:a')")
        # Live rows.
        con.execute("insert into node_provenance values ('Concept:a', 'a.md', '', '')")
        con.execute("insert into node_memory values ('Concept:a', 1.0)")
        con.execute(
            "insert into edge_provenance values"
            " ('Concept:a', 'relates_to', 'Concept:a', 'a.md', '', '')"
        )
        # Orphans a compile's delete-all left behind.
        con.execute("insert into node_provenance values ('CodeFunction:z', 'z.py', '', '')")
        con.execute("insert into node_memory values ('CodeFunction:z', 0.5)")
        con.execute(
            "insert into edge_provenance values"
            " ('SourceFile:z', 'discussed_in', 'Session:1', 'z.py', '', '')"
        )
        con.commit()


def test_orphaned_sidecar_rows_are_counted_then_deleted(tmp_path):
    wiki = _project(tmp_path)
    _seed_sqlite(wiki.paths.sqlite)

    dry = doctor.migrate_code_scope(tmp_path)
    assert dry.sqlite.deleted_rows == {
        "node_provenance": 1,
        "node_memory": 1,
        "edge_provenance": 1,
    }
    assert dry.sqlite.vacuumed is False
    with sqlite3.connect(wiki.paths.sqlite) as con:
        assert con.execute("select count(*) from node_memory").fetchone()[0] == 2

    applied = doctor.migrate_code_scope(tmp_path, apply=True)
    assert applied.sqlite.vacuumed is True
    with sqlite3.connect(wiki.paths.sqlite) as con:
        assert [r[0] for r in con.execute("select node_id from node_memory")] == [
            "Concept:a"
        ]
        assert [r[0] for r in con.execute("select node_id from node_provenance")] == [
            "Concept:a"
        ]
        assert con.execute("select count(*) from edge_provenance").fetchone()[0] == 1


def test_code_rows_still_in_the_nodes_table_are_reported_not_deleted(tmp_path):
    """The nodes/edges tables self-heal on the next compile; say so.

    A sidecar row is only an orphan relative to those tables, so migrating
    before recompiling finds nothing to delete. Rather than silently reclaim
    nothing, the sweep names the state and the order to run things in.
    """
    wiki = _project(tmp_path)
    _seed_sqlite(wiki.paths.sqlite)
    with sqlite3.connect(wiki.paths.sqlite) as con:
        con.execute("insert into nodes values ('CodeFunction:z', 'CodeFunction')")
        con.commit()

    result = doctor.migrate_code_scope(tmp_path)

    assert result.sqlite.code_nodes_remaining == 1
    assert "tesserae compile" in (result.sqlite.note or "")
    # 'CodeFunction:z' now HAS a parent row, so its sidecars are not orphans.
    assert result.sqlite.deleted_rows["node_memory"] == 0


def test_vacuum_is_skipped_when_the_disk_could_not_hold_the_rebuild(tmp_path, monkeypatch):
    """VACUUM rebuilds into a temporary copy — refuse rather than fill the disk."""
    import shutil

    wiki = _project(tmp_path)
    _seed_sqlite(wiki.paths.sqlite)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(1, 1, 0)
    )

    result = doctor.migrate_code_scope(tmp_path, apply=True)

    assert result.sqlite.vacuumed is False
    assert "skipped VACUUM" in (result.sqlite.note or "")
    # The row deletions still committed; only the reclaim was deferred.
    with sqlite3.connect(wiki.paths.sqlite) as con:
        assert con.execute("select count(*) from node_memory").fetchone()[0] == 1


def test_a_workspace_with_no_sqlite_is_fine(tmp_path):
    _project(tmp_path)
    result = doctor.migrate_code_scope(tmp_path)
    assert result.sqlite is None


def test_an_uninitialized_directory_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        doctor.migrate_code_scope(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# reporting and CLI
# ---------------------------------------------------------------------------


def test_the_rendered_report_leads_with_the_survivor_count(tmp_path):
    wiki = _project(tmp_path)
    _seed_projection(wiki)

    text = doctor.render_code_scope_migration(doctor.migrate_code_scope(tmp_path))

    projection_section = text.split(str(wiki.paths.markdown_projection))[1]
    survivors = projection_section.index("4 non-code pages survive")
    removals = projection_section.index("would remove 3 code-typed pages")
    assert survivors < removals, "the survivor count is the check; print it first"
    assert "mode: dry run" in text


def test_json_output_round_trips(tmp_path):
    wiki = _project(tmp_path)
    _seed_projection(wiki)

    payload = json.loads(
        doctor.code_scope_migration_json(doctor.migrate_code_scope(tmp_path))
    )

    assert payload["applied"] is False
    sweep = next(
        s for s in payload["sweeps"] if s["directory"] == str(wiki.paths.markdown_projection)
    )
    assert (sweep["survivors"], sweep["retired"]) == (4, 3)


def test_cli_defaults_to_the_dry_run(tmp_path, capsys):
    from tesserae import cli

    wiki = _project(tmp_path)
    projection = _seed_projection(wiki)

    rc = cli.main(["doctor", "migrate-code-scope", "--project", str(tmp_path)])

    assert rc == 0
    assert len(list(projection.rglob("*.md"))) == 7
    assert "mode: dry run" in capsys.readouterr().out


def test_cli_apply_deletes(tmp_path, capsys):
    from tesserae import cli

    wiki = _project(tmp_path)
    projection = _seed_projection(wiki)

    rc = cli.main(
        ["doctor", "migrate-code-scope", "--apply", "--project", str(tmp_path)]
    )

    assert rc == 0
    assert len(list(projection.rglob("*.md"))) == 4
    assert "mode: apply" in capsys.readouterr().out


def test_cli_refuses_all_projects(tmp_path, capsys):
    from tesserae import cli

    _project(tmp_path)
    rc = cli.main(["doctor", "migrate-code-scope", "--all"])

    assert rc == 2
    assert "--all is not supported" in capsys.readouterr().err


def test_plain_doctor_still_runs_the_checks(tmp_path, capsys):
    """The verb is optional; adding it must not move `tesserae doctor`."""
    from tesserae import cli

    _project(tmp_path)
    cli.main(["doctor", "--project", str(tmp_path)])

    assert "tesserae doctor —" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# discoverability
# ---------------------------------------------------------------------------


def test_doctor_flags_a_workspace_that_still_carries_the_code_layer(tmp_path):
    wiki = _project(tmp_path)
    (wiki.root / "code-graph-cache.json").write_text("{}", encoding="utf-8")

    report = doctor.run_doctor(tmp_path)

    found = next(f for f in report.findings if f.check_id == "code_scope_leftovers")
    assert found.severity == doctor.WARN
    assert "migrate-code-scope" in (found.suggestion or "")
    # Never routed through --fix: it is a mass delete, and --fix is documented
    # as safe repairs only.
    assert found.fixable is False


def test_a_clean_workspace_reports_ok(tmp_path):
    _project(tmp_path)
    report = doctor.run_doctor(tmp_path)
    found = next(f for f in report.findings if f.check_id == "code_scope_leftovers")
    assert found.severity == doctor.OK
