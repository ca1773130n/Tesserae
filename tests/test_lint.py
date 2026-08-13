"""Tests for ``tesserae.lint``.

Each check is exercised in isolation by hand-building a minimal
``.tesserae/`` workspace under ``tmp_path``. We never depend on
``ProjectWiki.compile()`` here so the tests stay fast and the linter's
contract is verified independently of the rest of the pipeline.
"""

from __future__ import annotations

import json
import subprocess as _sp
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tesserae.cli import main as cli_main
from tesserae.lint import (
    LintFinding,
    LintReport,
    SEVERITIES,
    WikiLinter,
    _ClaimCandidate,
    _iter_claim_candidates,
    _sample_claims,
)
from tesserae.project import ProjectWiki


# --------------------------------------------------------------------------- helpers


def _scaffold(tmp_path: Path, *, graph: dict | None = None) -> Path:
    """Create a minimal `.tesserae/` layout and return the project root."""
    project = tmp_path / "demo"
    project.mkdir()
    wiki_root = project / ".tesserae"
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
    path = project_root / ".tesserae" / "wiki" / "syntheses" / f"{slug}.md"
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
    page = project / ".tesserae" / "wiki" / "concepts" / "concept-a.md"
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
    site_index = project / ".tesserae" / "site" / "index.html"
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
    (project / ".tesserae" / "wiki" / "concepts" / "stranger.md").write_text(
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


def test_drift_covers_community_summary_nodes(tmp_path: Path) -> None:
    """Descent PR4: ``CommunitySummary`` is part of the lint kind table, so a
    minted community node without its ``wiki/communities/`` page is drift."""
    graph = {
        "nodes": [
            _node("CommunitySummary:abc123", "CommunitySummary", "Orphan Cluster"),
        ],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "GRAPH_WIKI_DRIFT"]
    assert any(f.node_id == "CommunitySummary:abc123" for f in matches)


def test_lint_kind_table_mirrors_wiki_projector() -> None:
    """The lint drift table must cover every public kind the projector writes
    (the missing ``CommunitySummary`` entry was the latent wiki-drift gap)."""
    from tesserae.lint import _KIND_FOR_TYPE as lint_kinds
    from tesserae.wiki_projector import _KIND_FOR_TYPE as projector_kinds

    for node_type, kind in sorted(projector_kinds.items(), key=lambda kv: kv[0].value):
        assert lint_kinds.get(node_type.value) == kind, (
            f"lint._KIND_FOR_TYPE is missing or mismapping {node_type.value!r}"
        )


def _contradicting_pair_graph(extra_edges: list | None = None) -> dict:
    return {
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
        "edges": list(extra_edges or []),
    }


def test_contradicting_claims_unresolved_pair_emits_one_warning(tmp_path: Path) -> None:
    # KB-04: an unresolved contradiction (no resolved_by edge) is now RAISED
    # from info to warning.
    project = _scaffold(tmp_path, graph=_contradicting_pair_graph())
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "CONTRADICTING_CLAIMS"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"


def test_contradicting_claims_resolved_pair_demoted_to_info(tmp_path: Path) -> None:
    # KB-04: a resolved_by edge between the pair demotes severity back to info.
    graph = _contradicting_pair_graph(
        extra_edges=[{"source": "claim-b", "target": "claim-a", "type": "resolved_by"}]
    )
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()
    matches = [f for f in report.findings if f.code == "CONTRADICTING_CLAIMS"]
    assert len(matches) == 1
    assert matches[0].severity == "info"
    assert "resolved by" in matches[0].message.lower()


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
    history = project / ".tesserae" / ".build-history.jsonl"
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
    (project / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    wiki = ProjectWiki.load(project)
    report = wiki.lint()
    assert isinstance(report, LintReport)
    assert any(f.code == "ORPHAN_PAPER" for f in report.findings)
    assert (project / ".tesserae" / "lint-report.md").exists()
    assert (project / ".tesserae" / "lint-report.json").exists()


def test_cli_lint_returns_warning_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    ProjectWiki.init(project, name="demo_lint")
    (project / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    code = cli_main(["lint", "--project", str(project)])
    assert code == 1


def test_cli_lint_clean_exits_zero(tmp_path: Path) -> None:
    project = _scaffold(tmp_path)
    ProjectWiki.init(project, name="demo_clean")
    # Replace whatever init wrote with an empty graph.
    (project / ".tesserae" / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    code = cli_main(["lint", "--project", str(project)])
    assert code == 0


def test_cli_lint_json_flag_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    graph = {
        "nodes": [_node("p1", "Paper", "Lonely", metadata={"arxiv_id": "1"})],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    ProjectWiki.init(project, name="demo_json")
    (project / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cli_main(["lint", "--project", str(project), "--json"])
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
    (project / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    code = cli_main(["lint", "--project", str(project), "--severity", "error"])
    assert code == 0


# --------------------------------------------------------------------------- compile-tail lint


def _seed_compile_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectWiki:
    """A real, compilable project (one paper doc under data/).

    Pins the LLM-backed ``community_summaries`` pass off — byte-idempotence is
    a guarantee of the DETERMINISTIC compile (same guard as
    ``tests/test_idempotence.py``).
    """
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "false")
    project = tmp_path / "proj"
    (project / "data").mkdir(parents=True)
    (project / "data" / "a.md").write_text(
        "---\ntype: paper\n---\n# Graph Neural Networks\n\nbody a\n", encoding="utf-8"
    )
    return ProjectWiki.init(project, name="lint_tail")


def test_compile_runs_lint_at_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every compile refreshes lint-report.md/json and reports lint counts."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    result = wiki.compile()
    wiki_root = wiki.project_root / ".tesserae"
    assert (wiki_root / "lint-report.md").exists()
    assert (wiki_root / "lint-report.json").exists()
    assert set(result["lint"]) == {"errors", "warnings", "info"}


def test_compile_twice_lint_report_is_byte_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second compile over the unchanged corpus changes no lint/graph bytes."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    wiki_root = wiki.project_root / ".tesserae"
    wiki.compile()
    first = {
        name: (wiki_root / name).read_bytes()
        for name in ("lint-report.md", "lint-report.json", "graph.json")
    }
    wiki.compile()
    for name, payload in sorted(first.items()):
        assert (wiki_root / name).read_bytes() == payload, (
            f"{name} not byte-identical across two compiles of the same corpus"
        )


def test_compile_survives_lint_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lint bug must never fail a compile — the error is logged and swallowed."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)

    def _boom(self, fix_trivial: bool = False, severity_floor: str = "info"):
        raise RuntimeError("lint exploded")

    monkeypatch.setattr(ProjectWiki, "lint", _boom)
    result = wiki.compile()
    assert "lint" not in result
    assert (wiki.project_root / ".tesserae" / "graph.json").exists()


def _canned_lint(severity: str):
    def _fake_lint(self, fix_trivial: bool = False, severity_floor: str = "info"):
        finding = LintFinding(severity=severity, code="TEST_FINDING", message="canned")
        return LintReport(
            findings=[finding],
            by_code={"TEST_FINDING": 1},
            by_severity={severity: 1},
        )

    return _fake_lint


def test_cli_compile_strict_maps_lint_errors_to_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    monkeypatch.setattr(ProjectWiki, "lint", _canned_lint("error"))
    code = cli_main(
        ["compile", "--project", str(wiki.project_root), "--strict", "--extractor", "deterministic"]
    )
    assert code == 2


def test_cli_compile_strict_maps_lint_warnings_to_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    monkeypatch.setattr(ProjectWiki, "lint", _canned_lint("warning"))
    code = cli_main(
        ["compile", "--project", str(wiki.project_root), "--strict", "--extractor", "deterministic"]
    )
    assert code == 1


def test_cli_compile_strict_fails_closed_when_lint_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lint crash omits result['lint']; --strict must fail closed, not exit 0."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)

    def _boom(self, fix_trivial: bool = False, severity_floor: str = "info"):
        raise RuntimeError("lint exploded")

    monkeypatch.setattr(ProjectWiki, "lint", _boom)
    code = cli_main(
        ["compile", "--project", str(wiki.project_root), "--strict", "--extractor", "deterministic"]
    )
    assert code == 2
    assert "lint did not run" in capsys.readouterr().err


def test_cli_compile_without_strict_stays_report_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    monkeypatch.setattr(ProjectWiki, "lint", _canned_lint("error"))
    code = cli_main(
        ["compile", "--project", str(wiki.project_root), "--extractor", "deterministic"]
    )
    assert code == 0


# --------------------------------------------------------------------------- claim support (opt-in)


def _claim_graph() -> dict:
    return {
        "nodes": [
            _node(
                "Paper:alpha:aaaa1111",
                "Paper",
                "Alpha Attention",
                metadata={"title_quality": "verified"},
            ),
            _node("Concept:beta:bbbb2222", "Concept", "Beta Routing"),
        ],
        "edges": [],
    }


def _claim_body() -> str:
    return (
        "## Overview\n\n"
        "Alpha introduced sparse attention over long contexts and reported "
        "a 2x speedup on retrieval tasks [Alpha Attention].\n\n"
        "Beta routing extends this with learned gating "
        "[Concept:beta:bbbb2222].\n\n"
        "See [Alpha Attention](papers/alpha.md) for details.\n"
    )


def test_claim_candidates_extracted_from_synthesis_pages(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_claim_graph())
    _write_synthesis(
        project,
        "daily-2026-07-01",
        ["Paper:alpha:aaaa1111", "Concept:beta:bbbb2222"],
        body=_claim_body(),
    )
    nodes = {n["id"]: n for n in _claim_graph()["nodes"]}
    got = _iter_claim_candidates(project / ".tesserae", nodes)
    pairs = {(c.node_id, c.claim_text[:20]) for c in got}
    # display-name marker resolved to the Paper id; raw-id marker resolved too
    assert ("Paper:alpha:aaaa1111", "Alpha introduced spa") in pairs
    assert any(c.node_id == "Concept:beta:bbbb2222" for c in got)
    # markdown link [Alpha Attention](...) is NOT a citation
    assert not any("See [Alpha Attention]" in c.claim_text for c in got)
    # heading paragraph skipped
    assert not any(c.claim_text.startswith("##") for c in got)


def test_claim_sampling_is_deterministic_and_capped(tmp_path: Path) -> None:
    cands = [
        _ClaimCandidate(
            "wiki/syntheses/a.md",
            f"Concept:x{i}:c{i:04d}",
            f"Claim text number {i} with enough length to matter.",
        )
        for i in range(50)
    ]
    first = _sample_claims(list(cands), cap=20)
    second = _sample_claims(list(reversed(cands)), cap=20)
    assert first == second          # input order irrelevant
    assert len(first) == 20         # capped


class _StubJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload

    def complete_text(self, **kwargs):
        return None


class _ExplodingClient:
    def complete_json(self, **kwargs):
        raise AssertionError("LLM must not be called")

    complete_text = complete_json


def _claim_project(tmp_path: Path) -> Path:
    project = _scaffold(tmp_path, graph=_claim_graph())
    _write_synthesis(
        project,
        "daily-2026-07-01",
        ["Paper:alpha:aaaa1111", "Concept:beta:bbbb2222"],
        body=_claim_body(),
    )
    return project


def test_claim_support_off_by_default(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    report = WikiLinter(project).run(llm_client=_ExplodingClient())
    assert not [f for f in report.findings if f.code.startswith("CLAIM_")]


def test_claim_support_unsupported_is_warning_partial_is_info(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    stub = _StubJsonClient({"verdicts": ["unsupported", "partial"]})
    report = WikiLinter(project).run(verify_claims=True, llm_client=stub)
    codes = report.by_code
    assert codes.get("CLAIM_UNSUPPORTED") == 1
    assert codes.get("CLAIM_PARTIAL") == 1
    assert codes.get("CLAIM_SUPPORT_SUMMARY") == 1
    warn = [f for f in report.findings if f.code == "CLAIM_UNSUPPORTED"][0]
    assert warn.severity == "warning" and not warn.auto_fixable
    assert len(stub.calls) == 1  # ONE batched call
    # summary counts are embedded in the message
    summary = [f for f in report.findings if f.code == "CLAIM_SUPPORT_SUMMARY"][0]
    assert "1 unsupported" in summary.message and "1 partial" in summary.message


def test_claim_support_skipped_without_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _claim_project(tmp_path)
    import tesserae.llm_json as llm_json

    monkeypatch.setattr(llm_json, "build_default_json_client", lambda **kw: None)
    report = WikiLinter(project).run(verify_claims=True)
    assert report.by_code.get("CLAIM_SUPPORT_SKIPPED") == 1
    # The claim check degrades to info-only — no CLAIM_* warning appears.
    claim_findings = [f for f in report.findings if f.code.startswith("CLAIM_")]
    assert claim_findings and all(f.severity == "info" for f in claim_findings)


def test_claim_support_bad_llm_output_degrades_to_skipped(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    stub = _StubJsonClient({"nonsense": True})
    report = WikiLinter(project).run(verify_claims=True, llm_client=stub)
    # wrong shape → all entries unverifiable; still a summary, never a crash
    assert report.by_code.get("CLAIM_SUPPORT_SUMMARY") == 1
    assert report.by_code.get("CLAIM_UNSUPPORTED") is None


def test_claim_support_prompt_bytes_are_stable(tmp_path: Path) -> None:
    project = _claim_project(tmp_path)
    a, b = _StubJsonClient({"verdicts": []}), _StubJsonClient({"verdicts": []})
    WikiLinter(project).run(verify_claims=True, llm_client=a)
    WikiLinter(project).run(verify_claims=True, llm_client=b)
    assert a.calls == b.calls  # identical system+user bytes across runs


def test_cli_lint_verify_claims_flag_forwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scaffold(tmp_path)
    ProjectWiki.init(project, name="demo_claims")
    seen: dict = {}

    def fake_lint(self, fix_trivial=False, severity_floor="info", **kw):
        seen.update(kw)
        return LintReport()

    monkeypatch.setattr(ProjectWiki, "lint", fake_lint)
    rc = cli_main(
        ["lint", "--project", str(project), "--verify-claims", "--claim-cap", "5"]
    )
    assert rc == 0
    assert seen["verify_claims"] is True and seen["claim_cap"] == 5


def test_compile_tail_lint_never_verifies_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compile tail lint runs with defaults — it can never opt in."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)

    def _no_claims(self, *args, **kwargs):
        raise AssertionError("compile must not verify claims")

    monkeypatch.setattr(WikiLinter, "_check_claim_support", _no_claims)
    result = wiki.compile()
    # Lint ran to completion (a claim-support invocation would have raised,
    # been swallowed by compile, and dropped the "lint" key).
    assert set(result["lint"]) == {"errors", "warnings", "info"}


# --------------------------------------------------------------------------- code-graph staleness (git delta)


def _git_init(root: Path) -> str:
    """git init + one commit; returns the full HEAD sha."""
    def g(*args: str) -> str:
        return _sp.run(
            ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    g("init", "-q", "-b", "main")
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "c1")
    return g("rev-parse", "HEAD")


def _ledger(project: Path, sha: str) -> None:
    (project / ".tesserae" / ".build-history.jsonl").write_text(
        json.dumps({"built_at": "2026-07-01T00:00:00Z", "code_edges": 0, "code_nodes": 1,
                    "git_head": sha, "research_edges": 0, "research_nodes": 0},
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _code_graph(paths: list[str]) -> dict:
    # Zero-padded ids so the report's (code, node_id, path) sort keeps the
    # findings in the same lexicographic order the cap selects by path.
    return {
        "nodes": [_node(f"sf{i:02d}", "SourceFile", p, metadata={"layer": "raw-code"})
                  for i, p in enumerate(paths)],
        "edges": [],
    }


def test_read_git_head_returns_sha_in_repo_and_none_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tesserae.lint import read_git_head
    # Keep git discovery from escaping the scaffold: if pytest's basetemp
    # happens to live inside a real repo, `git -C <scaffold>` would otherwise
    # resolve the enclosing repo's HEAD.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    project = _scaffold(tmp_path)
    assert read_git_head(project) is None  # tmp scaffold is not a repo
    sha = _git_init(project)
    assert read_git_head(project) == sha
    assert len(sha) == 40


def test_code_graph_staleness_flags_changed_source_files(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    sha = _git_init(project)
    _ledger(project, sha)
    (project / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-aqm", "c2"], check=True)
    report = WikiLinter(project).run()
    behind = [f for f in report.findings if f.code == "CODE_GRAPH_BEHIND"]
    stale = [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]
    assert len(behind) == 1 and "1 commit(s) behind" in behind[0].message
    assert len(stale) == 1
    assert stale[0].node_id == "sf00" and stale[0].path == "a.py"
    assert stale[0].severity == "info"


def test_code_graph_staleness_silent_when_head_unchanged(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _ledger(project, _git_init(project))
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_skips_without_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # See test_read_git_head_returns_sha_in_repo_and_none_outside: pin git
    # discovery to the scaffold so an enclosing repo can't leak in.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _ledger(project, "0" * 40)  # ledger has a head, but no repo exists
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_skips_without_recorded_head(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _git_init(project)  # repo exists, but ledger has no git_head key
    (project / ".tesserae" / ".build-history.jsonl").write_text(
        json.dumps({"built_at": "2026-07-01T00:00:00Z"}) + "\n", encoding="utf-8")
    report = WikiLinter(project).run()
    assert not [f for f in report.findings if f.code.startswith("CODE_GRAPH")]


def test_code_graph_staleness_unresolvable_head_emits_single_info(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    _git_init(project)
    _ledger(project, "1234567890abcdef1234567890abcdef12345678")
    report = WikiLinter(project).run()
    unresolved = [f for f in report.findings if f.code == "CODE_GRAPH_HEAD_UNRESOLVED"]
    assert len(unresolved) == 1 and unresolved[0].severity == "info"
    assert not [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]


def test_code_graph_staleness_caps_per_file_findings_at_twenty(tmp_path: Path) -> None:
    names = [f"m{i:02d}.py" for i in range(25)]
    project = _scaffold(tmp_path, graph=_code_graph(names))
    sha = _git_init(project)
    _ledger(project, sha)
    for n in names:
        (project / n).write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"], check=True)
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "c2"], check=True)
    report = WikiLinter(project).run()
    stale = [f for f in report.findings if f.code == "CODE_GRAPH_STALE_FILE"]
    assert len(stale) == 20
    assert [f.path for f in stale] == sorted(f.path for f in stale)
    behind = [f for f in report.findings if f.code == "CODE_GRAPH_BEHIND"]
    assert "25 tracked in graph" in behind[0].message


def test_code_graph_staleness_report_is_byte_stable_under_fixed_git_state(
    tmp_path: Path,
) -> None:
    project = _scaffold(tmp_path, graph=_code_graph(["a.py"]))
    sha = _git_init(project)
    _ledger(project, sha)
    (project / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _sp.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-aqm", "c2"], check=True)
    WikiLinter(project).run()
    report_md = project / ".tesserae" / "lint-report.md"
    report_json = project / ".tesserae" / "lint-report.json"
    first = (report_md.read_bytes(), report_json.read_bytes())
    WikiLinter(project).run()
    assert (report_md.read_bytes(), report_json.read_bytes()) == first


def test_compile_records_git_head_and_tail_lint_sees_no_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Head is recorded before the tail lint, so a fresh compile is never stale."""
    wiki = _seed_compile_project(tmp_path, monkeypatch)
    sha = _git_init(wiki.project_root)
    wiki.compile()
    ledger = (wiki.project_root / ".tesserae" / ".build-history.jsonl").read_text(
        encoding="utf-8"
    )
    last = json.loads(ledger.strip().splitlines()[-1])
    assert last["git_head"] == sha
    report = json.loads(
        (wiki.project_root / ".tesserae" / "lint-report.json").read_text(encoding="utf-8")
    )
    assert not [f for f in report["findings"] if f["code"].startswith("CODE_GRAPH")]


# --------------------------------------------------- reasoning-edge ratio


def _ratio_graph(structural: int, reasoning: int) -> dict:
    nodes = [_node("c0", "Claim", "anchor")]
    edges = []
    for i in range(structural):
        nodes.append(_node(f"s{i}", "Paper", f"paper {i}"))
        edges.append({"source": f"s{i}", "target": "c0", "type": "discussed_in"})
    for i in range(reasoning):
        nodes.append(_node(f"r{i}", "Paper", f"support {i}"))
        edges.append({"source": f"r{i}", "target": "c0", "type": "supports_claim"})
    return {"nodes": nodes, "edges": edges}


def test_lint_reasoning_edge_ratio_reports_counts(tmp_path: Path) -> None:
    """The exact counts must land in the report so CI can diff the number."""
    project = _scaffold(tmp_path, graph=_ratio_graph(structural=9, reasoning=1))
    report = WikiLinter(project).run()

    matches = [f for f in report.findings if f.code == "REASONING_EDGE_RATIO"]
    assert len(matches) == 1
    assert matches[0].severity == "info"  # 10.0% is above the 7.5 floor
    assert "1 of 10 edges (10.0%)" in matches[0].message

    payload = json.loads(
        (project / ".tesserae" / "lint-report.json").read_text(encoding="utf-8")
    )
    assert any(f["code"] == "REASONING_EDGE_RATIO" for f in payload["findings"])


def test_lint_reasoning_edge_ratio_warns_below_floor(tmp_path: Path) -> None:
    """The ratchet: flooding the graph with membership edges must warn."""
    project = _scaffold(tmp_path, graph=_ratio_graph(structural=99, reasoning=1))
    report = WikiLinter(project).run()

    matches = [f for f in report.findings if f.code == "REASONING_EDGE_RATIO"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert "1 of 100 edges (1.0%)" in matches[0].message


# --------------------------------------------------- interval coverage


def _coverage_graph(
    dated: int,
    undated: int,
    superseded: int,
    edge_dated: int = 0,
    chained: int = 0,
    resolved: int = 0,
    backdated: int = 0,
) -> dict:
    """Edges whose endpoints carry a first_seen_at get a real ``valid_from``;
    endpoints without one land in the literal ``"undated"`` bucket that
    ``timeline()`` sorts under with no signal to the caller.

    ``edge_dated``, ``chained``, ``resolved`` and ``backdated`` build the
    shapes that the probe's dating and invalidation rules BRANCH on. They
    default to 0 so the arithmetic in the per-number tests below stays round;
    the anti-drift test passes all of them, because a fixture that never
    reaches a branch cannot pin it. See
    :func:`test_lint_interval_coverage_matches_the_temporal_projector_exactly`.
    """
    nodes = [_node("hub", "Concept", "hub")]
    edges = []
    for i in range(dated):
        nodes.append(
            _node(f"d{i}", "Paper", f"dated {i}", metadata={"first_seen_at": "2026-01-02"})
        )
        edges.append({"source": f"d{i}", "target": "hub", "type": "discussed_in"})
    for i in range(undated):
        nodes.append(_node(f"u{i}", "Paper", f"undated {i}"))
        edges.append({"source": f"u{i}", "target": "hub", "type": "summarizes"})
    # A supersedes edge closes an interval, which is what populates
    # ``valid_to_basis`` — the second half of the histogram.
    for i in range(superseded):
        nodes.append(
            _node(f"new{i}", "SessionInsight", f"new {i}", metadata={"first_seen_at": "2026-03-04"})
        )
        nodes.append(
            _node(f"old{i}", "SessionInsight", f"old {i}", metadata={"first_seen_at": "2026-01-01"})
        )
        edges.append({"source": f"new{i}", "target": f"old{i}", "type": "supersedes"})
        edges.append({"source": f"old{i}", "target": "hub", "type": "discussed_in"})
    # An edge whose OWN metadata carries the date, between two endpoints that
    # carry none. ``_fact_from_edge`` takes valid_from as the MAX over (subject
    # ts, object ts, edge analysis_date), so this arm is the only thing in the
    # fixture that can tell the third term from a constant None — without it,
    # deleting analysis_date from the probe changes no number anyone asserts.
    for i in range(edge_dated):
        nodes.append(_node(f"ad_s{i}", "Paper", f"edge-dated subject {i}"))
        nodes.append(_node(f"ad_o{i}", "Paper", f"edge-dated object {i}"))
        edges.append(
            {
                "source": f"ad_s{i}",
                "target": f"ad_o{i}",
                "type": "discussed_in",
                "metadata": {"analysis_date": "2026-02-02"},
            }
        )
    # A supersedes CHAIN: mid is superseded by new and itself supersedes old,
    # so mid is both an endpoint of an invalidating fact and an ended node.
    # That is the only shape where "an invalidating fact is never ended by its
    # own target" is load-bearing: for (mid supersedes old), the subject-only
    # rule ends it at ts(new) and the basis is ``supersedes``, while including
    # the object ends it at ts(mid) == its own valid_from, which
    # ``_boundary_precedes_start`` rejects and the fact falls to ``open``.
    for i in range(chained):
        nodes.append(
            _node(f"c_new{i}", "SessionInsight", f"chain new {i}",
                  metadata={"first_seen_at": "2026-03-03"})
        )
        nodes.append(
            _node(f"c_mid{i}", "SessionInsight", f"chain mid {i}",
                  metadata={"first_seen_at": "2026-02-02"})
        )
        nodes.append(
            _node(f"c_old{i}", "SessionInsight", f"chain old {i}",
                  metadata={"first_seen_at": "2026-01-01"})
        )
        edges.append({"source": f"c_new{i}", "target": f"c_mid{i}", "type": "supersedes"})
        edges.append({"source": f"c_mid{i}", "target": f"c_old{i}", "type": "supersedes"})
    # ``resolved_by`` runs the OPPOSITE orientation: the edge's SOURCE is the
    # loser. Reading the endpoints as if it were ``supersedes`` closes the
    # winner's interval instead, so the probe has to consult _closing_roles
    # and this arm is what makes that visible in the histogram.
    for i in range(resolved):
        nodes.append(
            _node(f"r_loser{i}", "PerformanceClaim", f"resolved loser {i}",
                  metadata={"first_seen_at": "2026-01-01"})
        )
        nodes.append(
            _node(f"r_winner{i}", "PerformanceClaim", f"resolved winner {i}",
                  metadata={"first_seen_at": "2026-03-03"})
        )
        edges.append({"source": f"r_loser{i}", "target": f"r_winner{i}", "type": "resolved_by"})
        edges.append({"source": f"r_loser{i}", "target": "hub", "type": "discussed_in"})
    # Two winners for one loser, the earlier of them observed BEFORE the loser
    # it supposedly ended. Only a candidate filter that runs before "earliest
    # winner wins" keeps the later, genuine boundary; filtering at the fact
    # level alone drops the interval to ``open``, which is a different
    # histogram and a different answer.
    for i in range(backdated):
        nodes.append(
            _node(f"bd_old{i}", "SessionInsight", f"backdated old {i}",
                  metadata={"first_seen_at": "2026-02-02"})
        )
        nodes.append(
            _node(f"bd_early{i}", "SessionInsight", f"backdated early {i}",
                  metadata={"first_seen_at": "2026-01-01"})
        )
        nodes.append(
            _node(f"bd_late{i}", "SessionInsight", f"backdated late {i}",
                  metadata={"first_seen_at": "2026-04-04"})
        )
        edges.append({"source": f"bd_early{i}", "target": f"bd_old{i}", "type": "supersedes"})
        edges.append({"source": f"bd_late{i}", "target": f"bd_old{i}", "type": "supersedes"})
        edges.append({"source": f"bd_old{i}", "target": "hub", "type": "discussed_in"})
    # ``metadata.first_seen_at`` must survive graph_from_payload for the dated
    # arm to be dated at all; if it did not, this fixture would be all-undated
    # and the assertions below would be vacuous.
    return {"nodes": nodes, "edges": edges}


def test_lint_interval_coverage_reports_undated_percentage(tmp_path: Path) -> None:
    """The number itself is the instrument — it must land in the report so a
    later commit can set a floor from a measurement rather than a guess."""
    project = _scaffold(tmp_path, graph=_coverage_graph(dated=3, undated=7, superseded=0))
    report = WikiLinter(project).run()

    matches = [f for f in report.findings if f.code == "INTERVAL_COVERAGE"]
    assert len(matches) == 1
    assert "7 of 10" in matches[0].message
    assert "70.0%" in matches[0].message

    payload = json.loads(
        (project / ".tesserae" / "lint-report.json").read_text(encoding="utf-8")
    )
    assert any(f["code"] == "INTERVAL_COVERAGE" for f in payload["findings"])


def test_lint_interval_coverage_reports_valid_to_basis_histogram(tmp_path: Path) -> None:
    project = _scaffold(tmp_path, graph=_coverage_graph(dated=2, undated=2, superseded=1))
    report = WikiLinter(project).run()

    (finding,) = [f for f in report.findings if f.code == "INTERVAL_COVERAGE"]
    assert "supersedes=" in finding.message
    assert "open=" in finding.message


def test_lint_interval_coverage_is_info_only(tmp_path: Path) -> None:
    """Deliberately non-strict, like _check_code_graph_staleness: the live
    graph's real ratio is 73.38% undated, so any threshold picked today would
    turn every `compile --strict` red on day one."""
    project = _scaffold(tmp_path, graph=_coverage_graph(dated=0, undated=20, superseded=0))
    report = WikiLinter(project).run()

    (finding,) = [f for f in report.findings if f.code == "INTERVAL_COVERAGE"]
    assert finding.severity == "info"
    assert "100.0%" in finding.message


def test_lint_interval_coverage_silent_when_there_is_nothing_to_place(
    tmp_path: Path,
) -> None:
    """Nodes but no edges means no facts, so there is no ratio to report — and
    reporting one would divide by zero. The earlier version of this test passed
    with the guard deleted, because a redundant `if not edges` shadowed the
    guard that actually does the work; this fixture reaches the real one."""
    project = _scaffold(
        tmp_path,
        graph={"nodes": [_node("lonely", "Concept", "lonely")], "edges": []},
    )

    report = WikiLinter(project).run()

    assert not [f for f in report.findings if f.code == "INTERVAL_COVERAGE"]


def test_lint_interval_coverage_says_so_when_the_probe_itself_fails(
    tmp_path: Path,
) -> None:
    """A probe added to make a degradation loud must not degrade silently.

    A node whose type is outside ResearchNodeType makes graph_from_payload
    raise; the finding then vanished with no message at all, so an operator
    reading lint-report.md could not tell "fully dated" from "never ran"."""
    project = _scaffold(
        tmp_path,
        graph={
            "nodes": [
                {"id": "n1", "type": "not_a_real_node_type", "name": "X", "metadata": {}}
            ],
            "edges": [{"source": "n1", "target": "n1", "type": "supports_claim"}],
        },
    )

    report = WikiLinter(project).run()

    (finding,) = [f for f in report.findings if f.code == "LINT_PROBE_FAILED"]
    assert finding.severity == "info"
    assert "INTERVAL_COVERAGE" in finding.message


def test_lint_interval_coverage_matches_the_temporal_projector_exactly(
    tmp_path: Path,
) -> None:
    """The probe counts undated facts and valid_to_basis WITHOUT building the
    103k TemporalFact models the projector builds, because doing that cost
    ~4.2s and ~91MB at the tail of every compile. That is only safe while the
    cheap path agrees with the projector, so pin the agreement here: if
    TemporalFactProjector's dating or invalidation rules change, this goes red
    rather than the probe quietly reporting a number timeline() disagrees with.

    The fixture must REACH every rule the probe reimplements, or the pin is
    decorative. ``edge_dated`` supplies a fact datable only by the edge's own
    ``analysis_date``; ``chained`` supplies a supersedes edge whose object is
    itself superseded, the one shape where the surviving-endpoint rule changes
    the answer; ``resolved`` supplies the opposite loser orientation; and
    ``backdated`` supplies a winner observed before the node it ends, which
    only the candidate filter rejects. Verified by mutation: deleting the
    analysis_date term from the probe, making its endpoints symmetric, reading
    ``resolved_by`` as if it were ``supersedes``, and dropping the
    strictly-later filter each turn this test red.
    """
    from tesserae.research_graph import graph_from_payload
    from tesserae.temporal import TemporalFactProjector

    payload = _coverage_graph(
        dated=3, undated=7, superseded=2, edge_dated=1, chained=1,
        resolved=1, backdated=1,
    )
    project = _scaffold(tmp_path, graph=payload)

    report = WikiLinter(project).run()
    (finding,) = [f for f in report.findings if f.code == "INTERVAL_COVERAGE"]

    facts = TemporalFactProjector().project(graph_from_payload(payload))
    undated = sum(1 for f in facts if (f.valid_from or "undated") == "undated")
    basis: dict[str, int] = {}
    for fact in facts:
        key = fact.valid_to_basis or "open"
        basis[key] = basis.get(key, 0) + 1

    assert f"{undated} of {len(facts)} facts" in finding.message
    for key in sorted(basis):
        assert f"{key}={basis[key]}" in finding.message


def test_lint_procedural_pools_reports_document_extractions(tmp_path: Path) -> None:
    """A pool that looks populated but can reserve nothing must say so.

    ``Runbook``/``Gotcha``/``Event``/``DistilledNote``/``ExpertiseProfile`` are
    producer-owned, but document extraction may mint the same type names — a
    conference deadline lands typed ``Event``. Since roadmap step 4 those nodes
    can no longer take a reserved procedural slot, so a graph can hold hundreds
    of Event nodes and still have an empty Event pool. Unreported, that reads
    as working procedural memory.
    """
    graph = {
        "nodes": [
            _node("e1", "Event", "CVPR 2026"),
            _node("e2", "Event", "3DV2027"),
            _node("e3", "Event", "3rd Joint EgoVis Workshop"),
            _node(
                "rb1",
                "Runbook",
                "Runbook: release",
                metadata={
                    "extractor": "memory.distill.run_distillation_pass",
                    "member_ids": ["f1", "f2"],
                },
            ),
        ],
        "edges": [],
    }
    project = _scaffold(tmp_path, graph=graph)
    report = WikiLinter(project).run()

    matches = [f for f in report.findings if f.code == "PROCEDURAL_POOLS"]
    assert len(matches) == 1, f"expected one PROCEDURAL_POOLS finding; got {matches}"
    finding = matches[0]
    # Info, not warning: see
    # ``test_lint_procedural_pools_does_not_gate_strict_on_an_unfillable_pool``
    # — the projects this trips on cannot act on the remedy.
    assert finding.severity == "info"
    # reachable/producer-made/total. Nothing has edges here, so even the real
    # Runbook is unreachable — which the histogram says out loud.
    assert "Event=0/0/3" in finding.message, finding.message
    assert "Runbook=0/1/1" in finding.message, finding.message


def test_lint_procedural_pools_does_not_gate_strict_on_an_unfillable_pool(
    tmp_path: Path,
) -> None:
    """``compile --strict`` must not fail a project for never running a producer.

    The projects this probe trips on are exactly the ones whose procedural
    producers have never run — a documentation corpus with no sessions, say.
    The remedy on offer is "run the producer", which for them means "record
    agent sessions", not a fix they can apply to the graph in front of them.
    ``compile --strict`` maps warnings to exit 1, so a warning here breaks
    those builds over a condition they cannot act on.

    This is the posture INTERVAL_COVERAGE already ships with: report the
    number, at ``info``, with no threshold.
    """
    graph = {
        "nodes": [
            _node("e1", "Event", "CVPR 2026"),
            _node("e2", "Event", "3DV 2027"),
        ],
        "edges": [],
    }
    report = WikiLinter(_scaffold(tmp_path, graph=graph)).run()

    matches = [f for f in report.findings if f.code == "PROCEDURAL_POOLS"]
    assert len(matches) == 1, f"expected one PROCEDURAL_POOLS finding; got {matches}"
    assert matches[0].severity == "info", (
        "an unfillable pool is a statistic about which producers have run, "
        "not a defect the tripping project can act on"
    )
    assert not report.has_warnings() and not report.has_errors(), (
        "a graph whose only flaw is an unfilled procedural pool must leave "
        f"`compile --strict` green; got {[(f.code, f.severity) for f in report.findings]}"
    )


def test_lint_procedural_pools_reports_reachability_not_just_census(
    tmp_path: Path,
) -> None:
    """A pool nothing can retrieve from is not a healthy pool.

    Reservation only ever picks from the PPR neighbourhood of the seeds, so a
    producer-made node with no edges is unreachable in every query — it can
    never take the slot it is counted for. A census that says
    ``ExpertiseProfile=5/5`` reports the one pool that cannot be served as the
    healthiest one in the graph.
    """
    profiles = [
        _node(
            f"p{i}",
            "ExpertiseProfile",
            f"Expertise: agent-{i}",
            metadata={"agent": f"claude-code:acct:agent-{i}"},
        )
        for i in range(5)
    ]
    runbook = _node(
        "rb1",
        "Runbook",
        "Runbook: release",
        metadata={
            "extractor": "memory.distill.run_distillation_pass",
            "member_ids": ["f1"],
        },
    )
    finding = _node("f1", "SessionInsight", "cut the release from CI")
    # A LINKED document extraction, so "reachable" cannot be counted for a node
    # that is not producer-made. Without it every document extraction in this
    # fixture is isolated, and a mutant that counts reachability BEFORE the
    # provenance check still passes — it can only ever report reachable=0 for
    # them by accident of the fixture. With this node present such a mutant
    # reports Event=1/0/1, which is self-contradictory (reachable > made).
    doc_event = _node("de1", "Event", "CVPR 2026")
    graph = {
        "nodes": profiles + [runbook, finding, doc_event],
        "edges": [
            {"source": "rb1", "target": "f1", "type": "derived_from"},
            {"source": "de1", "target": "f1", "type": "mentions"},
        ],
    }
    report = WikiLinter(_scaffold(tmp_path, graph=graph)).run()

    matches = [f for f in report.findings if f.code == "PROCEDURAL_POOLS"]
    assert len(matches) == 1, f"expected one PROCEDURAL_POOLS finding; got {matches}"
    message = matches[0].message
    assert "ExpertiseProfile=0/5/5" in message, (
        "the histogram must lead with how many producer-made nodes are "
        f"reachable at all; got {message}"
    )
    assert "Runbook=1/1/1" in message, message
    # The linked document extraction is reachable but NOT producer-made, so it
    # must count only in the total. reachable > made is incoherent and is what a
    # mutant that hoists the reachability tally above the provenance check emits.
    assert "Event=0/0/1" in message, (
        "a reachable node that no producer made must not be counted as reachable "
        f"for the pool; got {message}"
    )
    assert "ExpertiseProfile" in (matches[0].suggested_fix or "") + message, message
    assert "unreachable" in message.lower(), (
        f"an isolated pool must be named as unservable; got {message}"
    )


# --- SUGGESTED_SUBTYPE: the ontology-growth loop's last rung (step 12) -------


def _write_ledger(project_root: Path, records: list[dict] | str) -> Path:
    from tesserae.schema_drift import PROPOSAL_LEDGER_NAME

    path = project_root / ".tesserae" / PROPOSAL_LEDGER_NAME
    payload = records if isinstance(records, str) else json.dumps(records, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path


def _proposal(**overrides) -> dict:
    record = {
        "approved": False,
        "cluster_key": "a" * 64,
        "description": "A section of a paper.",
        "host_type": "SourceDocument",
        "name": "PaperSection",
        "node_ids": ["p0", "p1", "p2", "p3", "p4"],
        "proposed_type": "PaperSection",
    }
    record.update(overrides)
    return record


def test_suggested_subtype_reports_a_pending_proposal(tmp_path):
    project = _scaffold(tmp_path)
    _write_ledger(project, [_proposal()])

    report = WikiLinter(project).run()

    findings = [f for f in report.findings if f.code == "SUGGESTED_SUBTYPE"]
    assert len(findings) == 1
    finding = findings[0]
    assert "5 SourceDocument nodes" in finding.message
    assert "'PaperSection'" in finding.message
    assert "p0, p1, p2" in finding.message  # first three, sorted
    assert finding.node_id == "p0"
    # The loop is only discoverable if the finding names BOTH halves of the
    # human step: the enum edit and the approval flag.
    assert "research_graph.py" in finding.suggested_fix
    assert "approved" in finding.suggested_fix


def test_suggested_subtype_is_info_and_never_fails_strict(tmp_path):
    """The remedy is a human editing an enum inside the Tesserae package —
    a gate whose remedy is outside the tripping project's control is not a
    gate, it is a broken build."""
    project = _scaffold(tmp_path)
    _write_ledger(project, [_proposal()])

    report = WikiLinter(project).run()

    assert all(f.severity == "info" for f in report.findings if f.code == "SUGGESTED_SUBTYPE")
    assert not report.has_warnings()
    assert not report.has_errors()


def test_no_ledger_is_silent(tmp_path):
    """Schema drift is opt-in: a 'never ran' row in every project is noise."""
    project = _scaffold(tmp_path)

    report = WikiLinter(project).run()

    assert not [f for f in report.findings if f.code == "SUGGESTED_SUBTYPE"]
    assert not [f for f in report.findings if f.code == "LINT_PROBE_FAILED"]


def test_an_approved_proposal_is_no_longer_pending(tmp_path):
    project = _scaffold(tmp_path)
    _write_ledger(project, [_proposal(approved=True), _proposal(name="CodeSnippet",
                                                               proposed_type="CodeSnippet",
                                                               cluster_key="b" * 64)])

    report = WikiLinter(project).run()

    findings = [f for f in report.findings if f.code == "SUGGESTED_SUBTYPE"]
    assert len(findings) == 1
    assert "CodeSnippet" in findings[0].message


def test_an_unreadable_ledger_says_so_instead_of_going_quiet(tmp_path):
    """Absent and unreadable are DIFFERENT states — collapsing them into the
    same silence is exactly what LINT_PROBE_FAILED exists to prevent."""
    project = _scaffold(tmp_path)
    _write_ledger(project, "{not json at all")

    report = WikiLinter(project).run()

    probe_failures = [
        f for f in report.findings
        if f.code == "LINT_PROBE_FAILED" and "SUGGESTED_SUBTYPE" in f.message
    ]
    assert len(probe_failures) == 1
    assert probe_failures[0].severity == "info"
    assert "not zero" in probe_failures[0].message


def test_a_hand_edited_ledger_cannot_crash_the_whole_lint_run(tmp_path):
    """The ledger is a human-editable file, so every field is untrusted.
    Numeric node ids (a plausible hand-edit) once made the sort raise — and
    a probe that escapes its own guard takes the entire lint run with it,
    which `compile --strict` then reports as "lint did not run"."""
    project = _scaffold(tmp_path)
    _write_ledger(project, [
        _proposal(node_ids=[1, "n2", 3.5]),
        _proposal(name="Scalar", proposed_type="Scalar", cluster_key="c" * 64, node_ids=5),
        _proposal(name="Missing", proposed_type="Missing", cluster_key="d" * 64, node_ids=[]),
    ])

    report = WikiLinter(project).run()  # must not raise

    findings = [f for f in report.findings if f.code == "SUGGESTED_SUBTYPE"]
    # Only the record with usable ids is reportable; the other two are skipped
    # rather than taking the run down.
    assert len(findings) == 1
    assert "1, 3.5, n2" in findings[0].message
    assert not report.has_errors()
