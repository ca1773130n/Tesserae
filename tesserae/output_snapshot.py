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
``report.md``, ``graphiti_episodes.jsonl``, ``agent_harness/``, ``sqlite.db``, the Obsidian
vault (bidirectional, user-owned), ``manifest.json`` (input state), lint
reports, and all ledgers/caches. Extending scope later is a one-line
allowlist edit. The state file this module writes is excluded from the hash
by construction (the allowlist never includes it) and carries no timestamps.

``temporal_facts.jsonl`` is in scope but hashed FIELD-WISE. It was excluded
wholesale because one of its fields — ``confidence`` — is read from the
mutable ``node_memory`` sidecar, so a recurrence-reinforcement bump would
have fired the tripwire with no projection drift behind it. Excluding the
whole file to dodge that also hid every other field, including the
``valid_from`` of all 103,705 facts, which ``temporal._source_ts`` derives
purely from graph.json. Since ``confidence`` is the ONLY sidecar-sourced
field on a ``TemporalFact``, dropping just that key restores full coverage of
the derived values while keeping the false alarm out.

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
# JSONL projections hashed with sidecar-sourced keys dropped per record, so
# the tripwire watches the graph-derived fields without firing on sidecar
# churn. See the module docstring for why ``confidence`` is the only one.
PROJECTION_LAYER_JSONL: tuple[tuple[str, frozenset[str]], ...] = (
    ("temporal_facts.jsonl", frozenset({"confidence"})),
)
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


def _hash_jsonl_without(path: Path, dropped_keys: frozenset[str]) -> bytes:
    """Digest of a JSONL file with ``dropped_keys`` removed from each record.

    Re-serialised with ``sort_keys`` so the digest depends on record VALUES,
    not on the writer's key order. A record that will not parse is hashed as
    its raw bytes rather than skipped: unreadable output is still output, and
    silently dropping it would make a corrupted projection look stable.
    """
    digest = hashlib.sha256()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _MISSING
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            digest.update(line.encode("utf-8") + b"\0")
            continue
        if isinstance(record, dict):
            record = {k: v for k, v in record.items() if k not in dropped_keys}
        digest.update(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\0"
        )
    return digest.digest()


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
    for name, dropped_keys in PROJECTION_LAYER_JSONL:
        projections.update(name.encode("utf-8") + b"\0")
        projections.update(_hash_jsonl_without(root / name, dropped_keys))
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
