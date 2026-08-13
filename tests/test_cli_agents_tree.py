"""`tesserae agents init|tree|show|drill` — the org-ergonomics CLI (CLI-2).

Two fixture families:

* Session-backed (``_seed_sessions_project``): imported harness sessions yield
  the observed keys ``claude-code:claude-home:{default,reviewer}`` and
  ``codex:codex-home:default`` — the input to ``agents init`` / ``agents tree``,
  which read the session corpus (no graph needed).
* Graph-backed (``_project_with_l0`` + ``_distill``): the shared ``_base_graph``
  L0 with ``AGENT`` distilled to a real L1 artifact — the input to
  ``agents show`` / ``agents drill``, which resolve views over the typed graph.

All in-process ``tesserae.cli.main`` calls; the distill summarizer is always the
deterministic stub, so no LLM ever fires.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.agent_distill import distill_agent
from tesserae.cli import main
from tesserae.harness_sessions import HarnessSession, HarnessSessionStore
from tesserae.project import ProjectWiki
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

from tests.test_agent_distill import AGENT, OTHER_AGENT, StubSummarizer, _base_graph

DEFAULT_KEY = "claude-code:claude-home:default"
REVIEWER_KEY = "claude-code:claude-home:reviewer"
CODEX_KEY = "codex:codex-home:default"


# --------------------------------------------------------------------------- fixtures


def _seed_sessions_project(tmp_path: Path) -> Path:
    """Project with imported sessions spanning three role-distinct agent keys.

    The claude-code session carries a typed ``reviewer`` subagent descriptor, so
    ``reviewer`` and its sibling ``default`` are both observed under the same
    ``claude-code:claude-home`` account — the signal ``infer_org_parents`` uses
    to nest reviewer under default.
    """
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ProjectWiki.init(proj, sources=["docs"])
    store = HarnessSessionStore(proj / ".tesserae" / "harness_sessions")
    store.write_sessions(
        [
            HarnessSession(
                id="s1",
                slug="fix-bug",
                harness="claude-code",
                agent_label="Claude Code",
                project_name="proj",
                project_root=str(proj),
                started_at="2026-07-01T10:00:00Z",
                metadata={
                    "config_root": str(tmp_path / "claude-home"),
                    "subagents": [
                        {"id": "claude-code:s1:abc", "title": "Review", "type": "reviewer"}
                    ],
                },
            ),
            HarnessSession(
                id="s2",
                slug="plan-work",
                harness="codex",
                agent_label="Codex",
                project_name="proj",
                project_root=str(proj),
                started_at="2026-07-02T10:00:00Z",
                metadata={"config_root": str(tmp_path / "codex-home")},
            ),
        ]
    )
    return proj


def _registry_payload(proj: Path) -> dict:
    return json.loads((proj / ".tesserae" / "agents" / "registry.json").read_text("utf-8"))


def _project_with_l0(tmp_path: Path):
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    (project / ".tesserae" / "config.json").write_text(
        json.dumps({"name": "proj", "sources": [], "external_tools": [], "memory_backends": {}}),
        encoding="utf-8",
    )
    graph = _base_graph()
    (project / ".tesserae" / "graph.json").write_text(graph.to_json(indent=2), encoding="utf-8")
    return project, graph


def _distill(project: Path, graph, agent: str = AGENT) -> None:
    distill_agent(graph, agent, project_root=project, summarizer=StubSummarizer())


def _write_registry(project: Path, agents: dict) -> None:
    agents_dir = project / ".tesserae" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "registry.json").write_text(
        json.dumps({"version": 1, "agents": agents}, indent=2) + "\n", encoding="utf-8"
    )


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


# --------------------------------------------------------------------------- init


def test_init_infers_nested_org(tmp_path, capsys):
    proj = _seed_sessions_project(tmp_path)

    assert main(["agents", "init", "--project", str(proj)]) == 0
    out = capsys.readouterr().out

    agents = _registry_payload(proj)["agents"]
    # A subagent role nests under its same-account main agent; main agents root.
    assert agents[REVIEWER_KEY]["parent"] == DEFAULT_KEY
    assert agents[DEFAULT_KEY]["parent"] == "org:root"
    assert agents[CODEX_KEY]["parent"] == "org:root"

    # The proposed org prints as an indented tree with reviewer below default.
    lines = out.splitlines()
    default_line = next(l for l in lines if DEFAULT_KEY in l and REVIEWER_KEY not in l)
    reviewer_line = next(l for l in lines if REVIEWER_KEY in l)
    assert _indent_of(reviewer_line) > _indent_of(default_line)
    assert any(l.strip() == "org:root" for l in lines)


def test_init_flat_forces_flat_org(tmp_path, capsys):
    proj = _seed_sessions_project(tmp_path)

    assert main(["agents", "init", "--project", str(proj), "--flat"]) == 0
    capsys.readouterr()

    agents = _registry_payload(proj)["agents"]
    assert {a["parent"] for a in agents.values()} == {"org:root"}


def test_init_refuses_overwrite_without_force(tmp_path, capsys):
    proj = _seed_sessions_project(tmp_path)
    assert main(["agents", "init", "--project", str(proj)]) == 0
    capsys.readouterr()
    assert main(["agents", "init", "--project", str(proj)]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["agents", "init", "--project", str(proj), "--force"]) == 0


# --------------------------------------------------------------------------- tree


def test_agents_tree_renders_hierarchy(tmp_path, capsys):
    proj = _seed_sessions_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])
    capsys.readouterr()

    assert main(["agents", "tree", "--project", str(proj)]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert any(l.strip() == "org:root" for l in lines)
    default_line = next(l for l in lines if DEFAULT_KEY in l and REVIEWER_KEY not in l)
    reviewer_line = next(l for l in lines if REVIEWER_KEY in l)
    assert _indent_of(reviewer_line) > _indent_of(default_line)
    # Un-distilled agents surface their staleness plainly.
    assert "(not distilled)" in reviewer_line
    assert "sessions=1" in reviewer_line


def test_agents_tree_empty_corpus(tmp_path, capsys):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ProjectWiki.init(proj, sources=["docs"])
    assert main(["agents", "tree", "--project", str(proj)]) == 0
    assert "No agents observed" in capsys.readouterr().out


# --------------------------------------------------------------------------- show


def test_agents_show_worker(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph)
    capsys.readouterr()

    assert main(["agents", "show", AGENT, "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert f"agent: {AGENT}" in out
    assert "mode: worker" in out
    assert AGENT in out


def test_agents_show_org(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph)
    capsys.readouterr()

    assert main(["agents", "show", "org", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "mode: org" in out
    assert AGENT in out


def test_agents_show_manager(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph)
    # OTHER_AGENT manages AGENT; only the child is distilled — enough for a
    # manager view (the manager's own artifact is optional).
    _write_registry(
        project,
        {
            AGENT: {"label": "rev", "parent": OTHER_AGENT, "aliases": [], "match": []},
            OTHER_AGENT: {"label": "mgr", "parent": "org:root", "aliases": [], "match": []},
        },
    )
    capsys.readouterr()

    assert main(["agents", "show", OTHER_AGENT, "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "mode: manager" in out
    assert AGENT in out


def test_agents_show_unknown_fails_loud(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph)
    capsys.readouterr()

    assert main(["agents", "show", "nope:nope:nope", "--project", str(project)]) == 1
    assert "Unknown agent" in capsys.readouterr().err


def test_agents_show_missing_artifact_fails_loud(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)  # no distill -> no L1 artifact
    capsys.readouterr()

    assert main(["agents", "show", AGENT, "--project", str(project)]) == 1
    assert "distill" in capsys.readouterr().err


# --------------------------------------------------------------------------- drill


def test_agents_drill_alive(tmp_path, capsys):
    project, _graph = _project_with_l0(tmp_path)
    capsys.readouterr()

    assert main(["agents", "drill", "SessionInsight:f1", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "status: alive" in out
    assert "node: SessionInsight:f1" in out
    assert "drill_down_audit" in out


def test_agents_drill_prints_artifact_asset_reference(tmp_path, capsys):
    """MCP parity: subprocess-only consumers read these fields off stdout, so
    whatever drill_down reports over MCP has to print here too."""
    project, graph = _project_with_l0(tmp_path)
    digest = "ab" * 32
    figure = ResearchNode(
        id="Artifact:fig",
        name="Figure: Pipeline",
        type=ResearchNodeType.ARTIFACT,
        description="Pipeline",
        metadata={
            "parser": "raganything",
            "kind": "image",
            "content_hash": digest,
            "asset_path": ".tesserae/external/raganything/parsed/deadbeef/x.png",
        },
    )
    (project / ".tesserae" / "graph.json").write_text(
        ResearchGraph(nodes=[*graph.nodes, figure], edges=list(graph.edges)).to_json(indent=2),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["agents", "drill", "Artifact:fig", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "asset_path=.tesserae/external/raganything/parsed/deadbeef/x.png" in out
    assert f"asset_sha256={digest}" in out
    assert f"asset_site_path=raw-assets/{digest[:16]}.png" in out


def test_agents_drill_gone(tmp_path, capsys):
    project, _graph = _project_with_l0(tmp_path)
    capsys.readouterr()

    assert main(["agents", "drill", "Nope:missing", "--project", str(project)]) == 0
    assert "status: gone" in capsys.readouterr().out


def test_agents_drill_no_graph_fails_loud(tmp_path, capsys):
    project = tmp_path / "proj"
    (project / "docs").mkdir(parents=True)
    wiki = ProjectWiki.init(project, sources=["docs"])
    wiki.paths.graph.unlink(missing_ok=True)  # initialized, but no compiled graph
    capsys.readouterr()

    assert main(["agents", "drill", "SessionInsight:f1", "--project", str(project)]) == 1
    assert "tesserae compile" in capsys.readouterr().err
