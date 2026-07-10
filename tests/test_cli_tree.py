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


def test_root_help_shows_package_version(capsys):
    from importlib.metadata import version

    from tesserae.cli import main

    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"tesserae {version('tesserae')}" in out.splitlines()[0]


def test_version_flag_prints_version(capsys):
    from importlib.metadata import version

    from tesserae.cli import main

    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == f"tesserae {version('tesserae')}"


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


def test_wiki_activate_stub_is_a_terminal_removal(capsys):
    """`wiki activate` no longer points at `projects activate` (which is gone):
    it prints a one-line removal explanation, exit 2."""
    from tesserae.cli import main

    rc = main(["wiki", "activate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"stub must be exactly one line, got: {err!r}"
    assert "was removed — all registered projects are active" in err
    assert "tesserae projects list" in err
    assert "has moved" not in err


def test_projects_activate_stub_is_a_terminal_removal(capsys):
    """`projects activate` gets the same removal explanation, exit 2."""
    from tesserae.cli import main

    rc = main(["projects", "activate", "demo"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "was removed — all registered projects are active" in err
    assert "tesserae projects list" in err


def test_bare_extraction_paths_get_extract_stub(tmp_path, capsys):
    from tesserae.cli import main

    md = tmp_path / "note.md"
    md.write_text("# x")
    rc = main([str(md)])
    assert rc == 2
    assert "tesserae extract" in capsys.readouterr().err


def test_existing_non_markdown_path_is_unknown_not_extract_stub(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main

    plain = tmp_path / "setup.py"
    plain.write_text("x = 1")
    monkeypatch.chdir(tmp_path)
    rc = main(["setup.py"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command" in err and "tesserae extract" not in err


def test_markdown_directory_gets_extract_stub(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("# a")
    monkeypatch.chdir(tmp_path)
    rc = main(["notes"])
    assert rc == 2
    assert "tesserae extract" in capsys.readouterr().err


def test_plain_directory_is_unknown_not_extract_stub(tmp_path, capsys, monkeypatch):
    from tesserae.cli import main

    d = tmp_path / "docsdir"
    d.mkdir()
    (d / "x.txt").write_text("t")
    monkeypatch.chdir(tmp_path)
    rc = main(["docsdir"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command" in err and "tesserae extract" not in err


def test_known_command_prints_clean_line_not_traceback(capsys):
    from tesserae.cli import main

    # After task 5 the whole new tree is wired, so there is no longer an
    # "unwired but known" command. `query` with no question reaches its real
    # handler, which reports a usage error (exit 2) — still a clean one-line
    # message, never a traceback. (That clean-error contract is what this
    # test guards; the previous "not wired up yet" fallback is now dead code
    # kept only for defense in depth.)
    rc = main(["query"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.strip()


def test_every_command_help_has_examples(capsys):
    import tesserae.cli as cli

    for cmd in (["init"], ["compile"], ["context"], ["ask"], ["serve"],
                ["status"], ["engine"], ["refresh"], ["research"], ["query"],
                ["lint"], ["extract"],
                ["sessions", "import"], ["vault", "sync"], ["export", "site"],
                ["code", "sync"], ["config", "llm"], ["projects", "register"],
                ["integrations", "refresh"], ["lab", "evolve"]):
        with pytest.raises(SystemExit):
            cli.main([*cmd, "--help"])
        out = capsys.readouterr().out
        assert "examples:" in out.lower(), f"{' '.join(cmd)} --help lacks EXAMPLES"
