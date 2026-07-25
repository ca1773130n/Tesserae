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
    assert "--changed-only could not skip this run" in err
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
    assert "could not skip this run" not in capsys.readouterr().err


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
