"""CLI tests for agent-scoped reads (CLI-1): ``--agent`` on query / ask / context.

``--agent KEY`` runs the existing retrieval/synthesis over one agent's resolved
view (``resolve_agent_view``) instead of the raw L0 graph. KEY may be a worker
key (its L0 ∪ own distillate), a manager key (its team's distillates) or ``org``
(every distilled agent). These are in-process ``tesserae.cli.main`` invocations
over a hand-built ``.tesserae/`` workspace; the distill summarizer is always the
deterministic stub from ``test_agent_distill`` so no LLM ever fires.

The fixture writes ``_base_graph()`` as L0 (``AGENT`` performs s1/s2 with a
Concept anchor; ``OTHER_AGENT`` owns the foreign s9/f9), distills ``AGENT`` to a
real L1 artifact, and — for the ``query`` path, which runs BM25 over the
projected search index rather than the typed graph — hand-builds a matching
``site/search-index.json`` + wiki pages.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_distill import agent_artifact_path, distill_agent
from tesserae.agent_view import resolve_agent_view
from tesserae.cli import main
from tesserae.research_graph import ResearchNodeType

from tests.test_agent_distill import (
    AGENT,
    OTHER_AGENT,
    StubSummarizer,
    _base_graph,
)


# --------------------------------------------------------------------------- fixtures


def _project_with_l0(tmp_path: Path):
    """Write the shared fixture graph as the project's L0 graph.json."""
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    (project / ".tesserae" / "config.json").write_text(
        json.dumps({"name": "proj", "sources": [], "external_tools": [],
                    "memory_backends": {}}),
        encoding="utf-8",
    )
    graph = _base_graph()
    (project / ".tesserae" / "graph.json").write_text(
        graph.to_json(indent=2), encoding="utf-8"
    )
    return project, graph


def _distill(project: Path, graph, agent: str = AGENT) -> None:
    distill_agent(graph, agent, project_root=project, summarizer=StubSummarizer())


