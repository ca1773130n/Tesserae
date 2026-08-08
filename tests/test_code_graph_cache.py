"""Code-graph extraction cache (delta-scoped regeneration).

Unit tests for the ``tesserae.code_graph`` cache primitives plus integration
tests for the compile gate in ``ProjectWiki.ingest``: when the walked code
tree is provably unchanged (stat manifest + extractor fingerprint match), the
compile reuses the cached extractor output instead of re-parsing every file —
whole-layer grain, never a partial graph. The cache file
(``.tesserae/code-graph-cache.json``) is INPUT state carrying ``mtime_ns``
and must stay out of every output-hash scope; the byte-parity tests here are
the merge gate for that contract.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tesserae.code_graph import (
    CodeGraphExtractor,
    extractor_fingerprint,
    manifest_delta,
    read_code_graph_cache,
    stat_manifest,
    write_code_graph_cache,
)
from tesserae.project import ProjectWiki, SessionExtractionOptions
from tesserae.research_graph import graph_from_payload


@pytest.fixture(autouse=True)
def _deterministic_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the LLM-backed community_summaries pass off (same guard as
    tests/test_idempotence.py): byte-parity is a guarantee of the
    DETERMINISTIC compile."""
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "false")


# --------------------------------------------------------------------------- #
# Unit tests: cache primitives over a tiny tree
# --------------------------------------------------------------------------- #


def _seed_tree(root: Path) -> None:
    """Two .py code files + one .md non-code file."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (root / "beta.py").write_text(
        "import json\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    (root / "notes.md").write_text("# Notes\n\nNot a code file.\n", encoding="utf-8")


def test_stat_manifest_sorted_and_deterministic(tmp_path: Path) -> None:
    _seed_tree(tmp_path)
    extractor = CodeGraphExtractor(tmp_path)
    files = extractor.iter_code_files([tmp_path])
    first = stat_manifest(files, tmp_path)
    second = stat_manifest(files, tmp_path)
    assert first is not None
    assert first == second
    paths = [entry[0] for entry in first]
    assert paths == sorted(paths)
    assert paths == ["alpha.py", "beta.py"]  # the .md file is not walked


def test_stat_manifest_none_on_vanished_file(tmp_path: Path) -> None:
    _seed_tree(tmp_path)
    files = [tmp_path / "alpha.py", tmp_path / "ghost.py"]
    assert stat_manifest(files, tmp_path) is None


def test_cache_roundtrip_graph_bytes_identical(tmp_path: Path) -> None:
    _seed_tree(tmp_path)
    extractor = CodeGraphExtractor(tmp_path)
    graph = extractor.extract_paths([tmp_path])
    manifest = stat_manifest(extractor.iter_code_files([tmp_path]), tmp_path)
    fingerprint = extractor_fingerprint()
    assert manifest is not None and fingerprint is not None
    cache_path = tmp_path / "cache" / "code-graph-cache.json"
    write_code_graph_cache(cache_path, graph, manifest, fingerprint)
    cached = read_code_graph_cache(cache_path)
    assert cached is not None
    assert cached.fingerprint == fingerprint
    assert cached.manifest == manifest
    rehydrated = graph_from_payload(cached.graph_payload)
    assert rehydrated.to_json(indent=2) == graph.to_json(indent=2)


def test_cache_write_is_byte_stable(tmp_path: Path) -> None:
    _seed_tree(tmp_path)
    extractor = CodeGraphExtractor(tmp_path)
    graph = extractor.extract_paths([tmp_path])
    manifest = stat_manifest(extractor.iter_code_files([tmp_path]), tmp_path)
    fingerprint = extractor_fingerprint()
    assert manifest is not None and fingerprint is not None
    cache_path = tmp_path / "code-graph-cache.json"
    write_code_graph_cache(cache_path, graph, manifest, fingerprint)
    first = cache_path.read_bytes()
    write_code_graph_cache(cache_path, graph, manifest, fingerprint)
    assert cache_path.read_bytes() == first


def test_read_cache_rejects_garbage(tmp_path: Path) -> None:
    assert read_code_graph_cache(tmp_path / "missing.json") is None
    junk = tmp_path / "junk.json"
    junk.write_text("junk", encoding="utf-8")
    assert read_code_graph_cache(junk) is None
    shaped = tmp_path / "shaped.json"
    shaped.write_text(
        json.dumps({"fingerprint": "abc", "manifest": []}), encoding="utf-8"
    )
    assert read_code_graph_cache(shaped) is None  # missing "graph"


def test_manifest_delta_counts() -> None:
    old = [["a.py", 1, 100], ["b.py", 2, 200], ["c.py", 3, 300]]
    new = [["a.py", 1, 100], ["b.py", 2, 999], ["d.py", 4, 400]]
    assert manifest_delta(old, new) == {"changed": 1, "added": 1, "removed": 1}
    assert manifest_delta(None, new) == {"changed": 0, "added": 3, "removed": 0}
    assert manifest_delta(old, old) == {"changed": 0, "added": 0, "removed": 0}


def test_extract_files_equals_extract_paths(tmp_path: Path) -> None:
    _seed_tree(tmp_path)
    extractor = CodeGraphExtractor(tmp_path)
    files = extractor.iter_code_files([tmp_path])
    assert extractor.extract_files(files).to_json(indent=2) == extractor.extract_paths(
        [tmp_path]
    ).to_json(indent=2)


# --------------------------------------------------------------------------- #
# Integration tests: the gate in ProjectWiki.ingest
# --------------------------------------------------------------------------- #


APP_PY = '''"""Fixture app module."""


def entry_point():
    return "ok"
'''


def _enable_code_layer(wiki: ProjectWiki) -> ProjectWiki:
    """Switch the opt-in code layer on for a fixture project.

    The layer is OFF unless a project asks for it by name, and
    ``source_kind="Repository"`` no longer implies it — that implicit trigger
    is exactly what the opt-in replaced. Every integration test below is about
    the cache sitting BEHIND the switch, so each one has to throw it first.
    ``test_code_layer_is_off_without_the_opt_in`` covers the other half.
    """
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["external_tools"] = [*(cfg.get("external_tools") or []), {"id": "codegraph"}]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return wiki


def _seed_repo_project(root: Path) -> ProjectWiki:
    """Repository-kind project with the code layer switched on: one root-level
    .py + a README markdown."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / "README.md").write_text(
        "# cgcache\n\nFixture repo for the code-graph cache.\n", encoding="utf-8"
    )
    return _enable_code_layer(
        ProjectWiki.init(root, name="cgcache", source_kind="Repository", sources=["."])
    )


