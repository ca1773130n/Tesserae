"""Surviving-owner RE-EXTRACTION payload parity (Phase 04.1 Plan 03, blocker #3).

A cross-file node survives a subtractive edit because an UNCHANGED co-owner file
still asserts it. But the prior node we keep carries the PRIOR merged payload
(name / aliases / description / metadata) and surviving cross-file edges keep
their prior evidence. When the changed file is the one that WON attribution
(e.g. it carried the title-case alias ``prefer_research_node`` /
``_merge_same_type_aliased_duplicates`` chose), the kept payload diverges from a
full compile of the post-edit corpus.

Plan 03 fixes this by RE-EXTRACTING the surviving co-owner files of stale nodes
and re-merging them through the exact ``merge_graphs`` path a full compile uses,
so the canonical winner + edge evidence is re-derived byte-identically.

Every assertion compares an INCREMENTAL compile against a from-scratch FULL
compile of the *same post-edit corpus* (the canonical oracle) — never against a
hand-written expected value.

Run with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import tesserae.project as project_mod
from tesserae.project import ProjectWiki

try:  # pytest puts the tests/ dir on sys.path -> import as a top-level module.
    from test_incremental_parity import (
        _ScriptedCommunityClient,
        _build_corpus,
        _seed_wiki,
    )
except ImportError:  # fallback when tests/ is importable as a package
    from tests.test_incremental_parity import (
        _ScriptedCommunityClient,
        _build_corpus,
        _seed_wiki,
    )


@pytest.fixture(autouse=True)
def _pin_community_client():
    """Pin the community-summary client so cluster nodes stay byte-stable."""
    project_mod.set_community_summaries_test_client(_ScriptedCommunityClient())
    try:
        yield
    finally:
        project_mod.set_community_summaries_test_client(None)


# --------------------------------------------------------------------------- #
# The ALIAS-IDENTITY shared node (the blocker #3 shape)
# --------------------------------------------------------------------------- #
#
# ``_build_corpus`` rotates a fixed author set across every paper, so each
# shared author (a Person node) is co-owned by SEVERAL files. The merged Person
# carries name / aliases / source_path WON by one specific file (its
# ``source_path`` points at the winning owner). Editing that winning owner to
# DROP the author must re-derive the surviving payload from the OTHER co-owner
# files — which only happens if those survivors are RE-EXTRACTED (blocker #3).
# Author spelling varies across files (e.g. ``Ada Lovelace`` vs ``Ada
# Lovelace.``) so dropping the winner genuinely changes name/aliases.


def _graph(wiki: ProjectWiki) -> dict:
    return json.loads(wiki.paths.graph.read_text(encoding="utf-8"))


def _nodes_by_id(wiki: ProjectWiki) -> Dict[str, dict]:
    return {n["id"]: n for n in _graph(wiki).get("nodes", [])}


def _edges(wiki: ProjectWiki) -> List[dict]:
    return _graph(wiki).get("edges", [])


def _person_nodes(wiki: ProjectWiki) -> List[dict]:
    return [
        n for n in _graph(wiki).get("nodes", [])
        if str(n.get("id", "")).startswith("Person:")
    ]


def _winning_owner_path(person: dict) -> Path:
    """The file that won the shared Person's attribution (its source_path)."""
    src = person.get("source_path")
    assert src, f"Person {person['id']} has no source_path to edit"
    return Path(src)


def _full_compile_oracle(oracle_root: Path) -> ProjectWiki:
    """From-scratch FULL compile in a FRESH root — the canonical oracle.

    A pristine root guarantees no prior graph / sidecar can influence the
    result, so its graph.json is a pure full compile of whatever corpus the
    caller laid down under ``oracle_root``.
    """
    wiki = _seed_wiki(oracle_root)
    wiki.compile()
    return wiki


