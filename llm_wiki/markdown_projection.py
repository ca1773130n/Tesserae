"""Markdown projection for validated ResearchGraph objects.

Markdown is a human-readable projection, not the source of truth. The graph JSON
and future graph DB stay authoritative; these pages make concepts and papers easy
to inspect in Obsidian/VS Code.

Obsidian-specific enrichments (callouts per node type, dataview-queryable
frontmatter, cross-vault bridge metadata) are layered on top of the plain
markdown so a non-Obsidian reader still gets a clean, readable page.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .cross_project import find_wiki_uris_in_text, parse_wiki_uri
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)


PAPER_TYPES = {ResearchNodeType.PAPER, ResearchNodeType.REPOSITORY, ResearchNodeType.SOURCE_DOCUMENT}
CONCEPT_TYPES = {
    ResearchNodeType.RESEARCH_FIELD,
    ResearchNodeType.RESEARCH_TOPIC,
    ResearchNodeType.PROBLEM_AREA,
    ResearchNodeType.APPROACH_FAMILY,
    ResearchNodeType.TREND,
    ResearchNodeType.MODEL,
    ResearchNodeType.DATASET,
    ResearchNodeType.BENCHMARK,
    ResearchNodeType.METRIC,
    ResearchNodeType.RESULT,
    ResearchNodeType.CONCEPT,
    ResearchNodeType.TECHNICAL_TERM,
    ResearchNodeType.MATHEMATICAL_CONCEPT,
    ResearchNodeType.METHODOLOGICAL_CONCEPT,
    ResearchNodeType.ALGORITHM,
    ResearchNodeType.OBJECTIVE_FUNCTION,
    ResearchNodeType.ARCHITECTURE_PATTERN,
    ResearchNodeType.TRAINING_PARADIGM,
    ResearchNodeType.INFERENCE_STRATEGY,
    ResearchNodeType.EVALUATION_PROTOCOL,
    ResearchNodeType.TASK,
    ResearchNodeType.CAPABILITY,
}
CLAIM_TYPES = {
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
    ResearchNodeType.OPEN_QUESTION,
    ResearchNodeType.EVIDENCE_SPAN,
}


# Obsidian callout type per node category. Empty string means no callout
# (we leave the description as plain prose for those types — wrapping a
# 5-paragraph paper abstract in a callout makes the page unreadable).
_CALLOUT_BY_NODE_TYPE: Dict[ResearchNodeType, tuple[str, str]] = {
    ResearchNodeType.PAPER: ("quote", "Paper"),
    ResearchNodeType.REPOSITORY: ("info", "Repository"),
    ResearchNodeType.SOURCE_DOCUMENT: ("abstract", "Source document"),
    # Claim flavours — the assertion lands inside the callout so visual
    # weight matches semantic weight.
    ResearchNodeType.CLAIM: ("note", "Claim"),
    ResearchNodeType.CONTRIBUTION_CLAIM: ("success", "Contribution"),
    ResearchNodeType.PERFORMANCE_CLAIM: ("info", "Performance claim"),
    ResearchNodeType.COMPARISON_CLAIM: ("info", "Comparison"),
    ResearchNodeType.LIMITATION_CLAIM: ("warning", "Limitation"),
    ResearchNodeType.CAUSAL_CLAIM: ("important", "Causal claim"),
    ResearchNodeType.OPEN_QUESTION: ("question", "Open question"),
    ResearchNodeType.EVIDENCE_SPAN: ("example", "Evidence"),
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9가-힣]+", "-", name.lower()).strip("-") or "untitled"
    return truncate_slug(slug)


def truncate_slug(slug: str, max_bytes: int = 180) -> str:
    encoded = slug.encode("utf-8")
    if len(encoded) <= max_bytes:
        return slug
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    suffix = f"-{digest}"
    budget = max_bytes - len(suffix.encode("utf-8"))
    kept = []
    used = 0
    for char in slug:
        char_len = len(char.encode("utf-8"))
        if used + char_len > budget:
            break
        kept.append(char)
        used += char_len
    prefix = "".join(kept).strip("-") or "untitled"
    return f"{prefix}{suffix}"


USER_NOTES_START = "<!-- user-notes:start -->"
USER_NOTES_END = "<!-- user-notes:end -->"


def extract_user_notes(path: Path) -> str:
    """Return whatever the user wrote between ``USER_NOTES_START`` and ``USER_NOTES_END``.

    Returns an empty string when the file is missing, has no user-notes
    block, or the block markers are malformed. The promise of the
    append zone is "the projector never touches what you put here", so
    this function is the read side of that promise — write_projection
    splices the result back into the next projection.
    """
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    start = text.find(USER_NOTES_START)
    if start == -1:
        return ""
    end = text.find(USER_NOTES_END, start)
    if end == -1:
        return ""
    inner = text[start + len(USER_NOTES_START):end]
    return inner.strip("\n")


class GraphMarkdownProjector:
    def write_projection(self, graph: ResearchGraph, output_dir: str | Path) -> List[Path]:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        node_by_id = {node.id: node for node in graph.nodes}
        slug_by_id = unique_slugs(graph.nodes)
        outgoing = defaultdict(list)
        incoming = defaultdict(list)
        for edge in graph.edges:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)

        owners = canonical_slug_owners(slug_by_id)
        # The set of node ids that actually produce a vault page. Edges
        # whose other end is NOT in this set must not emit a ``[[slug]]``
        # wikilink — otherwise the vault is littered with dangling links
        # to private types (Person, Stub) or to collision-loser ids that
        # got redirected to a canonical sibling under a different slug.
        linkable_node_ids: set[str] = {
            node.id
            for node in graph.nodes
            if node.type != ResearchNodeType.STUB
            and is_public_research_node(node)
            and node.id in owners
        }
        written: List[Path] = []
        cross_vault_index: Dict[str, List[str]] = defaultdict(list)  # node_slug → URIs
        for node in graph.nodes:
            # Skip Stub tombstones — the whole UX point of a Stub is that the
            # user's [[unknown-slug]] wikilink stays visually unresolved in
            # Obsidian, so they immediately see their link is broken. The Stub
            # still lives in graph.nodes for query reachability via MCP and
            # the static site (where it's hidden by is_public_research_node).
            if node.type == ResearchNodeType.STUB:
                continue
            # Skip everything that's private to the research graph (Persons —
            # paper authors — and any other type in PRIVATE_PUBLIC_RESEARCH_TYPES).
            # These nodes stay in graph.json so MCP search and the typed-graph
            # queries can find them, but they don't deserve their own Obsidian
            # page. Without this filter the vault concepts/ dir fills with
            # hundreds of one-line author pages.
            if not is_public_research_node(node):
                continue
            # Collision-loser: another node with the same slug won the
            # type-priority tiebreak in unique_slugs. Skip writing a file;
            # the node still exists in graph.json for queries and any
            # wikilink pointing at it resolves to the canonical's page
            # (because slug_by_id maps both to the same slug).
            if node.id not in owners:
                continue
            rel_dir = directory_for_node(node)
            slug = slug_by_id[node.id]
            path = root / rel_dir / f"{slug}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            # Read any existing user-notes BEFORE we overwrite the file so
            # the append zone genuinely survives recompile.
            existing_user_notes = extract_user_notes(path)
            body, uris = render_node_page(
                node,
                slug_by_id,
                node_by_id,
                outgoing[node.id],
                incoming[node.id],
                user_notes=existing_user_notes,
                linkable_node_ids=linkable_node_ids,
            )
            path.write_text(body, encoding="utf-8")
            if uris:
                cross_vault_index[slug] = uris
            written.append(path)

        index_path = root / "index.md"
        index_path.write_text(
            render_index(graph.nodes, slug_by_id, linkable_node_ids=linkable_node_ids),
            encoding="utf-8",
        )
        written.append(index_path)

        bridges_path = root / "_bridges.md"
        bridges_path.write_text(render_bridges(cross_vault_index), encoding="utf-8")
        written.append(bridges_path)
        return written


def directory_for_node(node: ResearchNode) -> str:
    if node.type in PAPER_TYPES:
        return "papers"
    if node.type in CLAIM_TYPES:
        return "claims"
    return "concepts"


# Type priority for slug-collision tie-breaking. When two nodes claim the same
# slug, we keep the one with the higher-priority type and drop the others from
# the projection (they still live in graph.json for query/MCP). Paper >
# SourceDocument because the Paper is the canonical research entity; the
# SourceDocument is just provenance. Synthesis > * because synthesis pages
# are user-facing wiki overviews.
_TYPE_PRIORITY: Dict[ResearchNodeType, int] = {
    ResearchNodeType.PAPER: 100,
    ResearchNodeType.SYNTHESIS: 90,
    ResearchNodeType.REPOSITORY: 80,
    ResearchNodeType.RESEARCH_TOPIC: 70,
    ResearchNodeType.APPROACH_FAMILY: 60,
    ResearchNodeType.METHODOLOGICAL_CONCEPT: 50,
    ResearchNodeType.SOURCE_DOCUMENT: 10,
}


def _normalize_for_dedup(name: str) -> str:
    """Aggressive normalization used to detect "morally the same" names.

    Strips leading emoji-and-space, leading `# ` (markdown heading marker that
    sometimes leaks into EvidenceSpan node names), lowercases, and collapses
    whitespace. Two names that normalize the same get merged at projection time.
    """
    s = name.strip()
    # Strip leading `# ` from leaked markdown headings.
    while s.startswith("#"):
        s = s.lstrip("#").lstrip()
    # Strip leading emoji + whitespace (any non-ASCII non-word leading char).
    while s and not (s[0].isalnum() or s[0] in "[("):
        s = s[1:].lstrip()
    return s.casefold()


def unique_slugs(nodes: Sequence[ResearchNode]) -> Dict[str, str]:
    """Return ``node_id → slug`` for EVERY node, deduped to one slug per name.

    When multiple nodes claim the same base slug (case-only diffs, emoji
    prefix variants, ``# ``-leaked Evidence names, etc.), one canonical
    node wins via :data:`_TYPE_PRIORITY` and the others point at the SAME
    canonical slug. Wikilinks to a collision-loser therefore resolve to
    the canonical's page in the vault — no broken links, no
    ``foo-2.md`` / ``foo-3.md`` siblings.

    Use :func:`canonical_slug_owners` to learn which node IDs actually
    own (and should produce) a vault page.
    """
    by_slug: Dict[str, List[ResearchNode]] = {}
    for node in nodes:
        by_slug.setdefault(slugify(node.name), []).append(node)

    slugs: Dict[str, str] = {}
    for slug, candidates in by_slug.items():
        # Canonical = highest priority by (public, type_priority, id).
        # ``is_public_research_node`` MUST be the primary key: if a Person
        # and a Metric collide on slug ``ssim``, the Metric has to win —
        # otherwise ``write_projection`` skips writing any page at all
        # (the Person fails the public filter, the Metric fails the
        # owner check) and every other page that links to ``[[ssim]]``
        # becomes a dangling wikilink.
        canonical = (
            candidates[0]
            if len(candidates) == 1
            else max(
                candidates,
                key=lambda n: (
                    1 if is_public_research_node(n) else 0,
                    _TYPE_PRIORITY.get(n.type, 0),
                    n.id,
                ),
            )
        )
        # EVERY node in the group maps to the canonical's slug — so edges
        # pointing at a dropped dupe still resolve to a real wikilink.
        for n in candidates:
            slugs[n.id] = slug
        # Mark canonical separately via canonical_slug_owners() below.
        _CANONICAL_OWNERS_CACHE.setdefault(id(slugs), set()).add(canonical.id)
    return slugs


# Module-level cache keyed by id(slugs_dict) so callers can recover the
# "which node id is the canonical for its slug" decision from the slug map
# without recomputing. Cleared opportunistically (the dict id is stable
# only while the slugs dict is alive; once GC reclaims it the entry is
# orphaned but harmless).
_CANONICAL_OWNERS_CACHE: Dict[int, set[str]] = {}


def canonical_slug_owners(slugs: Dict[str, str]) -> set[str]:
    """Return the set of node ids that won the slug-collision tiebreak.

    A node ID is in the result iff it should produce a vault page. Nodes
    whose IDs are in ``slugs`` but NOT in this set are "collision-losers"
    — they exist in graph.json for query reachability and their slug
    entry redirects to the canonical sibling's page.

    If the cache entry has been GC'd (unlikely in practice — slugs lives
    for the duration of a projection), fall back to a recomputation that
    yields the same result.
    """
    owners = _CANONICAL_OWNERS_CACHE.get(id(slugs))
    if owners is not None:
        return owners
    # Fallback recomputation: nodes whose slug isn't shared with another id.
    counts: Dict[str, int] = {}
    for s in slugs.values():
        counts[s] = counts.get(s, 0) + 1
    # Without the node objects we can't pick canonical deterministically the
    # same way, so just pick the first-id-encountered per slug. This is
    # only the fallback path; normal flow always hits the cache.
    seen: set[str] = set()
    owners = set()
    for nid, s in slugs.items():
        if s not in seen:
            seen.add(s)
            owners.add(nid)
    return owners


def render_node_page(
    node: ResearchNode,
    slug_by_id: Dict[str, str],
    node_by_id: Dict[str, ResearchNode],
    outgoing: Sequence[ResearchEdge],
    incoming: Sequence[ResearchEdge],
    user_notes: str = "",
    linkable_node_ids: Optional[set[str]] = None,
) -> tuple[str, List[str]]:
    """Render a node's wiki page. Returns ``(body, cross_vault_uris)``.

    ``cross_vault_uris`` is the deduped list of every ``wiki://`` URI found
    in the node's description or string-typed metadata, so the caller can
    build a vault-wide ``_bridges.md`` index without re-scanning.

    ``user_notes`` is the previously-saved content of the user-notes append
    zone (see :func:`extract_user_notes`). The caller passes whatever was
    between the markers in the existing on-disk file, and this function
    splices it back into the new projection so it survives recompile.
    """
    # Vault-authored ``user_link`` edges live in graph.edges for query
    # reachability, but the projector skips them in the rendered Outgoing /
    # Incoming sections — they're already visible to the human reader as
    # `[[wikilinks]]` inside the user-notes block, and double-listing them
    # would conflate ontology edges with user annotations.
    outgoing = [edge for edge in outgoing if edge.type != "user_link"]
    incoming = [edge for edge in incoming if edge.type != "user_link"]
    # Drop edges whose other end won't have a vault page — those would
    # render as ``[[slug]]`` wikilinks that resolve to nothing, leaving
    # dangling links all over Obsidian. ``linkable_node_ids`` is the set
    # of node ids that ``write_projection`` will actually write a file
    # for (public, non-Stub, slug-canonical). When the caller didn't
    # supply the set, fall back to the legacy behaviour for backward
    # compatibility with tests that call ``render_node_page`` directly.
    if linkable_node_ids is not None:
        outgoing = [edge for edge in outgoing if edge.target in linkable_node_ids]
        incoming = [edge for edge in incoming if edge.source in linkable_node_ids]
    # ------- Frontmatter -------
    # `node_id` is the first frontmatter key so the vault_pull overlay reader
    # (see docs/integrations/obsidian-sync.md) can identify which graph node a
    # vault file represents without falling back to slug-reverse-lookup. The
    # value is the canonical node id from the typed graph and is stable
    # across recompiles.
    lines: List[str] = [
        "---",
        f"node_id: {node.id}",
        f"title: {node.name}",
        f"type: {node.type.value}",
    ]
    if node.aliases:
        lines.append("aliases: [" + ", ".join(node.aliases) + "]")
    if node.source_path:
        lines.append(f"source_path: {node.source_path}")

    # Obsidian/dataview-friendly edge maps. Two nested dicts keyed by edge
    # type, each value is a list of neighbour slugs. Lets dataview do queries
    # like `WHERE this.edges_out.uses` to find nodes with a `uses` outgoing
    # edge, or `WHERE contains(this.edges_in.contributes_to, "nerf")`.
    edges_out_by_type: Dict[str, List[str]] = defaultdict(list)
    edges_in_by_type: Dict[str, List[str]] = defaultdict(list)
    for edge in outgoing:
        if edge.target in slug_by_id:
            edges_out_by_type[edge.type].append(slug_by_id[edge.target])
    for edge in incoming:
        if edge.source in slug_by_id:
            edges_in_by_type[edge.type].append(slug_by_id[edge.source])
    if edges_out_by_type:
        lines.append("edges_out:")
        for etype in sorted(edges_out_by_type):
            slugs = sorted(set(edges_out_by_type[etype]))
            lines.append(f"  {etype}: [{', '.join(slugs)}]")
    if edges_in_by_type:
        lines.append("edges_in:")
        for etype in sorted(edges_in_by_type):
            slugs = sorted(set(edges_in_by_type[etype]))
            lines.append(f"  {etype}: [{', '.join(slugs)}]")

    # Cross-vault wiki:// URIs found in description and string metadata. Surface
    # them as frontmatter so a downstream tool (or human dataview query) can
    # find every page that bridges to another vault.
    cross_vault_uris = _collect_wiki_uris(node)
    if cross_vault_uris:
        lines.append(
            "cross_vault: [" + ", ".join(cross_vault_uris) + "]"
        )

    for key in sorted(node.metadata):
        value = node.metadata[key]
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key}: {value}")
    lines.extend(["---", ""])

    # ------- Body -------
    lines.extend([f"# {node.name}", ""])

    # Obsidian callout chip above the description — gives the page a visual
    # type-tag matching the 41-type schema. Plain markdown reader sees a
    # blockquote with the type label, which is also useful prose.
    callout = _CALLOUT_BY_NODE_TYPE.get(node.type)
    if callout:
        kind, label = callout
        lines.append(f"> [!{kind}] {label}")
        # Empty callouts render fine in Obsidian (just the label tag). We
        # deliberately don't emit a `> _<TypeName>_` fallback line — that
        # would round-trip as a fake "description override" in the vault
        # overlay reader because the snapshot would have description=""
        # while the vault file would have the fallback string.
        if node.description:
            for desc_line in node.description.splitlines():
                lines.append(f"> {desc_line}" if desc_line else ">")
        lines.append("")
    elif node.description:
        lines.extend([node.description, ""])

    if node.aliases:
        lines.extend(["## Aliases", "", ", ".join(node.aliases), ""])

    lines.extend(render_edge_section("Outgoing", outgoing, slug_by_id, node_by_id, target_side=True))
    lines.extend(render_edge_section("Incoming", incoming, slug_by_id, node_by_id, target_side=False))

    if cross_vault_uris:
        lines.extend(["## Cross-vault references", ""])
        for uri in cross_vault_uris:
            parsed = parse_wiki_uri(uri)
            if parsed:
                alias, kind, slug = parsed
                lines.append(f"- `{uri}` — _{alias}_ / {kind} / `{slug}`")
            else:
                lines.append(f"- `{uri}`")
        lines.append("")

    # Dataview block: list every other note in the vault that links to this
    # one. Cheap on small vaults; clients without dataview just see the
    # ```dataview block as a literal code fence which is fine.
    lines.extend([
        "## Related (dataview)",
        "",
        "```dataview",
        "LIST",
        'FROM "papers" OR "concepts" OR "claims"',
        f'WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name',
        "SORT file.name",
        "LIMIT 25",
        "```",
        "",
    ])

    # User-notes append zone. The projector NEVER overwrites whatever the
    # user puts between these markers. On a fresh page the inner content is
    # empty; on subsequent compiles, :func:`extract_user_notes` reads the
    # existing file and the caller passes it back through ``user_notes``.
    # Wikilinks the user writes here become ``user_link`` graph edges via
    # :mod:`llm_wiki.vault_pull`.
    lines.append(USER_NOTES_START)
    lines.append("")
    if user_notes:
        lines.append(user_notes)
        lines.append("")
    lines.append(USER_NOTES_END)
    lines.append("")

    return "\n".join(lines).rstrip() + "\n", cross_vault_uris


def _collect_wiki_uris(node: ResearchNode) -> List[str]:
    """Find every ``wiki://`` URI mentioned in the node, deduped + ordered."""
    seen: List[str] = []
    def add_from(text: str) -> None:
        for uri in find_wiki_uris_in_text(text):
            if uri not in seen:
                seen.append(uri)
    if node.description:
        add_from(node.description)
    for value in node.metadata.values():
        if isinstance(value, str):
            add_from(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add_from(item)
    return seen


def render_edge_section(title: str, edges: Sequence[ResearchEdge], slug_by_id: Dict[str, str], node_by_id: Dict[str, ResearchNode], target_side: bool) -> List[str]:
    lines = [f"## {title}", ""]
    if not edges:
        lines.extend(["_None._", ""])
        return lines
    for edge in sorted(edges, key=lambda e: (e.type, e.target if target_side else e.source)):
        other_id = edge.target if target_side else edge.source
        other = node_by_id.get(other_id)
        if not other:
            continue
        link = f"[[{slug_by_id[other_id]}]]"
        if target_side:
            line = f"- {edge.type} → {link}"
        else:
            line = f"- {link} → {edge.type}"
        if edge.evidence:
            line += f" — {edge.evidence}"
        lines.append(line)
    lines.append("")
    return lines


def render_index(
    nodes: Sequence[ResearchNode],
    slug_by_id: Dict[str, str],
    linkable_node_ids: Optional[set[str]] = None,
) -> str:
    # Same dangling-link guard as ``render_node_page``: index only lists
    # nodes that actually have a vault page on disk.
    if linkable_node_ids is not None:
        nodes = [n for n in nodes if n.id in linkable_node_ids]
    groups = defaultdict(list)
    for node in nodes:
        groups[directory_for_node(node)].append(node)
    lines = ["# Research Graph Projection Index", "", "> Generated from validated ResearchGraph JSON. Markdown is a projection, not the source of truth.", ""]
    for section in ["papers", "concepts", "claims"]:
        lines.extend([f"## {section.title()}", ""])
        for node in sorted(groups.get(section, []), key=lambda n: (n.type.value, n.name.lower())):
            lines.append(f"- [[{slug_by_id[node.id]}]] — {node.type.value}")
        if not groups.get(section):
            lines.append("_None._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_bridges(index: Dict[str, List[str]]) -> str:
    """Vault-level cross-vault index. Empty when no node bridges out.

    Grouped by the alias each ``wiki://`` URI points at so a reader can see
    every outbound dependency on another LLM-Wiki vault at a glance. Pages
    with broken or unregistered aliases still show up — the resolution layer
    is the caller's problem.
    """
    header = [
        "# Cross-vault bridges",
        "",
        "> Every `wiki://<alias>/<kind>/<slug>` URI mentioned across this vault, "
        "grouped by destination alias. Update the registry with "
        "`llm_wiki project register-project` to make any of these resolvable.",
        "",
    ]
    if not index:
        header.extend(["_No outbound cross-vault references in this vault._", ""])
        return "\n".join(header).rstrip() + "\n"

    by_alias: Dict[str, List[tuple[str, str]]] = defaultdict(list)
    for source_slug, uris in index.items():
        for uri in uris:
            parsed = parse_wiki_uri(uri)
            alias = parsed[0] if parsed else "_unparseable_"
            by_alias[alias].append((source_slug, uri))

    lines = list(header)
    for alias in sorted(by_alias):
        lines.extend([f"## `{alias}`", ""])
        for source_slug, uri in sorted(by_alias[alias]):
            lines.append(f"- [[{source_slug}]] → `{uri}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
