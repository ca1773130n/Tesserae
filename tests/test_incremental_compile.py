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


def test_default_config_does_not_enable_incremental(tmp_path: Path) -> None:
    """DESCOPE GUARD: incremental compile is EXPERIMENTAL and must stay OFF by
    default. A freshly-initialised project's config must NOT enable it, and a
    changed_only compile on a default project must equal a full compile (the
    safe path). If a future change flips the default ON, this fails loudly.
    """
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    wiki = ProjectWiki.init(project_root, name="default_cfg_test")

    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    assert not cfg.get("incremental_compile", False), (
        "incremental_compile must be OFF in the default project config "
        "(experimental; byte-parity incomplete until the follow-up phase)"
    )

    wiki.compile()  # seed (default config → full compile)
    seed_count = _node_count(wiki)
    assert seed_count > 0, "seed full compile produced no nodes"
    # A changed_only compile with the default (flag-off) config must fall back
    # to a full recompile — no incremental divergence / collapse.
    next(iter((project_root / "docs").glob("*.md"))).write_text(
        "# Edited\n\nDefault-config changed_only must still full-recompile.\n",
        encoding="utf-8",
    )
    wiki.compile(changed_only=True, changed_paths=None)
    assert _node_count(wiki) >= seed_count, (
        "default (flag-off) changed_only compile collapsed the graph instead of "
        "falling back to a full recompile"
    )


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


# --- pin for the 2026-09-06 refresh: LLM-typed nodes of UNTOUCHED files must survive ---

class _ConceptExtractor:
    """Deterministic stand-in for the LLM extractor: the structural graph plus
    one ``Concept`` node per document, and a count of which files it saw.
    A Concept never comes from the deterministic extractor, so its presence
    after a compile says the LLM layer was produced OR carried over."""

    def __init__(self) -> None:
        from tesserae.research_graph import ResearchGraphExtractor

        self._det = ResearchGraphExtractor()
        self.calls: list = []

    def extract_file(self, path, source_kind="SourceDocument"):
        path = Path(path)
        return self.extract_text(path.read_text(encoding="utf-8"), source_path=str(path), source_kind=source_kind)

    def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
        # The batch runner reads the file itself and calls extract_text; both
        # entry points must add the LLM-only node or the fake proves nothing.
        from tesserae.research_graph import ResearchEdge, ResearchNode, ResearchNodeType, stable_id

        paper_dir = Path(source_path).parent.name if source_path else "x"
        self.calls.append(paper_dir)
        graph = self._det.extract_text(text, source_path=source_path, source_kind=source_kind)
        name = f"Idea {paper_dir.upper()}"
        concept = ResearchNode(
            id=stable_id("Concept", name), name=name, type=ResearchNodeType.CONCEPT,
            description="an LLM-only node type", source_path=source_path,
        )
        graph.nodes.append(concept)
        anchor = next((n for n in graph.nodes if n.type == ResearchNodeType.PAPER), None)
        if anchor is not None:
            graph.edges.append(ResearchEdge(source=anchor.id, target=concept.id, type="uses"))
        return graph


def _paper(root: Path, name: str, body: str) -> Path:
    d = root / "data" / "research" / "daily" / "2026-05-01" / "papers" / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "paper.md"
    arxiv = f"2605.{ord(name[0]) - ord('a') + 1:05d}"    # distinct per paper, or they merge
    p.write_text(f"# Paper {name.upper()}\n\n> - arxiv: https://arxiv.org/abs/{arxiv}\n\n{body}\n", encoding="utf-8")
    return p


def _concept_names(wiki: ProjectWiki) -> set:
    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    return {n["name"] for n in payload["nodes"] if n.get("type") == "Concept"}


