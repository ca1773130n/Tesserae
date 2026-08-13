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


def test_context_multi_pool_reports_the_pools_on_stderr(tmp_path, capsys):
    """An operator at a terminal must be able to see whether reservation worked.

    ``--multi-pool`` reserves a budget slot per procedural pool, and those
    pools are producer-scoped: on a project whose Runbook/Event nodes are all
    document extractions — or which has no sessions at all — every pool comes
    back empty and the bundle is byte-identical to the single-pool one. The MCP
    surface reports that in ``knobs.pool_reservations``; the CLI printed only
    the body, so the same silent story the field was added to remove was still
    being told to every command-line caller.

    On stderr, because stdout is the bundle and gets piped into files.
    """
    project = _compiled_project(tmp_path, capsys)

    # Without the flag no reservation runs, so there is nothing to report and
    # the report must not appear — the fix must not add unconditional noise.
    assert main(["context", "Gaussian Splatting", "--project", str(project)]) == 0
    assert "pool" not in capsys.readouterr().err.lower()

    rc = main([
        "context", "Gaussian Splatting",
        "--project", str(project), "--multi-pool",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# Context:"), (
        "the reservation report must not contaminate the bundle on stdout"
    )
    assert "pool" in captured.err.lower(), (
        f"--multi-pool must say what the pools returned; stderr was "
        f"{captured.err!r}"
    )
    # This project has no procedural producer output at all, so every pool is
    # empty — and the operator has to be told that, by name.
    assert "Runbook" in captured.err, captured.err
    assert "empty" in captured.err.lower(), captured.err


def test_context_view_flag_compiles_a_view_restricted_doc(tmp_path, capsys):
    """`--view` reaches the compiler (an unknown value would be rejected by
    argparse's choices; a known one must still produce a valid doc)."""
    project = _compiled_project(tmp_path, capsys)
    rc = main([
        "context", "Gaussian Splatting",
        "--project", str(project), "--view", "semantic",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("# Context:")
