"""MCP `query` (raw retrieval, no LLM) and `doctor_run` (read-only checks).

Contract: `query` mirrors `tesserae query` — deterministic BM25 hits for
backend='wiki' (never an LLM call), raganything pass-through for the optional
backend. `doctor_run` returns fresh findings as JSON and is byte-level
read-only: no fixes, no report artifacts.
"""
import hashlib
import json
from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer


def _seed_project(project: Path) -> None:
    """Minimal initialized project with one searchable wiki page."""
    from tesserae.project import ProjectWiki

    project.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text("# demo", encoding="utf-8")
    ProjectWiki.init(project, name="demo", sources=["README.md"])
    wiki_dir = project / ".tesserae" / "wiki" / "concepts"
    site = project / ".tesserae" / "site"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "hybrid-retriever.md").write_text(
        "---\ntitle: Hybrid retriever\n---\n# Hybrid retriever\nBM25 + embeddings.\n",
        encoding="utf-8",
    )
    (site / "search-index.json").write_text(
        json.dumps(
            [
                {
                    "id": "Concept:hybrid-retriever",
                    "kind": "concepts",
                    "title": "Hybrid retriever",
                    "summary": "BM25 + embeddings retriever.",
                    "tokens": ["hybrid", "retriever", "bm25", "embeddings"],
                    "len": 4,
                    "href": "concepts/hybrid-retriever.html",
                    "source_path": "",
                    "created_ts": 1_700_000_000,
                },
                {
                    "id": "Paper:some-paper",
                    "kind": "papers",
                    "title": "Some retriever paper",
                    "summary": "A paper about retriever design.",
                    "tokens": ["retriever", "paper", "design"],
                    "len": 3,
                    "href": "papers/some-paper.html",
                    "source_path": "",
                    "created_ts": 1_700_000_000,
                },
            ]
        ),
        encoding="utf-8",
    )


def _tree_checksums(root: Path) -> dict:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_mcp_lists_query_and_doctor_run_tools(tmp_path):
    tools = LLMWikiMCPServer(registry_path=tmp_path / "registry.json").list_tools()
    names = {t["name"] for t in tools}
    assert "query" in names
    assert "doctor_run" in names


def test_query_wiki_returns_ranked_hits_without_llm(tmp_path, monkeypatch):
    _seed_project(tmp_path / "demo")
    # An LLM call would be a contract violation — make one explode.
    monkeypatch.setattr(
        "tesserae.llm_json.build_rotating_client",
        lambda *a, **k: pytest.fail("query must never build an LLM client"),
    )
    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(tmp_path / "demo"), name="demo")
    result = server.call_tool("query", {"question": "hybrid retriever", "project": "demo"})
    assert result["backend"] == "wiki"
    assert result["used_llm"] is False
    assert result["answer"] is None
    assert result["hits"] and result["hits"][0]["title"] == "Hybrid retriever"


def test_query_honors_kind_filter_and_top_k(tmp_path):
    _seed_project(tmp_path / "demo")
    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(tmp_path / "demo"), name="demo")
    papers_only = server.call_tool(
        "query", {"question": "retriever", "kind": "papers", "project": "demo"}
    )
    assert {h["kind"] for h in papers_only["hits"]} == {"papers"}
    capped = server.call_tool("query", {"question": "retriever", "top_k": 1, "project": "demo"})
    assert len(capped["hits"]) == 1


def test_query_blank_question_rejected(tmp_path):
    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    with pytest.raises(ValueError, match="question is required"):
        server.call_tool("query", {"question": "   "})


def test_query_raganything_passthrough_envelope(tmp_path, monkeypatch):
    """Explicit raganything backend goes through ask_project's envelope."""
    _seed_project(tmp_path / "demo")
    monkeypatch.setattr(
        "tesserae.raganything_query.query", lambda q, backend_config=None: "rag says hi"
    )
    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(tmp_path / "demo"), name="demo")
    result = server.call_tool(
        "query", {"question": "anything", "backend": "raganything", "project": "demo"}
    )
    assert result["backend"] == "raganything"
    assert result["answer"] == "rag says hi"


def test_doctor_run_returns_findings_and_writes_nothing(tmp_path, monkeypatch):
    from tesserae import doctor

    # Pin machine-environment probes (same convention as tests/test_doctor.py).
    monkeypatch.setattr(doctor, "_llm_login_status", lambda: {"claude": True, "codex": None})
    monkeypatch.setattr(doctor, "_embedding_probe", lambda: {"backend": "pinned", "semantic": True})
    monkeypatch.setattr(doctor, "_environment_probe", lambda root: "pinned")

    project = tmp_path / "demo"
    _seed_project(project)
    before = _tree_checksums(project)
    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")
    before = _tree_checksums(project)  # re-snapshot after registration side effects
    report = server.call_tool("doctor_run", {"project": "demo"})
    assert isinstance(report.get("findings"), list) and report["findings"]
    assert report["exit_code"] in (0, 1, 2)
    assert {f["check_id"] for f in report["findings"]} >= {"project_initialized", "graph_parse"}
    # Byte-level read-only: no doctor-report artifacts, nothing mutated.
    assert not (project / ".tesserae" / "doctor-report.md").exists()
    assert _tree_checksums(project) == before
