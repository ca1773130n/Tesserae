"""Vault → graph overlay reader for the bidirectional sync feature.

Computes the per-field diff between user-editable vault markdown and the
projected state recorded in :mod:`llm_wiki.vault_snapshot`. Each divergence
becomes a :class:`VaultOverride` that downstream code applies onto the typed
graph before the next projection writes.

Design reference: docs/integrations/obsidian-sync.md (Tier 1a). Per-field
ownership matrix:

* ``name`` — frontmatter ``title``. Vault wins.
* ``aliases`` — frontmatter ``aliases``. Vault wins.
* ``description`` — extracted from the body callout (or first paragraph
  for node types without a callout). Vault wins.
* ``metadata.<key>`` — every frontmatter scalar that isn't in the
  reserved-system set (``node_id``, ``title``, ``type``, ``aliases``,
  ``source_path``, ``edges_out``, ``edges_in``, ``cross_vault``).

Out of scope for this Tier:

* Edge edits (vault wikilinks become ``user_link`` edges in Tier 1b/2).
* User-notes append zone (Tier 1b).
* Multi-locale vaults.

We deliberately don't depend on PyYAML — the projector emits a constrained
YAML subset under our control, so parsing it precisely here is safer than
relying on PyYAML's broader interpretation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .research_graph import ResearchGraph, ResearchNode
from .vault_snapshot import NodeSnapshot


# Frontmatter keys the projector controls. Anything else is treated as a
# user-editable metadata scalar. Keep in sync with :func:`render_node_page`
# in llm_wiki/markdown_projection.py.
_SYSTEM_FRONTMATTER_KEYS = frozenset({
    "node_id",
    "title",
    "type",
    "aliases",
    "source_path",
    "edges_out",
    "edges_in",
    "cross_vault",
})


@dataclass(frozen=True)
class VaultOverride:
    """A single user edit detected by diffing vault file against snapshot.

    ``field`` is the dotted name of the overridden property on the
    :class:`ResearchNode`. ``vault_value`` is what the user wrote in the
    vault; ``snapshot_value`` is what the projector last wrote, kept so the
    diverged-fields report can show the before/after.
    """

    node_id: str
    field: str
    vault_value: Any
    snapshot_value: Any


# ---------------------------------------------------------------- Parsing


def parse_frontmatter(text: str) -> Optional[Dict[str, Any]]:
    """Parse the constrained YAML subset the projector emits.

    Returns ``None`` when the document has no ``---`` frontmatter block.
    Supports:

    * ``key: scalar``
    * ``key: [a, b, c]`` inline lists
    * ``key:`` followed by ``  subkey: [...]`` lines (one-level nesting,
      used for ``edges_out`` / ``edges_in``).

    Doesn't try to handle every YAML feature — see module docstring.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            end_idx = idx
            break
    if end_idx == -1:
        return None

    result: Dict[str, Any] = {}
    current_key: Optional[str] = None
    nested: Dict[str, Any] = {}

    def _flush_nested() -> None:
        nonlocal current_key, nested
        if current_key is not None and nested:
            result[current_key] = dict(nested)
        current_key = None
        nested = {}

    for raw in lines[1:end_idx]:
        if not raw.strip():
            continue
        # Two-space indent = continuation of a previous nested block.
        if raw.startswith("  ") and current_key is not None:
            inner = raw.strip()
            if ":" in inner:
                k, _, v = inner.partition(":")
                nested[k.strip()] = _parse_scalar_or_list(v.strip())
            continue
        # Any non-indented line starts a new top-level entry.
        _flush_nested()
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            current_key = key  # nested block will follow on next iteration
        else:
            result[key] = _parse_scalar_or_list(value)
    _flush_nested()
    return result


def _parse_scalar_or_list(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",")]
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    # Numeric coercion — only for pure integers. We intentionally don't
    # try to parse floats: arxiv IDs like "2308.04079" look float-ish but
    # are strings, and the typed graph's float metadata is rare enough
    # that round-tripping it via plain string is fine.
    if value.lstrip("-").isdigit():
        try:
            return int(value)
        except ValueError:
            pass
    return value


def extract_description(body: str) -> str:
    """Pull the description out of a projected node page body.

    Two layouts the projector emits, depending on node type:

    A. Callout (``> [!quote] Paper`` etc.) — collect the continuation lines
       (those starting with ``> `` after the label line).
    B. Plain paragraph after the ``# Heading`` — collect until the next
       heading or blank line.

    Returns the empty string when neither layout matches.
    """
    lines = body.splitlines()
    idx = 0
    # Skip up to and through the H1.
    while idx < len(lines) and not lines[idx].startswith("# "):
        idx += 1
    idx += 1
    # Skip blank lines after the heading.
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return ""

    if lines[idx].startswith("> [!"):
        idx += 1  # skip the label line
        collected: List[str] = []
        while idx < len(lines):
            line = lines[idx]
            if line.startswith("> "):
                collected.append(line[2:])
            elif line.rstrip() == ">":
                collected.append("")
            else:
                break
            idx += 1
        return "\n".join(collected).strip()

    # Plain paragraph case.
    collected = []
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("#") or not line.strip():
            break
        collected.append(line)
        idx += 1
    return "\n".join(collected).strip()


