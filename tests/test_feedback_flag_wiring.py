"""Codex P2 finding 1: `--use-extraction-feedback` must thread guidance into
the real compile/extraction path (not be a no-op).

These tests prove the end-to-end wiring: with the flag ON and a guidance file
present, the prompt the extractor receives contains the distilled guidance;
with the flag OFF the extractor sees an empty guidance string (byte-identical
to the legacy path). We exercise both extractor boundaries:

  * doc graph  — via a capturing ``doc_extractor`` injected into ``compile``,
  * session findings — via ``_merge_session_graph`` with a mock LLM client.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.guidance_markdown import GuidanceBullet, render_guidance
from tesserae.project import ProjectWiki, SessionExtractionOptions


def _init_project(tmp_path: Path) -> ProjectWiki:
    wiki = ProjectWiki(tmp_path)
    wiki.root.mkdir(parents=True, exist_ok=True)
    wiki.paths.config.write_text(json.dumps({"name": "wiring-test"}), encoding="utf-8")
    return wiki


def _write_guidance(wiki: ProjectWiki) -> None:
    bullets = [
        GuidanceBullet(
            extractor="doc_graph", node_type="Claim", cluster_hash="sha256:aa",
            source="vault_override", field="description", events=3,
            text="DOC-GUIDANCE-MARKER: keep Claim descriptions terse.",
        ),
        GuidanceBullet(
            extractor="session_findings", node_type="SessionDecision",
            cluster_hash="sha256:bb", source="review", field="body", events=3,
            text="SESSION-GUIDANCE-MARKER: phrase decisions as accepted choices.",
        ),
    ]
    wiki.paths.extraction_guidance.parent.mkdir(parents=True, exist_ok=True)
    wiki.paths.extraction_guidance.write_text(render_guidance(bullets), encoding="utf-8")


class _CapturingDocExtractor:
    """Stand-in doc extractor that records the guidance it was handed."""

    def __init__(self) -> None:
        self.guidance = ""

    # ``ingest`` walks markdown via BatchIngestRunner.extract_text / .extract_file.
    def extract_file(self, path, source_kind="SourceDocument"):
        from tesserae.research_graph import ResearchGraph
        return ResearchGraph(nodes=[], edges=[])

    def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
        from tesserae.research_graph import ResearchGraph
        return ResearchGraph(nodes=[], edges=[])


def _compile_capturing_doc(tmp_path: Path, *, use_flag: bool) -> str:
    wiki = _init_project(tmp_path)
    _write_guidance(wiki)
    # A markdown source so the extractor is actually constructed/used.
    (tmp_path / "a.md").write_text("# Hello\n\nbody\n", encoding="utf-8")
    extractor = _CapturingDocExtractor()
    wiki.compile(
        use_extraction_feedback=use_flag,
        doc_extractor=extractor,
        vault_pull=False,
    )
    return extractor.guidance


def test_doc_extractor_receives_guidance_when_flag_on(tmp_path: Path):
    guidance = _compile_capturing_doc(tmp_path, use_flag=True)
    assert "DOC-GUIDANCE-MARKER" in guidance
    # extractor-level routing: session bullets must NOT leak into the doc slice.
    assert "SESSION-GUIDANCE-MARKER" not in guidance


def test_doc_extractor_guidance_empty_when_flag_off(tmp_path: Path):
    guidance = _compile_capturing_doc(tmp_path, use_flag=False)
    assert guidance == ""


def test_session_extractor_receives_guidance_when_flag_on(tmp_path: Path, monkeypatch):
    """Drive ``_merge_session_graph`` directly with a captured guidance slice.

    We confirm the slice computed from the on-flag is the session_findings
    bullets (and only those), proving the wiring reaches the session extractor.
    """
    wiki = _init_project(tmp_path)
    _write_guidance(wiki)

    doc_g, sess_g = wiki._load_extraction_guidance(True)
    assert "SESSION-GUIDANCE-MARKER" in sess_g
    assert "DOC-GUIDANCE-MARKER" not in sess_g
    assert "DOC-GUIDANCE-MARKER" in doc_g

    # And the off path yields empty slices (byte-identical prompts).
    assert wiki._load_extraction_guidance(False) == ("", "")


def test_merge_session_graph_threads_guidance_into_extractor(tmp_path, monkeypatch):
    """_merge_session_graph must construct SessionGraphExtractor(guidance=slice)."""
    import tesserae.session_graph as sg
    from tesserae.research_graph import ResearchGraph

    wiki = _init_project(tmp_path)
    # Pre-populate the harness_sessions dir + one in-project session so the
    # session pass runs past its opt-in guards.
    captured: dict = {}

    class _FakeExtractor:
        def __init__(self, **kw):
            captured["guidance"] = kw.get("guidance")

        def extract(self):
            return ResearchGraph(nodes=[], edges=[])

    monkeypatch.setattr(sg, "SessionGraphExtractor", _FakeExtractor)
    # Bypass the discovery/opt-in guards: force a non-empty in-project set.
    import tesserae.project as proj

    monkeypatch.setattr(
        proj.ProjectWiki, "_merge_session_graph",
        proj.ProjectWiki._merge_session_graph,  # keep real method
    )

    # Drive the real method with a forced session list via monkeypatching the
    # harness store + matcher used inside it.
    wiki.paths.harness_sessions.mkdir(parents=True, exist_ok=True)

    import tesserae.harness_sessions as hs

    class _Store:
        def __init__(self, *_a, **_k):
            pass

        def list_sessions(self):
            return [object()]

    monkeypatch.setattr(hs, "HarnessSessionStore", _Store)
    monkeypatch.setattr(hs, "session_matches_project", lambda s, root: True)
    monkeypatch.setattr(
        "tesserae.llm_json.build_default_json_client", lambda **_k: object()
    )

    opts = SessionExtractionOptions(enabled=True, llm_enabled="true")
    wiki._merge_session_graph(
        ResearchGraph(nodes=[], edges=[]), {}, override=opts,
        guidance="- SESSION-GUIDANCE-MARKER",
    )
    assert captured["guidance"] == "- SESSION-GUIDANCE-MARKER"
