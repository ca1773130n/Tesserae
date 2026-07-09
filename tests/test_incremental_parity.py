"""CMP-03 golden parity: incremental compile == full compile, byte-for-byte.

This is the decisive correctness gate for Phase 4. The provenance-driven
differ (Plan 04-03) is only trustworthy as the default compile path if an
*incremental* compile of K changed files produces the SAME canonical
``graph.json`` (and site/wiki tree) as a *full* compile of the same final
corpus — for K in {1, 5, 21} — and the documented 2400->1700 collapse never
reappears.

Design notes (why it is built the way it is):

* **Same project root for both arms.** ``graph.json`` and the derived
  ``site/`` artifacts embed *absolute* ``source_path`` values (and gzip
  streams that encode them). Comparing two different project roots would
  diverge on the root prefix alone, masking real differ behaviour. So each
  scenario seeds ONE root, runs the incremental compile, snapshots the tree,
  then forces a FULL recompile of the identical on-disk corpus in the SAME
  root and snapshots again. Any remaining byte difference is a genuine
  incremental-vs-full divergence, not path noise.

* **Deterministic community-summary client.** ``_merge_community_summaries``
  otherwise calls a non-deterministic JSON client whose free-text
  ``description`` varies run-to-run (it is cached per community-id, but a
  cache miss re-rolls the text). That nondeterminism is orthogonal to the
  differ, so we pin it with a scripted stub via
  ``set_community_summaries_test_client`` — both arms then see identical
  summary content and the test isolates the differ.

* **Synthetic corpus for K=21.** The committed ``wiki_corpus`` fixture has
  only ~9 source files, too few to change 21. ``_build_corpus`` lays down a
  multi-file research corpus with a *cross-file concept* (a shared
  ``ResearchField`` plus authors recurring across papers) so the anti-collapse
  path — a concept owned by UNCHANGED files surviving a changed-file
  re-extraction — is genuinely exercised at K=21.

The ``_hash_tree`` machinery is imported from ``tests.test_idempotence`` so
the exclusion semantics stay in lockstep with the idempotence suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

import pytest

import tesserae.project as project_mod
from tesserae.project import ProjectWiki

try:  # pytest puts the tests/ dir on sys.path -> import as a top-level module.
    from test_idempotence import _diff_keys, _hash_tree
except ImportError:  # fallback when tests/ is importable as a package
    from tests.test_idempotence import _diff_keys, _hash_tree

# Byte-compare everything EXCEPT the provenance sidecar (SQLite ``.db`` — the
# deterministic provenance timestamps live only here, intentionally outside the
# canonical artifact) and the append-only history ledgers (one line per
# build / per rewrite by design). ``log.md`` is the human-readable rendering of
# ``.build-history.jsonl`` (one timestamped table row per compile, derived
# straight from that ledger via ``KarpathyLayerWriter._render_log``); both arms
# share ONE root, so the full-recompile arm legitimately carries one extra build
# row. It belongs with the ledgers it projects, not with the canonical graph
# artifacts the parity assertion guards. ``output-snapshot.json`` is the
# compile's no-op-detector state file (tesserae/output_snapshot.py): its
# ``changed`` bool records the TRANSITION of the last compile, so it
# legitimately differs between the incremental arm (inputs mutated ->
# changed=true) and the full-recompile arm of the identical corpus
# (changed=false). Like manifest.json it is input/transition state, excluded
# from the snapshot hash by construction — not a canonical artifact.
PARITY_EXCLUDE = {
    "sqlite.db",
    ".build-history.jsonl",
    ".history.jsonl",
    "log.md",
    "output-snapshot.json",
}

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


class _ScriptedCommunityClient:
    """Deterministic stand-in for the community-summary LLM client.

    Returns a fixed triple so the COMMUNITY_SUMMARY node is byte-stable across
    both compile arms. The real client's free-text description is the only
    run-to-run nondeterministic field in the pipeline and is unrelated to the
    differ under test.
    """

    def complete_json(self, *, system, user, schema_name, cache_key=None):  # noqa: ANN001, D401
        return {
            "title": "Cross-File Concept Cluster",
            "description": "A deterministic cluster summary fixed for parity testing.",
            "tags": ["parity", "incremental", "fixture"],
        }


@pytest.fixture(autouse=True)
def _pin_community_client():
    """Pin the community-summary client for every test, restore afterwards."""
    project_mod.set_community_summaries_test_client(_ScriptedCommunityClient())
    try:
        yield
    finally:
        project_mod.set_community_summaries_test_client(None)


# --------------------------------------------------------------------------- #
# Corpus construction
# --------------------------------------------------------------------------- #

# The shared cross-file concept: a research field referenced by EVERY paper,
# plus authors that recur across papers. A concept owned by files we DON'T
# change in an incremental run must survive — that is the anti-collapse target.
_SHARED_FIELD = "Compositional Scene Understanding"
_SHARED_AUTHORS = ["Ada Lovelace", "Alan Turing", "Grace Hopper"]


def _paper_md(idx: int) -> str:
    arxiv = f"2604.3{idx:04d}"
    # Rotate authors so several papers co-cite the same Person; every paper
    # anchors the SAME research field so the field node is owned cross-file.
    a, b = _SHARED_AUTHORS[idx % 3], _SHARED_AUTHORS[(idx + 1) % 3]
    return (
        f"# 논문 분석: {arxiv}\n\n"
        f"> - arxiv: https://arxiv.org/abs/{arxiv}\n"
        f"> - 분석일: 2026-04-25\n\n"
        f"# Synthetic Paper {idx:03d}\n\n"
        f"저자: {a}, {b}.\n\n"
        f"This paper studies {_SHARED_FIELD} using the Transformer model. "
        f"It contributes a method numbered {idx:03d} to the shared field.\n"
    )


def _build_corpus(root: Path, *, n_papers: int = 30) -> List[Path]:
    """Lay down a synthetic multi-file research corpus under ``root``.

    Returns the list of paper source files in a stable order so callers can
    deterministically pick the first K to mutate.
    """
    root.mkdir(parents=True, exist_ok=True)
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "architecture.md").write_text(
        "# Architecture\n\nOverview of the synthetic parity corpus.\n",
        encoding="utf-8",
    )

    papers_root = root / "data" / "research" / "daily" / "2026-04-25" / "papers"
    paper_files: List[Path] = []
    for idx in range(n_papers):
        arxiv = f"2604.3{idx:04d}"
        pdir = papers_root / arxiv
        pdir.mkdir(parents=True, exist_ok=True)
        pf = pdir / "paper.md"
        pf.write_text(_paper_md(idx), encoding="utf-8")
        paper_files.append(pf)
    return paper_files


def _seed_wiki(root: Path) -> ProjectWiki:
    wiki = ProjectWiki.init(root, name="parity_test")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return wiki


def _graph(wiki: ProjectWiki) -> dict:
    return json.loads(wiki.paths.graph.read_text(encoding="utf-8"))


def _node_count(wiki: ProjectWiki) -> int:
    return len(_graph(wiki).get("nodes", []))


def _node_ids(wiki: ProjectWiki) -> set:
    return {n["id"] for n in _graph(wiki).get("nodes", [])}


def _mutate(path: Path, marker: str) -> None:
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n\nAddendum: {marker}.\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Golden parity test
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("k", [1, 5, 21])
def test_incremental_equals_full_compile(tmp_path: Path, k: int) -> None:
    """Incremental(K changed) graph.json/site == full compile of same corpus.

    Same root, two arms:
      1. seed full compile of original corpus (populates provenance + prior
         graph + community cache),
      2. mutate the first K paper files,
      3. INCREMENTAL compile (changed_only, changed_paths = the K files) ->
         snapshot C,
      4. FULL recompile of the identical on-disk corpus in the SAME root ->
         snapshot B (ground truth),
      5. assert B == C byte-for-byte (excluding the .db sidecar + ledgers).
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=30)
    assert k <= len(papers), f"corpus too small for K={k}"

    wiki = _seed_wiki(root)
    wiki.compile()  # seed: provenance + prior graph + community cache

    pre_edit_full_count = _node_count(wiki)
    pre_edit_full_ids = _node_ids(wiki)
    assert pre_edit_full_count > 0, "seed compile produced no nodes"

    # The shared research field is owned cross-file by EVERY paper; it must
    # survive even when only K papers are re-extracted (anti-collapse target).
    field_ids = {nid for nid in pre_edit_full_ids if nid.startswith("ResearchField:")}
    assert field_ids, "expected a cross-file ResearchField concept in the corpus"

    # (2) mutate the first K paper files.
    changed = papers[:k]
    for i, pf in enumerate(changed):
        _mutate(pf, f"k{k}-edit-{i}")

    # (3) incremental compile of exactly those K files.
    wiki.compile(changed_only=True, changed_paths=list(changed))
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    incr_count = _node_count(wiki)
    incr_ids = _node_ids(wiki)

    # ---- Anti-collapse guards (these are the 2400->1700 regression sentinels) ----
    # The cross-file concept owned by unchanged files MUST survive.
    assert field_ids <= incr_ids, (
        "cross-file ResearchField concept was tombstoned by a changed-file "
        f"re-extraction (K={k}) — the cross-file collapse has reappeared"
    )
    # No catastrophic node-count collapse relative to the pre-edit full graph.
    assert incr_count >= pre_edit_full_count, (
        f"incremental node count collapsed at K={k}: "
        f"{pre_edit_full_count} -> {incr_count}"
    )

    # (4) full recompile of the identical on-disk corpus, SAME root.
    wiki.compile()
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    full_count = _node_count(wiki)

    # Node counts must match exactly between the two arms.
    assert incr_count == full_count, (
        f"node count differs incremental({incr_count}) != full({full_count}) "
        f"at K={k}"
    )

    # (5) GOLDEN PARITY: byte-identical canonical artifact + site/wiki tree.
    assert incr_tree == full_tree, (
        "GOLDEN PARITY FAILED: incremental compile is NOT byte-identical to a "
        f"full compile of the same final corpus at K={k}.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )


# --------------------------------------------------------------------------- #
# Idempotence-with-provenance regression guards (Pitfall 1)
# --------------------------------------------------------------------------- #


def test_incremental_compile_is_byte_idempotent(tmp_path: Path) -> None:
    """An EMPTY incremental run is a no-op on the canonical artifact.

    With ``incremental_compile=true`` a full compile followed by an
    incremental compile with NO changed files must leave the wiki/site tree
    byte-identical — proving the provenance layer did not leak wall-clock
    nondeterminism into ``graph.json`` (04-RESEARCH Pitfall 1) and that the
    empty incremental path is a clean no-op.
    """
    root = tmp_path / "project"
    _build_corpus(root, n_papers=12)
    wiki = _seed_wiki(root)

    wiki.compile()
    before = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    assert before, "first compile produced no comparable files"

    # Empty incremental run: changed_only with no changed paths.
    wiki.compile(changed_only=True, changed_paths=[])
    after = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert after == before, (
        "empty incremental run was not byte-idempotent; "
        f"diff:{_diff_keys(before, after)}"
    )


def test_provenance_timestamps_absent_from_graph_json(tmp_path: Path) -> None:
    """Provenance timestamps must stay confined to the SQLite sidecar.

    ``graph.json`` is the byte-compared canonical artifact, so it must NOT
    contain ``first_seen_at`` / ``last_updated_at`` — those live only in the
    excluded ``node_provenance`` table.
    """
    root = tmp_path / "project"
    _build_corpus(root, n_papers=8)
    wiki = _seed_wiki(root)
    wiki.compile()

    graph_text = wiki.paths.graph.read_text(encoding="utf-8")
    assert "first_seen_at" not in graph_text, (
        "graph.json leaked the provenance field first_seen_at"
    )
    assert "last_updated_at" not in graph_text, (
        "graph.json leaked the provenance field last_updated_at"
    )


# --------------------------------------------------------------------------- #
# SUBTRACTIVE-edit parity (Codex B1/B2): incremental must DROP nodes/edges that
# a changed (or deleted) file stops asserting, matching a full compile. The
# additive K=1/5/21 cases above never exercise removal — these do.
# --------------------------------------------------------------------------- #


_STUB_PAPER = (
    "# 논문 분석: stub\n\n"
    "# Synthetic Paper STUB\n\n"
    "This file no longer references the shared field or any authors.\n"
)


def test_incremental_equals_full_after_content_reduction(tmp_path: Path) -> None:
    """Rewriting a paper to DROP its authors + shared-field references must
    remove the now-unasserted authored_by / field edges (their endpoints
    survive via other papers) — byte-identical to a full compile. Guards Codex
    B1 (stale edges linger) on a CONTENT-reducing edit.
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=30)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed

    # Subtractive edit: paper 0 stops asserting its authors + the shared field.
    # Those Person/ResearchField nodes survive (owned by papers 1..29), but the
    # edges FROM paper 0 to them must disappear.
    papers[0].write_text(_STUB_PAPER, encoding="utf-8")

    wiki.compile(changed_only=True, changed_paths=[papers[0]])
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    wiki.compile()  # full recompile, same root, identical on-disk corpus
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert incr_tree == full_tree, (
        "SUBTRACTIVE PARITY FAILED (content reduction): incremental kept stale "
        "nodes/edges that a full compile dropped.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )


def test_incremental_equals_full_after_file_deletion(tmp_path: Path) -> None:
    """Deleting a paper file entirely must remove its source node and all its
    incident edges on the incremental path — byte-identical to a full compile
    of the smaller corpus. Strongest guard for Codex B1 (orphaned incident
    edges) + node removal + deleted-changed_path handling.
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=30)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed
    seed_ids = _node_ids(wiki)

    deleted = papers[0]
    deleted.unlink()  # the file is gone; its changed_path no longer exists

    wiki.compile(changed_only=True, changed_paths=[deleted])
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    incr_ids = _node_ids(wiki)

    # Shared cross-file concepts (field/authors owned by the other 29 papers)
    # must survive the deletion.
    field_ids = {nid for nid in seed_ids if nid.startswith("ResearchField:")}
    assert field_ids <= incr_ids, "shared field wrongly dropped by a single-file deletion"

    wiki.compile()  # full recompile of the 29-paper corpus, same root
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert incr_tree == full_tree, (
        "SUBTRACTIVE PARITY FAILED (file deletion): incremental left a dangling "
        "source node / incident edges that a full compile dropped.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )


# --------------------------------------------------------------------------- #
# HARD-EDIT-SHAPE parity (Plan 04.1-04): the decisive proof that incremental ==
# full byte-for-byte across every edit shape, not just additive/reduction/
# deletion. Three new cases exercise the paths the earlier gates never touched:
#   EC-1 file RENAME           (delete old path + add new path, same content)
#   EC-2 alias-identity change (edit the merge-winning source so the canonical
#                               alias winner flips — exercises Plan 03 re-extract)
#   EC-3 both-endpoints-move   (an edge whose BOTH endpoints' sources change AND
#                               the edge-asserting file changes — edge tombstone
#                               + re-extraction must reconverge on the full graph)
# Every case uses a FROM-SCRATCH full compile of the post-edit corpus as the
# oracle; we never hand-write an expected graph.json.
# --------------------------------------------------------------------------- #


def test_incremental_equals_full_after_rename(tmp_path: Path) -> None:
    """Renaming a paper file (delete old path + add new path with identical
    content) must yield incremental output byte-identical to a full compile of
    the renamed corpus. Critically, the shared ResearchField that the renamed
    paper co-owns must NOT be tombstoned — it is re-emitted under the new path
    (and remains owned by the other papers regardless).
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=30)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed
    seed_ids = _node_ids(wiki)
    field_ids = {nid for nid in seed_ids if nid.startswith("ResearchField:")}
    assert field_ids, "expected a cross-file ResearchField concept in the corpus"

    old_path = papers[0]
    content = old_path.read_text(encoding="utf-8")
    new_path = old_path.parent / "paper_renamed.md"
    old_path.unlink()
    new_path.write_text(content, encoding="utf-8")

    # Rename == one deleted path + one added path in the same incremental run.
    wiki.compile(changed_only=True, changed_paths=[old_path, new_path])
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    incr_ids = _node_ids(wiki)

    # The shared cross-file field must survive the rename (anti-collapse).
    assert field_ids <= incr_ids, (
        "RENAME PARITY: shared ResearchField was wrongly tombstoned when its "
        "co-owning paper was renamed (delete old + add new path)"
    )

    wiki.compile()  # full recompile of the renamed corpus, same root
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert incr_tree == full_tree, (
        "RENAME PARITY FAILED: incremental compile after a file rename is NOT "
        "byte-identical to a full compile of the renamed corpus.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )


# A pair of papers that collide on a single dedup key under different surface
# forms. The canonicalizer picks one as canonical; when the winner's source is
# edited to stop asserting that form, the canonical identity must flip to the
# survivor — and incremental must reconverge on what a full compile produces.
_ALIAS_FIELD_LONG = "Compositional Generalization in Sequence Models"
_ALIAS_FIELD_SHORT = "Compositional Generalization"


def _alias_paper(idx: int, field: str) -> str:
    arxiv = f"2605.4{idx:04d}"
    return (
        f"# 논문 분석: {arxiv}\n\n"
        f"> - arxiv: https://arxiv.org/abs/{arxiv}\n"
        f"> - 분석일: 2026-05-01\n\n"
        f"# Alias Paper {idx:03d}\n\n"
        f"저자: {_SHARED_AUTHORS[idx % 3]}.\n\n"
        f"This paper advances {field} using the Transformer model.\n"
    )


def test_incremental_equals_full_after_alias_identity_change(tmp_path: Path) -> None:
    """Editing the merge-WINNING source so the canonical alias winner changes
    must yield incremental == full byte-for-byte. paper_a asserts the SHORT
    surface form, paper_b asserts the LONG form (canonical by length). Editing
    paper_b to drop the long form flips the canonical identity to paper_a's
    form; incremental re-extraction (Plan 03) must reconverge on the full graph.
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=8)
    wiki = _seed_wiki(root)

    papers_root = root / "data" / "research" / "daily" / "2026-05-01" / "papers"
    a_dir = papers_root / "2605.40000"
    b_dir = papers_root / "2605.40001"
    a_dir.mkdir(parents=True, exist_ok=True)
    b_dir.mkdir(parents=True, exist_ok=True)
    paper_a = a_dir / "paper.md"
    paper_b = b_dir / "paper.md"
    paper_a.write_text(_alias_paper(0, _ALIAS_FIELD_SHORT), encoding="utf-8")
    paper_b.write_text(_alias_paper(1, _ALIAS_FIELD_LONG), encoding="utf-8")

    wiki.compile()  # seed with both surface forms present

    # Edit the (long-form) winner to assert ONLY the short form, flipping which
    # fragment owns the canonical identity.
    paper_b.write_text(_alias_paper(1, _ALIAS_FIELD_SHORT), encoding="utf-8")

    wiki.compile(changed_only=True, changed_paths=[paper_b])
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    wiki.compile()  # full recompile of the post-edit corpus, same root
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert incr_tree == full_tree, (
        "ALIAS-IDENTITY PARITY FAILED: incremental compile after the merge-"
        "winning source changed is NOT byte-identical to a full compile.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )


def test_incremental_equals_full_after_both_endpoint_move(tmp_path: Path) -> None:
    """An edge whose BOTH endpoint sources are edited AND whose asserting file is
    edited must be re-derived to match a full compile byte-for-byte. We mutate
    two endpoint papers (changing their source) plus a third paper that co-cites
    the same shared field/authors (the edge-evidence carrier), all in one
    incremental run. The edge tombstone + re-extraction (Plans 01/03) must
    reconverge on the full graph.
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=30)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed
    seed_ids = _node_ids(wiki)
    field_ids = {nid for nid in seed_ids if nid.startswith("ResearchField:")}
    assert field_ids, "expected a cross-file ResearchField concept"

    # Three interlocking edits: two endpoint-source papers + a third that
    # re-asserts the shared field/author edges (both edge endpoints "move").
    _mutate(papers[0], "endpoint-A-moved")
    _mutate(papers[1], "endpoint-B-moved")
    _mutate(papers[2], "edge-evidence-moved")
    changed = [papers[0], papers[1], papers[2]]

    wiki.compile(changed_only=True, changed_paths=changed)
    incr_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)
    incr_ids = _node_ids(wiki)

    # Endpoints must survive; the cross-file field must not collapse.
    assert field_ids <= incr_ids, (
        "BOTH-ENDPOINT-MOVE PARITY: cross-file field collapsed when both edge "
        "endpoints' sources changed"
    )

    wiki.compile()  # full recompile, same root
    full_tree = _hash_tree(wiki.root, exclude=PARITY_EXCLUDE)

    assert incr_tree == full_tree, (
        "BOTH-ENDPOINT-MOVE PARITY FAILED: incremental compile after both edge "
        "endpoints' sources moved is NOT byte-identical to a full compile.\n"
        f"Differing files:{_diff_keys(full_tree, incr_tree)}"
    )
