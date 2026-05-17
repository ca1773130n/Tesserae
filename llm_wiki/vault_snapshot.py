"""Persisted record of what the projector last wrote per node.

The vault overlay reader (see :mod:`llm_wiki.vault_pull`) needs a baseline to
diff against. We can't trust the vault files themselves because the user may
have edited them — that's the whole point of the feature. Instead, every
compile writes ``.llm-wiki/vault_snapshot.json`` capturing the *projected*
state of each node right after :class:`GraphMarkdownProjector` ran. The next
compile loads this snapshot, compares each vault file against the matching
entry, and treats every divergence as a user override.

Design contract: see docs/integrations/obsidian-sync.md, section
"What counts as 'the previous projection' for diffing?".

The snapshot is intentionally minimal — only the fields that vault_pull
inspects today (``name``, ``aliases``, ``description``, ``metadata``). Adding
fields here is cheap (the snapshot is regenerated on every compile), but
removing fields is a breaking change for any vault that's already mid-edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

from .research_graph import ResearchNode


SNAPSHOT_VERSION = 1
"""Schema version of the snapshot file. Bump if the on-disk shape changes
in a way that requires migration."""


@dataclass(frozen=True)
class NodeSnapshot:
    """The fields a single node contributed to the previous projection.

    These are the values the projector wrote, NOT the values currently in
    the vault. Compare against the vault file to find user edits.
    """

    name: str
    aliases: tuple[str, ...]
    description: str
    # Only scalar metadata keys are captured — list/dict metadata is rare in
    # the typed graph and reshaping it would require richer YAML round-tripping
    # than the overlay reader handles today. Out of scope for v1.
    metadata: Mapping[str, object]

    @classmethod
    def from_node(cls, node: ResearchNode) -> "NodeSnapshot":
        scalar_metadata = {
            key: value
            for key, value in (node.metadata or {}).items()
            if isinstance(value, (str, int, float, bool))
        }
        return cls(
            name=node.name,
            aliases=tuple(node.aliases or ()),
            description=node.description or "",
            metadata=scalar_metadata,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NodeSnapshot":
        aliases_value = payload.get("aliases", [])
        metadata_value = payload.get("metadata", {})
        return cls(
            name=str(payload.get("name") or ""),
            aliases=tuple(str(alias) for alias in (aliases_value or [])),
            description=str(payload.get("description") or ""),
            metadata=dict(metadata_value) if isinstance(metadata_value, dict) else {},
        )


def write_snapshot(nodes: Iterable[ResearchNode], path: Path) -> None:
    """Persist each node's projected state for the next compile's overlay diff.

    Atomic via write-to-temp-then-rename so a Ctrl-C mid-write doesn't leave a
    truncated snapshot that would mis-flag every subsequent vault edit as a
    user override.
    """
    snapshot: Dict[str, Dict[str, object]] = {
        node.id: NodeSnapshot.from_node(node).to_dict() for node in nodes
    }
    payload = {"version": SNAPSHOT_VERSION, "nodes": snapshot}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_snapshot(path: Path) -> Optional[Dict[str, NodeSnapshot]]:
    """Load the previous snapshot, or ``None`` when it doesn't exist or is unreadable.

    Returning ``None`` on a missing or corrupt snapshot is the safe default —
    the overlay reader treats a missing snapshot as "no baseline, no overrides
    to compute", which gives the first-ever compile-after-feature-ships a free
    pass instead of mis-flagging every vault file as user-edited.
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    nodes_raw = payload.get("nodes")
    if not isinstance(nodes_raw, dict):
        return None
    return {
        node_id: NodeSnapshot.from_dict(entry)
        for node_id, entry in nodes_raw.items()
        if isinstance(entry, dict)
    }


__all__ = ["NodeSnapshot", "SNAPSHOT_VERSION", "read_snapshot", "write_snapshot"]
