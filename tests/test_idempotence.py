"""End-to-end idempotence tests for ``ProjectWiki.compile``.

These tests are the production-ready proof of §13's "byte-identical site
output" definition: running ``project compile`` twice in a row over the same
corpus must leave every file under ``.tesserae/site/`` and ``.tesserae/wiki/``
byte-identical, except for the two append-only history ledgers
(``.build-history.jsonl`` and ``.history.jsonl``) which intentionally record
each build / each rewrite.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Dict, Iterable, Set

import pytest

from tesserae.project import ProjectWiki, SessionExtractionOptions


WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


@pytest.fixture(autouse=True)
def _deterministic_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the LLM-backed ``community_summaries`` pass off for these
    byte-idempotence tests.

    Byte-idempotence is a guarantee of the DETERMINISTIC compile. The
    default-on ``community_summaries`` pass mints a ``CommunitySummary`` node
    only when a live LLM client succeeds, so on a dev box with codex/claude
    configured but intermittently available, one compile mints a summary the
    other skips → ``graph.json`` diverges. CI (no LLM) was always deterministic;
    this makes local runs match. See the identical guard in
    ``test_byte_idempotence_phase5``.
    """
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "false")


def _hash_tree(root: Path, exclude: Iterable[str] = ()) -> Dict[str, str]:
    """Map every file under ``root`` to ``sha256(content)``.

    Paths are returned relative to ``root`` with forward slashes so the result
    is stable across platforms. Files whose *basename* is in ``exclude`` are
    skipped — used to drop the append-only ledger files from the comparison.
    """
    skip: Set[str] = set(exclude)
    out: Dict[str, str] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in skip:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _seed_project(project_root: Path) -> ProjectWiki:
    """Copy the wiki_corpus fixture into ``project_root`` and init the wiki."""
    project_root.mkdir(parents=True, exist_ok=True)
    # Mirror the fixture layout under the project root: ``data/`` and ``docs/``
    # are auto-included by ``compile()`` (data/ via the implicit data-dir hook
    # and docs/ via the default sources list when README.md/docs/ exist).
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    return ProjectWiki.init(project_root, name="idempotence_test")


def test_compile_is_byte_idempotent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()

    site_dir = wiki.paths.site
    wiki_dir = wiki.paths.wiki

    snapshot_site_a = _hash_tree(site_dir, exclude={".build-history.jsonl"})
    snapshot_wiki_a = _hash_tree(wiki_dir, exclude={".history.jsonl"})

    # Sanity: the first compile actually produced output, and our exclude
    # filter didn't accidentally swallow everything.
    assert snapshot_site_a, "first compile produced no site files"
    assert snapshot_wiki_a, "first compile produced no wiki files"

    # Second compile over the unchanged corpus.
    wiki.compile()

    snapshot_site_b = _hash_tree(site_dir, exclude={".build-history.jsonl"})
    snapshot_wiki_b = _hash_tree(wiki_dir, exclude={".history.jsonl"})

    assert snapshot_site_b == snapshot_site_a, (
        "second compile should leave .tesserae/site/ byte-identical (excluding "
        ".build-history.jsonl); diff: "
        f"{_diff_keys(snapshot_site_a, snapshot_site_b)}"
    )
    assert snapshot_wiki_b == snapshot_wiki_a, (
        "second compile should leave .tesserae/wiki/ byte-identical (excluding "
        ".history.jsonl); diff: "
        f"{_diff_keys(snapshot_wiki_a, snapshot_wiki_b)}"
    )


def test_hierarchy_sidecar_is_byte_idempotent(tmp_path: Path) -> None:
    """``.tesserae/hierarchy.json`` (Descent PR4) is a pure function of graph
    content: recompiling an unchanged corpus must rewrite it byte-identically,
    and its shape must match the sidecar contract (schema_version 1, dendrogram
    levels finest→coarsest keyed by ``community_id`` with sorted members,
    sorted high-degree hub ids)."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()
    sidecar = wiki.paths.hierarchy
    assert sidecar.exists(), "compile did not write the hierarchy sidecar"
    first_bytes = sidecar.read_bytes()

    payload = json.loads(first_bytes)
    assert payload["schema_version"] == 1
    assert isinstance(payload["levels"], list)
    assert payload["levels"], "expected at least one dendrogram level"
    for level in payload["levels"]:
        assert isinstance(level, dict)
        for cid, members in level.items():
            assert cid.startswith("CommunitySummary:")
            assert len(members) > 1, "singleton communities must be filtered"
            assert members == sorted(members)
    assert payload["hubs"] == sorted(payload["hubs"])

    wiki.compile()
    assert sidecar.read_bytes() == first_bytes, (
        "hierarchy sidecar is not byte-idempotent across recompiles of an "
        "unchanged corpus"
    )


def test_synthesis_pages_have_no_generated_at_on_disk(tmp_path: Path) -> None:
    """The on-disk synthesis frontmatter must not carry a build timestamp."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki.compile()

    syntheses_dir = wiki.paths.wiki / "syntheses"
    md_files = sorted(p for p in syntheses_dir.glob("*.md"))
    assert md_files, "expected at least one synthesis page"
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        assert "generated_at" not in text, (
            f"{path} still contains a generated_at field; the on-disk "
            "frontmatter must be timestamp-free for byte-idempotence"
        )


