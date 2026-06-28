"""`tesserae compile --extractor` wires the LLM extractor into the compile pipeline."""

from __future__ import annotations


def test_compile_parser_accepts_extractor_flags():
    from tesserae.cli import _build_compile_parser

    args = _build_compile_parser().parse_args(
        ["--extractor", "selective-claude", "--claude-include", "docs/**/*.md", "--claude-limit", "5"]
    )
    assert args.extractor == "selective-claude"
    assert args.claude_include == ["docs/**/*.md"] and args.claude_limit == 5


def test_build_doc_extractor_selects_backend():
    from tesserae.cli import _build_compile_parser, _build_doc_extractor
    from tesserae.llm_extractor import ClaudeCLIResearchExtractor
    from tesserae.research_graph import ResearchGraphExtractor
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    p = _build_compile_parser()
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "deterministic"])), ResearchGraphExtractor)
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "claude-cli"])), ClaudeCLIResearchExtractor)
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "selective-claude"])), SelectiveClaudeResearchExtractor)


def test_selective_extract_text_routes_by_absolute_path_and_limit():
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    calls = []

    class _Rec:
        def __init__(self, tag):
            self.tag = tag

        def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
            calls.append((self.tag, source_path))
            return ResearchGraph(nodes=[], edges=[])

        def extract_file(self, path, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=_Rec("det"), claude=_Rec("claude"),
        include_patterns=["*docs/superpowers*"], claude_limit=1,
    )
    sel.extract_text("a", "/Users/x/Agented/docs/superpowers/spec.md")   # matches -> claude
    sel.extract_text("b", "/Users/x/Agented/README.md")                  # no match -> det
    sel.extract_text("c", "/Users/x/Agented/docs/superpowers/spec2.md")  # limit hit -> det
    assert calls == [
        ("claude", "/Users/x/Agented/docs/superpowers/spec.md"),
        ("det", "/Users/x/Agented/README.md"),
        ("det", "/Users/x/Agented/docs/superpowers/spec2.md"),
    ]


def test_compile_paths_threads_extractor_into_ingest(monkeypatch):
    from tesserae import cli
    from tesserae.llm_extractor import ClaudeCLIResearchExtractor

    captured = {}

    class _FakeWiki:
        def ingest(self, inputs, **kwargs):
            captured.update(kwargs)
            return {"processed_files": 1, "skipped_files": 0, "node_count": 0,
                    "edge_count": 0, "graph_path": "/tmp/x.json"}

    monkeypatch.setattr("tesserae.cli.ProjectWiki.load", lambda p: _FakeWiki())
    args = cli._build_compile_parser().parse_args(["--extractor", "claude-cli", "doc.md"])
    assert cli._handle_compile(args) == 0  # dispatches to the paths-ingest branch
    assert isinstance(captured.get("doc_extractor"), ClaudeCLIResearchExtractor)


def test_llm_payload_drops_bad_edges_instead_of_aborting():
    """One hallucinated edge type / dangling endpoint must not void a doc's
    extraction (else a single bad doc aborts a whole multi-doc compile)."""
    from tesserae.llm_extractor import graph_from_llm_payload

    payload = {
        "nodes": [{"name": "A", "type": "Concept"}, {"name": "B", "type": "Concept"}],
        "edges": [
            {"type": "used_by", "source": "A", "target": "B"},   # not in the vocab -> drop
            {"type": "uses", "source": "A", "target": "B"},      # valid -> keep
            {"type": "is_a", "source": "A", "target": "GHOST"},  # dangling endpoint -> drop
        ],
    }
    g = graph_from_llm_payload(payload, source_path="x.md", source_kind="SourceDocument")
    kept = {e.type for e in g.edges}
    assert "uses" in kept and "used_by" not in kept and "is_a" not in kept


def test_selective_extract_text_falls_back_when_claude_fails():
    """A claude timeout/error on one doc must fall back to deterministic, not
    raise (else one slow doc aborts the whole compile)."""
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    calls = []

    class _Det:
        def extract_text(self, t, sp=None, sk="SourceDocument"):
            calls.append("det")
            return ResearchGraph(nodes=[], edges=[])

        def extract_file(self, p, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    class _Claude:
        def extract_text(self, t, sp=None, sk="SourceDocument"):
            raise TimeoutError("claude slow")

        def extract_file(self, p, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=_Det(), claude=_Claude(), include_patterns=["*docs*"], claude_limit=5
    )
    sel.extract_text("x", "/a/docs/spec.md")  # routed to claude -> raises -> deterministic
    assert calls == ["det"]  # fell back, did not propagate


def test_claude_extract_text_retries_transient_bad_generation():
    """A transient invalid generation (out-of-vocab node type) is retried, not fatal."""
    from tesserae.llm_extractor import ClaudeCLIResearchExtractor

    calls = []
    bad = '{"nodes":[{"name":"X","type":"Vulnerability"}],"edges":[]}'   # not in vocab
    good = '{"nodes":[{"name":"X","type":"Concept"}],"edges":[]}'

    def runner(prompt, cd, model, timeout):
        calls.append(1)
        return bad if len(calls) == 1 else good

    ex = ClaudeCLIResearchExtractor(model="sonnet", timeout=5)
    ex.config_dirs = [None]
    ex.runner = runner
    g = ex.extract_text("doc text", "x.md", "SourceDocument")
    assert len(calls) == 2          # first bad -> retried once -> good
    assert len(g.nodes) >= 1


def test_claude_extract_text_gives_up_after_retries():
    """A persistently invalid generation raises after the bounded retries."""
    import pytest

    from tesserae.llm_extractor import (
        ClaudeCLIResearchExtractor, GraphJSONValidationError, _VALIDATION_RETRIES,
    )

    calls = []

    def runner(prompt, cd, model, timeout):
        calls.append(1)
        return '{"nodes":[{"name":"X","type":"Vulnerability"}],"edges":[]}'

    ex = ClaudeCLIResearchExtractor(model="sonnet", timeout=5)
    ex.config_dirs = [None]
    ex.runner = runner
    with pytest.raises(GraphJSONValidationError):
        ex.extract_text("t", "x.md", "SourceDocument")
    assert len(calls) == _VALIDATION_RETRIES + 1   # initial + retries, then gives up
