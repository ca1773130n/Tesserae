import tesserae.cli as cli


def test_ingest_is_a_top_level_command():
    assert "ingest" in cli._NEW_DISPATCH


def test_ingest_parser_accepts_inputs_and_flags():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md", "https://x.com", "--exact", "--project", "/tmp/p"])
    assert args.inputs == ["a.md", "https://x.com"]
    assert args.exact is True
    assert args.project == "/tmp/p"
    assert args._handler == "_handle_ingest_docs"


def test_ingest_defaults_to_fast_path():
    parser = cli._build_ingest_parser()
    args = parser.parse_args(["a.md"])
    assert args.exact is False  # fast path by default; --exact opts into full recompile


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
    rc = cli.main(["ingest", "x.md", "--project", str(tmp_path), "--exact"])
    assert rc == 0
    assert captured["inputs"] == ["x.md"]
    assert captured["kw"]["exact"] is True


class _StubWiki:
    project_root = "."
    @staticmethod
    def load(project):
        return _StubWiki()
