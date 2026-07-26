"""Scope regression for ``compile --changed-only --retry-fallbacks``.

The recovery hint printed by :mod:`tesserae.batch` promises a *scoped* re-run:
only the documents whose typed extraction degraded to the deterministic
baseline should be re-extracted. Before the fix, ``ProjectWiki.ingest`` forced
``effective_changed_only=False`` whenever the experimental provenance differ
was inactive (the normal case), so the flag landed in the full-recompile path
and re-extracted the WHOLE corpus — turning a minutes-long recovery into hours.

These tests pin the narrow ``fallback_only`` mode and, just as importantly, the
Codex B4 invariant it must not break: ``graph.json`` is never partial.
"""

import json
from pathlib import Path

import pytest

from tesserae.project import ProjectWiki
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


class FlakyExtractor:
    """Doc extractor that reports a deterministic fallback for chosen paths.

    Mirrors ``SelectiveClaudeResearchExtractor``'s duck-typed contract (the
    batch runner reads ``last_was_fallback`` after each ``extract_text``), and
    emits a *degraded* graph for the failing docs so a real recovery is
    observable in ``graph.json``.

    The degraded and healthy graphs differ in BOTH ways a real extractor's do,
    because a fixture that only ADDS a node on recovery is structurally blind to
    how a prior-first merge actually fails:

    * SAME id, different payload — node ids are ``stable_id(type, name)``, so
      collisions are the norm. ``merge_graphs`` puts the prior graph first and
      ``prefer_research_node`` defaults to ``chosen = existing``, so without a
      tombstone the DEGRADED description/metadata survive the "recovery".
    * DIFFERENT id — deterministic and LLM extraction name things differently,
      so the stale node just lives on beside its typed replacement.
    """

    #: Emitted only by the degraded path. Its survival is the different-id
    #: contamination; its absence is the tombstone working.
    JUNK_SUFFIX = "-keyword-blob"

    def __init__(self, failing: set[str] | None = None):
        self.calls: list[str] = []
        self.failing = failing if failing is not None else set()
        self.last_was_fallback = False

    def extract_text(self, content, source_path, source_kind="SourceDocument"):
        self.calls.append(source_path)
        p = Path(source_path)
        fell_back = p.name in self.failing
        self.last_was_fallback = fell_back
        nodes = [
            ResearchNode(
                id=f"Paper:{p.stem}",
                name=p.stem,
                type=ResearchNodeType.PAPER,
                description=(
                    "DEGRADED keyword blurb" if fell_back else f"typed summary of {p.stem}"
                ),
                metadata={"extractor": "deterministic" if fell_back else "llm"},
                source_path=str(source_path),
            )
        ]
        if fell_back:
            nodes.append(
                ResearchNode(
                    id=f"Concept:{p.stem}{self.JUNK_SUFFIX}",
                    name=f"{p.stem} keyword blob",
                    type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                    source_path=str(source_path),
                )
            )
        else:
            # The typed layer the deterministic baseline cannot produce — its
            # presence is what proves the retry actually recovered the doc.
            nodes.append(
                ResearchNode(
                    id=f"ResearchTopic:{p.stem}-typed",
                    name=f"{p.stem} typed",
                    type=ResearchNodeType.RESEARCH_TOPIC,
                    source_path=str(source_path),
                )
            )
        return ResearchGraph(nodes=nodes, edges=[])


class SharedConceptExtractor(FlakyExtractor):
    """Emits a CROSS-FILE node every doc asserts, gated by ``emit_shared``.

    Turning the gate off for the retry run models the case that makes the
    tombstone's scoping observable: the recovered extraction stops emitting a
    node the UNCHANGED docs still assert. Scoped correctly, the node survives on
    their provenance; tombstoned too widely it disappears for good, because the
    retried doc no longer re-adds it.
    """

    SHARED_ID = "ResearchField:shared"

    def __init__(self, failing: set[str] | None = None, emit_shared: bool = True):
        super().__init__(failing=failing)
        self.emit_shared = emit_shared

    def extract_text(self, content, source_path, source_kind="SourceDocument"):
        graph = super().extract_text(content, source_path, source_kind)
        if self.emit_shared:
            graph.nodes.append(
                ResearchNode(
                    id=self.SHARED_ID,
                    name="shared field",
                    type=ResearchNodeType.RESEARCH_FIELD,
                    source_path=str(source_path),
                )
            )
        return graph


@pytest.fixture(autouse=True)
def _serial_extraction(monkeypatch):
    """Pin extractor call ORDER so ``extractor.calls`` assertions are stable."""
    monkeypatch.setenv("TESSERAE_EXTRACT_CONCURRENCY", "1")


def _make_project(tmp_path, name, docs):
    project = tmp_path / name
    project.mkdir()
    doc_dir = project / "docs"
    doc_dir.mkdir()
    for stem in docs:
        (doc_dir / f"{stem}.md").write_text(
            f"# {stem}\nGaussian Splatting supports novel view synthesis.",
            encoding="utf-8",
        )
    return project, ProjectWiki.init(project, source_kind="Paper", sources=["docs"])


def _graph_node_ids(project):
    payload = json.loads((project / ".tesserae" / "graph.json").read_text(encoding="utf-8"))
    return {node["id"] for node in payload["nodes"]}


def _manifest(project):
    payload = json.loads((project / ".tesserae" / "manifest.json").read_text(encoding="utf-8"))
    return payload["files"]


def _graph_nodes(project):
    payload = json.loads((project / ".tesserae" / "graph.json").read_text(encoding="utf-8"))
    return {node["id"]: node for node in payload["nodes"]}


def _kill_during(monkeypatch):
    """Make the NEXT ``_write_artifacts`` die the way a real kill does.

    The manifest is already on disk at this point (``BatchIngestRunner`` writes
    it per document, inside the worker); ``graph.json`` is not. Reproduces
    Ctrl-C / a wedged extract / a harness reaping the process group.
    """

    def _boom(self, *args, **kwargs):
        raise KeyboardInterrupt("compile killed before _write_artifacts")

    monkeypatch.setattr(ProjectWiki, "_write_artifacts", _boom)


