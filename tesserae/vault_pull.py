"""Vault → graph overlay reader for the bidirectional sync feature.

Computes the per-field diff between user-editable vault markdown and the
projected state recorded in :mod:`tesserae.vault_snapshot`. Each divergence
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

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .markdown_projection import USER_NOTES_END, USER_NOTES_START
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)
from .vault_snapshot import NodeSnapshot


# Frontmatter keys the projector controls. Anything else is treated as a
# user-editable metadata scalar. Keep in sync with :func:`render_node_page`
# in tesserae/markdown_projection.py.
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


@dataclass(frozen=True)
class VaultUserLinkChange:
    """An add/remove for a ``user_link`` edge driven by vault wikilinks.

    Separate from :class:`VaultOverride` because it operates on edges, not
    on node fields. ``target_node_id`` is ``None`` when the user's slug
    doesn't resolve to a known graph node — :func:`apply_user_link_changes`
    will create a :class:`~tesserae.research_graph.ResearchNodeType.STUB`
    tombstone node to anchor the link.
    """

    source_node_id: str
    target_slug: str
    target_node_id: Optional[str]
    action: str  # "add" | "remove"


_WIKILINK_RE = re.compile(r"\[\[([^|\]\n]+?)(?:\|[^\]\n]+)?\]\]")
"""Match ``[[slug]]`` and ``[[slug|display text]]``, capturing the slug."""


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


def extract_user_notes_block(body: str) -> str:
    """Return the inner content between the user-notes markers, or empty string.

    The append zone is `<!-- user-notes:start --> ... <!-- user-notes:end -->`
    (see :data:`tesserae.markdown_projection.USER_NOTES_START`). Anything
    outside those markers is projector-owned and is NOT user-editable in
    the "vault wins" sense.
    """
    start = body.find(USER_NOTES_START)
    if start == -1:
        return ""
    end = body.find(USER_NOTES_END, start)
    if end == -1:
        return ""
    return body[start + len(USER_NOTES_START):end].strip("\n").strip()


def extract_wikilink_slugs(text: str) -> List[str]:
    """Return every ``[[slug]]`` (or ``[[slug|alias]]``) inside ``text``, deduped + ordered.

    Tolerant of Unicode slugs, alias syntax, and inline placement (a single
    paragraph can hold several links). Anchors and embeds aren't parsed —
    ``[[note#heading]]`` returns ``"note#heading"`` verbatim and falls
    through to slug-lookup as-is, which lets us surface broken-anchor
    references too.
    """
    seen: List[str] = []
    for match in _WIKILINK_RE.finditer(text):
        slug = match.group(1).strip()
        if not slug or slug in seen:
            continue
        seen.append(slug)
    return seen


# ---------------------------------------------------------------- Overlay


def _load_vault_files(vault_dir: Path) -> List[tuple]:
    """Walk vault_dir once, returning (path, text, frontmatter) for node files.

    Only files with a ``node_id`` key in their frontmatter are included.
    Dot-directories (.obsidian etc.) are skipped.
    """
    result: List[tuple] = []
    for path in sorted(vault_dir.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault_dir).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if not fm or "node_id" not in fm:
            continue
        result.append((path, text, fm))
    return result


def compute_overrides(
    vault_dir: Path,
    snapshot: Mapping[str, NodeSnapshot],
    node_by_id: Mapping[str, ResearchNode],
    vault_files: Optional[List[tuple]] = None,
) -> List[VaultOverride]:
    """Walk the vault and emit a VaultOverride per detected user edit.

    Files without a ``node_id`` in their frontmatter are skipped (vault
    index / dashboard / user-authored notes). Files whose ``node_id``
    doesn't appear in the snapshot are also skipped — they were added
    by a recent recompile and have no baseline yet.

    Pass ``vault_files`` (from :func:`_load_vault_files`) to reuse an
    already-walked file list and avoid a second filesystem scan.
    """
    if not vault_dir.is_dir():
        return []
    files = vault_files if vault_files is not None else _load_vault_files(vault_dir)
    overrides: List[VaultOverride] = []
    for path, text, frontmatter in files:
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


def compute_user_link_changes(
    vault_dir: Path,
    graph: ResearchGraph,
    slug_by_id: Mapping[str, str],
    vault_files: Optional[List[tuple]] = None,
) -> List[VaultUserLinkChange]:
    """Diff wikilinks in vault user-notes blocks against existing user_link edges.

    Returns a list of add/remove records the caller can apply via
    :func:`apply_user_link_changes`. The diff is symmetric: a link the
    user typed but the graph doesn't have yet → ``add``; an edge the
    graph has but the user has since deleted from notes → ``remove``.

    Pass ``vault_files`` (from :func:`_load_vault_files`) to reuse an
    already-walked file list and avoid a second filesystem scan.
    """
    if not vault_dir.is_dir():
        return []
    id_by_slug = {slug: nid for nid, slug in slug_by_id.items()}

    files = vault_files if vault_files is not None else _load_vault_files(vault_dir)
    vault_links: set[tuple[str, str]] = set()  # (source_node_id, target_slug)
    for _path, text, frontmatter in files:
        source_id = str(frontmatter["node_id"])
        notes = extract_user_notes_block(_split_body(text))
        if not notes:
            continue
        for slug in extract_wikilink_slugs(notes):
            vault_links.add((source_id, slug))

    graph_links: set[tuple[str, str]] = set()  # (source_node_id, target_slug)
    for edge in graph.edges:
        if edge.type != "user_link":
            continue
        target_slug = slug_by_id.get(edge.target)
        if target_slug is None:
            # Edge points at a node we don't have a slug for (shouldn't
            # happen in practice — slug_by_id covers all nodes — but be
            # defensive so a corrupt graph doesn't crash the diff).
            continue
        graph_links.add((edge.source, target_slug))

    changes: List[VaultUserLinkChange] = []
    for source_id, slug in sorted(vault_links - graph_links):
        changes.append(VaultUserLinkChange(
            source_node_id=source_id,
            target_slug=slug,
            target_node_id=id_by_slug.get(slug),
            action="add",
        ))
    for source_id, slug in sorted(graph_links - vault_links):
        changes.append(VaultUserLinkChange(
            source_node_id=source_id,
            target_slug=slug,
            target_node_id=id_by_slug.get(slug),
            action="remove",
        ))
    return changes


def apply_user_link_changes(
    graph: ResearchGraph,
    changes: Sequence[VaultUserLinkChange],
) -> ResearchGraph:
    """Apply add/remove records to the graph, minting :class:`STUB` nodes as needed.

    Stub nodes carry ``metadata['vault_slug']`` so the next compile's slug
    map can resolve back to them and recognize the link as still-current.
    The node ``id`` is ``Stub:<slug>`` for deterministic re-resolution.
    """
    if not changes:
        return graph
    nodes_by_id = {node.id: node for node in graph.nodes}
    edges: List[ResearchEdge] = list(graph.edges)

    # Collect all removes up front for a single-pass filter.
    remove_keys: set = {
        (c.source_node_id, c.target_node_id)
        for c in changes
        if c.action == "remove" and c.target_node_id is not None
    }
    remove_sources_wild: set = {
        c.source_node_id
        for c in changes
        if c.action == "remove" and c.target_node_id is None
    }
    if remove_keys or remove_sources_wild:
        edges = [
            e for e in edges
            if not (
                e.type == "user_link"
                and (
                    e.source in remove_sources_wild
                    or (e.source, e.target) in remove_keys
                )
            )
        ]

    # Build O(1) membership set for idempotency checks on adds.
    existing_user_links: set = {(e.source, e.target) for e in edges if e.type == "user_link"}

    for change in changes:
        if change.action == "remove":
            continue
        # action == "add"
        target_id = change.target_node_id
        if target_id is None:
            target_id = f"Stub:{change.target_slug}"
            if target_id not in nodes_by_id:
                nodes_by_id[target_id] = ResearchNode(
                    id=target_id,
                    name=change.target_slug,
                    type=ResearchNodeType.STUB,
                    metadata={
                        "vault_slug": change.target_slug,
                        "created_by": "vault_pull",
                    },
                )
        # Idempotency: don't duplicate an edge that already exists.
        already_present = (change.source_node_id, target_id) in existing_user_links
        if not already_present:
            edges.append(ResearchEdge(
                source=change.source_node_id,
                target=target_id,
                type="user_link",
            ))
            existing_user_links.add((change.source_node_id, target_id))
    return ResearchGraph(nodes=list(nodes_by_id.values()), edges=edges)


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


def write_diverged_fields_report(
    overrides: Sequence[VaultOverride],
    path: Path,
    user_link_changes: Sequence[VaultUserLinkChange] = (),
) -> None:
    """Render the per-compile audit log of vault-vs-snapshot divergences.

    Emitted as a separate ``.tesserae/diverged-fields.md`` (not folded into
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
    if not overrides and not user_link_changes:
        lines.append("_No divergences detected._")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    if overrides:
        by_node: Dict[str, List[VaultOverride]] = defaultdict(list)
        for override in overrides:
            by_node[override.node_id].append(override)
        lines.append(
            f"## Field overrides — {len(overrides)} across {len(by_node)} node(s)"
        )
        lines.append("")
        for node_id in sorted(by_node):
            lines.append(f"### `{node_id}`")
            lines.append("")
            for override in by_node[node_id]:
                lines.append(f"- **{override.field}**")
                lines.append(f"  - snapshot value: `{override.snapshot_value!r}`")
                lines.append(f"  - vault value: `{override.vault_value!r}`")
            lines.append("")

    if user_link_changes:
        adds = [c for c in user_link_changes if c.action == "add"]
        removes = [c for c in user_link_changes if c.action == "remove"]
        stubs = [c for c in adds if c.target_node_id is None]
        lines.append(
            f"## User-link edges — {len(adds)} added, {len(removes)} removed"
        )
        if stubs:
            lines.append(
                f"_{len(stubs)} of the added link(s) target unknown slugs and minted a `Stub` tombstone node._"
            )
        lines.append("")
        for change in adds:
            tag = "Stub" if change.target_node_id is None else "ok"
            lines.append(
                f"- +`{change.source_node_id}` → `[[{change.target_slug}]]` ({tag})"
            )
        for change in removes:
            lines.append(
                f"- −`{change.source_node_id}` ↛ `[[{change.target_slug}]]`"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@dataclass(frozen=True)
class PruneResult:
    """Outcome of a one-shot vault prune pass.

    ``deleted`` is every orphan page actually unlinked. ``skipped_with_user_notes``
    is orphans we deliberately KEPT because their ``<!-- user-notes -->`` block
    is non-empty — the operator may want to migrate the notes before losing
    them. Re-run with ``force=True`` to delete those too.
    """

    deleted: List[Path]
    skipped_with_user_notes: List[Path]
    removed_empty_dirs: List[Path]


# Vault files that are always projector-regenerated each compile. They never
# count as "orphan" because the next compile/sync will overwrite them anyway.
_ALWAYS_REGENERATED = frozenset({
    "README.md",
    "index.md",
    "_bridges.md",
    "_meta/dashboard.md",
})


def prune_orphan_pages(
    vault_dir: Path,
    graph: ResearchGraph,
    *,
    force: bool = False,
) -> PruneResult:
    """Delete projected pages whose ``node_id`` is no longer in ``graph``.

    The projector only OVERWRITES files; it never deletes. After any compile
    that shrinks the source set (e.g. an exclusion landed, a data dir got
    pruned), the vault accumulates stale projected pages whose
    ``node_id:`` frontmatter points at nodes that no longer exist. This
    function removes them.

    Safety rules:

    * Files without ``node_id`` in frontmatter are user-authored; never touched.
    * Files under dot-prefix dirs (e.g. ``.obsidian/``) are never touched.
    * Files matching :data:`_ALWAYS_REGENERATED` (README, index, _bridges,
      _meta/dashboard) are kept — they get refreshed each compile.
    * Orphans with non-empty user-notes content are kept by default and
      surfaced separately so the operator can review. Pass ``force=True``
      to delete those too.
    * Empty directories left after deletion are pruned.

    Designed to be safe to re-run: any state where vault matches the graph
    is a no-op.
    """
    # A vault page is an orphan whenever it's NOT the exact file the projector
    # would write for its node_id today. Four ways a file becomes stale:
    #   1. node_id no longer in graph
    #   2. node is private (Person, Stub) — projector skips it
    #   3. node is a slug-collision loser (a sibling node won the canonical
    #      slug; the loser's stale `<slug>-2.md` is left behind from when
    #      the projector still suffixed dupes)
    #   4. file path no longer matches the slug (rare; renames)
    # We compute the EXPECTED file path for every canonical, projectable node
    # and prune anything outside that set.
    from .markdown_projection import (
        canonical_slug_owners,
        directory_for_node,
        unique_slugs,
    )

    projectable = [
        n for n in graph.nodes
        if n.type != ResearchNodeType.STUB and is_public_research_node(n)
    ]
    slug_by_id = unique_slugs(projectable)
    owners = canonical_slug_owners(slug_by_id)
    expected_files: Dict[Path, str] = {}
    for n in projectable:
        if n.id not in owners:
            continue
        rel_path = Path(directory_for_node(n)) / f"{slug_by_id[n.id]}.md"
        expected_files[rel_path] = n.id

    deleted: List[Path] = []
    skipped: List[Path] = []

    if not vault_dir.is_dir():
        return PruneResult(deleted=[], skipped_with_user_notes=[], removed_empty_dirs=[])

    for md in vault_dir.rglob("*.md"):
        rel = md.relative_to(vault_dir)
        if str(rel) in _ALWAYS_REGENERATED:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter = parse_frontmatter(text)
        if not frontmatter or "node_id" not in frontmatter:
            continue  # user-authored, leave alone
        # The file is an orphan unless its path matches what the projector
        # would write for SOME current canonical node.
        if rel in expected_files:
            continue
        # Orphan. Check for user-notes content before deleting.
        notes = extract_user_notes_block(_split_body(text))
        if notes and not force:
            skipped.append(md)
            continue
        try:
            md.unlink()
            deleted.append(md)
        except OSError:
            continue

    # Sweep empty directories bottom-up.
    removed_dirs: List[Path] = []
    for d in sorted(vault_dir.rglob("*"), reverse=True):
        if not d.is_dir():
            continue
        if any(part.startswith(".") for part in d.relative_to(vault_dir).parts):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed_dirs.append(d)
        except OSError:
            continue

    return PruneResult(deleted=deleted, skipped_with_user_notes=skipped, removed_empty_dirs=removed_dirs)


__all__ = [
    "PruneResult",
    "VaultOverride",
    "VaultUserLinkChange",
    "apply_overrides",
    "apply_user_link_changes",
    "compute_overrides",
    "compute_user_link_changes",
    "extract_description",
    "extract_user_notes_block",
    "extract_wikilink_slugs",
    "parse_frontmatter",
    "prune_orphan_pages",
    "write_diverged_fields_report",
]
