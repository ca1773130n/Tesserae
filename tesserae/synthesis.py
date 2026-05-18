"""Deterministic synthesis projector for the wiki layer.

Produces seven kinds of higher-order pages from a `ResearchGraph` (pulse,
daily_digest, weekly, topic, comparison, field_overview) with stable, hashable
markdown bodies and matching `Synthesis` nodes/edges. Idempotent: a page is
only rewritten when its content hash changes.

The deterministic heuristic is the default ship and the always-available
fallback. An optional LLM upgrade path lives in ``tesserae.llm_synthesis``;
it activates only when ``TESSERAE_SYNTHESIS_LLM`` is truthy, an Anthropic
API key is available, and the SDK can be imported. Any failure on a single
page falls back to the heuristic body for that page only.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
    stable_id,
)
from .site.raw_view import derive_project_root, relativize_source_path
from .wiki_store import WikiPage, WikiPageStore


GENERATOR = "heuristic-v1"
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"


_DAILY_RE = re.compile(r"data/research/daily/(\d{4}-\d{2}-\d{2})/")
_WEEKLY_RE = re.compile(r"data/research/weekly/(\d{4}-W\d{2})/")


_SOURCE_TYPES = {
    ResearchNodeType.SOURCE_DOCUMENT,
    ResearchNodeType.PAPER,
    ResearchNodeType.REPOSITORY,
    ResearchNodeType.PROJECT,
}


_CONCEPT_TYPES = {
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


_TOPIC_TYPES = {
    ResearchNodeType.RESEARCH_TOPIC,
    ResearchNodeType.APPROACH_FAMILY,
}


def _canonical_synthesis_seed(plan: "_PagePlan") -> str:
    """Delegate to the shared title-driven seed in :mod:`research_graph`.

    Programmatic synthesis (this module) and frontmatter-driven digest
    ingestion (``ResearchGraphExtractor``) must converge on identical
    Synthesis node ids for the same logical day/week. The shared helper
    derives ``kind`` from the title prefix so neither caller needs to
    pre-agree on a label.
    """
    from tesserae.research_graph import canonical_synthesis_id_seed

    return canonical_synthesis_id_seed(plan.title or "")


def _slugify(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if not safe:
        safe = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return safe[:80]


def _escape_markdown_link_label(value: str) -> str:
    value = re.sub(r"!?\[([^\]]+)\]\([^\)]+\)", r"\1", value)
    value = value.replace("|", "-")
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _arxiv_id_for(node: ResearchNode) -> Optional[str]:
    raw = node.metadata.get("arxiv_id")
    if raw:
        return str(raw)
    for alias in node.aliases:
        match = re.fullmatch(r"arXiv:(\d{4}\.\d{4,6})", alias, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _linked_node_label(node: ResearchNode, kind: str, slug: Optional[str] = None) -> str:
    label = node.name
    if node.type == ResearchNodeType.PAPER:
        arxiv_id = _arxiv_id_for(node)
        if arxiv_id:
            label = f"{label} (arXiv:{arxiv_id})"
    slug = slug or _slugify(node.name)
    return f"[{_escape_markdown_link_label(label)}](../{kind}/{slug}.md)"


def _linked_paper_label(paper: ResearchNode, slug: Optional[str] = None) -> str:
    return _linked_node_label(paper, "papers", slug=slug)


def _linked_repo_label(repo: ResearchNode, slug: Optional[str] = None) -> str:
    return _linked_node_label(repo, "repos", slug=slug)


def _linked_source_label(node: ResearchNode, slug: Optional[str] = None) -> str:
    if node.type == ResearchNodeType.PAPER:
        return _linked_paper_label(node, slug=slug)
    if node.type in {ResearchNodeType.REPOSITORY, ResearchNodeType.PROJECT, ResearchNodeType.CODE_PROJECT}:
        return _linked_repo_label(node, slug=slug)
    if node.type == ResearchNodeType.SOURCE_DOCUMENT:
        return _linked_node_label(node, "sources", slug=slug)
    return node.name


def _hash_body(body: str) -> str:
    return "sha256-" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _yaml_scalar(value: object) -> str:
    if isinstance(value, str):
        # Quote when the value contains characters YAML treats specially.
        if value == "" or any(ch in value for ch in ":#\n\"'") or value.startswith(("-", "?", "&", "*", "[", "{", "!", "|", ">")):
            escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
            return f"\"{escaped}\""
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _yaml_list(values: Sequence[object]) -> str:
    if not values:
        return "[]"
    parts = [_yaml_scalar(v) for v in values]
    return "[" + ", ".join(parts) + "]"


def _format_frontmatter(fm: Dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}: {_yaml_list(value)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _node_field(node: ResearchNode) -> Optional[str]:
    """Return the linked ResearchField name, if any (looked up at call site)."""
    return None  # placeholder; field discovery is graph-wide, done in builder.


class SynthesisProjector:
    """Deterministic synthesis layer over a `ResearchGraph`."""

    def __init__(
        self,
        wiki_store: WikiPageStore,
        manifest_path: Path | str | None = None,
    ) -> None:
        self.wiki_store = wiki_store
        self.manifest_path = Path(manifest_path) if manifest_path else None
        # Memoized per ``project()`` call; None means "not yet checked".
        self._llm_state: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # LLM gating
    # ------------------------------------------------------------------

    def _llm_state_for_run(self, ctx: "_GraphContext") -> Dict[str, Any]:
        """Decide once per ``project()`` whether the LLM path is active."""

        if self._llm_state is not None:
            return self._llm_state

        from . import llm_synthesis  # lazy: avoid hard dep on import order

        state: Dict[str, Any] = {"enabled": False, "synthesizer": None, "model": None}

        if not llm_synthesis.env_enabled():
            self._llm_state = state
            return state

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        dry_run = llm_synthesis.env_dry_run()

        if not api_key and not dry_run:
            print(
                "[tesserae] LLM synthesis disabled (ANTHROPIC_API_KEY not set)",
                file=sys.stderr,
            )
            self._llm_state = state
            return state

        # Test seam OR a real anthropic install both satisfy the import gate.
        has_factory = getattr(llm_synthesis, "_CLIENT_FACTORY", None) is not None
        if not dry_run and not has_factory:
            try:
                import anthropic  # type: ignore[import-not-found]  # noqa: F401
            except ImportError:
                print(
                    "[tesserae] LLM synthesis disabled (anthropic SDK not "
                    "installed; run `pip install tesserae[synthesis-llm]`)",
                    file=sys.stderr,
                )
                self._llm_state = state
                return state

        model = os.environ.get("TESSERAE_SYNTHESIS_MODEL", "").strip() or DEFAULT_LLM_MODEL
        try:
            synthesizer = llm_synthesis.LlmSynthesizer(
                model=model,
                api_key=api_key or None,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — never block compile on construct
            print(
                f"[tesserae] LLM synthesis disabled (constructor failed: {exc})",
                file=sys.stderr,
            )
            self._llm_state = state
            return state

        state.update(enabled=True, synthesizer=synthesizer, model=model, ctx=ctx)
        # Reset the per-process error dedupe so each compile starts fresh —
        # a transient failure last run shouldn't silence a real one this run.
        llm_synthesis.reset_failure_log_for_tests()
        self._llm_state = state
        return state

    def _build_llm_request(
        self, plan: "_PagePlan", ctx: "_GraphContext"
    ) -> "LlmSynthesisRequest":
        """Project a plan into a structured prompt input for the LLM.

        Only ids + names + types + light counts are sent. Source-document
        bodies are NOT shipped to the API; the privacy contract is "graph
        metadata only".

        The ``heuristic_body`` field carries the deterministic projector's
        output verbatim — the model uses it as the EDITORIAL ANGLE
        (Rule 1 fallback) so it can rephrase the same facts without
        introducing new ones.
        """

        from .llm_synthesis import LlmSynthesisRequest, _MAX_INPUTS

        # Cap inputs at 25; sample by degree descending so the page sees its
        # highest-signal contributors when the plan has more than 25 nodes.
        ranked_ids = self._rank_inputs_by_degree(plan, ctx)[:_MAX_INPUTS]
        inputs: List[Dict[str, Any]] = []
        for node_id in ranked_ids:
            node = ctx.nodes_by_id.get(node_id)
            if not node:
                continue
            metadata: Dict[str, Any] = {}
            arxiv_id = node.metadata.get("arxiv_id") if node.metadata else None
            if arxiv_id:
                metadata["arxiv_id"] = str(arxiv_id)
            quality = node.metadata.get("title_quality") if node.metadata else None
            if quality:
                metadata["title_quality"] = str(quality)
            entry: Dict[str, Any] = {
                "id": node.id,
                "name": node.name,
                "type": node.type.value,
                "description": (node.description or "").strip()[:280] or None,
            }
            if metadata:
                entry["metadata"] = metadata
            inputs.append(entry)

        # Pull plan metadata (field name, days/weeks) out of the plan if it's
        # been recorded; defaults are safe no-ops if the plan didn't set one.
        plan_meta = getattr(plan, "metadata", {}) or {}
        days_or_weeks = list(plan_meta.get("days") or plan_meta.get("weeks") or [])

        context: Dict[str, Any] = {
            "site_title": "Tesserae",
            "total_nodes": len(ctx.graph.nodes),
            "total_edges": len(ctx.graph.edges),
            "field": plan_meta.get("field_name"),
            "days": days_or_weeks,
            "kind": plan.kind,
            "summary": plan.summary,
            "source_paths": list(plan.sources),
            "summarize_targets": list(plan.summarize_targets),
            # The deterministic body is the model's EDITORIAL ANGLE.
            "heuristic_body": plan.body,
        }
        return LlmSynthesisRequest(
            kind=plan.kind,
            title=plan.title,
            inputs=tuple(inputs),
            context=context,
        )

    def _rank_inputs_by_degree(
        self, plan: "_PagePlan", ctx: "_GraphContext"
    ) -> List[str]:
        """Order ``plan.input_ids`` by graph degree (high first), then id.

        The degree count uses the plan's own ``input_ids`` as the universe of
        interest — we rank by *internal* connectivity within the page's
        neighborhood, not global popularity, so a tightly-connected core
        cluster ranks above a one-off outlier.
        """

        ids: List[str] = list(plan.input_ids)
        if len(ids) <= 1:
            return ids

        in_set = set(ids)
        degrees: Dict[str, int] = {nid: 0 for nid in ids}
        for nid in ids:
            for edge in ctx.out_edges.get(nid, []):
                if edge.target in in_set:
                    degrees[nid] = degrees.get(nid, 0) + 1
            for edge in ctx.in_edges.get(nid, []):
                if edge.source in in_set:
                    degrees[nid] = degrees.get(nid, 0) + 1

        # ``-degree`` for descending; secondary on id for stable order across
        # runs (the input list itself is already sorted upstream).
        return sorted(ids, key=lambda nid: (-degrees.get(nid, 0), nid))

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def project(self, graph: ResearchGraph) -> Tuple[ResearchGraph, List[WikiPage]]:
        ctx = _GraphContext(graph)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        plans: List[_PagePlan] = []

        plans.append(self._plan_pulse(ctx))
        plans.extend(self._plan_daily(ctx))
        plans.extend(self._plan_weekly(ctx))
        plans.extend(self._plan_topics(ctx))
        plans.extend(self._plan_comparisons(ctx))
        plans.extend(self._plan_fields(ctx))
        self._remove_stale_synthesis_pages({plan.slug for plan in plans})

        # Normalise every plan's ``sources`` list to project-relative paths.
        # Recovered from the wiki store's root via the same convention
        # ``ProjectPaths`` uses (``<project_root>/.tesserae/wiki/``). When we
        # cannot recover a project root (e.g. the test fixtures point the
        # wiki store at an arbitrary tmp path) the helper is a no-op for
        # already-relative paths — absolute paths simply pass through.
        project_root = derive_project_root(Path(self.wiki_store.root))
        for plan in plans:
            plan.sources = sorted({
                relativize_source_path(s, project_root=project_root)
                for s in plan.sources
                if s
            })

        # Reset memoized LLM state for this run, then evaluate it once.
        self._llm_state = None
        llm_state = self._llm_state_for_run(ctx)

        # Per-plan LLM upgrade. Each successful response replaces the
        # heuristic body in-place; failures leave the heuristic body intact.
        if llm_state["enabled"]:
            synthesizer = llm_state["synthesizer"]
            llm_model = llm_state["model"]
            plan_requests = [(plan, self._build_llm_request(plan, ctx)) for plan in plans]
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(int(os.environ.get("TESSERAE_SYNTHESIS_WORKERS", "4")), max(1, len(plan_requests)))) as executor:
                future_to_plan = {
                    executor.submit(synthesizer.synthesize, request): plan
                    for plan, request in plan_requests
                }
                for future in concurrent.futures.as_completed(future_to_plan):
                    plan = future_to_plan[future]
                    try:
                        response = future.result()
                    except Exception:  # noqa: BLE001
                        continue
                    if response is None:
                        continue
                    plan.body = response.body
                    plan.llm_metadata = {
                        "generator": f"llm-{llm_model}",
                        "llm_model": response.model,
                        "llm_cache_id": response.cache_id,
                        "llm_citations": list(response.citations),
                    }

        new_nodes: List[ResearchNode] = list(graph.nodes)
        new_edges: List[ResearchEdge] = list(graph.edges)
        # Index existing nodes/edges by id so the synthesis pass can
        # merge with frontmatter-driven Synthesis nodes that
        # ``ResearchGraphExtractor`` already minted for the same logical
        # day/week (same ``canonical_synthesis_id_seed`` → same node id).
        # Without this the projector blindly appends and we end up with
        # two ``Synthesis:daily-digest-<date>:…`` entries per date.
        existing_node_index = {node.id: idx for idx, node in enumerate(new_nodes)}
        existing_edge_keys = {(e.source, e.type, e.target) for e in new_edges}
        written: List[WikiPage] = []
        ledger_entries: List[Dict[str, str]] = []

        for plan in plans:
            content_hash = _hash_body(plan.body)
            # On-disk frontmatter omits volatile timestamps so two consecutive
            # compiles produce byte-identical files. The audit trail (when each
            # body was first/last produced) lives in the append-only history
            # ledger below. ``WikiPageStore._render`` is the single source of
            # truth for on-disk frontmatter formatting — we hand it the
            # timestamp-free dict and pass ``plan.body`` (no embedded
            # frontmatter), avoiding the dual-frontmatter trap that otherwise
            # leaks ``generated_at`` into the body hash.
            generator_label = (
                plan.llm_metadata.get("generator")
                if plan.llm_metadata
                else GENERATOR
            )
            disk_frontmatter: Dict[str, object] = {
                "synthesis_kind": plan.kind,
                "slug": plan.slug,
                "title": plan.title,
                "sources": sorted(plan.sources),
                "inputs": sorted(plan.input_ids),
                "generator": generator_label,
                "content_hash": content_hash,
            }
            if plan.llm_metadata:
                disk_frontmatter["llm_model"] = plan.llm_metadata["llm_model"]
                disk_frontmatter["llm_cache_id"] = plan.llm_metadata["llm_cache_id"]
            page = WikiPage(
                kind="syntheses",
                slug=plan.slug,
                title=plan.title,
                body=plan.body,
                path=self.wiki_store.path_for("syntheses", plan.slug),
                frontmatter=dict(disk_frontmatter),
            )
            changed = self.wiki_store.write_page(page)
            if changed:
                written.append(page)
                ledger_entries.append(
                    {
                        "slug": plan.slug,
                        "content_hash": content_hash,
                        "generated_at": generated_at,
                        "generator": generator_label,
                    }
                )

            # The id is derived from a *canonical* seed (not raw plan.slug)
            # so two synthesis generators producing the same logical
            # synthesis — e.g. one with slug "daily-digest-2026-04-10" and
            # another with slug "daily-2026-04-10" — converge on the same
            # node_id and merge instead of creating duplicate nodes.
            node_id = stable_id(
                ResearchNodeType.SYNTHESIS.value,
                _canonical_synthesis_seed(plan),
            )
            # Keep ``generated_at`` out of the in-graph metadata too: it would
            # otherwise leak into ``graph.json`` and break the byte-idempotence
            # invariant. The append-only history ledger is the canonical audit
            # trail for "when was this body produced".
            metadata = {
                "synthesis_kind": plan.kind,
                "content_hash": content_hash,
                "input_ids": sorted(plan.input_ids),
            }
            synthesis_node = ResearchNode(
                id=node_id,
                name=plan.title,
                type=ResearchNodeType.SYNTHESIS,
                aliases=[],
                description=plan.summary,
                source_path=None,
                metadata=metadata,
            )
            existing_idx = existing_node_index.get(node_id)
            if existing_idx is None:
                existing_node_index[node_id] = len(new_nodes)
                new_nodes.append(synthesis_node)
            else:
                # Merge with a frontmatter-driven Synthesis node already in
                # the graph: keep the projector's richer description /
                # metadata (synthesis_kind, content_hash, input_ids) but
                # carry over the source_path the original had so the page
                # still tracks back to the digest file on disk.
                prior = new_nodes[existing_idx]
                merged_metadata = {**(prior.metadata or {}), **metadata}
                new_nodes[existing_idx] = ResearchNode(
                    id=node_id,
                    name=synthesis_node.name,
                    type=ResearchNodeType.SYNTHESIS,
                    aliases=prior.aliases,
                    description=synthesis_node.description or prior.description,
                    source_path=prior.source_path,
                    metadata=merged_metadata,
                )
            for input_id in plan.input_ids:
                key = (node_id, "synthesizes", input_id)
                if key in existing_edge_keys:
                    continue
                existing_edge_keys.add(key)
                new_edges.append(
                    ResearchEdge(
                        source=node_id,
                        target=input_id,
                        type="synthesizes",
                        evidence=None,
                        metadata={},
                    )
                )
            for source_id in plan.summarize_targets:
                key = (node_id, "summarizes", source_id)
                if key in existing_edge_keys:
                    continue
                existing_edge_keys.add(key)
                new_edges.append(
                    ResearchEdge(
                        source=node_id,
                        target=source_id,
                        type="summarizes",
                        evidence=None,
                        metadata={},
                    )
                )

        # Append per-rewrite entries to the synthesis history ledger. The ledger
        # is the audit trail that lets RSS / sitemap derive deterministic
        # ``lastBuildDate`` / ``pubDate`` values without leaking ``datetime.now``
        # into the rendered artifacts. One JSON object per line, keys sorted so
        # diffs stay tight.
        if ledger_entries:
            self._append_history(ledger_entries)

        return ResearchGraph(nodes=new_nodes, edges=new_edges), written

    # ------------------------------------------------------------------
    # History ledger
    # ------------------------------------------------------------------

    def _history_path(self) -> Path:
        return Path(self.wiki_store.root) / "syntheses" / ".history.jsonl"

    def _remove_stale_synthesis_pages(self, planned_slugs: Set[str]) -> None:
        directory = Path(self.wiki_store.root) / "syntheses"
        if not directory.exists():
            return
        for path in directory.glob("*.md"):
            if path.stem not in planned_slugs:
                path.unlink()

    def _append_history(self, entries: Sequence[Dict[str, str]]) -> None:
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def _plan_pulse(self, ctx: "_GraphContext") -> "_PagePlan":
        counts = ctx.type_counts()
        recent = ctx.recent_nodes(limit=5)
        top_fields = ctx.top_fields(limit=3)

        lines: List[str] = []
        lines.append("# Project Pulse")
        lines.append("")
        lines.append("Snapshot of the wiki at the most recent compile.")
        lines.append("")
        lines.append("## Counts")
        for label, total in counts:
            lines.append(f"- {label}: {total}")
        lines.append("")
        lines.append("## Recently added")
        if recent:
            for node in recent:
                lines.append(f"- {node.name} ({node.type.value})")
        else:
            lines.append("- (none)")
        lines.append("")
        lines.append("## Top fields")
        if top_fields:
            for name, total in top_fields:
                lines.append(f"- {name} — {total} linked artifacts")
        else:
            lines.append("- (none)")
        lines.append("")
        lines.append("## Tagline")
        lines.append("Tesserae — a self-evolving research notebook.")

        body = "\n".join(lines).rstrip() + "\n"
        sources = sorted({node.source_path for node in ctx.graph.nodes if node.source_path})
        input_ids = sorted({node.id for node in recent})
        # Pulse summarises every source document at a glance.
        summarize_targets = sorted({node.id for node in ctx.graph.nodes if node.type in _SOURCE_TYPES})
        return _PagePlan(
            kind="pulse",
            slug="pulse",
            title="Project Pulse",
            body=body,
            summary="Top-level snapshot of the wiki at compile time.",
            sources=sources,
            input_ids=input_ids,
            summarize_targets=summarize_targets,
        )

    def _plan_daily(self, ctx: "_GraphContext") -> List["_PagePlan"]:
        plans: List[_PagePlan] = []
        for date, source_nodes in sorted(ctx.daily_sources().items()):
            slug = f"daily-{date}"
            title = f"Daily Digest — {date}"
            concept_ids: List[str] = []
            concepts: List[ResearchNode] = []
            seen_concept_ids = set()
            for source in source_nodes:
                for concept in ctx.concepts_for_source(source.id):
                    if concept.id in seen_concept_ids:
                        continue
                    seen_concept_ids.add(concept.id)
                    concepts.append(concept)
                    concept_ids.append(concept.id)

            lines: List[str] = []
            lines.append(f"# Daily Digest — {date}")
            lines.append("")
            lines.append(f"Sources ingested under `data/research/daily/{date}/`.")
            lines.append("")
            lines.append("## Papers and repos")
            for source in source_nodes:
                node_slug = self.wiki_store.slug_for(source.name)
                lines.append(f"- {_linked_source_label(source, slug=node_slug)} ({source.type.value})")
            lines.append("")
            lines.append("## Extracted concepts")
            if concepts:
                for concept in concepts:
                    lines.append(f"- {concept.name} ({concept.type.value})")
            else:
                lines.append("- (none extracted)")

            body = "\n".join(lines).rstrip() + "\n"
            sources = sorted({n.source_path for n in source_nodes if n.source_path})
            input_ids = sorted({n.id for n in source_nodes} | set(concept_ids))
            plans.append(
                _PagePlan(
                    kind="daily_digest",
                    slug=slug,
                    title=title,
                    body=body,
                    summary=f"Digest of {date} ingest.",
                    sources=sources,
                    input_ids=input_ids,
                    summarize_targets=sorted({n.id for n in source_nodes}),
                    metadata={"days": [date]},
                )
            )
        return plans

    def _plan_weekly(self, ctx: "_GraphContext") -> List["_PagePlan"]:
        plans: List[_PagePlan] = []
        for week, source_nodes in sorted(ctx.weekly_sources().items()):
            slug = f"weekly-{week}"
            title = f"Weekly Synthesis — {week}"

            family_counts: Counter = Counter()
            for source in source_nodes:
                for family in ctx.approach_families_for_source(source.id):
                    family_counts[family.name] += 1

            lines: List[str] = []
            lines.append(f"# Weekly Synthesis — {week}")
            lines.append("")
            lines.append(f"Coverage of `data/research/weekly/{week}/`.")
            lines.append("")
            lines.append("## Papers and repos")
            for source in source_nodes:
                node_slug = self.wiki_store.slug_for(source.name)
                lines.append(f"- {_linked_source_label(source, slug=node_slug)} ({source.type.value})")
            lines.append("")
            lines.append("## Dominant approach families")
            if family_counts:
                for name, total in family_counts.most_common():
                    lines.append(f"- {name} — {total} contributing source(s)")
            else:
                lines.append("- (none)")

            body = "\n".join(lines).rstrip() + "\n"
            sources = sorted({n.source_path for n in source_nodes if n.source_path})
            input_ids = sorted({n.id for n in source_nodes})
            plans.append(
                _PagePlan(
                    kind="weekly",
                    slug=slug,
                    title=title,
                    body=body,
                    summary=f"Synthesis of week {week}.",
                    sources=sources,
                    input_ids=input_ids,
                    summarize_targets=sorted({n.id for n in source_nodes}),
                    metadata={"weeks": [week]},
                )
            )
        return plans

    def _plan_topics(self, ctx: "_GraphContext") -> List["_PagePlan"]:
        plans: List[_PagePlan] = []
        for topic in ctx.topics_with_threshold(min_papers=3):
            papers = ctx.papers_for_topic(topic.id)
            related_concepts = ctx.related_concepts_for_topic(topic.id)
            related_repos = ctx.related_repos_for_topic(topic.id)
            slug = f"topic-{_slugify(topic.name)}"
            title = f"Topic — {topic.name}"

            lines: List[str] = []
            lines.append(f"# Topic — {topic.name}")
            lines.append("")
            lines.append(f"Type: {topic.type.value}.")
            lines.append("")
            lines.append("## Contributing papers")
            for paper in papers:
                node_slug = self.wiki_store.slug_for(paper.name)
                lines.append(f"- {_linked_paper_label(paper, slug=node_slug)}")
            lines.append("")
            lines.append("## Related concepts")
            if related_concepts:
                for concept in related_concepts:
                    lines.append(f"- {concept.name} ({concept.type.value})")
            else:
                lines.append("- (none)")
            lines.append("")
            lines.append("## Related repos")
            if related_repos:
                for repo in related_repos:
                    node_slug = self.wiki_store.slug_for(repo.name)
                    lines.append(f"- {_linked_repo_label(repo, slug=node_slug)}")
            else:
                lines.append("- (none)")

            body = "\n".join(lines).rstrip() + "\n"
            sources = sorted({n.source_path for n in papers if n.source_path})
            input_ids = sorted({topic.id, *(n.id for n in papers), *(n.id for n in related_concepts), *(n.id for n in related_repos)})
            plans.append(
                _PagePlan(
                    kind="topic",
                    slug=slug,
                    title=title,
                    body=body,
                    summary=f"Topic synthesis for {topic.name}.",
                    sources=sources,
                    input_ids=input_ids,
                    summarize_targets=sorted({n.id for n in papers}),
                    metadata={"topic_name": topic.name, "topic_type": topic.type.value},
                )
            )
        return plans

    def _plan_comparisons(self, ctx: "_GraphContext") -> List["_PagePlan"]:
        plans: List[_PagePlan] = []
        for (family_a, family_b, shared) in ctx.competing_family_pairs():
            slug = f"compare-{_slugify(family_a.name)}-vs-{_slugify(family_b.name)}"
            title = f"Comparison — {family_a.name} vs {family_b.name}"
            papers_a = ctx.papers_for_topic(family_a.id)
            papers_b = ctx.papers_for_topic(family_b.id)

            lines: List[str] = []
            lines.append(f"# Comparison — {family_a.name} vs {family_b.name}")
            lines.append("")
            lines.append(f"Both approach families connect to `{shared.name}` ({shared.type.value}).")
            lines.append("")
            lines.append("| Family | Papers | Shared target |")
            lines.append("| --- | --- | --- |")
            lines.append(f"| {family_a.name} | {len(papers_a)} | {shared.name} |")
            lines.append(f"| {family_b.name} | {len(papers_b)} | {shared.name} |")

            body = "\n".join(lines).rstrip() + "\n"
            sources = sorted({n.source_path for n in (papers_a + papers_b) if n.source_path})
            input_ids = sorted({family_a.id, family_b.id, shared.id, *(n.id for n in papers_a + papers_b)})
            plans.append(
                _PagePlan(
                    kind="comparison",
                    slug=slug,
                    title=title,
                    body=body,
                    summary=f"{family_a.name} vs {family_b.name} on {shared.name}.",
                    sources=sources,
                    input_ids=input_ids,
                    summarize_targets=sorted({n.id for n in papers_a + papers_b}),
                    metadata={
                        "family_a": family_a.name,
                        "family_b": family_b.name,
                        "shared": shared.name,
                    },
                )
            )
        return plans

    def _plan_fields(self, ctx: "_GraphContext") -> List["_PagePlan"]:
        plans: List[_PagePlan] = []
        for field in ctx.fields():
            topics = ctx.topics_for_field(field.id)
            concepts = ctx.concepts_for_field(field.id)
            slug = f"field-{_slugify(field.name)}"
            title = f"Field Overview — {field.name}"

            lines: List[str] = []
            lines.append(f"# Field Overview — {field.name}")
            lines.append("")
            if topics:
                for topic in topics:
                    paper_count = len(ctx.papers_for_topic(topic.id))
                    lines.append(
                        f"{topic.name} ({topic.type.value}) — {paper_count} contributing paper(s) connect to this thread."
                    )
                    lines.append("")
            else:
                lines.append("No linked topics yet.")
                lines.append("")
            lines.append("## Representative concepts")
            if concepts:
                for concept in concepts[:20]:
                    lines.append(f"- {concept.name} ({concept.type.value})")
            else:
                lines.append("- (none)")

            body = "\n".join(lines).rstrip() + "\n"
            papers = ctx.papers_for_field(field.id)
            sources = sorted({n.source_path for n in papers if n.source_path})
            input_ids = sorted({field.id, *(n.id for n in topics), *(n.id for n in concepts)})
            plans.append(
                _PagePlan(
                    kind="field_overview",
                    slug=slug,
                    title=title,
                    body=body,
                    summary=f"Overview of the {field.name} research field.",
                    sources=sources,
                    input_ids=input_ids,
                    summarize_targets=sorted({n.id for n in papers}),
                    metadata={"field_name": field.name},
                )
            )
        return plans


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class _PagePlan:
    __slots__ = (
        "kind",
        "slug",
        "title",
        "body",
        "summary",
        "sources",
        "input_ids",
        "summarize_targets",
        "llm_metadata",
        "metadata",
    )

    def __init__(
        self,
        kind: str,
        slug: str,
        title: str,
        body: str,
        summary: str,
        sources: List[str],
        input_ids: List[str],
        summarize_targets: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.kind = kind
        self.slug = slug
        self.title = title
        self.body = body
        self.summary = summary
        self.sources = sources
        self.input_ids = input_ids
        self.summarize_targets = summarize_targets
        # Populated only when the LLM upgrade path generated this body. The
        # heuristic path leaves this as ``None`` and the on-disk frontmatter
        # uses ``GENERATOR`` ("heuristic-v1").
        self.llm_metadata: Optional[Dict[str, Any]] = None
        # Plan-time metadata used to enrich the LLM prompt (field name for
        # field overviews, contributing days for daily/weekly digests, etc.).
        # Heuristic body generation does not consult this — it's prompt
        # context only.
        self.metadata: Dict[str, Any] = dict(metadata or {})


class _GraphContext:
    """Indices over a `ResearchGraph` so plan builders stay declarative."""

    def __init__(self, graph: ResearchGraph) -> None:
        self.graph = graph
        self.nodes_by_id: Dict[str, ResearchNode] = {n.id: n for n in graph.nodes}
        self.out_edges: Dict[str, List[ResearchEdge]] = defaultdict(list)
        self.in_edges: Dict[str, List[ResearchEdge]] = defaultdict(list)
        for edge in graph.edges:
            self.out_edges[edge.source].append(edge)
            self.in_edges[edge.target].append(edge)

    # -- type-based slices ---------------------------------------------

    def _nodes_of(self, types) -> List[ResearchNode]:
        wanted = types if isinstance(types, set) else {types}
        return [n for n in self.graph.nodes if n.type in wanted and is_public_research_node(n)]

    def fields(self) -> List[ResearchNode]:
        return sorted(
            self._nodes_of({ResearchNodeType.RESEARCH_FIELD}),
            key=lambda n: n.name,
        )

    def papers(self) -> List[ResearchNode]:
        return self._nodes_of({ResearchNodeType.PAPER})

    def repositories(self) -> List[ResearchNode]:
        return self._nodes_of({ResearchNodeType.REPOSITORY})

    # -- pulse helpers --------------------------------------------------

    def type_counts(self) -> List[Tuple[str, int]]:
        counter: Counter = Counter()
        for node in self.graph.nodes:
            counter[node.type.value] += 1
        # Stable sort: alphabetical on type value.
        return sorted(counter.items(), key=lambda item: item[0])

    def recent_nodes(self, limit: int) -> List[ResearchNode]:
        eligible = [
            n for n in self.graph.nodes
            if n.type in (_CONCEPT_TYPES | {ResearchNodeType.PAPER, ResearchNodeType.REPOSITORY} | _TOPIC_TYPES)
        ]
        # Deterministic: prefer nodes whose metadata has analysis_date; fall back
        # to sorted order on (type, name).
        with_date = [n for n in eligible if n.metadata.get("analysis_date")]
        without_date = [n for n in eligible if not n.metadata.get("analysis_date")]
        with_date.sort(key=lambda n: (str(n.metadata.get("analysis_date")), n.name), reverse=True)
        without_date.sort(key=lambda n: (n.type.value, n.name))
        ordered = with_date + without_date
        return ordered[:limit]

    def top_fields(self, limit: int) -> List[Tuple[str, int]]:
        results = []
        for field in self.fields():
            count = sum(
                1 for edge in self.in_edges.get(field.id, [])
                if edge.type == "part_of"
            )
            results.append((field.name, count))
        results.sort(key=lambda item: (-item[1], item[0]))
        return results[:limit]

    # -- daily/weekly ---------------------------------------------------

    def daily_sources(self) -> Dict[str, List[ResearchNode]]:
        buckets: Dict[str, List[ResearchNode]] = defaultdict(list)
        for node in self.graph.nodes:
            if node.type not in _SOURCE_TYPES or not node.source_path or not is_public_research_node(node):
                continue
            match = _DAILY_RE.search(node.source_path)
            if match:
                buckets[match.group(1)].append(node)
        for date, items in buckets.items():
            items.sort(key=lambda n: (n.type.value, n.name))
        return buckets

    def weekly_sources(self) -> Dict[str, List[ResearchNode]]:
        buckets: Dict[str, List[ResearchNode]] = defaultdict(list)
        for node in self.graph.nodes:
            if node.type not in _SOURCE_TYPES or not node.source_path or not is_public_research_node(node):
                continue
            match = _WEEKLY_RE.search(node.source_path)
            if match:
                buckets[match.group(1)].append(node)
        for week, items in buckets.items():
            items.sort(key=lambda n: (n.type.value, n.name))
        return buckets

    def concepts_for_source(self, source_id: str) -> List[ResearchNode]:
        out: List[ResearchNode] = []
        seen = set()
        for edge in self.out_edges.get(source_id, []):
            target = self.nodes_by_id.get(edge.target)
            if target and target.type in _CONCEPT_TYPES and target.id not in seen:
                seen.add(target.id)
                out.append(target)
        out.sort(key=lambda n: (n.type.value, n.name))
        return out

    def approach_families_for_source(self, source_id: str) -> List[ResearchNode]:
        out: List[ResearchNode] = []
        seen = set()
        for edge in self.out_edges.get(source_id, []):
            if edge.type != "belongs_to_approach_family":
                continue
            target = self.nodes_by_id.get(edge.target)
            if target and target.id not in seen:
                seen.add(target.id)
                out.append(target)
        out.sort(key=lambda n: n.name)
        return out

    # -- topics ---------------------------------------------------------

    def topics_with_threshold(self, min_papers: int) -> List[ResearchNode]:
        topics = self._nodes_of(_TOPIC_TYPES)
        result = [t for t in topics if len(self.papers_for_topic(t.id)) >= min_papers]
        result.sort(key=lambda n: (n.type.value, n.name))
        return result

    def papers_for_topic(self, topic_id: str) -> List[ResearchNode]:
        out: List[ResearchNode] = []
        seen = set()
        for edge in self.in_edges.get(topic_id, []):
            source = self.nodes_by_id.get(edge.source)
            if source and source.type == ResearchNodeType.PAPER and source.id not in seen:
                if source.metadata.get("title_quality") not in {"paper_file", "verified"}:
                    continue
                seen.add(source.id)
                out.append(source)
        out.sort(key=lambda n: n.name)
        return out

    def related_concepts_for_topic(self, topic_id: str) -> List[ResearchNode]:
        # Concepts mentioned by any paper attached to this topic.
        papers = self.papers_for_topic(topic_id)
        out: Dict[str, ResearchNode] = {}
        for paper in papers:
            for concept in self.concepts_for_source(paper.id):
                out[concept.id] = concept
        ordered = sorted(out.values(), key=lambda n: (n.type.value, n.name))
        return ordered

    def related_repos_for_topic(self, topic_id: str) -> List[ResearchNode]:
        papers = self.papers_for_topic(topic_id)
        repos: Dict[str, ResearchNode] = {}
        # Repos that share an approach_family with the topic, OR repos linked
        # directly to the topic via belongs_to_approach_family.
        for edge in self.in_edges.get(topic_id, []):
            source = self.nodes_by_id.get(edge.source)
            if source and source.type == ResearchNodeType.REPOSITORY:
                repos[source.id] = source
        return sorted(repos.values(), key=lambda n: n.name)

    # -- comparisons ----------------------------------------------------

    def competing_family_pairs(self) -> List[Tuple[ResearchNode, ResearchNode, ResearchNode]]:
        families = self._nodes_of({ResearchNodeType.APPROACH_FAMILY})
        family_targets: Dict[str, List[ResearchNode]] = {}
        for fam in families:
            connected: List[ResearchNode] = []
            seen = set()
            # Outbound edges from family (e.g., uses Task/Benchmark) and inbound
            # edges into family from papers — inspect both directions for
            # shared Task/Benchmark links.
            for edge in self.out_edges.get(fam.id, []):
                target = self.nodes_by_id.get(edge.target)
                if target and target.type in {ResearchNodeType.TASK, ResearchNodeType.BENCHMARK} and target.id not in seen:
                    seen.add(target.id)
                    connected.append(target)
            # Pull through papers: papers belonging to this family, and the tasks
            # those papers address.
            for paper in self.papers_for_topic(fam.id):
                for edge in self.out_edges.get(paper.id, []):
                    target = self.nodes_by_id.get(edge.target)
                    if target and target.type in {ResearchNodeType.TASK, ResearchNodeType.BENCHMARK} and target.id not in seen:
                        seen.add(target.id)
                        connected.append(target)
            family_targets[fam.id] = connected

        results: List[Tuple[ResearchNode, ResearchNode, ResearchNode]] = []
        ordered = sorted(families, key=lambda n: n.name)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                a = ordered[i]
                b = ordered[j]
                a_targets = {n.id: n for n in family_targets.get(a.id, [])}
                b_targets = {n.id: n for n in family_targets.get(b.id, [])}
                shared_ids = sorted(set(a_targets) & set(b_targets))
                if not shared_ids:
                    continue
                shared = a_targets[shared_ids[0]]
                results.append((a, b, shared))
        return results

    # -- fields ---------------------------------------------------------

    def topics_for_field(self, field_id: str) -> List[ResearchNode]:
        topics: Dict[str, ResearchNode] = {}
        # A topic is "linked" to a field if any paper in that topic is part_of
        # the field (via the paper -> field part_of edge).
        for edge in self.in_edges.get(field_id, []):
            if edge.type != "part_of":
                continue
            source = self.nodes_by_id.get(edge.source)
            if not source:
                continue
            if source.type in _TOPIC_TYPES:
                topics[source.id] = source
                continue
            # If the source is a paper, harvest topics it belongs to.
            if source.type == ResearchNodeType.PAPER:
                for outgoing in self.out_edges.get(source.id, []):
                    candidate = self.nodes_by_id.get(outgoing.target)
                    if candidate and candidate.type in _TOPIC_TYPES:
                        topics[candidate.id] = candidate
        return sorted(topics.values(), key=lambda n: (n.type.value, n.name))

    def concepts_for_field(self, field_id: str) -> List[ResearchNode]:
        concepts: Dict[str, ResearchNode] = {}
        for paper in self.papers_for_field(field_id):
            for concept in self.concepts_for_source(paper.id):
                concepts[concept.id] = concept
        return sorted(concepts.values(), key=lambda n: (n.type.value, n.name))

    def papers_for_field(self, field_id: str) -> List[ResearchNode]:
        out: Dict[str, ResearchNode] = {}
        for edge in self.in_edges.get(field_id, []):
            if edge.type != "part_of":
                continue
            source = self.nodes_by_id.get(edge.source)
            if source and source.type == ResearchNodeType.PAPER:
                out[source.id] = source
        return sorted(out.values(), key=lambda n: n.name)