def test_retry_fallbacks_extracts_only_marked_docs(tmp_path):
    """The scoped retry touches ONLY the marked doc and still writes a whole graph."""
    project, wiki = _make_project(tmp_path, "retry-scope", ["a", "b", "c", "d"])

    degraded = FlakyExtractor(failing={"c.md"})
    first = wiki.compile(changed_only=True, doc_extractor=degraded)
    assert first["processed_files"] == 4
    assert any(entry.get("fallback") is True for entry in _manifest(project).values())

    recovered = FlakyExtractor(failing=set())  # provider healthy again
    second = wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    # Scoped: exactly the marked doc, not the whole corpus.
    assert [Path(c).name for c in recovered.calls] == ["c.md"]
    assert second["processed_files"] == 1
    assert second["skipped_files"] == 3

    # Codex B4 invariant: graph.json is still the WHOLE corpus, not a partial
    # graph holding only the retried doc.
    node_ids = _graph_node_ids(project)
    for stem in ("a", "b", "c", "d"):
        assert f"Paper:{stem}" in node_ids
    # ...and the retry genuinely recovered the typed layer for the marked doc.
    assert "ResearchTopic:c-typed" in node_ids

    # The marker is cleared, so a plain changed-only rerun is a no-op again.
    assert all("fallback" not in entry for entry in _manifest(project).values())


def _prov_sources(wiki):
    """The ``node_provenance`` source_paths, filenames only (``__synthesis__`` kept)."""
    import sqlite3

    with sqlite3.connect(str(wiki.paths.sqlite)) as con:
        return {
            Path(row[0]).name if "/" in row[0] else row[0]
            for row in con.execute("select distinct source_path from node_provenance")
        }


def test_a_plain_changed_only_noop_does_not_disarm_the_scoped_retry(tmp_path, capsys):
    """The 137->35 bound must survive an ORDINARY compile in between.

    ``noop_skip`` extracts nothing, so ``extracted_graphs`` is empty — and with
    the differ off (the default) that run still reported ``full_compile=True``,
    handing ``reconcile_provenance`` an EMPTY row-set. Reconcile REPLACES the
    row-set, so every per-document provenance row was deleted by a compile that
    changed nothing at all. The next ``--changed-only --retry-fallbacks`` then
    failed ``_provenance_ready``, warned, and re-extracted the WHOLE corpus.

    Measured before the fix: seed 3 docs -> per-doc rows for a/b/c; ONE plain
    ``--changed-only`` no-op -> only ``__synthesis__`` left; retry processed 3.
    The scoped bound existed only if the retry was the LITERAL next compile,
    which is not how anyone works.
    """
    project, wiki = _make_project(tmp_path, "retry-after-noop", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))
    assert {"a.md", "b.md", "c.md"} <= _prov_sources(wiki)

    # An ORDINARY compile in between — nothing changed, nothing extracted.
    quiet = FlakyExtractor(failing=set())
    assert wiki.compile(changed_only=True, doc_extractor=quiet)["processed_files"] == 0
    assert quiet.calls == []
    # A run that extracted NOTHING must not delete what the last real one wrote.
    assert {"a.md", "b.md", "c.md"} <= _prov_sources(wiki)
    capsys.readouterr()

    recovered = FlakyExtractor(failing=set())
    result = wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    # Still SCOPED to the marked doc, and silent about it.
    assert [Path(c).name for c in recovered.calls] == ["c.md"]
    assert result["processed_files"] == 1
    assert result["skipped_files"] == 2
    assert "could not scope this run" not in capsys.readouterr().err
    # ...and the retry still recovered the typed layer over a WHOLE graph.
    node_ids = _graph_node_ids(project)
    assert "ResearchTopic:c-typed" in node_ids
    for stem in ("a", "b", "c"):
        assert f"Paper:{stem}" in node_ids


def test_a_full_recompile_still_prunes_provenance_for_a_departed_doc(tmp_path):
    """The safety half: gating reconcile must not cost it its real job.

    ``reconcile_provenance`` exists to purge rows for content that genuinely
    left the graph. A run that extracts nothing is skipped now — but a REAL full
    compile of an emptied corpus still runs the batch, so ``extracted_graphs``
    is ``[batch.graph]`` (truthy) and the reconcile still fires.
    """
    project, wiki = _make_project(tmp_path, "full-prune", ["a", "b", "c"])
    wiki.compile(doc_extractor=FlakyExtractor())
    assert {"a.md", "b.md", "c.md"} <= _prov_sources(wiki)

    (project / "docs" / "c.md").unlink()
    wiki.compile(doc_extractor=FlakyExtractor())

    assert "c.md" not in _prov_sources(wiki)
    assert {"a.md", "b.md"} <= _prov_sources(wiki)
    assert "Paper:c" not in _graph_node_ids(project)


def test_a_full_recompile_still_drops_a_stale_row_for_a_still_live_node(tmp_path):
    """The M5 false-keeper, the case only RECONCILE (not prune) can fix.

    ``prune_provenance_to_graph`` only drops rows whose node left the graph. A
    row saying ``b.md`` contributed a node that b.md no longer asserts — while
    a.md/c.md keep it LIVE — survives the prune and must be killed by reconcile.
    """
    project, wiki = _make_project(tmp_path, "full-false-keeper", ["a", "b", "c"])
    wiki.compile(doc_extractor=SharedConceptExtractor(emit_shared=True))
    shared = SharedConceptExtractor.SHARED_ID
    assert shared in _graph_node_ids(project)

    class DropSharedForB(SharedConceptExtractor):
        def extract_text(self, content, source_path, source_kind="SourceDocument"):
            self.emit_shared = Path(source_path).name != "b.md"
            return super().extract_text(content, source_path, source_kind)

    (project / "docs" / "b.md").write_text("# b\nedited.", encoding="utf-8")
    wiki.compile(doc_extractor=DropSharedForB())

    import sqlite3

    with sqlite3.connect(str(wiki.paths.sqlite)) as con:
        owners = {
            Path(row[0]).name
            for row in con.execute(
                "select source_path from node_provenance where node_id = ?", (shared,)
            )
        }
    assert shared in _graph_node_ids(project), "the node is still LIVE"
    assert "b.md" not in owners, "the stale row for a still-live node must be reconciled away"
    assert {"a.md", "c.md"} == owners