def _split_body(text: str) -> str:
    """Return the body after the ``---`` frontmatter, or the whole text if there is no frontmatter."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else text


# ---------------------------------------------------------------- Overlay


def compute_overrides(
    vault_dir: Path,
    snapshot: Mapping[str, NodeSnapshot],
    node_by_id: Mapping[str, ResearchNode],
) -> List[VaultOverride]:
    """Walk the vault and emit a VaultOverride per detected user edit.

    Files without a ``node_id`` in their frontmatter are skipped (vault
    index / dashboard / user-authored notes). Files whose ``node_id``
    doesn't appear in the snapshot are also skipped — they were added
    by a recent recompile and have no baseline yet.
    """
    if not vault_dir.is_dir():
        return []
    overrides: List[VaultOverride] = []
    for path in sorted(vault_dir.rglob("*.md")):
        # Skip the .obsidian config tree and any other dot-directory.
        if any(part.startswith(".") for part in path.relative_to(vault_dir).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = parse_frontmatter(text)
        if not frontmatter or "node_id" not in frontmatter:
            continue
        node_id = str(frontmatter["node_id"])
        snap = snapshot.get(node_id)
        if snap is None:
            continue
        overrides.extend(_diff_node(node_id, frontmatter, text, snap))
    return overrides


def _diff_node(
    node_id: str,
    frontmatter: Mapping[str, Any],
    text: str,
    snap: NodeSnapshot,
) -> Iterable[VaultOverride]:
    body = _split_body(text)
    vault_description = extract_description(body)

    vault_name = str(frontmatter.get("title", "")).strip()
    if vault_name and vault_name != snap.name:
        yield VaultOverride(
            node_id=node_id, field="name",
            vault_value=vault_name, snapshot_value=snap.name,
        )

    vault_aliases_raw = frontmatter.get("aliases", [])
    if isinstance(vault_aliases_raw, list):
        vault_aliases = tuple(str(a).strip() for a in vault_aliases_raw if str(a).strip())
    else:
        vault_aliases = (str(vault_aliases_raw).strip(),) if vault_aliases_raw else ()
    if list(vault_aliases) != list(snap.aliases):
        yield VaultOverride(
            node_id=node_id, field="aliases",
            vault_value=list(vault_aliases), snapshot_value=list(snap.aliases),
        )

    if vault_description != snap.description:
        yield VaultOverride(
            node_id=node_id, field="description",
            vault_value=vault_description, snapshot_value=snap.description,
        )

    for key, value in frontmatter.items():
        if key in _SYSTEM_FRONTMATTER_KEYS:
            continue
        snap_value = snap.metadata.get(key)
        if value != snap_value:
            yield VaultOverride(
                node_id=node_id, field=f"metadata.{key}",
                vault_value=value, snapshot_value=snap_value,
            )


def apply_overrides(graph: ResearchGraph, overrides: Sequence[VaultOverride]) -> ResearchGraph:
    """Return a new :class:`ResearchGraph` with overrides applied.

    Overrides that reference an unknown ``node_id`` are silently ignored —
    the diverged-fields report still surfaces them so the operator can see
    that a vault file is referencing a no-longer-existing node.
    """
    if not overrides:
        return graph
    nodes_by_id = {n.id: n for n in graph.nodes}
    for override in overrides:
        node = nodes_by_id.get(override.node_id)
        if node is None:
            continue
        if override.field == "name":
            nodes_by_id[node.id] = replace(node, name=str(override.vault_value))
        elif override.field == "aliases":
            new_aliases = [str(a) for a in (override.vault_value or [])]
            nodes_by_id[node.id] = replace(node, aliases=new_aliases)
        elif override.field == "description":
            nodes_by_id[node.id] = replace(node, description=str(override.vault_value))
        elif override.field.startswith("metadata."):
            key = override.field[len("metadata."):]
            new_metadata = dict(node.metadata or {})
            new_metadata[key] = override.vault_value
            nodes_by_id[node.id] = replace(node, metadata=new_metadata)
    return ResearchGraph(nodes=list(nodes_by_id.values()), edges=list(graph.edges))


def write_diverged_fields_report(overrides: Sequence[VaultOverride], path: Path) -> None:
    """Render the per-compile audit log of vault-vs-snapshot divergences.

    Emitted as a separate ``.llm-wiki/diverged-fields.md`` (not folded into
    ``lint-report.md``) so it can be diffed in git independently of the
    extractor's lint output. Empty input produces a stub file rather than
    deleting the existing one — keeps the path stable for tooling.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        "# Diverged fields",
        "",
        "> Per-compile audit of every field where the Obsidian vault and the previous",
        "> projection disagreed. Vault values won on this compile; entries here are",
        "> informational, not errors.",
        "",
    ]
    if not overrides:
        lines.append("_No divergences detected._")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    by_node: Dict[str, List[VaultOverride]] = defaultdict(list)
    for override in overrides:
        by_node[override.node_id].append(override)

    lines.append(f"Detected **{len(overrides)} override(s)** across **{len(by_node)} node(s)**.")
    lines.append("")
    for node_id in sorted(by_node):
        lines.append(f"## `{node_id}`")
        lines.append("")
        for override in by_node[node_id]:
            lines.append(f"### `{override.field}`")
            lines.append("")
            lines.append(f"- snapshot value: `{override.snapshot_value!r}`")
            lines.append(f"- vault value: `{override.vault_value!r}`")
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


__all__ = [
    "VaultOverride",
    "apply_overrides",
    "compute_overrides",
    "extract_description",
    "parse_frontmatter",
    "write_diverged_fields_report",
]
