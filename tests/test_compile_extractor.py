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
