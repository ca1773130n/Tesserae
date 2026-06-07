"""CLI tests for `tesserae context` (CTX-02, Plan 07-03).

In-process invocations via ``tesserae.cli.main`` with a compiled tmp project.
No-LLM path only (no ``--synthesize``) so these are CI-safe without an API key.
"""

from pathlib import Path

from tesserae.cli import main


def _compiled_project(tmp_path, capsys) -> Path:
    """Init + compile a tiny project the context handler can load.

    Drains ``capsys`` afterwards so the init/compile chatter never leaks into
    the context-command assertions.
    """
    project = tmp_path / "ctx-project"
    project.mkdir()
    (project / "note.md").write_text(
        "# Splatting Note\n"
        "Gaussian Splatting supports real-time novel view synthesis.\n"
        "It builds on radiance fields and point-based rendering.\n",
        encoding="utf-8",
    )
    assert main([
        "init", "--bare", "--project", str(project),
        "--name", "ctx_demo", "--source", "note.md",
    ]) == 0
    assert main(["compile", "--project", str(project)]) == 0
    capsys.readouterr()  # drain init/compile output
    return project


def test_context_stdout(tmp_path, capsys):
    project = _compiled_project(tmp_path, capsys)
    rc = main([
        "context", "Gaussian Splatting", "--project", str(project),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("# Context:")


def test_context_output_file(tmp_path, capsys):
    project = _compiled_project(tmp_path, capsys)
    out_path = tmp_path / "ctx.md"
    rc = main([
        "context", "Gaussian Splatting",
        "--project", str(project), "--output", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8").startswith("# Context:")
    summary = capsys.readouterr().out
    assert "Written to" in summary
    assert "citations" in summary


def test_context_deterministic(tmp_path, capsys):
    project = _compiled_project(tmp_path, capsys)
    main(["context", "Gaussian Splatting", "--project", str(project)])
    first = capsys.readouterr().out
    main(["context", "Gaussian Splatting", "--project", str(project)])
    second = capsys.readouterr().out
    assert first == second
    assert first.startswith("# Context:")