def test_changed_only_compile_keeps_llm_typed_nodes_of_untouched_files(tmp_path: Path) -> None:
    """The 2026-09-06 `refresh --changed-only` on the real project left a graph
    with every LLM-typed node gone (Concept 466 -> 0, Capability 470 -> 0 …)
    while all 2,551 files stayed `graphed: true` and the LLM made ~116 calls
    against ~12,600 for the previous full compile. Default config, so
    ``incremental_compile`` is OFF and the documented contract is "fall back to
    a full recompile". Either every file is re-extracted (so the LLM-only nodes
    of an untouched file come back) or the untouched file's prior nodes are
    carried over; losing them is the bug this pins."""
    root = tmp_path / "project"
    a = _paper(root, "a", "We study idea A in depth.")
    _paper(root, "b", "We study idea B in depth.")
    wiki = ProjectWiki.init(root, name="pin_llm_nodes")
    fake = _ConceptExtractor()

    wiki.compile(doc_extractor=fake)
    assert {"Idea A", "Idea B"} <= _concept_names(wiki), _concept_names(wiki)
    assert sorted(fake.calls) == ["a", "b"], fake.calls

    fake.calls.clear()
    a.write_text(a.read_text(encoding="utf-8") + "\nA second paragraph changes paper A.\n", encoding="utf-8")
    wiki.compile(changed_only=True, doc_extractor=fake)

    names = _concept_names(wiki)
    assert "Idea B" in names, (
        f"untouched paper B lost its LLM-typed node: concepts now {sorted(names)}; "
        f"the extractor was called for {sorted(fake.calls)}"
    )
    assert "Idea A" in names, sorted(names)


def test_refresh_builds_the_same_document_extractor_as_compile(tmp_path: Path, monkeypatch) -> None:
    """`tesserae refresh` called ``wiki.compile()`` with no ``doc_extractor``, so
    the compile fell back to the deterministic extractor and rebuilt the whole
    document layer without the LLM — on the real project (2026-09-06) that
    erased every LLM-typed node of the previous full compile in one run, while
    `tesserae compile` on the same config would have kept them. Whatever
    extractor `compile` builds, `refresh` must build too."""
    import tesserae.cli as cli

    fake = _ConceptExtractor()
    # The seam every entry point should share: what compile() builds when the
    # caller passes no doc_extractor. raising=False so the test is RED (not an
    # AttributeError) while that seam does not exist yet.
    monkeypatch.setattr(ProjectWiki, "_default_doc_extractor", lambda self: fake, raising=False)
    root = tmp_path / "project"
    _paper(root, "a", "We study idea A in depth.")
    wiki = ProjectWiki.init(root, name="refresh_extractor")

    assert cli.main(["refresh", "--project", str(root), "--no-sessions"]) == 0
    assert "Idea A" in _concept_names(wiki), (
        f"refresh compiled without the document extractor: calls={fake.calls}, "
        f"concepts={sorted(_concept_names(wiki))}"
    )


def test_compile_without_an_extractor_uses_the_configured_llm_one(tmp_path: Path, monkeypatch) -> None:
    """The seam itself: ``wiki.compile()`` with no ``doc_extractor`` — what the
    engine daemon, the MCP ``materialize`` path, ``deploy --build`` and
    ``refresh`` all call — builds the same LLM-backed extractor `compile`
    does. With no usable backend it still degrades to the deterministic one
    (conftest makes the client None), which is why the builder is patched
    rather than the client."""
    fake = _ConceptExtractor()
    monkeypatch.setattr(ProjectWiki, "_default_doc_extractor", lambda self: fake)
    root = tmp_path / "project"
    _paper(root, "a", "We study idea A in depth.")
    wiki = ProjectWiki.init(root, name="seam")

    wiki.compile()
    assert "Idea A" in _concept_names(wiki), fake.calls


def test_no_backend_degrades_to_the_deterministic_extractor_with_a_warning(tmp_path: Path, caplog) -> None:
    """conftest turns the client into None for every test; that must land on
    the structural extractor LOUDLY, never on a raise and never in silence."""
    import logging

    root = tmp_path / "project"
    _paper(root, "a", "We study idea A in depth.")
    wiki = ProjectWiki.init(root, name="nobackend")
    with caplog.at_level(logging.WARNING, logger="tesserae.project"):
        wiki.compile()
    assert _node_count(wiki) > 0
    assert any("STRUCTURAL graph only" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records][:5]
