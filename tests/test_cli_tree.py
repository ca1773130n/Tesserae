"""tests/test_cli_tree.py"""
from __future__ import annotations

import pytest


def test_root_help_shows_grouped_commands(capsys):
    from tesserae.cli import main

    rc = main([])  # bare invocation prints grouped help, exit 0
    out = capsys.readouterr().out
    assert rc == 0
    for section in ("EVERYDAY", "AUTOMATION", "ANALYSIS", "GROUPS", "LAB"):
        assert section in out
    for cmd in ("init", "compile", "context", "ask", "serve", "status",
                "engine", "refresh", "research", "query", "lint",
                "sessions", "vault", "export", "code", "config", "projects",
                "integrations", "extract", "lab"):
        assert f"\n  {cmd}" in out or f" {cmd} " in out


def test_root_help_flag_matches_bare(capsys):
    from tesserae.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "EVERYDAY" in capsys.readouterr().out


def test_unknown_command_exits_2_and_points_at_help(capsys):
    from tesserae.cli import main

    rc = main(["frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "tesserae --help" in err


@pytest.mark.parametrize(
    "old, hint",
    [
        (["project", "compile"], "tesserae compile"),
        (["project", "setup", "--yes"], "tesserae init"),
        (["project", "init"], "tesserae init --bare"),
        (["project", "ingest", "x.md"], "tesserae compile <paths>"),
        (["project", "build-site"], "tesserae export site"),
        (["project", "deploy"], "tesserae export site --deploy"),
        (["project", "serve"], "tesserae serve"),
        (["project", "watch"], "tesserae export site --watch"),
        (["project", "daemon"], "tesserae engine"),
        (["project", "obsidian-sync"], "tesserae vault sync"),
        (["project", "export-obsidian"], "tesserae vault export"),
        (["project", "export-agent-harness"], "tesserae export harness"),
        (["project", "export-graphiti"], "tesserae export graphiti"),
        (["project", "sync-graphiti"], "tesserae export graphiti --sync"),
        (["project", "mcp-config"], "tesserae projects mcp-config"),
        (["project", "refresh-raganything"], "tesserae integrations refresh raganything"),
        (["project", "refresh-understand-anything"], "tesserae integrations refresh understand-anything"),
        (["project", "evolve"], "tesserae lab evolve"),
        (["project", "schema-drift"], "tesserae lab schema-drift"),
        (["project", "sessions", "import"], "tesserae sessions import"),
        (["project", "sessions", "discover"], "tesserae sessions discover"),
        (["project", "ingest-code"], "tesserae code ingest"),
        (["project", "sync-code", "--auto-sync"], "tesserae code sync"),
        (["project", "research", "q"], "tesserae research"),
        (["project", "lint"], "tesserae lint"),
        (["project", "query", "q"], "tesserae query"),
        (["wiki", "register"], "tesserae projects register"),
        (["wiki", "list"], "tesserae projects list"),
        (["wiki", "activate"], "tesserae projects activate"),
        (["wiki", "unregister"], "tesserae projects unregister"),
        (["wiki", "obsidian-set-root"], "tesserae vault set-root"),
        (["wiki", "obsidian-sync-all"], "tesserae vault sync-all"),
        (["llm-defaults", "--show"], "tesserae config show"),
        (["llm-defaults"], "tesserae config llm"),
    ],
)
def test_moved_commands_print_one_line_stub(old, hint, capsys):
    from tesserae.cli import main

    rc = main(old)
    assert rc == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"stub must be exactly one line, got: {err!r}"
    assert "has moved" in err and hint in err


def test_bare_extraction_paths_get_extract_stub(tmp_path, capsys):
    from tesserae.cli import main

    md = tmp_path / "note.md"
    md.write_text("# x")
    rc = main([str(md)])
    assert rc == 2
    assert "tesserae extract" in capsys.readouterr().err