def test_retry_fallbacks_falls_back_to_full_recompile_when_corpus_changed(tmp_path):
    """A deleted file breaks ``corpus_unchanged``, so the subtractive guard holds."""
    project, wiki = _make_project(tmp_path, "retry-deleted", ["a", "b", "c"])

    degraded = FlakyExtractor(failing={"c.md"})
    wiki.compile(changed_only=True, doc_extractor=degraded)
    assert "Paper:a" in _graph_node_ids(project)

    (project / "docs" / "a.md").unlink()  # delete an UNMARKED doc

    recovered = FlakyExtractor(failing=set())
    result = wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    # Full recompile of the survivors, not a scoped retry.
    assert sorted(Path(c).name for c in recovered.calls) == ["b.md", "c.md"]
    assert result["processed_files"] == 2
    # The deleted doc's nodes must NOT be resurrected from the prior graph.
    assert "Paper:a" not in _graph_node_ids(project)


def test_changed_only_without_pending_fallback_still_noops(tmp_path):
    """The no-op short-circuit survives the guard restructure."""
    project, wiki = _make_project(tmp_path, "retry-noop", ["a", "b"])

    first = wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert first["processed_files"] == 2

    second_extractor = FlakyExtractor()
    second = wiki.compile(changed_only=True, doc_extractor=second_extractor)
    assert second["processed_files"] == 0
    assert second["skipped_files"] == 2
    assert second_extractor.calls == []

    # ...and asking for a retry when nothing is marked is still a no-op.
    third_extractor = FlakyExtractor()
    third = wiki.compile(
        changed_only=True, retry_fallbacks=True, doc_extractor=third_extractor
    )
    assert third["processed_files"] == 0
    assert third_extractor.calls == []


def test_changed_only_noop_with_trends_keeps_the_prior_graph(tmp_path):
    """A trends project's no-op must REUSE the prior graph, not recompute it.

    The ``noop_skip`` path leaves ``graphs == []`` and loads ``base_graph`` from
    disk, but the trend layer was recomputed unconditionally — and
    ``summarize_trends([])`` returns an EMPTY graph, so graph.json was
    overwritten with nothing while every manifest entry still said ``graphed``.
    That made the loss PERMANENT: the next ``--changed-only`` no-opped again on
    the wreck, so a project compiled with ``--trends`` lost its whole corpus to
    one idempotent rerun.
    """
    project, wiki = _make_project(tmp_path, "noop-trends", ["a", "b", "c"])

    first = wiki.compile(changed_only=True, trends=True, doc_extractor=FlakyExtractor())
    assert first["processed_files"] == 3
    seeded = _graph_node_ids(project)
    assert len(seeded) > 1, "fixture must seed a non-trivial graph to lose"

    rerun = FlakyExtractor()
    second = wiki.compile(changed_only=True, trends=True, doc_extractor=rerun)

    assert second["processed_files"] == 0
    assert second["skipped_files"] == 3
    assert rerun.calls == []
    assert _graph_node_ids(project) == seeded


def test_deleting_a_doc_does_not_disable_changed_only_forever(tmp_path):
    """The stale manifest key of a deleted doc must be pruned by the recompile.

    ``BatchIngestRunner`` only merges into the manifest, so a deleted document
    left its key behind for good — and ``corpus_unchanged`` (manifest keys ==
    candidate keys) could then never be True again. Every later
    ``--changed-only --retry-fallbacks`` re-extracted the WHOLE corpus, which is
    exactly the bound this mode exists to deliver. Pruning is safe on the run
    that re-extracted every candidate: graph.json was rebuilt without the
    deleted doc, so the key has done its subtractive job.
    """
    project, wiki = _make_project(tmp_path, "manifest-prune", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())["processed_files"] == 0

    (project / "docs" / "a.md").unlink()

    recompile = FlakyExtractor()
    first = wiki.compile(changed_only=True, doc_extractor=recompile)
    assert sorted(Path(c).name for c in recompile.calls) == ["b.md", "c.md"]
    assert first["processed_files"] == 2
    # The deletion still takes effect — the guard is not being bypassed.
    assert "Paper:a" not in _graph_node_ids(project)
    assert {Path(k).name for k in _manifest(project)} == {"b.md", "c.md"}

    # ...and the very next run is a no-op again, instead of re-extracting the
    # survivors forever.
    rerun = FlakyExtractor()
    second = wiki.compile(changed_only=True, doc_extractor=rerun)
    assert second["processed_files"] == 0
    assert second["skipped_files"] == 2
    assert rerun.calls == []
    assert "Paper:a" not in _graph_node_ids(project)


def test_a_scoped_incremental_run_keeps_the_stale_key_the_guard_needs(tmp_path):
    """Pruning is only safe on the run that rebuilt graph.json from scratch.

    With the experimental differ active the batch re-extracts a SUBSET and the
    PRIOR graph is merged back in, so a document deleted without an explicit
    ``changed_paths`` keeps its nodes in graph.json (a known differ gap). Prune
    its manifest key there and the next plain ``--changed-only`` would see a
    matching key-set, no-op, and reuse a graph that still carries the deleted
    doc — the resurrection the subtractive guard exists to prevent.
    """
    project, wiki = _make_project(tmp_path, "manifest-prune-incremental", ["a", "b", "c"])
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True  # EXPERIMENTAL — off by default
    wiki.paths.config.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

    wiki.compile(doc_extractor=FlakyExtractor())
    (project / "docs" / "a.md").unlink()

    result = wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    # The differ ran (a scoped run: nothing re-extracted, the prior graph
    # reused) and left the deleted doc's nodes behind...
    assert result["processed_files"] == 0
    assert "Paper:a" in _graph_node_ids(project)
    # ...so its manifest key MUST survive to keep the no-op refused.
    assert {Path(k).name for k in _manifest(project)} == {"a.md", "b.md", "c.md"}