def _write_search_index(project: Path, pages) -> None:
    """Hand-build ``site/search-index.json`` + wiki pages for the ``query`` path.

    ``pages`` is a list of ``(node_id, tokens)`` — each becomes one indexed
    concept page whose BM25 tokens are exactly ``tokens`` so tests control which
    hits rank, and whose ``node_id`` drives the agent-view membership filter.
    """
    wiki = project / ".tesserae" / "wiki"
    site = project / ".tesserae" / "site"
    (wiki / "concepts").mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True, exist_ok=True)

    index = []
    for i, (node_id, tokens) in enumerate(pages):
        slug = f"page-{i}"
        title = node_id
        (wiki / "concepts" / f"{slug}.md").write_text(
            "---\n"
            f"title: {title}\n"
            "kind: concepts\n"
            f"node_id: {node_id}\n"
            "---\n"
            f"# {title}\n\n{' '.join(tokens)}\n",
            encoding="utf-8",
        )
        index.append(
            {
                "id": node_id,
                "title": title,
                "kind": "concepts",
                "href": f"concepts/{slug}.html",
                "summary": " ".join(tokens),
                "source_path": "",
                "tokens": tokens,
                "len": len(tokens),
                "created_ts": 1_700_000_000,
            }
        )

    (site / "search-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (wiki / "overview.md").write_text("# Demo Wiki\n\ntiny.\n", encoding="utf-8")


# --------------------------------------------------------------------------- query


def test_query_agent_worker_filters_to_view(tmp_path, capsys):
    """A worker view is L0 ∪ own distillate: an in-view node's page survives,
    a page whose node_id is outside the view is filtered out."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)

    view, _info = resolve_agent_view(project, AGENT, graph)
    ids = {n.id for n in view.nodes}
    assert "Concept:staging-deploy:abc123" in ids  # raw L0 concept is in-view

    _write_search_index(
        project,
        [
            ("Concept:staging-deploy:abc123", ["alpha", "unique", "token"]),
            ("Orphan:not-in-graph", ["alpha", "unique", "token"]),
        ],
    )
    capsys.readouterr()
    rc = main(["query", "alpha unique token", "--project", str(project),
               "--agent", AGENT, "--json"])
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)["hits"]
    hit_ids = {h["node_id"] for h in hits}
    assert "Concept:staging-deploy:abc123" in hit_ids
    assert "Orphan:not-in-graph" not in hit_ids  # outside the view -> dropped


def test_query_agent_org_excludes_raw_sessions(tmp_path, capsys):
    """The ``org`` view federates distillates only — a raw L0 session page is
    filtered out while a real distillate node's page survives."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)

    org_view, info = resolve_agent_view(project, "org", graph)
    assert info["mode"] == "org"
    org_ids = {n.id for n in org_view.nodes}
    assert "Session:s1" not in org_ids  # raw session never enters a distillate
    survivor = sorted(
        n.id for n in org_view.nodes if n.type == ResearchNodeType.DISTILLED_NOTE
    )[0]

    _write_search_index(
        project,
        [
            (survivor, ["alpha", "unique", "token"]),
            ("Session:s1", ["alpha", "unique", "token"]),
        ],
    )
    capsys.readouterr()
    rc = main(["query", "alpha unique token", "--project", str(project),
               "--agent", "org", "--json"])
    assert rc == 0
    hit_ids = {h["node_id"] for h in json.loads(capsys.readouterr().out)["hits"]}
    assert survivor in hit_ids
    assert "Session:s1" not in hit_ids


def test_query_no_agent_unchanged(tmp_path, capsys):
    """Without --agent, query returns every ranking hit (no membership filter)."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    _write_search_index(
        project,
        [
            ("Concept:staging-deploy:abc123", ["alpha", "unique", "token"]),
            ("Session:s1", ["alpha", "unique", "token"]),
        ],
    )
    capsys.readouterr()
    rc = main(["query", "alpha unique token", "--project", str(project), "--json"])
    assert rc == 0
    hit_ids = {h["node_id"] for h in json.loads(capsys.readouterr().out)["hits"]}
    assert {"Concept:staging-deploy:abc123", "Session:s1"} <= hit_ids


def test_query_agent_unknown_fails_loud(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _write_search_index(project, [("Concept:staging-deploy:abc123", ["alpha"])])
    capsys.readouterr()
    rc = main(["query", "alpha", "--project", str(project),
               "--agent", "nope:nope:nope", "--json"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Unknown agent" in err
    assert "tesserae agents list" in err


def test_query_agent_raganything_rejected(tmp_path, capsys):
    project, _graph = _project_with_l0(tmp_path)
    capsys.readouterr()
    rc = main(["query", "q", "--project", str(project),
               "--agent", AGENT, "--backend", "raganything"])
    assert rc == 2
    assert "--agent is not supported with --backend raganything" in capsys.readouterr().err


# --------------------------------------------------------------------------- context


def test_context_agent_missing_artifact_fails_loud(tmp_path, capsys):
    """AGENT is a known L0 Agent node but was never distilled -> fail loud with
    the ``run: tesserae distill --agent`` remedy, exit 1."""
    project, _graph = _project_with_l0(tmp_path)  # no distill
    capsys.readouterr()
    rc = main(["context", "staging", "--project", str(project), "--agent", AGENT])
    assert rc == 1
    err = capsys.readouterr().err
    assert "run: tesserae distill --agent" in err


def test_context_agent_worker_runs_over_view(tmp_path, capsys):
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    capsys.readouterr()
    rc = main(["context", "staging deploy", "--project", str(project), "--agent", AGENT])
    assert rc == 0
    assert capsys.readouterr().out.startswith("# Context:")


def test_context_no_agent_unaffected_by_artifacts(tmp_path, capsys):
    """The default (no --agent) read is byte-identical whether or not per-agent
    distillates exist on disk — artifacts never leak into the base read."""
    project, graph = _project_with_l0(tmp_path)
    main(["context", "staging deploy", "--project", str(project)])
    before = capsys.readouterr().out

    _distill(project, graph, AGENT)
    assert agent_artifact_path(project, AGENT).is_file()
    main(["context", "staging deploy", "--project", str(project)])
    after = capsys.readouterr().out

    assert before == after
    assert before.startswith("# Context:")


# --------------------------------------------------------------------------- ask


def test_ask_agent_scopes_graph_via_proxy(tmp_path, capsys, monkeypatch):
    """ask reaches the graph through ``wiki.paths.graph``; --agent re-points it
    at the materialized worker view, so the graph ask_project sees carries the
    distilled nodes that the raw L0 lacks."""
    import tesserae.query as qmod
    from tesserae.project import load_graph_file

    captured: dict = {}

    def fake_ask(wiki, question, **kwargs):
        captured["ids"] = {n.id for n in load_graph_file(wiki.paths.graph).nodes}
        captured["project_root"] = wiki.project_root
        return {"backend": "wiki", "question": question, "answer": "ok",
                "hits": [], "used_llm": True}

    monkeypatch.setattr(qmod, "ask_project", fake_ask)

    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    l0_ids = {n.id for n in graph.nodes}

    capsys.readouterr()
    rc = main(["ask", "staging", "--project", str(project), "--agent", AGENT, "--json"])
    assert rc == 0
    # The scoped graph carries distilled nodes the raw L0 does not.
    assert captured["ids"] - l0_ids
    # project_root stays the real resolved root (proxy only swaps paths.graph).
    assert Path(captured["project_root"]).resolve() == project.resolve()


def test_ask_agent_scope_federated_rejected(tmp_path, capsys):
    project, _graph = _project_with_l0(tmp_path)
    capsys.readouterr()
    rc = main(["ask", "q", "--project", str(project), "--agent", AGENT,
               "--scope", "federated"])
    assert rc == 2
    assert "incompatible with --scope all-registered/federated" in capsys.readouterr().err


def test_ask_agent_no_llm_rejected(tmp_path, capsys):
    project, _graph = _project_with_l0(tmp_path)
    capsys.readouterr()
    rc = main(["ask", "q", "--project", str(project), "--agent", AGENT, "--no-llm"])
    assert rc == 2
    assert "--agent needs the LLM planner" in capsys.readouterr().err
