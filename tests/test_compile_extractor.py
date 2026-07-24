"""`tesserae compile --extractor` wires the LLM extractor into the compile pipeline."""

from __future__ import annotations


def test_compile_extractor_default_is_llm():
    from tesserae.cli import _build_compile_parser

    assert _build_compile_parser().parse_args([]).extractor == "llm"  # LLM by default


def test_compile_parser_accepts_extractor_flags():
    from tesserae.cli import _build_compile_parser

    args = _build_compile_parser().parse_args(
        ["--extractor", "selective-llm", "--llm-include", "docs/**/*.md", "--llm-limit", "5", "--llm-provider", "codex"]
    )
    assert args.extractor == "selective-llm"
    assert args.llm_include == ["docs/**/*.md"] and args.llm_limit == 5 and args.llm_provider == "codex"


def test_build_doc_extractor_selects_backend(monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor
    from tesserae.llm_extractor import LLMResearchExtractor
    from tesserae.research_graph import ResearchGraphExtractor
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    p = _build_compile_parser()
    # deterministic -> structural baseline
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "deterministic"])), ResearchGraphExtractor)

    # llm with NO backend available -> graceful fallback to deterministic
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: None)
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "llm"])), ResearchGraphExtractor)

    # llm WITH a backend -> LLMResearchExtractor wrapped in the per-doc fallback
    # router (include=["*"] routes every doc to the LLM; a failure falls back).
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: object())
    plain = _build_doc_extractor(p.parse_args(["--extractor", "llm"]))
    assert isinstance(plain, SelectiveClaudeResearchExtractor)
    assert isinstance(plain.claude, LLMResearchExtractor) and plain.include_patterns == ["*"]
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "selective-llm"])), SelectiveClaudeResearchExtractor)
    # deprecated alias still works
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "claude-cli"])), SelectiveClaudeResearchExtractor)


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
    import tesserae.llm_json as lj
    from tesserae import cli
    from tesserae.llm_extractor import LLMResearchExtractor
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    captured = {}

    class _FakeWiki:
        def config(self):
            return {}

        def ingest(self, inputs, **kwargs):
            captured.update(kwargs)
            return {"processed_files": 1, "skipped_files": 0, "node_count": 0,
                    "edge_count": 0, "graph_path": "/tmp/x.json"}

    monkeypatch.setattr("tesserae.cli.ProjectWiki.load", lambda p: _FakeWiki())
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: object())  # a backend is available
    args = cli._build_compile_parser().parse_args(["--extractor", "llm", "doc.md"])
    assert cli._handle_compile(args) == 0  # dispatches to the paths-ingest branch
    doc_ex = captured.get("doc_extractor")
    assert isinstance(doc_ex, SelectiveClaudeResearchExtractor)  # per-doc fallback router
    assert isinstance(doc_ex.claude, LLMResearchExtractor)


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


def test_llm_research_extractor_drives_the_client():
    """The provider-agnostic extractor calls the LLMJsonClient (any backend),
    passes a content cache_key, and validates the returned payload into a graph."""
    from tesserae.llm_extractor import LLMResearchExtractor

    seen = {}

    class _FakeClient:
        def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
            seen["schema"] = schema_name
            seen["cache_key"] = cache_key
            return {"nodes": [{"name": "Gaussian Splatting", "type": "Concept"}], "edges": []}

    ex = LLMResearchExtractor(_FakeClient())
    g = ex.extract_text("Gaussian splatting renders radiance fields.", "x.md", "SourceDocument")
    assert any(n.name == "Gaussian Splatting" for n in g.nodes)
    assert seen["schema"] == "research-graph-v1" and seen["cache_key"]  # content-keyed


def test_llm_extractor_falls_back_per_doc_on_backend_failure(monkeypatch):
    """A backend failure on ONE doc (complete_json -> None: auth/timeout/parse)
    must fall back to deterministic for that doc, NOT abort the whole compile."""
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor

    class _FailClient:
        def complete_json(self, **k):
            return None  # auth expiry / timeout / unparseable

    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: _FailClient())
    ex = _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]))
    g = ex.extract_text("a document body", "/proj/doc.md", "SourceDocument")  # must NOT raise
    assert g is not None  # fell back to the deterministic baseline


def test_doc_extractor_honors_project_provider_and_disables_timeout(monkeypatch):
    """_build_doc_extractor threads the PROJECT config's llm_provider and asks for
    no timeout (extraction runs to completion)."""
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor

    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TESSERAE_EXTRACT_TIMEOUT", raising=False)
    seen = {}

    def _fake(**k):
        seen.update(k)
        return object()

    monkeypatch.setattr(lj, "build_default_json_client", _fake)
    _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]),
                         cfg={"llm_provider": "codex"})
    assert seen["provider"] == "codex"   # project config wins, not the global default
    assert seen["timeout"] is None       # no default cutoff


def test_doc_extractor_opt_in_timeout_via_env(monkeypatch):
    """TESSERAE_EXTRACT_TIMEOUT bounds each extraction call so a wedged codex child
    is killed and that doc falls back to deterministic — instead of hanging the compile.
    Unset / non-positive / garbage all mean 'no cutoff' (None), byte-identical to prior."""
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor, _extract_timeout

    # helper parses the env directly
    monkeypatch.delenv("TESSERAE_EXTRACT_TIMEOUT", raising=False)
    assert _extract_timeout() is None
    monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", "600")
    assert _extract_timeout() == 600.0
    for bad in ("0", "-5", "", "abc"):
        monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", bad)
        assert _extract_timeout() is None, bad

    # and it is threaded into the client the compile builds
    seen = {}
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: seen.update(k) or object())
    monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", "600")
    _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]))
    assert seen["timeout"] == 600.0
