"""Task 6: guidance injection into the extractor prompts (flag-gated, byte-identical off)."""

from __future__ import annotations


def test_doc_prompt_unchanged_without_guidance():
    from tesserae.llm_extractor import build_research_extraction_prompt
    base = build_research_extraction_prompt("text", "a.md", "Paper")
    assert build_research_extraction_prompt("text", "a.md", "Paper", guidance="") == base


def test_doc_prompt_includes_guidance_block_when_present():
    from tesserae.llm_extractor import build_research_extraction_prompt
    p = build_research_extraction_prompt("text", "a.md", "Paper",
                                         guidance="- Be concise.")
    assert "Project-specific extraction guidance" in p and "Be concise." in p


def test_session_extractor_system_prompt_byte_identical_without_guidance():
    # When guidance is empty the session extractor must send the unchanged
    # system prompt (flag-off contract).
    import tesserae.session_graph_llm as sgl

    captured = {}

    class _Client:
        def complete_json(self, *, system, user, schema_name, cache_key=None):
            captured["system"] = system
            return {"findings": []}

    sgl.extract_with_llm(
        session=type("S", (), {"id": "s1"})(),
        transcript_turns=[{"role": "user", "content": "hi", "turn_id": 1}],
        doc_id_context=[],
        client=_Client(),
        guidance="",
    )
    assert captured["system"] == sgl._PROMPT_SYSTEM


def test_session_extractor_system_prompt_includes_guidance_when_present():
    import tesserae.session_graph_llm as sgl

    captured = {}

    class _Client:
        def complete_json(self, *, system, user, schema_name, cache_key=None):
            captured["system"] = system
            return {"findings": []}

    sgl.extract_with_llm(
        session=type("S", (), {"id": "s1"})(),
        transcript_turns=[{"role": "user", "content": "hi", "turn_id": 1}],
        doc_id_context=[],
        client=_Client(),
        guidance="- Phrase decisions as accepted choices.",
    )
    assert "Project-specific extraction guidance" in captured["system"]
    assert "accepted choices" in captured["system"]