def _compile(wiki: ProjectWiki) -> dict:
    return wiki.compile(session_options=SessionExtractionOptions(enabled=False))


def test_code_layer_is_off_without_the_opt_in(tmp_path: Path) -> None:
    """A Repository-kind project compiles NO code layer until it asks for one.

    This is the whole point of the switch, and it is asserted on the kind that
    used to trigger the layer implicitly — ``Repository``. Everything else in
    this module runs with the layer on, so without this test the default path
    (the one almost every real project takes) would have no coverage at all.
    """
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / "README.md").write_text("# cgcache\n\nNo code layer here.\n", encoding="utf-8")
    wiki = ProjectWiki.init(root, name="cgcache", source_kind="Repository", sources=["."])

    result = _compile(wiki)

    # The branch did not run: no report, no cache file, no artifact.
    assert result.get("code_graph_cache") is None
    assert not wiki.paths.code_graph_cache.exists()
    assert not wiki.paths.code_graph.exists()
    # And nothing code-shaped reached the graph the agents actually read.
    graph = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    assert not [n for n in graph["nodes"] if n.get("type") in {"SourceFile", "CodeSymbol"}]

    # Flipping the switch on the SAME project turns it on — so it is the
    # opt-in deciding, not anything about the project's kind or contents.
    _enable_code_layer(wiki)
    enabled = _compile(wiki)
    assert enabled["code_graph_cache"]["reused"] is False
    assert wiki.paths.code_graph.exists()

    # ...and flipping it back off removes the artifact rather than leaving a
    # stale snapshot of the repo behind to answer reads.
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["external_tools"] = [{"id": "codegraph", "enabled": False}]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    disabled = _compile(wiki)
    assert disabled.get("code_graph_cache") is None
    assert not wiki.paths.code_graph.exists()


def test_second_compile_reuses_code_graph(tmp_path: Path) -> None:
    wiki = _seed_repo_project(tmp_path / "proj")
    first = _compile(wiki)
    assert first["code_graph_cache"]["reused"] is False
    code_graph_bytes = wiki.paths.code_graph.read_bytes()
    graph_bytes = wiki.paths.graph.read_bytes()
    second = _compile(wiki)
    assert second["code_graph_cache"]["reused"] is True
    assert second["code_graph_cache"]["delta"] is None
    assert wiki.paths.code_graph.read_bytes() == code_graph_bytes
    assert wiki.paths.graph.read_bytes() == graph_bytes


