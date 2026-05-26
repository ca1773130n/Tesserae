"""Task 7: end-to-end feedback loop with mocked LLM + routing isolation."""

from __future__ import annotations

from pathlib import Path

from tesserae.extraction_feedback import FeedbackEvent, append_events
from tesserae.extraction_guidance import MIN_EVENTS
from tesserae.guidance_markdown import parse_guidance, slice_guidance
from tesserae.llm_extractor import build_research_extraction_prompt
from tesserae.project import ProjectWiki


class _ScriptedClient:
    """Returns a different phrasing depending on the cluster's node_type."""

    def complete_json(self, *, system, user, schema_name, cache_key=None):
        if "node_type=SessionDecision" in user:
            return {"bullet": "Phrase decisions as accepted choices, not next steps."}
        return {"bullet": "Prefer concise claim descriptions; omit broad framing."}


def _doc_cluster(n: int):
    return [
        FeedbackEvent(
            source="vault_override", target_extractor="doc_graph",
            node_type="Claim", field="description", action="replace",
            node_id=f"Claim:{i}", source_path="docs/a.md",
            before_value=f"verbose framing {i}", after_value=f"concise {i}",
            negative_value=f"verbose framing {i}",
        )
        for i in range(n)
    ]


def _session_cluster(n: int):
    return [
        FeedbackEvent(
            source="vault_override", target_extractor="session_findings",
            node_type="SessionDecision", field="body", action="replace",
            node_id=f"SessionDecision:{i}", source_path="sess/1",
            before_value=f"next step {i}", after_value=f"accepted choice {i}",
            negative_value=f"next step {i}",
        )
        for i in range(n)
    ]


def test_feedback_loop_end_to_end_and_routing_isolation(tmp_path: Path):
    wiki = ProjectWiki(tmp_path)
    wiki.root.mkdir(parents=True, exist_ok=True)
    append_events(wiki.paths.extraction_feedback, _doc_cluster(MIN_EVENTS))
    append_events(wiki.paths.extraction_feedback, _session_cluster(MIN_EVENTS))

    # evolve → guidance markdown with both bullets
    summary = wiki.evolve(json_client=_ScriptedClient())
    assert summary["bullets"] == 2

    md = wiki.paths.extraction_guidance.read_text(encoding="utf-8")
    assert "concise claim descriptions" in md
    assert "accepted choices" in md

    parsed = parse_guidance(md)

    # doc_graph/Claim slice contains only the Claim bullet — routing isolation.
    doc_slice = slice_guidance(parsed, extractor="doc_graph", node_types={"Claim"})
    assert len(doc_slice) == 1
    assert "concise claim descriptions" in doc_slice[0].text
    assert all("accepted choices" not in b.text for b in doc_slice)

    # The doc extractor prompt carries the doc bullet, not the session bullet.
    doc_text = "\n".join(f"- {b.text}" for b in doc_slice)
    prompt = build_research_extraction_prompt("body", "docs/a.md", "Paper",
                                              guidance=doc_text)
    assert "concise claim descriptions" in prompt
    assert "accepted choices" not in prompt

    # session_findings slice has the session bullet, not the doc bullet.
    sess_slice = slice_guidance(parsed, extractor="session_findings",
                                node_types={"SessionDecision"})
    assert len(sess_slice) == 1
    assert "accepted choices" in sess_slice[0].text


def test_doc_prompt_byte_identical_when_flag_off():
    # Flag-off regression: empty guidance == no-guidance prompt, byte-for-byte.
    base = build_research_extraction_prompt("body", "docs/a.md", "Paper")
    assert build_research_extraction_prompt("body", "docs/a.md", "Paper", guidance="") == base
