"""Agent-harness exports for popular coding assistants.

The harness is a dependency-free set of context/config files that lets external
coding agents discover the compiled Tesserae graph and its MCP server from a
project workspace. It is intentionally file-based so Claude Code, Codex, Gemini,
Kiro, Cursor, and OpenCode can all consume it without bespoke runtime plugins.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .agent_identity import ORG_ROOT, AgentRegistry, sanitize_agent_key
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType


logger = logging.getLogger(__name__)

SUPPORTED_AGENT_HARNESSES = ["claude-code", "codex", "gemini", "kiro", "cursor", "opencode"]

# Builtin pseudo-key for the team-overview federation (mirrors
# ``agent_view.AGENT_ORG_KEY``; defined locally to avoid importing the heavier
# ``agent_view``/``agent_distill``/``federation`` stack into the harness module).
AGENT_ORG_KEY = "org"


@dataclass(frozen=True)
class AgentHarnessAdapter:
    project_name: str = "tesserae_project"

    def write_harness(
        self,
        graph: ResearchGraph,
        output_dir: str | Path,
        mcp_command: str = "python3",
        mcp_args: Optional[Sequence[str]] = None,
        targets: Optional[Iterable[str]] = None,
        topic: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> List[Path]:
        selected = list(targets or SUPPORTED_AGENT_HARNESSES)
        unknown = sorted(set(selected) - set(SUPPORTED_AGENT_HARNESSES))
        if unknown:
            raise ValueError(f"Unsupported agent harness target(s): {', '.join(unknown)}")

        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        args = list(mcp_args or ["-m", "tesserae.mcp_server", "--graph", ".tesserae/graph.json"])

        if agent is not None:
            return self._write_agent_harness(graph, root, mcp_command, args, selected, agent)
        # The harness lives under the project's ``.tesserae/`` artifacts dir, so the
        # project root is the parent of ``output_dir`` (best-effort; topic scoping
        # falls back to ``node.description`` when this isn't a real project root).
        project_root = root.parent if topic else None
        summary = render_harness_context(
            self.project_name,
            graph,
            mcp_command,
            args,
            topic=topic,
            project_root=project_root,
            charter=_read_charter_for_harness(root),
        )
        manifest = {
            "project_name": self.project_name,
            "supported_targets": SUPPORTED_AGENT_HARNESSES,
            "selected_targets": selected,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "mcp": {"command": mcp_command, "args": args},
            "notes": "Copy or symlink the target-specific files into the project root for the corresponding agent.",
        }

        written: List[Path] = []
        common = root / "TESSERAE.md"
        common.write_text(summary, encoding="utf-8")
        written.append(common)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(manifest_path)

        for target in selected:
            writer = TARGET_WRITERS[target]
            written.extend(writer(root, summary, self.project_name, mcp_command, args))
        return written

    # ------------------------------------------------------------ agent mode
    def _write_agent_harness(
        self,
        graph: ResearchGraph,
        base_root: Path,
        mcp_command: str,
        args: List[str],
        selected: List[str],
        agent: str,
    ) -> List[Path]:
        """Write a per-agent harness dir (spec §9).

        Emits ``<base_root>/agents/<sanitized-key>/``. Every agent's *resolved
        view* (§8.1) is an in-memory computation with no serialized file — a
        worker's is L0 ∪ own-L1 (full access to its raw experience plus the
        §6.1 absorption overlay), a manager's / ``org``'s is the federation over
        its reports' L1 — so for **all** modes the MCP config keeps ``--graph``
        at the project ``graph.json`` (L0) and the pointer-block instruction
        directs the agent to pass ``agent="<key>"``;
        :func:`~tesserae.agent_view.resolve_agent_view` then builds the correct
        view at read time.

        Pointing ``--graph`` straight at a worker's ``distilled.graph.json``
        would strand it on L1-only — dropping its raw L0 experience and the
        absorption overlay (violating §8.1) — and would also conflict with the
        ``agent="<key>"`` instruction, which over an L1 ``--graph`` resolves to
        L1 ∪ L1. So the worker's L1 artifact is *referenced* (purpose.md,
        manifest) but is never the ``--graph`` target.

        A per-agent ``purpose.md`` (self-describing mission, seeded once and
        thereafter user-owned — reusing :data:`karpathy_layer.PURPOSE_MARKER`
        and its seed-once discipline) is derived from the agent's
        ``ExpertiseProfile`` + registry label. The text embedded into the
        instruction files is :func:`render_pointer_block`, a pure function of
        ``agent_key`` (no counts/timestamps), so instruction files never churn
        as the corpus grows.

        Fail-loud on an unknown agent key. Graceful degrade (documented) when a
        worker has no distilled artifact yet: the harness is still written with
        the artifact path it *will* occupy and a ``run: tesserae distill`` note;
        ``manifest.json`` records ``artifact_present``.
        """
        requested = str(agent).strip()
        if not requested:
            raise ValueError("agent must be a non-empty agent key or 'org'")

        project_root = _project_root_from_output(base_root)
        registry = AgentRegistry.for_project(project_root) if project_root is not None else None
        canonical = registry.resolve_alias(requested) if registry is not None else requested
        known = _known_agent_keys(graph, registry)

        is_org = canonical == AGENT_ORG_KEY
        if not is_org and canonical not in known and canonical != ORG_ROOT:
            raise ValueError(
                f"Unknown agent: {requested}. Known agents: {', '.join(known) or '(none)'}. "
                "Use `tesserae agents list`."
            )

        sanitized = AGENT_ORG_KEY if is_org else sanitize_agent_key(canonical)
        if is_org:
            mode = "org"
        else:
            children = [
                k
                for k in known
                if k != canonical and _effective_parent(registry, k) == canonical
            ]
            mode = "manager" if (children or canonical == ORG_ROOT) else "worker"

        # The worker's L1 artifact is referenced (purpose.md / manifest) but is
        # NOT the --graph target: --graph stays L0 and the resolved worker view
        # (L0 ∪ own-L1, §8.1) is built at read time from the agent="<key>"
        # instruction. Surface its presence so purpose.md can emit the distill
        # note when the artifact the read-time view needs is not built yet.
        artifact_rel = str(Path(".tesserae") / "agents" / sanitized / "distilled.graph.json")
        artifact_present: Optional[bool] = None
        if mode == "worker" and project_root is not None:
            artifact_present = (project_root / artifact_rel).is_file()

        agent_root = base_root / "agents" / sanitized
        agent_root.mkdir(parents=True, exist_ok=True)

        summary = _agent_instruction_doc(self.project_name, canonical)
        manifest = {
            "project_name": self.project_name,
            "agent": canonical,
            "mode": mode,
            "supported_targets": SUPPORTED_AGENT_HARNESSES,
            "selected_targets": selected,
            "graph_arg": ".tesserae/graph.json",
            "artifact_present": artifact_present,
            "mcp": {"command": mcp_command, "args": args},
            "notes": (
                "Copy or symlink the target-specific files into the project root for the "
                "corresponding agent. Every agent passes agent=\"<key>\" to the MCP read "
                "tools to scope to its resolved view — a worker gets L0 plus its own "
                "distilled layer, a manager/org gets the federation over its reports' layers."
            ),
        }

        written: List[Path] = []
        common = agent_root / "TESSERAE.md"
        common.write_text(summary, encoding="utf-8")
        written.append(common)
        manifest_path = agent_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(manifest_path)

        purpose = _write_agent_purpose(
            agent_root, graph, self.project_name, canonical, mode, artifact_rel, artifact_present
        )
        if purpose is not None:
            written.append(purpose)

        for target in selected:
            writer = TARGET_WRITERS[target]
            written.extend(writer(agent_root, summary, self.project_name, mcp_command, args))
        return written


def render_harness_context(
    project_name: str,
    graph: ResearchGraph,
    mcp_command: str,
    mcp_args: Sequence[str],
    topic: Optional[str] = None,
    project_root: Optional[Path] = None,
    charter: Optional[dict] = None,
) -> str:
    if topic:
        from .context_compiler import compile_context

        node_index = {n.id: n for n in graph.nodes}
        bundle = compile_context(
            graph,
            str(project_root) if project_root is not None else None,
            query=topic,
            depth=2,
            budget=8_000,
        )
        # PITFALL 6: preserve PPR/compile_context rank order — do NOT re-sort.
        top_nodes = [node_index[nid] for nid in bundle.selected_nodes if nid in node_index]
        if not top_nodes:  # topic matched nothing -> graceful static fallback
            top_nodes = sorted(graph.nodes, key=node_sort_key)[:12]
    else:
        top_nodes = sorted(graph.nodes, key=node_sort_key)[:12]
    lines = [
        f"# Tesserae Harness: {project_name}",
        "",
        "This project has a compiled Tesserae research graph. Treat markdown pages as a human-readable projection; the graph JSON is authoritative.",
        "",
        "## Artifacts",
        "",
        "- `.tesserae/graph.json` — authoritative typed ResearchGraph",
        "- `.tesserae/wiki/index.md` — wiki entrypoint: query guidance + table of contents",
        "- `.tesserae/markdown_projection/` — Obsidian/VS Code markdown projection",
        "- `.tesserae/obsidian_vault/` — generated Obsidian vault",
        "- `.tesserae/temporal_facts.jsonl` — temporal/provenance fact projection",
        "- `.tesserae/graphiti_episodes.jsonl` — Graphiti-compatible episode export",
        "",
        "## MCP server",
        "",
        "Use the local MCP server to query the graph:",
        "",
        "```text",
        f"command: {mcp_command}",
        f"args: {json.dumps(list(mcp_args), ensure_ascii=False)}",
        "```",
        "",
        "Expected MCP tools: `graph_map` (canonical entry point for graph navigation), `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`.",
        "",
        "## Graph summary",
        "",
        f"- Nodes: {len(graph.nodes)}",
        f"- Edges: {len(graph.edges)}",
        "",
        *_division_block(_division_rows(charter, graph)),
        "## Representative nodes",
        "",
    ]
    for node in top_nodes:
        lines.append(f"- **{node.name}** (`{node.type.value}`) — {node.description or node.source_path or node.id}")
    if not top_nodes:
        lines.append("_No nodes yet. Run `python3 -m tesserae.cli compile` first._")
    lines.extend([
        "",
        "## Agent instructions",
        "",
        "- Navigate the graph through `graph_map` first — the canonical entry point: budgeted cards, descend by a card's `scope_id`, ascend by `parent_scope`; sibling registered projects via scope='<alias>::'.",
        "- Prefer MCP graph queries before grep-style rediscovery.",
        "- When you do browse the wiki, start at `.tesserae/wiki/index.md` and follow its links; do not crawl pages blindly.",
        "- Preserve the controlled ontology; do not invent node or edge types outside the Tesserae schema.",
        "- Keep markdown projection generated; update sources and re-run compile instead of hand-editing generated pages.",
        "- When adding code, run the project tests before reporting success.",
        "",
    ])
    return "\n".join(lines)


def node_sort_key(node: ResearchNode) -> tuple:
    return (node.type.value, node.name.lower())


def _read_charter_for_harness(output_dir: Path) -> Optional[dict]:
    """The project's charter, or ``None`` when there is none to render.

    Located from ``output_dir`` through :func:`_project_root_from_output`
    rather than from the ``project_root`` the topic path computes: that one is
    ``output_dir.parent`` (the ``.tesserae`` dir, not the project root), so
    reusing it would look for ``.tesserae/.tesserae/charter/charter.json``,
    find nothing, and silently render no ``## Divisions`` block on every real
    project — a no-op that no test on an ad-hoc export path could catch.

    Degrades to ``None`` on :class:`~tesserae.charter.CharterUnreadable`
    instead of propagating it. The compile pass that derives charter.json has
    already reported the damaged file loudly
    (:meth:`ProjectManager._write_charter_sidecar`); failing here on top of
    that would take out every agent's entry file over one decorative block.

    That one exception class is the whole guard, and it is enough because
    ``read_charter`` validates the two shapes a downstream reader cannot
    survive — a top-level parse that is not an object, and a ``domains`` that
    is not a mapping — and raises this class for both. Everything below that
    (a domain record that is not a mapping, a null ``tier``, a
    ``member_count`` that is a string) is absorbed by the charter readers
    themselves, so a second try/except here would catch nothing that can
    still be raised. The charter module is imported lazily because it pulls
    in ``agent_distill``/``community_summaries``, and this module is
    deliberately the light one every harness target loads.
    """
    project_root = _project_root_from_output(output_dir)
    if project_root is None:
        return None
    from .charter import CharterUnreadable, read_charter

    try:
        return read_charter(project_root)
    except CharterUnreadable as exc:
        logger.warning("agent harness: no ## Divisions block — %s", exc)
        return None


def _division_rows(
    charter: Optional[dict], graph: ResearchGraph
) -> List[Tuple[str, int, str]]:
    """This project's live divisions as ``(slug, member_count, anchor_name)``.

    Which slugs, in what order, and how many members each holds are all
    :mod:`tesserae.charter`'s to decide, not this module's, because
    ``graph_map()``'s root is built from those same three calls. Those are the
    two entry points an agent reads, and a block whose premise is that a root
    must be a directory of names cannot ship disagreeing with the other one
    about which names it lists. Deriving them here a second time is how the
    two drift; each re-derivation below was a real disagreement:

    * Membership was ``tier == 1``. :func:`~tesserae.charter.live_divisions`
      keys on "no LIVE parent" instead, and the difference shows on an
      orphan — a live domain whose parent was retired without it. Tier is a
      label, ``parent_slug`` is the tree, so keying on the label hid a domain
      that was still holding members from every descent path there is, while
      ``graph_map()`` listed it and CH-01 reported the partition complete.
    * Order was ``(-member_count, slug)``. Size order is the dendrogram
      root's rule and the one the charter entry point exists to replace: on
      this project it opens the first file every agent reads with ``intake``,
      7,581 members and no name at all (charter.py mints it with
      ``anchor_id: ""``). Slug order makes the block a directory. The count
      stays on the line as information; it is simply not what sorts it.
    * The count was the record's stored ``member_count``.
      :func:`~tesserae.charter.domain_members` is the live-subtree walk
      ``graph_map`` measures its domain card's ``size`` with, and the two
      differ wherever a retired child still carries the frozen member
      snapshot it had when it was last live.

    Tombstones are excluded by ``live_divisions`` — a retired domain stays in
    the file so a pinned attach path can be told what happened to it, but it
    is not a place an agent can go.
    """
    if not charter:
        return []
    from .charter import domain_members, live_divisions

    domains = charter.get("domains") or {}
    by_id = {n.id: n for n in graph.nodes}
    rows: List[Tuple[str, int, str]] = []
    for slug in live_divisions(charter):
        record = domains.get(slug) or {}
        anchor = by_id.get(str(record.get("anchor_id") or ""))
        # An anchor id naming no node in THIS graph is expected rather than a
        # bug: ``intake`` has no anchor at all, and the charter is derived
        # before three passes rebind the graph, so a member named in it can be
        # absent from the graph.json this file renders from — the ordering
        # window ``_write_hierarchy_sidecar`` documents. The slug and the count
        # are still true, so drop the name, never the division. ``graph_map``'s
        # domain card degrades identically, to the slug as its own title.
        rows.append(
            (slug, len(domain_members(charter, slug)), anchor.name if anchor else "")
        )
    return rows


def _division_block(rows: Sequence[Tuple[str, int, str]]) -> List[str]:
    """The ``## Divisions`` section, or nothing at all.

    No charter means no block. That is the below-the-bound case — a corpus
    small enough to be one read is never chartered — and TESSERAE.md must stay
    byte-identical to its pre-charter output there, because every harness
    target file is derived from this string.
    """
    if not rows:
        return []
    block = [
        "## Divisions",
        "",
        "Top-level domains of this project's charter (`.tesserae/charter/charter.json`). A slug is a stable name: it survives re-ingest, unlike a community id, which is a hash over its own members.",
        "",
    ]
    for slug, count, anchor_name in rows:
        line = f"- `{slug}` — {count} member{'' if count == 1 else 's'}"
        if anchor_name:
            line += f" — {anchor_name}"
        block.append(line)
    block.append("")
    return block


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


POINTER_BEGIN = "<!-- tesserae:pointer:begin -->"
POINTER_END = "<!-- tesserae:pointer:end -->"


def render_pointer_block(project_name: str, agent_key: Optional[str] = None) -> str:
    # DETERMINISM: pure function of (project_name, agent_key) — no counts,
    # timestamps, or graph content. This block is written into instruction
    # files; any dynamic value here reintroduces the byte-idempotence bug
    # class. ``agent_key`` is a declared input (spec §9: "pointer block stays a
    # pure function of agent_key"), so the sanitized harness path derived from
    # it is stable, but view node counts / distilled_through must stay OUT.
    if agent_key:
        sanitized = sanitize_agent_key(agent_key)
        agent_body = "\n".join([
            f"## Tesserae — agent `{agent_key}`",
            "",
            f"Project `{project_name}` has a layered Tesserae knowledge graph in `.tesserae/`.",
            f"You are agent `{agent_key}`; work from your own distilled expertise layer.",
            "",
            "Start here:",
            f"- `.tesserae/agent_harness/agents/{sanitized}/purpose.md` — this agent's mission (editable)",
            f"- `.tesserae/agent_harness/agents/{sanitized}/TESSERAE.md` — compiled context brief for this agent",
            "",
            f'Query your scoped view via the local MCP server; pass `agent="{agent_key}"` to '
            "scope reads to your layer:",
            "",
            "    python3 -m tesserae.mcp_server --graph .tesserae/graph.json",
            "",
            "Preferred MCP tools: `graph_map` (canonical entry point for graph navigation), `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `compile_context`.",
        ])
        return POINTER_BEGIN + "\n" + agent_body + "\n" + POINTER_END
    body = "\n".join([
        "## Tesserae",
        "",
        f"Project `{project_name}` has a compiled Tesserae knowledge graph in `.tesserae/`.",
        "",
        "Start here:",
        "- `.tesserae/agent_harness/TESSERAE.md` — compiled context brief (artifacts, MCP config, agent instructions)",
        "- `.tesserae/graph.json` — authoritative typed ResearchGraph (markdown pages are projections)",
        "",
        "Query the graph via the local MCP server instead of grep-style rediscovery:",
        "",
        "    python3 -m tesserae.mcp_server --graph .tesserae/graph.json",
        "",
        "Preferred MCP tools: `graph_map` (canonical entry point for graph navigation), `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `compile_context`.",
    ])
    return POINTER_BEGIN + "\n" + body + "\n" + POINTER_END


def _project_root_from_output(output_dir: Path) -> Optional[Path]:
    """Recover the project root from an agent-harness ``output_dir``.

    The harness lives under ``<project>/.tesserae/agent_harness``, so the
    project root is the parent of the nearest ``.tesserae`` ancestor. Returns
    ``None`` when ``output_dir`` is not under a ``.tesserae`` tree (e.g. an
    ad-hoc export path) — callers then fall back to graph-only agent discovery.
    """
    try:
        resolved = output_dir.resolve()
    except OSError:
        resolved = output_dir
    for cand in (resolved, *resolved.parents):
        if cand.name == ".tesserae":
            return cand.parent
    return None


def _known_agent_keys(graph: ResearchGraph, registry: Optional[AgentRegistry]) -> List[str]:
    """All agent keys this project knows: L0 Agent nodes ∪ registry entries.

    Mirrors :func:`agent_view._known_agent_keys` but tolerates a ``None``
    registry (zero-config projects) so the harness can validate against the
    structural Agent nodes alone.
    """
    keys = {
        str(node.metadata.get("agent_key") or "")
        for node in graph.nodes
        if node.type == ResearchNodeType.AGENT
    }
    keys.discard("")
    if registry is not None:
        declared = registry.load().get("agents")
        if isinstance(declared, dict):
            keys.update(declared.keys())
    return sorted(keys)


def _effective_parent(registry: Optional[AgentRegistry], key: str) -> str:
    """Parent of ``key`` — the registry's declaration, else implicit ORG_ROOT."""
    return registry.effective_parent(key) if registry is not None else ORG_ROOT