def _drop_author(path: Path) -> None:
    """Rewrite a paper so it asserts NO authors (its 저자 line is removed)."""
    text = path.read_text(encoding="utf-8")
    kept = [ln for ln in text.splitlines() if not ln.startswith("저자")]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _norm(obj, base: Path):
    """Strip an absolute ``base`` root prefix from every string in ``obj``.

    The incremental arm and the oracle arm live under different temp roots
    (project/ vs oracle/), so absolute ``source_path`` / evidence strings carry
    a different prefix that is NOT a real divergence. Normalising both to a
    root-relative form lets the byte-parity comparison test the PAYLOAD, not the
    test harness's directory layout.
    """
    b = str(base)
    if isinstance(obj, str):
        return obj.replace(b, "<root>")
    if isinstance(obj, list):
        return [_norm(x, base) for x in obj]
    if isinstance(obj, dict):
        return {k: _norm(v, base) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- #
# Test 1: node payload (name / aliases) re-derivation after a winning-source edit
# --------------------------------------------------------------------------- #


def test_incremental_node_payload_equals_full_after_winning_source_edit(
    tmp_path: Path,
) -> None:
    """Edit the file that WON a shared Person's attribution; the incremental
    node's name/aliases/description must equal a full compile of the post-edit
    corpus.

    Without re-extraction the kept Person retains the PRIOR merged payload
    (name/aliases won by the now-edited file) even though that file no longer
    asserts the author — diverging from a full compile where a surviving
    co-owner's spelling wins. Re-extracting the surviving co-owners re-derives
    the winner through the exact merge path, restoring byte-parity.
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=12)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed: provenance + prior graph

    persons = _person_nodes(wiki)
    assert persons, "seed compile produced no shared Person nodes"
    # Pick a Person whose winning owner is co-owned (others still assert it).
    target = persons[0]
    target_id = target["id"]
    winner = _winning_owner_path(target)
    assert winner in papers, f"winning owner {winner} not a corpus paper"

    # Edit the WINNING source to drop ALL authors. The shared author survives
    # via other (unchanged) papers, so its payload must be re-derived.
    _drop_author(winner)

    wiki.compile(changed_only=True, changed_paths=[winner])
    incr_nodes = _nodes_by_id(wiki)
    assert target_id in incr_nodes, (
        "shared Person was tombstoned despite surviving co-owners "
        "(cross-file collapse regression)"
    )
    incr_person = incr_nodes[target_id]

    # Oracle: from-scratch full compile of the identical post-edit corpus.
    oracle_root = tmp_path / "oracle"
    shutil.copytree(root, oracle_root, ignore=shutil.ignore_patterns(".tesserae"))
    oracle = _full_compile_oracle(oracle_root)
    oracle_nodes = _nodes_by_id(oracle)
    assert target_id in oracle_nodes, "oracle dropped the shared Person"
    oracle_person = oracle_nodes[target_id]

    # The winning owner must have flipped away from the edited file.
    assert incr_person.get("source_path") != str(winner), (
        "stale source_path: still points at the edited (no-longer-owning) file"
    )
    # source_path is an absolute path; the two arms live under different roots
    # (project/ vs oracle/), so compare it ROOT-RELATIVE — equal relative path
    # means both arms chose the SAME surviving owner file.
    def _rel(node: dict, base: Path) -> Optional[str]:
        sp = node.get("source_path")
        return str(Path(sp).relative_to(base)) if sp else None

    assert _rel(incr_person, root) == _rel(oracle_person, oracle_root), (
        "Person chose a different surviving owner file than the full compile:\n"
        f"  incremental={_rel(incr_person, root)!r}\n"
        f"  full       ={_rel(oracle_person, oracle_root)!r}"
    )
    # Byte-parity on the rest of the payload against the full-compile oracle.
    for f in ("name", "aliases", "description", "metadata"):
        assert incr_person.get(f) == oracle_person.get(f), (
            f"Person.{f} diverged from full compile:\n"
            f"  incremental={incr_person.get(f)!r}\n"
            f"  full       ={oracle_person.get(f)!r}"
        )


# --------------------------------------------------------------------------- #
# Test 2: cross-file edge evidence re-derivation
# --------------------------------------------------------------------------- #


def test_incremental_edges_equal_full_after_winning_source_edit(
    tmp_path: Path,
) -> None:
    """After the winning-source edit, the surviving graph's edge set (source/
    type/target + evidence/metadata) must equal a full compile's exactly."""
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=12)
    wiki = _seed_wiki(root)
    wiki.compile()

    persons = _person_nodes(wiki)
    assert persons, "seed compile produced no shared Person nodes"
    winner = _winning_owner_path(persons[0])
    _drop_author(winner)
    wiki.compile(changed_only=True, changed_paths=[winner])

    oracle_root = tmp_path / "oracle"
    shutil.copytree(root, oracle_root, ignore=shutil.ignore_patterns(".tesserae"))
    oracle = _full_compile_oracle(oracle_root)

    def _edge_key(e: dict):
        return (e.get("source"), e.get("type"), e.get("target"))

    incr_edges = {_edge_key(e): _norm(e, root) for e in _edges(wiki)}
    full_edges = {_edge_key(e): _norm(e, oracle_root) for e in _edges(oracle)}

    assert set(incr_edges) == set(full_edges), (
        "edge topology diverged from full compile:\n"
        f"  only-incremental={set(incr_edges) - set(full_edges)}\n"
        f"  only-full       ={set(full_edges) - set(incr_edges)}"
    )
    for key, full_edge in full_edges.items():
        incr_edge = incr_edges[key]
        assert incr_edge == full_edge, (
            f"edge {key} evidence/metadata diverged:\n"
            f"  incremental={incr_edge}\n  full={full_edge}"
        )


