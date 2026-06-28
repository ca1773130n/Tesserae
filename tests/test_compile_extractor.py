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