def _agent_instruction_doc(project_name: str, agent_key: str) -> str:
    """The instruction-file body for an agent harness — a pure function of
    ``(project_name, agent_key)`` (its only dynamic input is
    :func:`render_pointer_block`, which is itself pure), so per-target
    instruction files stay byte-identical as the corpus grows."""
    return "\n".join([
        f"# Tesserae Harness: {project_name} — agent `{agent_key}`",
        "",
        "This harness scopes you to a single agent's distilled knowledge layer.",
        "",
        render_pointer_block(project_name, agent_key),
        "",
    ])


def _agent_focus_lines(graph: ResearchGraph, agent_key: str) -> List[str]:
    """Top-concept names from the agent's structural ``ExpertiseProfile``,
    resolved to node names for display. Empty when no profile exists."""
    profile = next(
        (
            node
            for node in graph.nodes
            if node.type == ResearchNodeType.EXPERTISE_PROFILE
            and str(node.metadata.get("agent") or "") == agent_key
        ),
        None,
    )
    if profile is None:
        return []
    concept_ids = profile.metadata.get("top_concepts") or []
    if not isinstance(concept_ids, list):
        return []
    name_by_id = {node.id: node.name for node in graph.nodes}
    return [str(name_by_id.get(cid, cid)) for cid in concept_ids][:8]