def test_a_deferred_doc_loses_its_stamp_when_changed_paths_gutted_it(tmp_path):
    """Deferred-keeps-its-stamp holds only while nothing tombstoned its nodes.

    An explicit ``changed_paths`` tombstones by CALLER intent, not by what the
    batch got through — so with ``--limit`` cutting the work-list, a doc can be
    both deferred (entry not rewritten) and gutted (nodes deleted). Stamping it
    ``graphed`` there would hand the completeness guard a graph that is genuinely
    missing a document, and it stays missing until its bytes change.
    """
    project, wiki = _make_project(tmp_path, "deferred-but-gutted", ["a", "b", "c", "d"])
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True  # EXPERIMENTAL — off by default
    wiki.paths.config.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

    wiki.compile(doc_extractor=FlakyExtractor())
    docs = project / "docs"
    for name in ("b", "c"):
        (docs / f"{name}.md").write_text(f"# {name}\n\nedited\n", encoding="utf-8")

    # limit=1 lets only b.md through; c.md is deferred but named as changed, so
    # its prior nodes are tombstoned anyway.
    wiki.compile(
        changed_only=True,
        limit=1,
        changed_paths=[str(docs / "b.md"), str(docs / "c.md")],
        doc_extractor=FlakyExtractor(),
    )

    manifest = _manifest(project)
    stamped = {Path(k).name for k, v in manifest.items() if v.get("graphed")}
    gutted = "Paper:c" not in _graph_node_ids(project)
    # Whichever way the differ went, the stamp must not outlive the nodes.
    assert gutted == ("c.md" not in stamped), (
        f"c.md nodes gone={gutted} but stamped={'c.md' in stamped} — "
        f"the completeness guard would trust an incomplete graph"
    )
    # The untouched doc past the cut keeps its coverage either way.
    assert "d.md" in stamped


