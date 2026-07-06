"""CLI wiring for `tesserae summary` (Task 11).

The command is a thin adapter over ``tesserae.activity_summary``: it resolves
the time window(s), calls ``build_summary``, prints the markdown, and echoes any
written file paths. These tests patch ``build_summary`` so no gather/render work
runs — they assert the argument plumbing and the print behaviour only.
"""
import tesserae.cli as cli
from tesserae.activity_summary import SummaryResult


def test_cli_summary_no_llm(monkeypatch, capsys):
    called = {}

    def fake_build(windows, projects, *, synthesize, write, **_kw):
        called.update(windows=windows, projects=projects, synthesize=synthesize, write=write)
        return SummaryResult(markdown="# digest\n", paths=[])

    monkeypatch.setattr(cli, "build_summary", fake_build, raising=False)
    rc = cli.main(["summary", "--day", "2026-07-04", "--project", "proj", "--no-llm"])
    assert rc == 0
    assert called["projects"] == ["proj"]
    assert called["synthesize"] is False
    assert "# digest" in capsys.readouterr().out


def test_cli_summary_defaults_all_projects_and_synthesizes(monkeypatch, capsys):
    """No --project → projects=None (all registered); no --no-llm → synthesize=True."""
    called = {}

    def fake_build(windows, projects, *, synthesize, write, **_kw):
        called.update(windows=windows, projects=projects, synthesize=synthesize, write=write)
        return SummaryResult(markdown="# body\n", paths=[])

    monkeypatch.setattr(cli, "build_summary", fake_build, raising=False)
    rc = cli.main(["summary", "--day", "2026-07-04"])
    assert rc == 0
    assert called["projects"] is None
    assert called["synthesize"] is True
    # One --day selector → exactly one resolved window.
    assert len(called["windows"]) == 1


def test_cli_summary_prints_written_paths(monkeypatch, capsys):
    from pathlib import Path

    def fake_build(windows, projects, *, synthesize, write, **_kw):
        return SummaryResult(markdown="# d\n", paths=[Path("/tmp/proj/2026-07-04.md")])

    monkeypatch.setattr(cli, "build_summary", fake_build, raising=False)
    rc = cli.main(["summary", "--day", "2026-07-04", "--no-llm"])
    assert rc == 0
    captured = capsys.readouterr()
    # Markdown on stdout (clean/pipeable); the "wrote <path>" advisory on stderr.
    assert "# d" in captured.out
    assert "/tmp/proj/2026-07-04.md" in captured.err


def test_cli_summary_week_bare_flag_is_seven_windows(monkeypatch):
    """Bare --week (nargs='?') → last 7 daily windows via resolve_windows."""
    called = {}

    def fake_build(windows, projects, *, synthesize, write, **_kw):
        called.update(windows=windows)
        return SummaryResult(markdown="# w\n", paths=[])

    monkeypatch.setattr(cli, "build_summary", fake_build, raising=False)
    rc = cli.main(["summary", "--week", "--no-llm"])
    assert rc == 0
    assert len(called["windows"]) == 7
