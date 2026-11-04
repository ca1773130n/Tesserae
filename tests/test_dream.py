"""Acceptance tests for the gated, region-scoped dream pass (roadmap R2).

The three contracts the roadmap names, on tmp fixture graphs:

* dry-run ops == applied ops;
* apply then undo is byte-identical for ``graph.json`` and ``node_memory``;
* a second run on an unchanged corpus yields 0 ops.

Plus the gate (a regressing region is rejected and not re-proposed), the
per-session cursor in ``session_chunks.db``, the every-Nth-pass deferral, the
undo refusals, and the CLI / MCP / daemon surfaces. No model is loaded: the
associate leg uses a content-keyed fake backend and probes use the hash stub.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tesserae import dream as D
from tesserae.memory.store import NodeMemoryRow, write_memory
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import HashEmbeddingBackend


class _FakeBackend:
    """Orthogonal unit vectors keyed on content (same shape as test_associate)."""

    name = "fake-dream"

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "pagerank" in tl:
                out.append([1.0, 0.0, 0.0])
            elif "banana" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def _insight(nid: str, name: str, session: str) -> ResearchNode:
    return ResearchNode(
        id=f"SessionInsight:{nid}",
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={
            "session_id": f"claude-code:acct:{session}",
            "first_seen_at": "2026-10-01T00:00:00+00:00",
            "last_accessed_at": "2026-10-01T00:00:00+00:00",
        },
    )


def _graph() -> ResearchGraph:
    session = ResearchNode(id="Session:s1", name="dream fixture session", type=ResearchNodeType.SESSION)
    a = _insight("a", "Atomic writes need a PID plus random tmp suffix", "s1")
    b = _insight("b", "Atomic writes need a PID plus random tmp suffix always", "s2")
    c = _insight("c", "Louvain regions scope the consolidation pass", "s1")
    p1 = ResearchNode(id="Concept:ppr1", name="Personalized PageRank ranking", type=ResearchNodeType.CONCEPT)
    p2 = ResearchNode(id="Concept:ppr2", name="PageRank variant for retrieval", type=ResearchNodeType.CONCEPT)
    ban = ResearchNode(id="Concept:ban", name="Banana fruit ripeness", type=ResearchNodeType.CONCEPT)
    edges = [
        ResearchEdge(source=n.id, target=session.id, type="discussed_in", evidence="fixture")
        for n in (a, b, c)
    ] + [
        ResearchEdge(source=p1.id, target=c.id, type="uses", evidence="fixture"),
        ResearchEdge(source=p2.id, target=c.id, type="uses", evidence="fixture"),
    ]
    return ResearchGraph(nodes=[session, a, b, c, p1, p2, ban], edges=edges)


def _project(tmp_path: Path, graph: ResearchGraph | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".tesserae").mkdir(parents=True)
    graph = graph or _graph()
    (root / ".tesserae" / "graph.json").write_text(graph.to_json(indent=2) + "\n", encoding="utf-8")
    # Stale decay on every row: the compile reference instant is the fixture's
    # newest timestamp, so every finding re-scores to 1.0 -> one decay op each.
    write_memory(
        root / ".tesserae" / "sqlite.db",
        [NodeMemoryRow(node_id=n.id, decay_score=0.25, updated_at="2026-10-01") for n in graph.nodes],
    )
    return root


def _memory_dump(root: Path) -> list:
    con = sqlite3.connect(root / ".tesserae" / "sqlite.db")
    try:
        return con.execute("select * from node_memory order by node_id").fetchall()
    finally:
        con.close()


def _snapshot(root: Path) -> dict:
    t = root / ".tesserae"
    overlay = t / "discovered_links.json"
    return {
        "graph": (t / "graph.json").read_bytes(),
        "memory": _memory_dump(root),
        "overlay": overlay.read_bytes() if overlay.is_file() else None,
    }


def _run(root: Path, *, dry_run: bool, **settings):
    return D.run_dream(
        root,
        dry_run=dry_run,
        settings=D.DreamSettings(**settings),
        backend=_FakeBackend(),
        probe_backend=HashEmbeddingBackend(),
        cfg={},
        env={},
    )


def _key(op: dict) -> str:
    return json.dumps(op, sort_keys=True)


def test_dry_run_plans_every_kind_and_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    report = _run(root, dry_run=True)
    counts = report["op_counts"]
    assert counts["supersede"] == 1 and counts["decay"] >= 3
    links = {(op["source"], op["target"]) for op in report["promoted_ops"] if op["op"] == "associate"}
    assert ("Concept:ppr1", "Concept:ppr2") in links
    edge = next(op["edge"] for op in report["promoted_ops"] if op["op"] == "supersede")
    # deterministic verdict: the later session (s2) supersedes the earlier one
    assert (edge["source"], edge["target"]) == ("SessionInsight:b", "SessionInsight:a")
    assert report["gate_ran"] and all(r["promoted"] for r in report["regions"])
    assert _snapshot(root) == before
    assert not (root / ".tesserae" / "dream").exists()


def test_dry_run_ops_equal_applied_ops(tmp_path: Path) -> None:
    root = _project(tmp_path)
    planned = _run(root, dry_run=True)["promoted_ops"]
    applied = _run(root, dry_run=False)["applied_ops"]
    assert planned and sorted(map(_key, planned)) == sorted(map(_key, applied))


def test_apply_then_undo_is_byte_identical(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    report = _run(root, dry_run=False)
    after = _snapshot(root)
    assert after["graph"] != before["graph"] and after["memory"] != before["memory"]
    assert after["overlay"] is not None
    journal = json.loads(Path(report["journal"]).read_text(encoding="utf-8"))
    assert journal["status"] == "applied" and journal["ops"] == report["applied_ops"]

    undone = D.undo_dream(root, "last")
    assert undone["undone"] == report["pass_id"]
    assert _snapshot(root) == before
    assert json.loads(Path(report["journal"]).read_text(encoding="utf-8"))["status"] == "undone"
    with pytest.raises(D.DreamError, match="no applied dream pass"):
        D.undo_dream(root, "last")


def test_second_run_on_unchanged_corpus_yields_zero_ops(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _run(root, dry_run=False)["applied_ops"]
    again = _run(root, dry_run=False)
    assert again["applied_ops"] == [] and sum(again["op_counts"].values()) == 0
    assert "journal" not in again  # an idle pass writes no journal entry


def test_regressing_region_is_rejected_and_not_reproposed(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    real = D._probe_scores

    def worse_on_candidates(graph, probes, backend, top_k, **kw):
        scores = real(graph, probes, backend, top_k, **kw)
        is_candidate = any(e.type in ("supersedes", "shares_concept_with") for e in graph.edges)
        return [0.0 for _ in scores] if is_candidate else [1.0 for _ in scores]

    monkeypatch.setattr(D, "_probe_scores", worse_on_candidates)
    report = _run(root, dry_run=False)
    rejected = [r for r in report["regions"] if not r["promoted"]]
    assert rejected and all(r["gate"]["reason"] == "regression" for r in rejected)
    assert not any(op["op"] in ("supersede", "associate") for op in report["applied_ops"])
    assert b"supersedes" not in (root / ".tesserae" / "graph.json").read_bytes()
    monkeypatch.setattr(D, "_probe_scores", real)
    again = _run(root, dry_run=True)
    assert again["skipped_rejected"] > 0 and again["op_counts"]["supersede"] == 0


def test_paired_bootstrap_flags_only_a_drop() -> None:
    same = D.paired_bootstrap([0.5] * 10, [0.5] * 10, samples=200, alpha=0.05, seed=0)
    assert same == {"n": 10, "mean_delta": 0.0, "ci": [0.0, 0.0]}
    drop = D.paired_bootstrap([1.0] * 10, [0.0] * 5 + [1.0] * 5, samples=200, alpha=0.05, seed=0)
    assert drop["mean_delta"] == pytest.approx(-0.5) and drop["ci"][1] < 0


def test_cursor_scopes_regions_and_undo_restores_it(tmp_path: Path) -> None:
    from tesserae.session_chunks import SessionChunksDB, chunks_db_path

    root = _project(tmp_path)
    db = SessionChunksDB(chunks_db_path(root))
    db.record_turns("claude-code", "/x/s2.jsonl", "acct:s2", [
        {"timestamp": "2026-10-02T00:00:00Z", "role": "user", "text": "hello"},
    ])
    report = _run(root, dry_run=False)
    assert report["scope"] == "cursor"
    assert list(report["reviewed_sessions"]) == ["acct:s2"]
    head, sha = report["reviewed_sessions"]["acct:s2"]
    assert head == "2026-10-02T00:00:00Z"
    assert db.dream_cursors() == {"acct:s2": (head, sha)}
    # only the region holding s2's finding was planned: the banana node is elsewhere
    assert not any(op.get("node_id") == "Concept:ban" for op in report["applied_ops"])
    nothing = _run(root, dry_run=True)
    assert nothing["regions"] == [] and nothing["reviewed_sessions"] == {}
    D.undo_dream(root, "last")
    assert db.dream_cursors() == {}


def test_cursor_rereviews_a_session_whose_findings_arrive_after_its_turns(tmp_path: Path) -> None:
    """The tailer stores turns before the compile mints their findings."""
    from tesserae.session_chunks import SessionChunksDB, chunks_db_path

    graph = _graph()
    root = _project(tmp_path, graph)
    db = SessionChunksDB(chunks_db_path(root))
    db.record_turns("claude-code", "/x/s9.jsonl", "acct:s9", [
        {"timestamp": "2026-10-03T00:00:00Z", "role": "user", "text": "hello"},
    ])
    first = _run(root, dry_run=False)
    assert first["unmatched_sessions"] == ["acct:s9"]
    assert "acct:s9" in db.dream_cursors()
    # "the compile" now adds s9's two near-duplicate findings; no new turns
    graph.nodes += [
        _insight("n1", "Cursor must follow the compiled findings", "s9"),
        _insight("n2", "Cursor must follow the compiled findings always", "s9"),
    ]
    (root / ".tesserae" / "graph.json").write_text(graph.to_json(indent=2) + "\n", encoding="utf-8")
    again = _run(root, dry_run=True)
    assert again["scope"] == "cursor" and "acct:s9" in again["reviewed_sessions"]
    edges = {(op["edge"]["source"], op["edge"]["target"]) for op in again["promoted_ops"] if op["op"] == "supersede"}
    assert ("SessionInsight:n2", "SessionInsight:n1") in edges or ("SessionInsight:n1", "SessionInsight:n2") in edges


def test_probe_every_defers_writes_between_gated_passes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".tesserae" / "dream").mkdir()
    (root / ".tesserae" / "dream" / "state.json").write_text('{"passes": 1, "rejected": []}', encoding="utf-8")
    before = _snapshot(root)
    report = _run(root, dry_run=False, probe_every=2)
    assert report["deferred_reason"] and report["applied_ops"] == []
    assert _snapshot(root) == before
    assert _run(root, dry_run=False, probe_every=2)["applied_ops"]  # pass 2 is gated


def test_undo_refuses_after_graph_changed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _run(root, dry_run=False)
    graph_path = root / ".tesserae" / "graph.json"
    graph_path.write_text(_graph().to_json(indent=2) + "\n", encoding="utf-8")  # "a compile ran"
    with pytest.raises(D.DreamError, match="changed since dream pass"):
        D.undo_dream(root, "last")


def test_failed_apply_rolls_back_every_write(tmp_path: Path, monkeypatch) -> None:
    import tesserae.memory.associate as A

    root = _project(tmp_path)
    before = _snapshot(root)
    real = A.persist_links

    def boom(*_a, **_k):
        raise OSError("ENOSPC")

    monkeypatch.setattr(A, "persist_links", boom)
    with pytest.raises(OSError, match="ENOSPC"):
        _run(root, dry_run=False)
    # graph.json and node_memory were written before the failure; both are back
    assert _snapshot(root) == before
    journal = next((root / ".tesserae" / "dream" / "journal").glob("*/*.json"))
    assert json.loads(journal.read_text(encoding="utf-8"))["status"].startswith("rolled back")
    assert json.loads((root / ".tesserae" / "dream" / "state.json").read_text(encoding="utf-8"))["passes"] == 0
    monkeypatch.setattr(A, "persist_links", real)
    report = _run(root, dry_run=False)  # the whole pass again, not its leftovers
    assert report["op_counts"]["supersede"] == 1 and report["op_counts"]["associate"] >= 1
    D.undo_dream(root, "last")
    assert _snapshot(root) == before


def test_interrupted_apply_blocks_new_passes_until_undone(tmp_path: Path, monkeypatch) -> None:
    import tesserae.memory.associate as A

    root = _project(tmp_path)
    before = _snapshot(root)
    monkeypatch.setattr(A, "persist_links", lambda *_a, **_k: (_ for _ in ()).throw(OSError("EIO")))
    real_reverse = D._reverse
    monkeypatch.setattr(D, "_reverse", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk gone")))
    with pytest.raises(OSError, match="EIO"):
        _run(root, dry_run=False)
    monkeypatch.setattr(D, "_reverse", real_reverse)
    assert _snapshot(root) != before  # partial writes remain, as after a crash
    with pytest.raises(D.DreamError, match="interrupted mid-apply"):
        _run(root, dry_run=False)
    assert any("interrupted" in n for n in _run(root, dry_run=True)["notes"])
    D.undo_dream(root, "last")
    assert _snapshot(root) == before


def test_undo_orders_by_pass_index_not_filename_clock(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = _run(root, dry_run=False, ops=("supersede",))
    second = _run(root, dry_run=False, ops=("decay",))
    # pass 0 ran on a host whose clock is ahead: its journal name sorts last
    old = Path(first["journal"])
    old.rename(old.with_name("99991231T000000000000Z-000000.json"))
    assert D.undo_dream(root, "last")["undone"] == second["pass_id"]
    assert D.undo_dream(root, "last")["undone"] == first["pass_id"]


def test_probe_gate_uses_project_caches_and_a_local_backend(tmp_path: Path, monkeypatch) -> None:
    import tesserae.context_compiler as C
    import tesserae.retrieval.hybrid as H

    root = _project(tmp_path)
    seen: list = []
    real = C.compile_context
    monkeypatch.setattr(C, "compile_context", lambda *a, **k: seen.append(k) or real(*a, **k))
    asked: list = []
    monkeypatch.setattr(H, "active_embedding_backend", lambda prefer="auto": asked.append(prefer) or HashEmbeddingBackend())
    monkeypatch.setenv("TESSERAE_EMBEDDING_PREFER", "gemini")
    D.run_dream(root, dry_run=True, settings=D.DreamSettings(), backend=_FakeBackend(), cfg={}, env={})
    assert seen and all(k["project_root"] == str(root.resolve()) for k in seen)
    assert asked == ["model2vec"]  # never the active (possibly metered) backend


def test_should_stop_defers_ungated_regions_without_rejecting(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = D.run_dream(
        root, dry_run=False, settings=D.DreamSettings(), backend=_FakeBackend(),
        probe_backend=HashEmbeddingBackend(), cfg={}, env={}, should_stop=lambda: True,
    )
    assert report["deferred_regions"] and report["reviewed_sessions"] == {}
    assert not any(op["op"] in ("supersede", "associate") for op in report["applied_ops"])
    assert report["skipped_rejected"] == 0 and not D._load_state(root.resolve())["rejected"]
    assert _run(root, dry_run=True)["op_counts"]["supersede"] == 1  # still proposed next pass


def test_remove_links_leaves_a_pair_another_writer_repersisted(tmp_path: Path) -> None:
    from tesserae.memory.associate import _load_overlay_raw, persist_links, remove_links

    root = _project(tmp_path)
    persist_links(root, [("p", "q", 0.81), ("r", "s", 0.7)])
    persist_links(root, [("p", "q", 0.9)])  # another host rediscovered it, stronger
    remove_links(root, [("p", "q", 0.81), ("r", "s", 0.7)])
    assert _load_overlay_raw(root) == [["p", "q", 0.9]]


def test_lock_held_refuses(tmp_path: Path) -> None:
    from tesserae.locking import CompileLockHeldError, compile_lock, locking_unavailable

    if locking_unavailable():
        pytest.skip("no flock on this platform")
    root = _project(tmp_path)
    with compile_lock(root / ".tesserae"):
        # a preview only reads: it neither blocks nor fails behind a compile
        assert _run(root, dry_run=True)["promoted_ops"]
        with pytest.raises(CompileLockHeldError):
            _run(root, dry_run=False)


def test_cli_dry_run_json(tmp_path: Path, capsys, monkeypatch) -> None:
    from tesserae.cli import main

    root = _project(tmp_path)
    (root / ".tesserae" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(D, "resolve_backend", lambda: None)
    assert main(["dream", "--project", str(root), "--dry-run", "--json", "--no-gate"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True and report["op_counts"]["supersede"] == 1
    assert any("associate: no semantic embedding backend" in n for n in report["notes"])


def test_mcp_dream_previews_freely_and_gates_writes(tmp_path: Path, monkeypatch) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    root = _project(tmp_path)
    monkeypatch.setattr(D, "resolve_backend", lambda: None)
    monkeypatch.delenv("TESSERAE_DREAM", raising=False)
    server = LLMWikiMCPServer(default_graph_path=None)
    args = {"graph_path": str(root / ".tesserae" / "graph.json"), "gate": False}
    preview = server._mcp_dream(dict(args))
    assert preview["dry_run"] is True and preview["op_counts"]["supersede"] == 1
    with pytest.raises(ValueError, match="opt-in"):
        server._mcp_dream({**args, "dry_run": False})
    monkeypatch.setenv("TESSERAE_DREAM", "1")
    applied = server._mcp_dream({**args, "dry_run": False})
    assert applied["applied_ops"]
    assert server._mcp_dream({**args, "undo": "last"})["undone"] == applied["pass_id"]


def test_daemon_runs_dream_instead_of_ungated_ops_only_when_opted_in(tmp_path: Path, monkeypatch) -> None:
    from tesserae.engine.daemon import Daemon

    root = _project(tmp_path)
    calls: list = []
    monkeypatch.setattr(D, "run_dream", lambda *a, **k: calls.append("dream") or {"op_counts": {}})
    daemon = Daemon(
        root,
        distill=lambda *a, **k: calls.append("distill") or {},
        associate=lambda *a, **k: calls.append("associate") or {},
    )
    monkeypatch.setattr(daemon, "_summarize_once", lambda graph: {})
    monkeypatch.setattr(daemon, "_brief_once", lambda graph: {})
    monkeypatch.delenv("TESSERAE_DREAM", raising=False)
    daemon._consolidate_once()
    assert calls == ["distill", "associate"]
    calls.clear()
    monkeypatch.setenv("TESSERAE_DREAM", "1")
    daemon._consolidate_once()
    assert calls == ["dream"]


def test_max_regions_defers_without_starving(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = _run(root, dry_run=False, max_regions=1)
    assert first["deferred_regions"] and len(first["regions"]) == 1
    second = _run(root, dry_run=False, max_regions=1)
    assert second["regions"] and second["regions"][0]["region"] == first["deferred_regions"][0]


def test_probes_file_replaces_sampled_probes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".tesserae" / "dream").mkdir()
    (root / ".tesserae" / "dream" / "probes.jsonl").write_text(
        json.dumps({"query": "atomic writes tmp suffix", "gold": ["SessionInsight:a"]}) + "\n",
        encoding="utf-8",
    )
    report = _run(root, dry_run=True)
    assert {r["gate"].get("n") for r in report["regions"] if "n" in r["gate"]} == {1}
    (root / ".tesserae" / "dream" / "probes.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(D.DreamError, match="probes.jsonl:1"):
        _run(root, dry_run=True)


def _variant(seed: int) -> ResearchGraph:
    """A seeded fixture: random near-duplicate findings across random sessions."""
    import random

    rng = random.Random(seed)
    stems = ["atomic tmp writes", "louvain regions scope", "probe gate bootstrap", "cursor per session"]
    session = ResearchNode(id="Session:s", name="variant session", type=ResearchNodeType.SESSION)
    nodes, edges = [session], []
    for i in range(rng.randint(4, 9)):
        base = rng.choice(stems)
        extra = " ".join(rng.sample(["need", "pid", "random", "always", "suffix", "here"], rng.randint(0, 2)))
        node = _insight(f"v{i}", f"{base} {extra}".strip(), f"s{rng.randint(1, 3)}")
        nodes.append(node)
        edges.append(ResearchEdge(source=node.id, target=session.id, type="discussed_in", evidence="fixture"))
    nodes.append(ResearchNode(id="Concept:p", name="Personalized PageRank ranking", type=ResearchNodeType.CONCEPT))
    nodes.append(ResearchNode(id="Concept:q", name="PageRank variant scoring", type=ResearchNodeType.CONCEPT))
    return ResearchGraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize("seed", range(20))
def test_acceptance_contracts_hold_across_seeded_variants(tmp_path: Path, seed: int) -> None:
    root = _project(tmp_path, _variant(seed))
    before = _snapshot(root)
    planned = _run(root, dry_run=True)["promoted_ops"]
    report = _run(root, dry_run=False)
    assert sorted(map(_key, planned)) == sorted(map(_key, report["applied_ops"]))
    assert _run(root, dry_run=True)["promoted_ops"] == []
    if report["applied_ops"]:
        D.undo_dream(root, "last")
    assert _snapshot(root) == before


def test_dream_enabled_resolution() -> None:
    assert not D.dream_enabled({}, {})
    assert D.dream_enabled({"dream": {"enabled": True}}, {})
    assert not D.dream_enabled({"dream": {"enabled": True}}, {"TESSERAE_DREAM": "0"})
    assert D.dream_enabled({}, {"TESSERAE_DREAM": "yes"})