def test_manifest_prune_leaves_loader_keyed_entries_alone(tmp_path):
    """``_ingest_via_loader`` shares this manifest under ``source:`` keys.

    Those keys are never filesystem candidates, so an unguarded prune would
    erase every loader-keyed entry on the next FS compile — the exact erasure
    the merge-don't-replace comment on the loader path forbids.
    """
    project, wiki = _make_project(tmp_path, "manifest-prune-loader", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    manifest_path = project / ".tesserae" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["source:session-42"] = {"sha256": "deadbeef", "source_kind": "Session"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    (project / "docs" / "a.md").unlink()  # force the pruning recompile
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    files = _manifest(project)
    assert "source:session-42" in files, "an FS run must not erase loader-keyed entries"
    assert {Path(k).name for k in files if not k.startswith("source:")} == {"b.md"}


def test_a_loader_keyed_entry_conservatively_disables_the_filesystem_no_op(tmp_path):
    """A mixed manifest must NOT take the filesystem no-op. Deliberately.

    Both reuse predicates COUNT ``source:`` keys, and nothing stamps one
    ``graphed`` — so one loader run makes ``corpus_unchanged`` and
    ``graph_covers_corpus`` permanently false and every later filesystem
    ``--changed-only`` re-extracts the whole corpus. That is the cost, and it is
    the point.

    Excluding ``source:`` keys instead buys the scoped fast path back and opens
    a silent-loss hole in the same move: both origins rebuild graph.json from
    their OWN sources alone, so a loader run genuinely removes the filesystem
    documents from the graph while their entries still match by sha. With the
    exclusion, this predicate calls that graph complete and serves it until a
    document's bytes change — which, for an untouched corpus, is never. Every
    attempt to close that hole from inside the exclusion (cross-origin stamp
    drops, prior-graph reuse arms) introduced a defect of its own.

    Pinned as a REGRESSION GUARD, not as desirable behaviour: re-adding the
    exclusion must fail here. The honest fix is per-origin coverage tracking,
    booked as a ceiling in ``project.py`` and unjustified while
    ``compile(loader=...)`` has no caller.
    """
    project, wiki = _make_project(tmp_path, "loader-entry-conservative", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    # A no-op is available right up until a loader-keyed entry appears...
    quiet = FlakyExtractor()
    assert wiki.compile(changed_only=True, doc_extractor=quiet)["processed_files"] == 0
    assert quiet.calls == []

    manifest_path = project / ".tesserae" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["source:session-42"] = {"sha256": "deadbeef", "source_kind": "Session"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    # ...and then it is gone, and the corpus is re-extracted instead of reused.
    after = FlakyExtractor()
    result = wiki.compile(changed_only=True, doc_extractor=after)
    assert result["processed_files"] == 2
    assert [Path(c).name for c in after.calls] == ["a.md", "b.md"]
    # Self-healing, not destructive: the graph is whole and the entry survives.
    assert {"Paper:a", "Paper:b"} <= _graph_node_ids(project)
    assert "source:session-42" in _manifest(project)


def test_retry_fallbacks_keeps_full_recompile_when_trends_on(tmp_path, capsys):
    """``trends`` recomputes from THIS run's per-doc graphs, so it needs them all."""
    project, wiki = _make_project(tmp_path, "retry-trends", ["a", "b", "c"])

    wiki.compile(changed_only=True, trends=True, doc_extractor=FlakyExtractor(failing={"c.md"}))
    capsys.readouterr()

    recovered = FlakyExtractor(failing=set())
    result = wiki.compile(
        changed_only=True, retry_fallbacks=True, trends=True, doc_extractor=recovered
    )
    assert sorted(Path(c).name for c in recovered.calls) == ["a.md", "b.md", "c.md"]
    assert result["processed_files"] == 3
    # ...and the operator is told WHY the scoped retry they asked for became a
    # whole-corpus run, instead of just watching it take hours.
    assert "trends" in capsys.readouterr().err


# --------------------------------------------------- the graph.json is WHOLE


def test_retry_refuses_the_subset_when_a_kill_left_the_manifest_ahead(
    tmp_path, monkeypatch, capsys
):
    """An interrupted compile must not let the retry drop the docs it missed.

    The manifest is written per-document INSIDE the batch worker; ``graph.json``
    only at the very end of ``compile()``. Kill a run between the two and the
    manifest is AHEAD of the graph: every sha matches, so ``corpus_unchanged``
    is True and the scoped path looks admissible — but merging the one retried
    doc over that stale graph silently loses the others, AND clears the fallback
    marker, so every later ``--changed-only`` no-ops and the corpus stays
    partial forever. The ``graphed`` marker (stamped only after
    ``_write_artifacts`` returned) is what makes that state visible.
    """
    project, wiki = _make_project(tmp_path, "retry-killed", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert _graph_node_ids(project) >= {"Paper:a", "Paper:b"}

    for stem in ("c", "d"):
        (project / "docs" / f"{stem}.md").write_text(f"# {stem}\nbody", encoding="utf-8")

    with monkeypatch.context() as kill:
        _kill_during(kill)
        with pytest.raises(KeyboardInterrupt):
            wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))

    # The state the repro depends on: manifest has all four, graph.json has two.
    assert {Path(k).name for k in _manifest(project)} == {"a.md", "b.md", "c.md", "d.md"}
    assert "Paper:d" not in _graph_node_ids(project)
    capsys.readouterr()

    recovered = FlakyExtractor(failing=set())
    wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    # Whole corpus re-extracted, not the scoped subset...
    assert sorted(Path(c).name for c in recovered.calls) == ["a.md", "b.md", "c.md", "d.md"]
    # ...so the doc the killed run never graphed is present rather than lost.
    assert "Paper:d" in _graph_node_ids(project)
    assert "graph.json is not known to cover every tracked document" in capsys.readouterr().err


def test_plain_changed_only_refuses_the_noop_when_a_kill_left_the_manifest_ahead(
    tmp_path, monkeypatch, capsys
):
    """The no-op short-circuit needs the completeness marker too, not just shas.

    Same interrupted-compile state as the retry case above, reached WITHOUT a
    fallback marker (the killed run degraded nothing), so the plain
    ``--changed-only`` branch decides. Matching shas made ``corpus_unchanged``
    True and the run reported a clean ``processed=0, skipped=4`` over a
    ``graph.json`` that never got the two new docs — permanently, because every
    later ``--changed-only`` reported the same clean skip until a doc's bytes
    changed.
    """
    project, wiki = _make_project(tmp_path, "noop-killed", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert _graph_node_ids(project) >= {"Paper:a", "Paper:b"}

    for stem in ("c", "d"):
        (project / "docs" / f"{stem}.md").write_text(f"# {stem}\nbody", encoding="utf-8")

    with monkeypatch.context() as kill:
        _kill_during(kill)
        with pytest.raises(KeyboardInterrupt):
            wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    # The state the repro depends on: four tracked docs at matching shas, a
    # graph.json holding two, and NO fallback marker to trip the sibling guard.
    assert {Path(k).name for k in _manifest(project)} == {"a.md", "b.md", "c.md", "d.md"}
    assert all("fallback" not in entry for entry in _manifest(project).values())
    assert "Paper:d" not in _graph_node_ids(project)
    capsys.readouterr()

    # The operator's natural response to a Ctrl-C: run the same command again.
    rerun = FlakyExtractor()
    result = wiki.compile(changed_only=True, doc_extractor=rerun)

    assert sorted(Path(c).name for c in rerun.calls) == ["a.md", "b.md", "c.md", "d.md"]
    assert result["processed_files"] == 4
    assert "Paper:d" in _graph_node_ids(project)
    # ...and the recompile the operator did not ask for is explained.
    err = capsys.readouterr().err
    assert "--changed-only could not reuse the prior graph" in err
    assert "graph.json is not known to cover every tracked document" in err


def test_kill_inside_the_scoped_retry_window_is_not_a_silent_success_next_run(
    tmp_path, monkeypatch, capsys
):
    """P2: the scoped retry's own kill window must not become a clean no-op.

    ``delete_nodes_by_source_with_edges`` lands before ``_write_artifacts``, so
    a kill between them leaves sqlite provenance UNDER-covering graph.json — the
    retried doc's rows are gone while its degraded nodes are still in the graph.
    The batch also rewrote that doc's manifest entry from scratch, clearing the
    fallback marker, so ``has_pending_fallback`` is False on the next run and
    only the completeness marker is left to notice.
    """
    import sqlite3

    project, wiki = _make_project(tmp_path, "retry-killed-window", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))
    assert f"Concept:c{FlakyExtractor.JUNK_SUFFIX}" in _graph_node_ids(project)

    with monkeypatch.context() as kill:
        _kill_during(kill)
        with pytest.raises(KeyboardInterrupt):
            wiki.compile(
                changed_only=True, retry_fallbacks=True, doc_extractor=FlakyExtractor()
            )

    # What the window leaves behind: no provenance for c.md, a still-degraded
    # graph, and no fallback marker to ask for another retry.
    with sqlite3.connect(str(wiki.paths.sqlite)) as con:
        assert (
            con.execute(
                "select 1 from node_provenance where source_path like ?", ("%c.md",)
            ).fetchall()
            == []
        )
    assert f"Concept:c{FlakyExtractor.JUNK_SUFFIX}" in _graph_node_ids(project)
    assert all("fallback" not in entry for entry in _manifest(project).values())
    capsys.readouterr()

    recovered = FlakyExtractor()
    result = wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    # Full recompile, so the degraded nodes actually leave and provenance is
    # rebuilt — rather than a "success" reported over the same degraded graph.
    assert result["processed_files"] == 3
    nodes = _graph_nodes(project)
    assert f"Concept:c{FlakyExtractor.JUNK_SUFFIX}" not in nodes
    assert nodes["Paper:c"]["description"] == "typed summary of c"
    assert "graph.json is not known to cover every tracked document" in capsys.readouterr().err


def test_manifest_predating_the_marker_recompiles_once_and_says_why(tmp_path, capsys):
    """Existing workspaces pay the completeness guard exactly once, out loud.

    Every manifest written before ``graphed`` existed has no stamps, so the
    first ``--changed-only`` on such a workspace is a full recompile. That is
    correct (nothing proves the graph is whole) but must be explained, and the
    recompile re-stamps — so the SECOND run no-ops again and the guard is not a
    standing tax.

    ONCE is scoped to an UNLIMITED recompile, which is what this test runs.
    Under a retained ``--limit`` the repair re-stamps only the docs it reached,
    so the warning repeats every run — the ceiling recorded at
    ``_recompile_note``, pinned by
    ``test_the_completeness_guard_says_so_when_limit_makes_it_unclearable``.
    """
    project, wiki = _make_project(tmp_path, "noop-legacy", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    manifest_path = project / ".tesserae" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["files"].values():
        entry.pop("graphed", None)  # a pre-marker manifest
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()

    first = FlakyExtractor()
    assert wiki.compile(changed_only=True, doc_extractor=first)["processed_files"] == 2
    assert "graph.json is not known to cover every tracked document" in capsys.readouterr().err

    second = FlakyExtractor()
    assert wiki.compile(changed_only=True, doc_extractor=second)["processed_files"] == 0
    assert second.calls == []
    assert "could not reuse the prior graph" not in capsys.readouterr().err


def test_graphed_marker_is_dropped_for_docs_a_narrower_compile_no_longer_covers(
    tmp_path,
):
    """``--limit`` rebuilds graph.json from FEWER docs; their stamps must go.

    Otherwise a later ``--changed-only --retry-fallbacks`` would trust a marker
    describing a graph that no longer exists — the same data loss by a different
    route than the kill above.
    """
    project, wiki = _make_project(tmp_path, "retry-limit", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert all(entry.get("graphed") is True for entry in _manifest(project).values())

    wiki.compile(limit=1, doc_extractor=FlakyExtractor())

    stamped = {
        Path(k).name for k, v in _manifest(project).items() if v.get("graphed") is True
    }
    assert stamped == {"a.md"}, "only the doc the limited compile graphed stays stamped"


def test_retry_falls_back_when_the_provenance_sidecar_cannot_tombstone(
    tmp_path, capsys
):
    """No sidecar coverage => no tombstone => the scoped path must not run.

    Without provenance rows the degraded nodes cannot be removed before the
    merge, so the "recovery" would preserve exactly what it is meant to replace.
    Falling through to the full recompile is the correct, always-right answer.
    """
    import sqlite3

    project, wiki = _make_project(tmp_path, "retry-noprov", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))

    with sqlite3.connect(str(wiki.paths.sqlite)) as con:
        con.execute("delete from node_provenance")
        con.commit()
    capsys.readouterr()

    recovered = FlakyExtractor(failing=set())
    wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    assert sorted(Path(c).name for c in recovered.calls) == ["a.md", "b.md", "c.md"]
    assert "provenance sidecar does not cover the prior graph" in capsys.readouterr().err


def test_retry_warns_when_the_corpus_moved_under_it(tmp_path, capsys):
    """The realistic workflow: ``refresh`` appends a session doc, then retry.

    ``corpus_unchanged`` goes False, the scoped path is refused, and all N docs
    are re-extracted. That is CORRECT — but it must not be silent, or the
    operator just watches a "35-doc retry" run for hours over 137 documents.
    """
    project, wiki = _make_project(tmp_path, "retry-moved", ["a", "b", "c"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))

    (project / "docs" / "today.md").write_text("# today\nnew session", encoding="utf-8")
    capsys.readouterr()

    recovered = FlakyExtractor(failing=set())
    wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    assert len(recovered.calls) == 4  # the whole corpus, not just c.md
    err = capsys.readouterr().err
    assert "--retry-fallbacks could not scope this run" in err
    assert "the tracked corpus changed since the last compile" in err


# ------------------------------------------------ the retry REPAIRS the graph


def test_retry_replaces_the_degraded_nodes_instead_of_merging_over_them(tmp_path):
    """Both contamination modes at once: same id, and different id.

    ``merge_graphs([prior, batch.graph])`` puts the prior FIRST and
    ``prefer_research_node`` defaults to ``chosen = existing``, so a same-id
    node keeps the DEGRADED payload; a node the deterministic pass named
    differently just survives beside its typed replacement. Both leave the
    fallback marker cleared, so the operator gets no signal and a second
    ``--retry-fallbacks`` is a no-op.
    """
    project, wiki = _make_project(tmp_path, "retry-repair", ["a", "b", "c"])

    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"c.md"}))
    degraded = _graph_nodes(project)
    assert degraded["Paper:c"]["description"] == "DEGRADED keyword blurb"
    assert f"Concept:c{FlakyExtractor.JUNK_SUFFIX}" in degraded

    recovered = FlakyExtractor(failing=set())
    wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)
    assert [Path(c).name for c in recovered.calls] == ["c.md"], "still scoped"

    nodes = _graph_nodes(project)
    # (i) same id — the typed payload wins, not the degraded one it replaces.
    assert nodes["Paper:c"]["description"] == "typed summary of c"
    assert nodes["Paper:c"]["metadata"]["extractor"] == "llm"
    # (ii) different id — the degraded-only node is gone, not living alongside.
    assert f"Concept:c{FlakyExtractor.JUNK_SUFFIX}" not in nodes
    assert "ResearchTopic:c-typed" in nodes
    # ...and the untouched docs are untouched.
    assert {"Paper:a", "Paper:b"} <= set(nodes)


