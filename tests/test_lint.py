"""Tests for ``llm_wiki.lint``.

Each check is exercised in isolation by hand-building a minimal
``.llm-wiki/`` workspace under ``tmp_path``. We never depend on
``ProjectWiki.compile()`` here so the tests stay fast and the linter's
contract is verified independently of the rest of the pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_wiki.cli import main as cli_main
from llm_wiki.lint import (
    LintFinding,
    LintReport,
    SEVERITIES,
    WikiLinter,
)
from llm_wiki.project import ProjectWiki


# --------------------------------------------------------------------------- helpers


def _scaffold(tmp_path: Path, *, graph: dict | None = None) -> Path:
    """Create a minimal `.llm-wiki/` layout and return the project root."""
    project = tmp_path / "demo"
    project.mkdir()
    wiki_root = project / ".llm-wiki"
    (wiki_root / "wiki" / "papers").mkdir(parents=True)
    (wiki_root / "wiki" / "concepts").mkdir(parents=True)
    (wiki_root / "wiki" / "repos").mkdir(parents=True)
    (wiki_root / "wiki" / "syntheses").mkdir(parents=True)
    (wiki_root / "wiki" / "entities").mkdir(parents=True)
    (wiki_root / "site").mkdir(parents=True)
    payload = graph or {"nodes": [], "edges": []}
    (wiki_root / "graph.json").write_text(json.dumps(payload), encoding="utf-8")
    return project


def _write_synthesis(
    project_root: Path, slug: str, inputs: list[str], body: str = "# synth\n"
) -> Path:
    path = project_root / ".llm-wiki" / "wiki" / "syntheses" / f"{slug}.md"
    lines = ["---", "synthesis_kind: daily", f"slug: {slug}"]
    if inputs:
        lines.append("inputs:")
        for entry in inputs:
            lines.append(f'  - "{entry}"')
    else:
        lines.append("inputs: []")
    lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _node(node_id: str, type_: str, name: str, **extras) -> dict:
    metadata = extras.pop("metadata", {})
    return {
        "id": node_id,
        "type": type_,
        "name": name,
        "aliases": [],
        "description": "",
        "source_path": extras.pop("source_path", None),
        "metadata": metadata,
    }


# --------------------------------------------------------------------------- per-check tests


def test_orphan_paper_is_flagged(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node("p1", "Paper", "Lonely Paper", metadata={"arxiv_id": "0001"}),
        ],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "ORPHAN_PAPER"]
    assert len(matches) == 1
    assert matches[0].node_id == "p1"
    assert matches[0].severity == "warning"


def test_orphan_paper_with_only_mentioned_in_is_still_flagged(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node("p1", "Paper", "Mention Only", metadata={"arxiv_id": "0002"}),
            _node("s1", "SourceDocument", "Some Source"),
        ],
        "edges": [
            {"source": "s1", "target": "p1", "type": "mentioned_in", "evidence": None, "metadata": {}},
        ],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "ORPHAN_PAPER"]
    assert len(matches) == 1


def test_missing_implemented_in_emits_one_warning(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node("p1", "Paper", "Paper A", metadata={"arxiv_id": "1234.5678"}),
            _node("r1", "Repository", "Repo A", metadata={"arxiv_id": "1234.5678"}),
            # Add a non-mentioned_in edge so the orphan check doesn't fire too.
            _node("c1", "Concept", "Some Concept"),
        ],
        "edges": [
            {"source": "p1", "target": "c1", "type": "uses", "evidence": None, "metadata": {}},
        ],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "MISSING_IMPLEMENTED_IN"]
    assert len(matches) == 1
    assert matches[0].auto_fixable is True


def test_stale_citation_in_wiki_body(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    page = project / ".llm-wiki" / "wiki" / "concepts" / "concept-a.md"
    page.write_text(
        "# Concept A\n\nSee [Paper](papers/missing-paper.md) for details.\n",
        encoding="utf-8",
    )
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "STALE_CITATION"]
    assert len(matches) == 1
    assert matches[0].path == str(page)


def test_dangling_html_link_in_site(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    site_index = project / ".llm-wiki" / "site" / "index.html"
    site_index.write_text(
        '<a href="papers/ghost.html">ghost</a>',
        encoding="utf-8",
    )
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "DANGLING_HTML_LINK"]
    assert len(matches) == 1


def test_drift_graph_to_wiki_and_back(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node("c1", "Concept", "Real Concept", metadata={}),
        ],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    # Wiki page exists for an unrelated concept (reverse drift).
    (project / ".llm-wiki" / "wiki" / "concepts" / "stranger.md").write_text(
        "# Stranger\n", encoding="utf-8"
    )
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "GRAPH_WIKI_DRIFT"]
    # Forward direction: graph "Real Concept" has no wiki page.
    forward = [f for f in matches if f.node_id == "c1"]
    assert len(forward) == 1
    # Reverse direction: stranger.md wiki page has no graph node.
    reverse = [f for f in matches if f.path and f.path.endswith("stranger.md")]
    assert len(reverse) == 1


def test_contradicting_claims_pair_emits_one_info(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            {
                "id": "claim-a",
                "type": "PerformanceClaim",
                "name": "Model X outperforms Model Y on DTU benchmark",
                "aliases": [],
                "description": "Model X outperforms Model Y on DTU benchmark.",
                "source_path": "data/research/paper_a.md",
                "metadata": {},
            },
            {
                "id": "claim-b",
                "type": "PerformanceClaim",
                "name": "Model X is outperformed by Model Y on DTU benchmark",
                "aliases": [],
                "description": "Model X is outperformed by Model Y on DTU benchmark.",
                "source_path": "data/research/paper_b.md",
                "metadata": {},
            },
        ],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "CONTRADICTING_CLAIMS"]
    assert len(matches) == 1
    assert matches[0].severity == "info"


def test_low_title_quality_flagged_as_info(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node(
                "p1",
                "Paper",
                "arXiv:9999.99999",
                metadata={"arxiv_id": "9999.99999", "title_quality": "arxiv_only"},
            ),
            _node("c1", "Concept", "Some Concept"),
        ],
        # Add an edge so this paper isn't orphan-flagged too — keeps the
        # assertion focused.
        "edges": [
            {"source": "p1", "target": "c1", "type": "uses", "evidence": None, "metadata": {}},
        ],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "LOW_TITLE_QUALITY"]
    assert len(matches) == 1
    assert matches[0].severity == "info"


def test_synthesis_ghost_input_is_warning_and_auto_fixable(tmp_path: Path) -> None:
    graph = {"nodes": [_node("real-id", "Concept", "Real")], "edges": []}
    project = _scaffold(tmp_path, graph=graph)
    _write_synthesis(project, "demo", ["real-id", "Concept:ghost-id:abc"])
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "SYNTHESIS_GHOST_INPUT"]
    assert len(matches) == 1
    assert matches[0].auto_fixable is True
    assert matches[0].node_id == "Concept:ghost-id:abc"


def test_suggested_merge_for_two_repositories_with_same_url(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node(
                "repo-a",
                "Repository",
                "Mirror A",
                metadata={"github_repo": "https://github.com/foo/bar"},
            ),
            _node(
                "repo-b",
                "Repository",
                "Mirror B",
                metadata={"github_repo": "https://github.com/foo/bar"},
            ),
            _node("c1", "Concept", "C"),
        ],
        "edges": [
            {"source": "repo-a", "target": "c1", "type": "uses", "evidence": None, "metadata": {}},
            {"source": "repo-b", "target": "c1", "type": "uses", "evidence": None, "metadata": {}},
        ],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "SUGGESTED_MERGE"]
    assert len(matches) == 1


def test_stale_build_history_emits_info(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    history = project / ".llm-wiki" / ".build-history.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    history.write_text(
        json.dumps({"built_at": old_ts, "research_nodes": 1}) + "\n"
        + json.dumps({"built_at": fresh_ts, "research_nodes": 1}) + "\n",
        encoding="utf-8",
    )
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "STALE_BUILD_HISTORY"]
    assert len(matches) == 1


# --------------------------------------------------------------------------- auto-fix + report contracts


def test_fix_trivial_resolves_auto_fixable_findings(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _node("p1", "Paper", "Paper A", metadata={"arxiv_id": "1234.5678"}),
            _node("r1", "Repository", "Repo A", metadata={"arxiv_id": "1234.5678"}),
            _node("real-id", "Concept", "Real"),
        ],
        "edges": [
            {"source": "p1", "target": "real-id", "type": "uses", "evidence": None, "metadata": {}},
            {"source": "r1", "target": "real-id", "type": "uses", "evidence": None, "metadata": {}},
        ],
    }
    project = _scaffold(tmp_path, graph=graph)
    _write_synthesis(project, "demo", ["real-id", "Concept:ghost-id:abc"])

    first = WikiLinter(project).run(fix_trivial=True)
    auto_codes = {f.code for f in first.findings if f.auto_fixable}
    assert "MISSING_IMPLEMENTED_IN" in auto_codes
    assert "SYNTHESIS_GHOST_INPUT" in auto_codes

    second = WikiLinter(project).run()
    second_codes = {f.code for f in second.findings}
    assert "MISSING_IMPLEMENTED_IN" not in second_codes
    assert "SYNTHESIS_GHOST_INPUT" not in second_codes


def test_report_round_trips_through_json_and_markdown(tmp_path: Path) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()

    md = report.to_markdown()
    assert md.endswith("\n")
    assert "ORPHAN_PAPER" in md
    payload = json.loads(report.to_json())
    assert any(f["code"] == "ORPHAN_PAPER" for f in payload["findings"])
    assert payload["by_severity"]["warning"] >= 1
    # Byte stability: a second run with the same inputs produces the same JSON.
    assert WikiLinter(project).run().to_json() == report.to_json()


def test_findings_sort_deterministically() -> None:
    findings = [
        LintFinding(severity="warning", code="B_CODE", message="b"),
        LintFinding(severity="info", code="A_CODE", message="a"),
        LintFinding(severity="error", code="A_CODE", message="z"),
    ]
    findings.sort(key=LintFinding.sort_key)
    severities = [f.severity for f in findings]
    # info first, error last.
    assert severities == ["info", "warning", "error"]


def test_severities_constant_is_three_levels() -> None:
    assert SEVERITIES == ("info", "warning", "error")


# --------------------------------------------------------------------------- ProjectWiki + CLI smoke


def test_project_wiki_lint_returns_report(tmp_path: Path) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    # Need a config for ProjectWiki.load.
    ProjectWiki.init(project, name="demo_lint")
    # The init call wrote a fresh empty graph — restore ours.
    (project / ".llm-wiki" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    wiki = ProjectWiki.load(project)
    report = wiki.lint()
    assert isinstance(report, LintReport)
    assert any(f.code == "ORPHAN_PAPER" for f in report.findings)
    assert (project / ".llm-wiki" / "lint-report.md").exists()
    assert (project / ".llm-wiki" / "lint-report.json").exists()


def test_cli_lint_returns_warning_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    ProjectWiki.init(project, name="demo_lint")
    (project / ".llm-wiki" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    code = cli_main(["project", "lint", "--project", str(project)])
    assert code == 1


def test_cli_lint_clean_exits_zero(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    ProjectWiki.init(project, name="demo_clean")
    # Replace whatever init wrote with an empty graph.
    (project / ".llm-wiki" / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    code = cli_main(["project", "lint", "--project", str(project)])
    assert code == 0


def test_cli_lint_json_flag_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    ProjectWiki.init(project, name="demo_json")
    (project / ".llm-wiki" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cli_main(["project", "lint", "--project", str(project), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert any(f["code"] == "ORPHAN_PAPER" for f in payload["findings"])


def test_cli_lint_severity_error_only_fails_on_errors(tmp_path: Path) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    ProjectWiki.init(project, name="demo_sev")
    (project / ".llm-wiki" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    code = cli_main(["project", "lint", "--project", str(project), "--severity", "error"])
    assert code == 0
