from pathlib import Path

import pytest

import tesserae.cli as cli


def test_ingest_is_a_top_level_command():
    assert "ingest" in cli._NEW_DISPATCH


def test_ingest_parser_accepts_inputs_and_flags():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md", "https://x.com", "--full", "--project", "/tmp/p"])
    assert args.inputs == ["a.md", "https://x.com"]
    assert args.full is True
    assert args.project == "/tmp/p"
    assert args._handler == "_handle_ingest_docs"


def test_ingest_defaults_to_fast_path():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md"])
    assert args.full is False  # fast path by default; --full opts into full recompile


def test_ingest_exact_flag_is_a_removed_stub(capsys):
    """`ingest --exact` was renamed --full: one-line stderr stub, exit 2."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "x.md", "--exact"])
    assert exc.value.code == 2
    assert "ingest: --exact was renamed --full" in capsys.readouterr().err


def test_code_ingest_still_exists_unchanged():
    code_parser = cli._build_code_parser()
    ns = code_parser.parse_args(["ingest", "--project", "."])
    assert ns._handler == "_handle_code_ingest"


def test_ingest_dispatch_calls_orchestrator(monkeypatch, tmp_path):
    captured = {}
    def _fake_ingest_sources(wiki, inputs, **kw):
        captured["inputs"] = list(inputs)
        captured["kw"] = kw
        return {"path_taken": "full-recompile", "node_count": 1, "edge_count": 0,
                "processed_files": 1, "skipped_files": 0,
                "graph_path": str(tmp_path / "g.json"), "sources": list(inputs)}
    monkeypatch.setattr("tesserae.cli.ingest_sources", _fake_ingest_sources)
    monkeypatch.setattr("tesserae.cli.ProjectWiki", _StubWiki)
    rc = cli.main(["ingest", "x.md", "--project", str(tmp_path), "--full"])
    assert rc == 0
    assert captured["inputs"] == ["x.md"]
    assert captured["kw"]["exact"] is True  # --full maps onto the library's exact= kwarg


class _StubWiki:
    project_root = "."
    @staticmethod
    def load(project):
        return _StubWiki()

# ---------------------------------------------------------------------------
# item-6 riders: --dry-run is truly dry; inputs are validated up front;
# --source-kind is choices=-validated.
# ---------------------------------------------------------------------------


def test_ingest_source_kind_has_choices():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md", "--source-kind", "Paper"])
    assert args.source_kind == "Paper"
    with pytest.raises(SystemExit):
        parser.parse_args(["a.md", "--source-kind", "NotAKind"])


def test_ingest_sources_dry_run_is_truly_dry(tmp_path):
    """--dry-run must not fetch, not copy into data/ingested, not compile."""
    from tesserae.ingest.orchestrator import ingest_sources
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="dry", source_kind="Repository")
    wiki = ProjectWiki.load(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("# outside doc\n", encoding="utf-8")

    result = ingest_sources(wiki, [str(outside)], dry_run=True)
    assert result["path_taken"] == "dry-run"
    assert result["processed_files"] == 0
    # No copy into the corpus happened.
    assert not (tmp_path / "data" / "ingested" / outside.name).exists()


def test_ingest_sources_missing_input_raises(tmp_path):
    from tesserae.ingest.orchestrator import ingest_sources
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="missing", source_kind="Repository")
    wiki = ProjectWiki.load(tmp_path)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        ingest_sources(wiki, [str(tmp_path / "nope.md")], dry_run=True)


def test_cli_ingest_missing_input_exits_2(tmp_path, capsys):
    """The central FileNotFoundError catch maps a typo'd path to exit 2."""
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="typo", source_kind="Repository")
    rc = cli.main(["ingest", str(tmp_path / "nope.md"), "--project", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not exist" in err and "Traceback" not in err


# ---------------------------------------------------------------------------
# Binary inputs at the CLI: a named error and exit 2, never a success line
# carrying node/edge counts from the rest of the corpus.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["paper.pdf", "figure.png", "scan.jpg"])
def test_cli_ingest_binary_input_exits_2_with_a_remedy(tmp_path, capsys, name):
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="binary", source_kind="Repository")
    binary = tmp_path / name
    binary.write_bytes(b"%PDF-1.4\n\x89PNG\r\n\x1a\n\xff\xd8\xff\xe0 not text\n")

    rc = cli.main(["ingest", str(binary), "--project", str(tmp_path)])

    out = capsys.readouterr()
    assert rc == 2
    assert "Traceback" not in out.err
    assert "raganything" in out.err
    assert Path(name).suffix in out.err
    # The old behaviour printed a success line with counts from the rest of
    # the corpus. It must be gone.
    assert "Ingested (" not in out.out


def test_cli_ingest_directory_of_binaries_exits_2(tmp_path, capsys):
    """`tesserae ingest <dir>` printed "processed=1 ... nodes=1" where the 1 was
    the DIRECTORY — the same success-for-work-not-done the file guard removed."""
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="binarydir", source_kind="Repository")
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "a.pdf").write_bytes(b"%PDF-1.4\nbinary\n%%EOF\n")
    (papers / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    rc = cli.main(["ingest", str(papers), "--project", str(tmp_path)])

    out = capsys.readouterr()
    assert rc == 2
    assert "Traceback" not in out.err
    assert "a.pdf" in out.err
    assert "Ingested (" not in out.out