def test_retry_keeps_nodes_an_unchanged_doc_still_asserts(tmp_path):
    """The tombstone must not over-delete: co-owned nodes survive.

    ``delete_nodes_by_source_with_edges`` drops a node only when the retried
    doc was its LAST provenance owner. A shared field the unchanged docs still
    assert must survive; removing it would be the cross-file collapse the
    differ's provenance model exists to prevent. The recovered extraction
    deliberately stops emitting it, so nothing re-adds it and an over-wide
    tombstone is visible rather than masked by the re-extraction.
    """
    project, wiki = _make_project(tmp_path, "retry-shared", ["a", "b", "c"])

    wiki.compile(changed_only=True, doc_extractor=SharedConceptExtractor(failing={"c.md"}))
    assert SharedConceptExtractor.SHARED_ID in _graph_node_ids(project)

    recovered = SharedConceptExtractor(failing=set(), emit_shared=False)
    wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=recovered)

    assert [Path(c).name for c in recovered.calls] == ["c.md"]
    assert SharedConceptExtractor.SHARED_ID in _graph_node_ids(project)


def test_fallback_hint_names_both_flags(tmp_path, capsys):
    """The printed recovery hint is what operators paste; it must be runnable.

    ``--retry-fallbacks`` alone is a whole-corpus re-extract (see the CLI
    warning below), so the hint has to name ``--changed-only`` too.
    """
    from tesserae.batch import BatchIngestRunner

    doc = tmp_path / "bad.md"
    doc.write_text("# Bad\nNovel View Synthesis", encoding="utf-8")
    BatchIngestRunner(
        extractor=FlakyExtractor(failing={"bad.md"}), manifest_path=tmp_path / "manifest.json"
    ).run([doc], source_kind="Paper", changed_only=True)

    assert (
        "re-attempt with `compile --changed-only --retry-fallbacks`."
        in capsys.readouterr().err
    )


