"""Compile-output snapshot hashing — no-op detector + idempotence tripwire.

After every ``ProjectWiki.compile`` the compiled artifact set is hashed twice
(before ingest / after the post-compile lint) and the pair is compared to
produce two signals:

* ``output_changed`` — any part differs; the machine-readable no-op signal
  for downstream automation (vault sync, CI refresh PRs, agents). This is
  OpenWiki's snapshot-gate pattern (``createOpenWikiContentSnapshot``).
* ``idempotence_suspect`` — the graph layer is byte-identical but a
  projection changed: a deterministic-projection violation, i.e. the
  permanent byte-idempotence tripwire (compile determinism broke 4× via
  wall-clock/mutable state; tests only cover the fixture corpus, this
  watches every real compile).

The hash scope is an ALLOWLIST of test-proven byte-stable artifacts only
(see ``tests/test_idempotence.py::test_compile_is_byte_idempotent`` and the
phase-5 suite). Deliberately excluded because their byte-stability is
unproven and one noisy artifact would make the signal cry wolf:
``report.md``, ``competitive_report.md``, ``temporal_facts.jsonl`` (depends
on the mutable ``node_memory`` sidecar), ``cognee_bundle/``,
``graphiti_episodes.jsonl``, ``agent_harness/``, ``sqlite.db``, the Obsidian
vault (bidirectional, user-owned), ``manifest.json`` (input state), lint
reports, and all ledgers/caches. Extending scope later is a one-line
allowlist edit. The state file this module writes is excluded from the hash
by construction (the allowlist never includes it) and carries no timestamps.

See docs/superpowers/plans/2026-07-09-openwiki-output-snapshot-plan.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

# The exact inputs the projections are a pure function of, in hash order.
# ``config.json`` belongs here (not to the projections layer): ``site_title``
# / ``name`` feed KarpathyLayerWriter and the site build, so a config-only
# change must read as "inputs changed", not as projection drift.
GRAPH_LAYER_FILES: tuple[str, ...] = (
    "config.json", "graph.json", "code-graph.json", "combined-graph.json",
)
PROJECTION_LAYER_DIRS: tuple[str, ...] = ("wiki", "site", "markdown_projection")
# Append-only ledgers / OS noise inside allowlisted dirs — the same
# exclusions ``tests/test_idempotence.py::_hash_tree`` uses.
EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {".history.jsonl", ".build-history.jsonl", ".DS_Store"}
)

# Fed to the hash in place of a missing file/dir (mirrors OpenWiki's
# ``addDirectoryToSnapshot`` race handling): a deleted allowlisted artifact
# changes the hash — correct, it is an output change.
_MISSING = b"missing"


@dataclass(frozen=True)
class OutputSnapshot:
    """Two-part digest of the byte-idempotent compile output."""

    graph_sha256: str
    projections_sha256: str

    @property
    def output_sha256(self) -> str:
        """sha256 over the two part-digests — the single combined hash."""
        combined = hashlib.sha256()
        combined.update(self.graph_sha256.encode("ascii"))
        combined.update(self.projections_sha256.encode("ascii"))
        return combined.hexdigest()


def snapshot_output(root: Path) -> OutputSnapshot:
    """Hash the allowlisted compile artifacts under ``root`` (``.tesserae/``).

    Deterministic and platform-stable: fixed file order for the graph layer,
    sorted walks with forward-slashed relative paths for the projections
    layer, NUL-separated path prefixes so file boundaries can't alias.
    """
    graph = hashlib.sha256()
    for name in GRAPH_LAYER_FILES:
        graph.update(name.encode("utf-8") + b"\0")
        try:
            graph.update((root / name).read_bytes())
        except OSError:
            graph.update(_MISSING)
    projections = hashlib.sha256()
    for dir_name in PROJECTION_LAYER_DIRS:
        directory = root / dir_name
        if not directory.is_dir():
            projections.update(_MISSING)
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name in EXCLUDED_BASENAMES:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            projections.update(rel.encode("utf-8") + b"\0")
            try:
                projections.update(path.read_bytes())
            except OSError:
                projections.update(_MISSING)
    return OutputSnapshot(
        graph_sha256=graph.hexdigest(),
        projections_sha256=projections.hexdigest(),
    )


def write_state(path: Path, snapshot: OutputSnapshot, changed: bool) -> None:
    """Persist the snapshot to ``path`` (``.tesserae/output-snapshot.json``).

    Hex digests + a bool only — no timestamps, no wall-clock state — so two
    compiles over identical inputs write byte-identical state files. Written
    tmp-file + ``os.replace`` (mirrors ``BatchIngestRunner._write_manifest``).
    """
    payload = {
        "changed": changed,
        "graph_sha256": snapshot.graph_sha256,
        "output_sha256": snapshot.output_sha256,
        "projections_sha256": snapshot.projections_sha256,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