def test_history_ledger_records_writes(tmp_path: Path) -> None:
    """The synthesis history ledger should grow when content actually changes."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()
    ledger = wiki.paths.wiki / "syntheses" / ".history.jsonl"
    assert ledger.exists(), "expected synthesis history ledger after first compile"
    first_lines = ledger.read_text(encoding="utf-8").splitlines()
    assert first_lines, "ledger should be non-empty after first compile"

    # Second compile rewrites nothing → ledger does not grow.
    wiki.compile()
    second_lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(second_lines) == len(first_lines), (
        "ledger should not grow when nothing rewrote; "
        f"first={len(first_lines)} second={len(second_lines)}"
    )


def test_build_history_ledger_grows_each_compile(tmp_path: Path) -> None:
    """The build-history ledger appends one line per compile, even if nothing changed.

    Codex review F-11 fixed: the ledger now lives at the project-wiki root
    (``.tesserae/.build-history.jsonl``) so it survives the rebuild of
    ``site/``. ``ProjectWiki._append_build_history`` writes one line per
    compile recording node/edge counts of both partitions.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()
    build_history = wiki.paths.build_history
    assert build_history.exists(), "expected build-history ledger after first compile"
    assert build_history.parent == wiki.root, (
        "ledger must live at the project-wiki root, not inside the wiped site/ dir"
    )
    first_lines = [
        line for line in build_history.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(first_lines) == 1

    wiki.compile()
    second_lines = [
        line for line in build_history.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(second_lines) == 2, (
        "second compile should append a new build-history entry; "
        f"got {len(second_lines)} line(s)"
    )


def _seed_recurring_sessions(wiki: ProjectWiki) -> None:
    """Seed 3 distinct harness sessions sharing a decision (drives numeric
    recurrence confidence) plus a near-dup decision pair (drives a
    deterministic ``supersedes`` edge on the default compile path)."""
    from tesserae.harness_sessions import HarnessSession
    from tesserae.harness_sessions_db import HarnessSessionsDB

    db_path = wiki.project_root / ".tesserae" / "harness_sessions.db"
    db = HarnessSessionsDB(db_path)
    shared = "Cache the deterministic supersede verdict on disk to skip the LLM"
    near_dup = "Disk cache the supersede verdict to avoid calling the LLM"
    for i in range(3):
        db.upsert(
            HarnessSession(
                id=f"recur-session-{i:03d}",
                slug=f"recur-session-{i}",
                harness="claude-code",
                agent_label="Claude Code",
                project_name="idempotence_test",
                project_root=str(wiki.project_root.resolve()),
                started_at=f"2026-05-2{i}T10:00:00Z",
                ended_at=f"2026-05-2{i}T11:00:00Z",
                title=f"recurring decision session {i}",
                # Same decision across 3 distinct sessions -> recurrence; the
                # near-dup in session 0 drives a supersedes edge.
                decisions=[shared] + ([near_dup] if i == 0 else []),
            )
        )


def test_compile_byte_idempotent_with_confidence_and_supersedes(tmp_path: Path) -> None:
    """Byte-idempotence guard covering numeric confidence + supersedes edges.

    Two compiles of the same multi-session corpus must produce byte-identical
    graph.json AND temporal_facts.jsonl; numeric confidence must surface in
    temporal_facts; supersedes edges must be minted on the default (no-creds)
    path; and NO node.metadata may carry a baked confidence key.
    """
    from tesserae.memory.supersede import SUPERSEDE_EDGE

    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_recurring_sessions(wiki)
    opts = SessionExtractionOptions(enabled=True, llm_enabled="false")

    wiki.compile(session_options=opts, vault_pull=False)
    graph_path = wiki.paths.graph
    facts_path = wiki.paths.temporal_facts
    assert graph_path.exists() and facts_path.exists()

    graph_a = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    facts_a = hashlib.sha256(facts_path.read_bytes()).hexdigest()

    # Numeric confidence present in temporal_facts.jsonl.
    facts = [
        json.loads(line)
        for line in facts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    numeric = [
        f for f in facts
        if _is_numeric_confidence(f.get("confidence"))
    ]
    assert numeric, (
        "expected at least one temporal fact with numeric confidence "
        f"(0->1); got confidences: {sorted({f.get('confidence') for f in facts})}"
    )
    for f in numeric:
        assert 0.0 <= float(f["confidence"]) <= 1.0

    # supersedes edges minted on the default compile path (no credentials).
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    supersede_edges = [e for e in graph["edges"] if e.get("type") == SUPERSEDE_EDGE]
    assert supersede_edges, "expected supersedes edges on the default compile path"

    # NO node carries a sidecar-baked confidence in graph.json (byte-idempotence
    # invariant — the corpus never sets it, so assert absence outright).
    for node in graph["nodes"]:
        for field_name in ("confidence", "proposed_type"):
            assert field_name not in (node.get("metadata") or {}), (
                f"node {node['id']} leaked {field_name} into graph.json metadata"
            )

    # Second compile over the unchanged corpus -> byte-identical.
    wiki.compile(session_options=opts, vault_pull=False)
    graph_b = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    facts_b = hashlib.sha256(facts_path.read_bytes()).hexdigest()

    assert graph_b == graph_a, "graph.json not byte-identical across two compiles"
    assert facts_b == facts_a, (
        "temporal_facts.jsonl not byte-identical across two compiles"
    )


def _is_numeric_confidence(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        float(value)
    except ValueError:
        return False
    return True


def _diff_keys(a: Dict[str, str], b: Dict[str, str]) -> str:
    """Render a short diagnostic of where two file-hash maps diverge."""
    keys = sorted(set(a) | set(b))
    rows = []
    for key in keys:
        ha = a.get(key, "<missing>")
        hb = b.get(key, "<missing>")
        if ha != hb:
            rows.append(f"  {key}: {ha[:8]} -> {hb[:8]}")
    if not rows:
        return "(no differences)"
    return "\n" + "\n".join(rows[:20])


# ---------------------------------------------------------------------------
# Output snapshot hashing (tesserae/output_snapshot.py) — the no-op detector
# and byte-idempotence tripwire wired into ProjectWiki.compile. See
# docs/superpowers/plans/2026-07-09-openwiki-output-snapshot-plan.md.
# ---------------------------------------------------------------------------


def test_snapshot_output_stable_and_ignores_ledgers_and_state(tmp_path: Path) -> None:
    from tesserae.output_snapshot import snapshot_output, write_state
    wiki = _seed_project(tmp_path / "proj")
    wiki.compile(session_options=SessionExtractionOptions(enabled=False))
    root = wiki.root
    first = snapshot_output(root)
    # Excluded churn: ledgers, state file, lint noise at the root.
    with (root / ".build-history.jsonl").open("a") as fh:
        fh.write('{"noise": true}\n')
    (root / "wiki" / "syntheses").mkdir(parents=True, exist_ok=True)
    with (root / "wiki" / "syntheses" / ".history.jsonl").open("a") as fh:
        fh.write('{"noise": true}\n')
    write_state(root / "output-snapshot.json", first, changed=False)
    assert snapshot_output(root) == first
    # Included churn: a projection file flips only the projections part.
    (root / "wiki" / "drift.md").write_text("drift", encoding="utf-8")
    second = snapshot_output(root)
    assert second.projections_sha256 != first.projections_sha256
    assert second.graph_sha256 == first.graph_sha256
    assert second.output_sha256 != first.output_sha256


def test_snapshot_covers_temporal_fact_values_but_not_sidecar_confidence(
    tmp_path: Path,
) -> None:
    """D5: a drift in ``valid_from`` must trip the tripwire; a confidence bump must not.

    ``temporal_facts.jsonl`` was excluded from the hash wholesale because ONE
    of its fields (``confidence``) is read from the mutable ``node_memory``
    sidecar — which left every other field, including the 63,780 ``valid_from``
    values the path rung now decides, invisible to the only guard that watches
    real compiles rather than the fixture corpus.

    (63,780 is the shipped, root-bounded figure. An earlier draft of this
    docstring said 64,883, which was measured before the rung was bounded to
    the root-relative path — i.e. while a dated ancestor directory could still
    date a node. Quoting the pre-fix number here would have advertised the
    defect's reach as the feature's.) Everything except
    ``confidence`` is a pure function of the graph layer, so the file is hashed
    with that one field elided: full coverage of the derived values, and no
    false alarm when recurrence reinforcement bumps a confidence.
    """
    from tesserae.output_snapshot import snapshot_output

    wiki = _seed_project(tmp_path / "proj")
    wiki.compile(session_options=SessionExtractionOptions(enabled=False))
    root = wiki.root
    facts_path = wiki.paths.temporal_facts
    assert facts_path.exists(), "compile must write temporal_facts.jsonl"
    first = snapshot_output(root)
    original = facts_path.read_text(encoding="utf-8")

    def rewrite(mutate) -> None:
        lines = [json.loads(ln) for ln in original.splitlines() if ln.strip()]
        assert lines, "fixture corpus must project at least one fact"
        mutate(lines[0])
        facts_path.write_text(
            "".join(json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n" for f in lines),
            encoding="utf-8",
        )

    # A drifted valid_from IS projection drift -> the hash must move.
    rewrite(lambda fact: fact.__setitem__("valid_from", "1999-01-01"))
    drifted = snapshot_output(root)
    assert drifted.projections_sha256 != first.projections_sha256, (
        "a changed valid_from must reach the drift guard"
    )
    assert drifted.graph_sha256 == first.graph_sha256

    # A sidecar-sourced confidence is NOT projection drift -> the hash holds.
    rewrite(lambda fact: fact.__setitem__("confidence", "0.99"))
    reconfidenced = snapshot_output(root)
    assert reconfidenced.projections_sha256 == first.projections_sha256, (
        "a node_memory confidence bump must not be reported as projection drift"
    )


def test_snapshot_output_handles_missing_artifacts(tmp_path: Path) -> None:
    from tesserae.output_snapshot import snapshot_output
    empty = snapshot_output(tmp_path / "nothing-here")
    assert empty == snapshot_output(tmp_path / "nothing-here")  # deterministic
    assert len(empty.graph_sha256) == 64 and len(empty.projections_sha256) == 64


def test_compile_result_reports_output_unchanged_on_recompile(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    first = wiki.compile(session_options=opts)
    second = wiki.compile(session_options=opts)
    assert first["output_changed"] is True          # first compile populated an empty tree
    assert second["output_changed"] is False        # no-op detected
    assert second["idempotence_suspect"] is False
    assert second["output_sha256"] == first["output_sha256"]


def test_compile_result_reports_output_changed_on_new_source(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    first = wiki.compile(session_options=opts)
    (tmp_path / "proj" / "docs" / "new-note.md").write_text(
        "# New Note\n\nA fresh concept: snapshot gating.\n", encoding="utf-8"
    )
    second = wiki.compile(session_options=opts)
    assert second["output_changed"] is True
    assert second["output_sha256"] != first["output_sha256"]
    assert second["idempotence_suspect"] is False   # graph layer changed too


def test_compile_flags_idempotence_suspect_on_projection_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the historical failure class: a projector emitting bytes not
    derived from the graph. Graph layer identical + projections drifted must
    raise the tripwire."""
    from tesserae.karpathy_layer import KarpathyLayerWriter
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    wiki.compile(session_options=opts)

    original = KarpathyLayerWriter.write_all
    def drifting(self, graph, build_history_path=None):
        written = original(self, graph, build_history_path)
        (Path(self.wiki_root) / "drift.md").write_text("wall-clock leak", encoding="utf-8")
        return written
    monkeypatch.setattr(KarpathyLayerWriter, "write_all", drifting)

    second = wiki.compile(session_options=opts)
    assert second["idempotence_suspect"] is True
    assert second["output_changed"] is True


def test_output_snapshot_state_file_is_byte_stable(tmp_path: Path) -> None:
    wiki = _seed_project(tmp_path / "proj")
    opts = SessionExtractionOptions(enabled=False)
    wiki.compile(session_options=opts)
    state_path = wiki.paths.output_snapshot
    assert state_path.exists()
    first_bytes = state_path.read_bytes()
    payload = json.loads(first_bytes)
    assert set(payload) == {"changed", "graph_sha256", "output_sha256", "projections_sha256"}
    wiki.compile(session_options=opts)
    second = json.loads(state_path.read_bytes())
    assert second["changed"] is False
    # Identical hashes both runs; only `changed` may differ on the first run.
    assert second["output_sha256"] == payload["output_sha256"]