def _write_agent_purpose(
    agent_root: Path,
    graph: ResearchGraph,
    project_name: str,
    agent_key: str,
    mode: str,
    artifact_rel: str,
    artifact_present: Optional[bool],
) -> Optional[Path]:
    """Seed the agent's ``purpose.md`` once, then leave it to the user.

    Reuses :data:`karpathy_layer.PURPOSE_MARKER` and the KarpathyLayerWriter
    seed-once contract (generated header above the marker, user-owned mission
    below). The header is derived from the agent's ``ExpertiseProfile`` (top
    concepts) and its registry/graph label — but purpose.md is *seed-once*, so
    it lives in the (non-byte-idempotent) harness tree and never regenerates
    over a user's edits. Returns ``None`` when the file already exists.
    """
    from .karpathy_layer import PURPOSE_MARKER

    path = agent_root / "purpose.md"
    if path.exists():
        return None

    agent_node = next(
        (
            node
            for node in graph.nodes
            if node.type == ResearchNodeType.AGENT
            and str(node.metadata.get("agent_key") or "") == agent_key
        ),
        None,
    )
    label = (agent_node.metadata.get("label") if agent_node else "") or agent_key
    focus = _agent_focus_lines(graph, agent_key)

    lines = [
        f"# Purpose — agent `{agent_key}`",
        "",
        f"Mission page for **{label}** in project **{project_name}**.",
        "",
    ]
    if mode == "worker":
        if artifact_present is False:
            lines += [
                f"This agent's distilled expertise layer will live at `{artifact_rel}`. "
                f"It has not been built yet — run: `tesserae distill --agent {agent_key}`.",
                "",
            ]
        else:
            lines += [
                f"This agent's distilled expertise layer lives at `{artifact_rel}`.",
                "",
            ]
    else:
        lines += [
            f"This agent sees the distilled layers of its reports (mode: {mode}). "
            f'Pass `agent="{agent_key}"` to the MCP read tools to stay in scope.',
            "",
        ]
    if focus:
        lines.append("Observed focus areas (from the structural expertise profile):")
        lines.append("")
        lines += [f"- {name}" for name in focus]
        lines.append("")

    lines += [
        PURPOSE_MARKER,
        "",
        "## Mission",
        "",
        "_Edit this section to describe what this agent is for._",
        "",
        "- A responsibility you want this agent to keep in mind.",
        "",
        "## In scope",
        "",
        "- What this agent should own.",
        "",
        "## Out of scope",
        "",
        "- What this agent should defer to others.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _splice_pointer(text: str, block: str) -> tuple[str, str]:
    n_begin, n_end = text.count(POINTER_BEGIN), text.count(POINTER_END)
    if n_begin == 0 and n_end == 0:
        base = text.rstrip("\n")
        return ((base + "\n\n" + block + "\n") if base.strip() else (block + "\n")), "appended"
    # Splice only when there is exactly one well-ordered marker pair. Orphans,
    # duplicates, or END-before-BEGIN would make the span ambiguous and risk
    # deleting user content between stray markers — bail out without writing.
    b, e = text.find(POINTER_BEGIN), text.find(POINTER_END)
    if n_begin != 1 or n_end != 1 or e < b:
        return text, "malformed"
    span = text[b : e + len(POINTER_END)]
    if span == block:
        return text, "current"
    return text[:b] + block + text[e + len(POINTER_END):], "updated"


def install_instruction_pointer(project_root: str | Path, project_name: str) -> dict[str, str]:
    """Install/refresh the marker-delimited Tesserae pointer block into the
    project's top-level ``AGENTS.md``/``CLAUDE.md`` (creating ``AGENTS.md``
    when neither exists). Byte-idempotent: a current block is never rewritten."""
    root = Path(project_root)
    block = render_pointer_block(project_name)
    targets = [p for p in (root / "AGENTS.md", root / "CLAUDE.md") if p.exists()]
    if not targets:
        (root / "AGENTS.md").write_text(block + "\n", encoding="utf-8")
        return {"AGENTS.md": "created"}
    has_agents = any(p.name == "AGENTS.md" for p in targets)
    results: dict[str, str] = {}
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if path.name == "CLAUDE.md" and has_agents and "@AGENTS.md" in text and POINTER_BEGIN not in text:
            # Claude Code inlines AGENTS.md via `@AGENTS.md`; writing both
            # instruction files would double-inject the block.
            results[path.name] = "skipped-include"
            continue
        new_text, status = _splice_pointer(text, block)
        if status in ("appended", "updated"):
            path.write_text(new_text, encoding="utf-8")
        results[path.name] = status
    return results


def claude_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    settings = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [
        write_text(root / "claude" / "CLAUDE.md", summary),
        write_text(root / "claude" / ".claude" / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n"),
    ]


def codex_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    config = f"""# Add to ~/.codex/config.toml or project Codex config if supported.\n[mcp_servers.{project_name}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(list(args))}\n"""
    return [write_text(root / "codex" / "AGENTS.md", summary), write_text(root / "codex" / "mcp.toml", config)]


def gemini_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    settings = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "gemini" / "GEMINI.md", summary), write_text(root / "gemini" / ".gemini" / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")]


def kiro_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    mcp = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "kiro" / ".kiro" / "steering" / "tesserae.md", summary), write_text(root / "kiro" / ".kiro" / "settings" / "mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")]


def cursor_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    rule = "---\nalwaysApply: true\n---\n\n" + summary
    mcp = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "cursor" / ".cursor" / "rules" / "tesserae.mdc", rule), write_text(root / "cursor" / ".cursor" / "mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")]


def opencode_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    config = {"mcp": {project_name: {"type": "local", "command": [command, *list(args)]}}}
    return [write_text(root / "opencode" / "AGENTS.md", summary), write_text(root / "opencode" / "opencode.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")]


TARGET_WRITERS = {
    "claude-code": claude_writer,
    "codex": codex_writer,
    "gemini": gemini_writer,
    "kiro": kiro_writer,
    "cursor": cursor_writer,
    "opencode": opencode_writer,
}