def test_bare_retry_fallbacks_warns(tmp_path, capsys):
    """The flag alone buys a whole-corpus re-extract; the CLI must say so."""
    from tesserae.cli import main

    project, _ = _make_project(tmp_path, "retry-warn", ["a"])

    assert main([
        "compile",
        "--project",
        str(project),
        "--retry-fallbacks",
        "--extractor",
        "deterministic",
    ]) == 0
    assert "--retry-fallbacks has no effect without --changed-only" in capsys.readouterr().err


def test_limit_defers_a_doc_without_forfeiting_its_graph_coverage(tmp_path):
    """``--limit`` on a SCOPED retry must not cost the deferred doc its stamp.

    ``BatchIngestRunner`` breaks out of its scan at the limit, so every document
    past the cut is neither processed nor skipped. On a scoped run the prior
    graph is merged in and nothing tombstoned those documents, so graph.json
    still covers them exactly as their (untouched) manifest entries describe.
    Unstamping them anyway made the very next ``--changed-only
    --retry-fallbacks`` see incomplete coverage and re-extract the WHOLE corpus
    — the 137->35 bound defeated by one ``--limit`` (Codex A1).
    """
    project, wiki = _make_project(tmp_path, "retry-limit-defer", ["a", "b", "c", "d"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor(failing={"b.md", "d.md"}))

    limited = FlakyExtractor(failing=set())
    wiki.compile(changed_only=True, retry_fallbacks=True, limit=1, doc_extractor=limited)
    assert [Path(c).name for c in limited.calls] == ["b.md"]

    files = _manifest(project)
    # ``d.md`` is still marked for retry and still covered by graph.json...
    assert files[str(project / "docs" / "d.md")].get("fallback") is True
    assert "Concept:d" + FlakyExtractor.JUNK_SUFFIX in _graph_node_ids(project)
    # ...so the deferred doc keeps the stamp that says so.
    assert {Path(k).name for k, v in files.items() if v.get("graphed") is True} == {
        "a.md",
        "b.md",
        "c.md",
        "d.md",
    }

    # The pay-off: the follow-up retry is still SCOPED to the remaining doc.
    resumed = FlakyExtractor(failing=set())
    result = wiki.compile(changed_only=True, retry_fallbacks=True, doc_extractor=resumed)
    assert [Path(c).name for c in resumed.calls] == ["d.md"]
    assert result["processed_files"] == 1
    assert result["skipped_files"] == 3
    assert "Concept:d" + FlakyExtractor.JUNK_SUFFIX not in _graph_node_ids(project)


def test_a_scoped_run_unstamps_the_departed_doc_but_not_the_deferred_one(tmp_path):
    """The two reasons a doc is absent from the batch get OPPOSITE answers.

    One scoped run, both directions: ``a.md`` left the corpus (nothing vouches
    for its coverage any more — the stamp must go, which is what keeps the
    completeness guard honest) while ``c.md`` and ``d.md`` were merely deferred
    past ``--limit`` (their prior nodes are still in graph.json — the stamp
    stays). Widening the keep-set to every manifest key would pass the deferred
    half and silently lose this one.
    """
    project, wiki = _make_project(tmp_path, "scoped-limit-mixed", ["a", "b", "c", "d"])
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True  # EXPERIMENTAL — off by default
    wiki.paths.config.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

    wiki.compile(doc_extractor=FlakyExtractor())
    assert all(entry.get("graphed") is True for entry in _manifest(project).values())

    (project / "docs" / "a.md").unlink()  # GONE
    for stem in ("b", "c"):  # both changed; --limit 1 defers c
        (project / "docs" / f"{stem}.md").write_text(f"# {stem}\nRevised.", encoding="utf-8")

    scoped = FlakyExtractor()
    wiki.compile(changed_only=True, limit=1, doc_extractor=scoped)
    assert [Path(c).name for c in scoped.calls] == ["b.md"]

    files = _manifest(project)
    # The stale key survives a scoped run (the subtractive guard needs it)...
    assert {Path(k).name for k in files} == {"a.md", "b.md", "c.md", "d.md"}
    # ...but only as an UNSTAMPED key, so the completeness guard refuses reuse.
    assert {Path(k).name for k, v in files.items() if v.get("graphed") is True} == {
        "b.md",
        "c.md",
        "d.md",
    }


def test_incremental_reuse_refuses_a_prior_graph_a_kill_left_partial(
    tmp_path, monkeypatch, capsys
):
    """The experimental differ has a reuse path of its own, and it needs the marker.

    With ``incremental_compile=true`` the completeness guard was skipped
    outright (it required ``not incremental_active``), yet the differ reuses the
    prior ``graph.json`` verbatim whenever the batch re-extracts nothing. Kill a
    run after the manifest write but before ``_write_artifacts`` and the added
    doc sits at a matching sha with no ``graphed`` stamp: the next
    ``--changed-only`` skips it by sha, hits ``processed == 0``, reuses a graph
    that never had it — and reports a clean run. Nothing ever re-stamps, so the
    document is missing for good.
    """
    project, wiki = _make_project(tmp_path, "incremental-killed", ["a", "b"])
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True  # EXPERIMENTAL — off by default
    wiki.paths.config.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

    wiki.compile(doc_extractor=FlakyExtractor())
    assert all(entry.get("graphed") is True for entry in _manifest(project).values())

    (project / "docs" / "c.md").write_text("# c\nbody", encoding="utf-8")
    with monkeypatch.context() as kill:
        _kill_during(kill)
        with pytest.raises(KeyboardInterrupt):
            wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    # The state the repro depends on: three tracked docs at matching shas, a
    # graph.json holding two, and the new one unstamped.
    assert {Path(k).name for k in _manifest(project)} == {"a.md", "b.md", "c.md"}
    assert "Paper:c" not in _graph_node_ids(project)
    capsys.readouterr()

    rerun = FlakyExtractor()
    result = wiki.compile(changed_only=True, doc_extractor=rerun)

    assert sorted(Path(c).name for c in rerun.calls) == ["a.md", "b.md", "c.md"]
    assert result["processed_files"] == 3
    assert "Paper:c" in _graph_node_ids(project)
    assert "graph.json is not known to cover every tracked document" in capsys.readouterr().err

    # ...and the guard is not a standing tax on the differ: that recompile
    # re-stamped, so the very next incremental run reuses the prior graph again
    # instead of re-extracting everything forever.
    after = FlakyExtractor()
    second = wiki.compile(changed_only=True, doc_extractor=after)
    assert second["processed_files"] == 0
    assert after.calls == []
    assert "Paper:c" in _graph_node_ids(project)


class _EmptyLoader:
    """A ``SourceLoader`` whose ``discover()`` yields nothing this run."""

    def discover(self):
        return iter(())

    def fetch(self, source_id: str):  # pragma: no cover - never reached
        raise KeyError(source_id)


def test_an_empty_loader_with_no_graph_on_disk_still_compiles(tmp_path):
    """The loader branch must not assume a prior graph.json exists.

    ``ProjectWiki.init`` writes one, so the only way here is deleting it — but
    that is exactly what an operator does to force a rebuild, and any read of
    the missing file would turn "nothing to extract" into a crash.
    """
    project, wiki = _make_project(tmp_path, "loader-empty-nograph", ["a"])
    wiki.paths.graph.unlink()

    result = wiki.compile(
        loader=_EmptyLoader(), changed_only=True, doc_extractor=FlakyExtractor()
    )

    assert result["processed_files"] == 0
    assert "Paper:a" not in _graph_node_ids(project)


def test_an_empty_loader_run_under_the_differ_still_applies_a_deletion(tmp_path):
    """The differ OWNS the reuse on an empty changed-set; the base must stay empty.

    ``graphs`` is empty here, so the loader branch hands the differ an EMPTY base
    and lets it rebind ``graph`` to its own prior graph. Handing it the
    POPULATED prior graph instead looks equivalent — and is, until something
    was TOMBSTONED. A deleted ``changed_paths`` entry takes the differ's
    ``else`` arm, which appends ``graph`` to ``merge_inputs`` after the
    tombstone: the populated base then merges the deleted document's nodes
    straight back in. Measured on this fixture, ``Paper:a`` was resurrected.
    """
    project, wiki = _make_project(tmp_path, "loader-empty-deletion", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())
    assert {"Paper:a", "Paper:b"} <= _graph_node_ids(project)

    doc_a = project / "docs" / "a.md"
    doc_a.unlink()
    wiki.compile(
        loader=_EmptyLoader(),
        changed_only=True,
        changed_paths=[doc_a],
        doc_extractor=FlakyExtractor(),
        incremental_override=True,
    )

    node_ids = _graph_node_ids(project)
    assert "Paper:a" not in node_ids, "the deleted document's node was resurrected"
    assert "Paper:b" in node_ids, "...and the survivors must not be collateral"


def test_the_completeness_guard_says_so_when_limit_makes_it_unclearable(tmp_path, capsys):
    """The guard's warning must not promise a recompile ``--limit`` forbids.

    An entry can be unstamped for a THIRD reason beyond the interrupted compile
    and the pre-marker manifest: it was DEFERRED past ``--limit`` at the stamp
    block. That one never clears while the limit is retained — the demoted run
    is full, so it rebuilds graph.json from the docs it processed alone,
    restarts from the top of the work-list, defers the same doc, and unstamps
    it again. Measured: four consecutive runs, four warnings, and ``c.md``
    never re-stamped.

    The livelock is a documented ceiling (a limited run is a partial compile by
    construction; the guard demands totality). What must NOT stand is the
    warning claiming it is "re-extracting the whole corpus" and implying one
    more run fixes it. Pinned here: the loop, and the message that names both
    the limit and the exit.
    """
    project, wiki = _make_project(tmp_path, "guard-limit-livelock", ["a", "b", "c"])
    wiki.compile(doc_extractor=FlakyExtractor())
    assert all(entry.get("graphed") is True for entry in _manifest(project).values())

    wiki.compile(limit=2, doc_extractor=FlakyExtractor())  # defers c.md, unstamps it
    capsys.readouterr()

    for _ in range(3):
        run = FlakyExtractor()
        wiki.compile(changed_only=True, limit=2, doc_extractor=run)
        err = capsys.readouterr().err
        # The ceiling: no progress, every run, forever.
        assert [Path(c).name for c in run.calls] == ["a.md", "b.md"]
        assert _manifest(project)[str(project / "docs" / "c.md")].get("graphed") is None
        # The honesty: the warning names --limit and the one action that clears it.
        assert "graph.json is not known to cover every tracked document" in err
        assert "a document deferred past --limit" in err
        assert "re-extracting up to --limit=2 of 3" in err
        assert "repeats every run until you compile WITHOUT --limit" in err
        assert "re-extracting the whole corpus" not in err


def test_an_unlimited_guard_warning_still_promises_the_whole_corpus(tmp_path, capsys):
    """The note must stay unchanged when ``--limit`` is absent or generous.

    Without a limit the demoted recompile really does cover the corpus and
    really does clear the guard in ONE run — so the original wording is the
    honest one there, and the new branch must not leak into it.
    """
    project, wiki = _make_project(tmp_path, "guard-unlimited", ["a", "b"])
    wiki.compile(changed_only=True, doc_extractor=FlakyExtractor())

    manifest_path = project / ".tesserae" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["files"].values():
        entry.pop("graphed", None)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()

    # A limit that cannot defer anything counts as unlimited for this message.
    assert wiki.compile(changed_only=True, limit=2, doc_extractor=FlakyExtractor())[
        "processed_files"
    ] == 2
    err = capsys.readouterr().err
    assert "re-extracting the whole corpus." in err
    assert "--limit" not in err.split("(interrupted compile,")[1].split(")")[1]
    # ...and it really did clear in ONE run.
    quiet = FlakyExtractor()
    assert wiki.compile(changed_only=True, doc_extractor=quiet)["processed_files"] == 0
    assert quiet.calls == []
    assert "could not reuse the prior graph" not in capsys.readouterr().err