def test_cache_parity_with_fresh_project(tmp_path: Path) -> None:
    """THE byte-parity gate: after a miss + rewrite cycle, code-graph.json must
    equal a fresh single compile of the identical tree with no cache at all.

    Same-root two-arm comparison (mirrors tests/test_incremental_parity.py):
    code-graph.json embeds absolute source paths, so two projects at different
    roots can never be byte-equal — the fresh arm wipes ``.tesserae`` and
    re-inits in place instead.
    """
    root = tmp_path / "proj"
    wiki = _seed_repo_project(root)
    _compile(wiki)  # seed: cache written
    app = root / "app.py"
    app.write_text(
        app.read_text(encoding="utf-8") + "\n\ndef added_after_seed():\n    return 3\n",
        encoding="utf-8",
    )
    mutated = _compile(wiki)  # cache miss: manifest changed
    assert mutated["code_graph_cache"]["reused"] is False
    arm_a = wiki.paths.code_graph.read_bytes()

    # Fresh arm: identical final tree, single compile, no cache file.
    shutil.rmtree(wiki.root)
    fresh = _enable_code_layer(
        ProjectWiki.init(root, name="cgcache", source_kind="Repository", sources=["."])
    )
    assert not fresh.paths.code_graph_cache.exists()
    fresh_result = _compile(fresh)
    assert fresh_result["code_graph_cache"]["reused"] is False
    assert fresh.paths.code_graph.read_bytes() == arm_a


def test_source_change_invalidates_cache(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    wiki = _seed_repo_project(root)
    _compile(wiki)
    app = root / "app.py"
    app.write_text(
        app.read_text(encoding="utf-8") + "\n\ndef newly_added_fn():\n    return 42\n",
        encoding="utf-8",
    )
    second = _compile(wiki)
    assert second["code_graph_cache"]["reused"] is False
    assert second["code_graph_cache"]["delta"] == {"changed": 1, "added": 0, "removed": 0}
    assert "newly_added_fn" in wiki.paths.code_graph.read_text(encoding="utf-8")


def test_file_add_and_remove_invalidate_cache(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    wiki = _seed_repo_project(root)
    _compile(wiki)
    extra = root / "extra.py"
    extra.write_text("def extra_helper():\n    return 7\n", encoding="utf-8")
    added = _compile(wiki)
    assert added["code_graph_cache"]["reused"] is False
    assert added["code_graph_cache"]["delta"] == {"changed": 0, "added": 1, "removed": 0}
    assert '"extra.py"' in wiki.paths.code_graph.read_text(encoding="utf-8")
    extra.unlink()
    removed = _compile(wiki)
    assert removed["code_graph_cache"]["reused"] is False
    assert removed["code_graph_cache"]["delta"] == {"changed": 0, "added": 0, "removed": 1}
    # Full re-extract guarantees deletion — no tombstoning needed.
    assert '"extra.py"' not in wiki.paths.code_graph.read_text(encoding="utf-8")


def test_corrupt_cache_recovers(tmp_path: Path) -> None:
    wiki = _seed_repo_project(tmp_path / "proj")
    _compile(wiki)
    wiki.paths.code_graph_cache.write_text("junk", encoding="utf-8")
    second = _compile(wiki)
    assert second["code_graph_cache"]["reused"] is False
    # The compile succeeded and rewrote a valid cache.
    payload = json.loads(wiki.paths.code_graph_cache.read_text(encoding="utf-8"))
    assert set(payload) == {"fingerprint", "graph", "manifest"}


def test_cache_excluded_from_output_snapshot(tmp_path: Path) -> None:
    """The mtime-bearing cache write on run 1 (and the cache state generally)
    must not register as output churn: a reuse compile is a clean no-op."""
    wiki = _seed_repo_project(tmp_path / "proj")
    _compile(wiki)
    second = _compile(wiki)
    assert second["code_graph_cache"]["reused"] is True
    assert second["output_changed"] is False
    assert second["idempotence_suspect"] is False


def test_non_code_project_has_no_cache_key(tmp_path: Path) -> None:
    root = tmp_path / "docs_proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text("# Notes\n\nA document.\n", encoding="utf-8")
    wiki = ProjectWiki.init(
        root, name="cgcache_docs", source_kind="SourceDocument", sources=["."]
    )
    result = _compile(wiki)
    assert "code_graph_cache" not in result
    assert not wiki.paths.code_graph_cache.exists()