# --------------------------------------------------------------------------- #
# Test 3: producer-owned nodes are NOT re-extracted from markdown
# --------------------------------------------------------------------------- #


def test_producer_nodes_excluded_from_reextraction(tmp_path: Path) -> None:
    """With the session graph enabled, editing a markdown file must not cause a
    Session/SessionDecision node to be re-extracted from markdown.

    Producer-owned nodes carry only a ``__``-prefixed sidecar source; Plan 03
    excludes them from the stale re-extraction set, and the incremental graph
    stays byte-identical to a full compile (producers regenerate their own
    nodes every compile).
    """
    root = tmp_path / "project"
    papers = _build_corpus(root, n_papers=6)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed

    # Edit a paper (subtractive on its co-owned author/field).
    papers[0].write_text(
        "# 논문 분석: stub\n\n# Synthetic Paper STUB\n\n"
        "This file no longer references the shared field or authors.\n",
        encoding="utf-8",
    )
    wiki.compile(changed_only=True, changed_paths=[papers[0]])
    incr_nodes = _nodes_by_id(wiki)

    # Oracle: full compile of the post-edit corpus in a fresh root.
    oracle_root = tmp_path / "oracle"
    shutil.copytree(root, oracle_root, ignore=shutil.ignore_patterns(".tesserae"))
    owiki = _seed_wiki(oracle_root)
    owiki.compile()
    full_nodes = _nodes_by_id(owiki)

    # Any producer-owned node types present must match the full compile exactly
    # (none were corrupted / dropped / spuriously re-extracted).
    producer_prefixes = ("Session:", "SessionDecision:")
    incr_producer = {
        nid: n for nid, n in incr_nodes.items()
        if str(nid).startswith(producer_prefixes)
    }
    full_producer = {
        nid: n for nid, n in full_nodes.items()
        if str(nid).startswith(producer_prefixes)
    }
    assert set(incr_producer) == set(full_producer), (
        "producer node set diverged from full compile (producer node was "
        "spuriously re-extracted or dropped):\n"
        f"  only-incremental={set(incr_producer) - set(full_producer)}\n"
        f"  only-full       ={set(full_producer) - set(incr_producer)}"
    )
    for nid, full_node in full_producer.items():
        assert _norm(incr_producer[nid], root) == _norm(full_node, oracle_root), (
            f"producer node {nid} payload diverged from full compile"
        )

    # The whole node set must match the oracle (the edit's subtractive effect +
    # surviving payload re-derivation is byte-parity correct).
    assert set(incr_nodes) == set(full_nodes), (
        "incremental node set diverged from full compile:\n"
        f"  only-incremental={set(incr_nodes) - set(full_nodes)}\n"
        f"  only-full       ={set(full_nodes) - set(incr_nodes)}"
    )
