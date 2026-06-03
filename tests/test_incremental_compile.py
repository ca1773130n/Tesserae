"""Provenance-driven incremental compile differ (Plan 04-03).

These tests pin the CMP-01/02/03/04 contract:

* a single-file content change re-extracts only that file, yet a concept node
  co-owned by an unchanged file SURVIVES (no 2400->1700 collapse);
* with ``incremental_compile=false`` a ``changed_only`` compile falls back to a
  full recompile (no partial graph);
* the daemon forwards the coalesced ``changed_paths`` into ``compile`` instead
  of dropping the list;
* ``node_provenance`` is non-empty after a plain FULL compile (so the 04-04
  parity test's full-compile seed is populated).

The deterministic (non-LLM) extractor path used by ``test_idempotence.py`` is
reused so the corpus is reproducible without network/LLM access.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from tesserae.project import ProjectWiki

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


def _seed_project(project_root: Path, *, incremental: bool) -> ProjectWiki:
    project_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    wiki = ProjectWiki.init(project_root, name="incremental_test")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = incremental
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return wiki


def _node_count(wiki: ProjectWiki) -> int:
    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    return len(payload.get("nodes", []))


def _node_ids(wiki: ProjectWiki) -> set[str]:
    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    return {n["id"] for n in payload.get("nodes", [])}


def _provenance_rows(wiki: ProjectWiki) -> list[tuple[str, str]]:
    if not wiki.paths.sqlite.exists():
        return []
    with sqlite3.connect(str(wiki.paths.sqlite)) as con:
        has_table = con.execute(
            "select name from sqlite_master where type='table' and name='node_provenance'"
        ).fetchone()
        if not has_table:
            return []
        return list(con.execute("select node_id, source_path from node_provenance"))


def test_full_compile_populates_provenance(tmp_path: Path) -> None:
    """A plain full compile must leave node_provenance NON-EMPTY (04-04 seed)."""
    wiki = _seed_project(tmp_path / "proj", incremental=True)
    wiki.compile()
    rows = _provenance_rows(wiki)
    assert rows, "node_provenance must be non-empty after a full compile"
    # Every persisted research node should have at least one provenance row.
    prov_ids = {node_id for node_id, _ in rows}
    assert _node_ids(wiki) <= prov_ids, "every graph node should have provenance"


def test_single_file_change_preserves_cross_file_nodes(tmp_path: Path) -> None:
    """Incremental compile of ONE changed file must not collapse cross-file nodes."""
    wiki = _seed_project(tmp_path / "proj", incremental=True)
    wiki.compile()

    baseline_ids = _node_ids(wiki)
    baseline_count = len(baseline_ids)
    assert baseline_count > 0, "full compile produced no nodes"

    # Provenance maps each node to its owning source file(s). The changed file
    # is one source; nodes attributed to OTHER (unchanged) files must survive
    # the incremental recompile — that is the CMP-03 anti-collapse guarantee.
    changed = tmp_path / "proj" / "docs" / "architecture.md"
    changed_abs = str(changed.resolve())

    rows = _provenance_rows(wiki)
    by_node: dict[str, set[str]] = {}
    for node_id, src in rows:
        by_node.setdefault(node_id, set()).add(str(Path(src).resolve()) if src != "__synthesis__" else src)
    # Concepts owned ONLY by unchanged files — these MUST NOT be tombstoned when
    # ``architecture.md`` is re-extracted (provenance set stays non-empty).
    unchanged_owned = {
        nid for nid, srcs in by_node.items()
        if srcs and changed_abs not in srcs and "__synthesis__" not in srcs
    }
    assert unchanged_owned, "fixture corpus has no node owned by an unchanged file"

    # Mutate ONE source file's content and recompile incrementally.
    changed.write_text(
        changed.read_text(encoding="utf-8") + "\n\nAppended a new paragraph.\n",
        encoding="utf-8",
    )
    wiki.compile(changed_only=True, changed_paths=[changed])

    after_ids = _node_ids(wiki)

    # CMP-03: every node owned by an unchanged file must survive the single-file
    # incremental compile — no 2400->1700 collapse.
    dropped = unchanged_owned - after_ids
    assert not dropped, (
        f"{len(dropped)} node(s) owned by unchanged files were dropped — the "
        f"2400->1700 cross-file collapse regression has reappeared: {sorted(dropped)[:5]}"
    )
    # No catastrophic collapse: incremental keeps at least the full node set.
    assert len(after_ids) >= baseline_count, (
        f"node count collapsed: {baseline_count} -> {len(after_ids)}"
    )


def test_flag_off_falls_back_to_full_compile(tmp_path: Path) -> None:
    """With incremental_compile=false a changed_only compile == a full compile."""
    # Full reference compile (flag off).
    ref = _seed_project(tmp_path / "ref", incremental=False)
    ref.compile()
    ref_ids = _node_ids(ref)

    # Same corpus, incremental flag off, run changed_only after a seed compile.
    wiki = _seed_project(tmp_path / "proj", incremental=False)
    wiki.compile()
    changed = tmp_path / "proj" / "docs" / "architecture.md"
    changed.write_text(
        changed.read_text(encoding="utf-8") + "\n\nFlag-off edit.\n",
        encoding="utf-8",
    )
    wiki.compile(changed_only=True, changed_paths=[changed])

    # Flag off => safe full recompile, NOT a partial graph. The node set must
    # be a superset of the reference full-compile set (the edit only adds text).
    assert ref_ids <= _node_ids(wiki), (
        "flag-off changed_only compile dropped nodes — it should fall back to "
        "a full recompile, never a partial graph"
    )


def test_changed_paths_threaded_into_compile(tmp_path: Path) -> None:
    """daemon._run_pipeline forwards changed_paths into wiki.compile (CMP-04)."""
    from tesserae.engine.daemon import Daemon
    import tesserae.project as project_mod

    project_root = tmp_path / "proj"
    _seed_project(project_root, incremental=True)

    captured: dict[str, object] = {}

    class _SpyWiki:
        @classmethod
        def load(cls, root):  # noqa: ANN001
            return cls()

        def compile(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {}

    original = project_mod.ProjectWiki
    # daemon imports ProjectWiki inside _run_pipeline, so patch the module attr.
    project_mod.ProjectWiki = _SpyWiki  # type: ignore[assignment]
    try:
        daemon = Daemon(project_root=project_root)
        paths = [project_root / "docs" / "architecture.md"]
        daemon._run_pipeline(paths)
    finally:
        project_mod.ProjectWiki = original  # type: ignore[assignment]

    assert captured.get("changed_only") is True
    assert captured.get("changed_paths") == paths, (
        "daemon must forward the coalesced changed_paths, not drop them"
    )
