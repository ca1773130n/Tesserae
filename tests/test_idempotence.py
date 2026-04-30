"""End-to-end idempotence tests for ``ProjectWiki.compile``.

These tests are the production-ready proof of §13's "byte-identical site
output" definition: running ``project compile`` twice in a row over the same
corpus must leave every file under ``.llm-wiki/site/`` and ``.llm-wiki/wiki/``
byte-identical, except for the two append-only history ledgers
(``.build-history.jsonl`` and ``.history.jsonl``) which intentionally record
each build / each rewrite.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Dict, Iterable, Set

import pytest

from llm_wiki.project import ProjectWiki


WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


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
        "second compile should leave .llm-wiki/site/ byte-identical (excluding "
        ".build-history.jsonl); diff: "
        f"{_diff_keys(snapshot_site_a, snapshot_site_b)}"
    )
    assert snapshot_wiki_b == snapshot_wiki_a, (
        "second compile should leave .llm-wiki/wiki/ byte-identical (excluding "
        ".history.jsonl); diff: "
        f"{_diff_keys(snapshot_wiki_a, snapshot_wiki_b)}"
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
    (``.llm-wiki/.build-history.jsonl``) so it survives the rebuild of
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
