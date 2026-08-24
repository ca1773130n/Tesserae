"""Karpathy-style ``project lint`` for Tesserae.

The linter walks the graph and the rendered wiki/site artifacts and produces
:class:`LintFinding` objects. Findings are sorted deterministically and the
report is byte-stable on identical input.

Severity ladder (3 levels): ``info`` < ``warning`` < ``error``.

Each check is a private method ``_check_*`` returning an iterable of
:class:`LintFinding`. The public entry point is :class:`WikiLinter` which
loads the graph + wiki + site, runs every check, optionally applies safe
auto-fixes (``fix_trivial=True``), and writes ``lint-report.md`` /
``lint-report.json`` next to the project graph.

Stdlib + local git only by default — no LLM, no network. ``run(verify_claims=True)``
opts into ONE batched LLM call (via ``tesserae.llm_json``, imported lazily)
that judges whether cited source nodes actually support sampled claims.
The report is intended to flag the kinds of corruption documented in
``docs/superpowers/codex-extraction-review.md`` (orphan papers, stale
citations, ghost synthesis inputs, drift, etc.) so the operator can fix
them cheaply.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .research_graph import CAUSAL_EDGE_TYPES


# --------------------------------------------------------------------------- types

SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")
_SEVERITY_RANK: Dict[str, int] = {name: idx for idx, name in enumerate(SEVERITIES)}

# Edge types that assert a REASON rather than co-occurrence. Everything not
# listed here (discussed_in, summarizes, authored_by, part_of, mentioned_in,
# ...) is structural/membership — it only says "X appeared near Y".
_REASONING_EDGE_TYPES: FrozenSet[str] = frozenset(
    {
        "improves_on",
        "compares_against",
        "criticizes",
        "contradicts_claim",
        "attributes_improvement_to",
        "derived_from",
        "supports_claim",
        "has_limitation",
        "supersedes",
        "resolved_by",
        "addresses",
        "optimizes_for",
        "extends",
    }
    # A causal edge asserts the strongest reason the graph can carry — "this
    # succeeded and it recovers that failure" — so it is reasoning by
    # construction. Derived, not spelled out, so the ratchet counts the next
    # causal type automatically.
    | set(CAUSAL_EDGE_TYPES)
)

# A RATCHET, not an aspiration: 7.5% is the value measured on a real
# 5,197-node / 15,284-edge graph (1,141 reasoning edges with exactly the set
# above). Dropping below it means membership edges outgrew reasoning edges —
# a silent regression that green tests would never catch.
_REASONING_EDGE_FLOOR_PCT: float = 7.5


@dataclass(frozen=True)
class LintFinding:
    severity: str  # "info" | "warning" | "error"
    code: str
    message: str
    node_id: Optional[str] = None
    path: Optional[str] = None
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY_RANK:
            raise ValueError(f"Unknown severity: {self.severity!r}")

    def sort_key(self) -> Tuple[int, str, str, str]:
        # Lower severity rank = info, sorted before warnings/errors so the
        # report reads from least- to most-urgent. The intent here is *byte*
        # stability — we want diffs of the report to be tight.
        return (
            _SEVERITY_RANK[self.severity],
            self.code,
            self.node_id or "",
            self.path or "",
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class LintReport:
    findings: List[LintFinding] = field(default_factory=list)
    by_code: Dict[str, int] = field(default_factory=dict)
    by_severity: Dict[str, int] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return self.by_severity.get("error", 0) > 0

    def has_warnings(self) -> bool:
        return self.by_severity.get("warning", 0) > 0

    # ------------------------------------------------------------------
    # serializers
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append("# Lint report")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        if not self.findings:
            lines.append("No findings. Wiki is clean.")
            lines.append("")
            return "\n".join(lines) + "\n"
        total = len(self.findings)
        lines.append(f"- Total findings: **{total}**")
        for severity in SEVERITIES:
            count = self.by_severity.get(severity, 0)
            if count:
                lines.append(f"- `{severity}`: {count}")
        lines.append("")
        lines.append("### By code")
        lines.append("")
        for code in sorted(self.by_code):
            lines.append(f"- `{code}`: {self.by_code[code]}")
        lines.append("")
        # Group findings by severity, severity ascending (info first).
        for severity in SEVERITIES:
            section = [f for f in self.findings if f.severity == severity]
            if not section:
                continue
            lines.append(f"## {severity.capitalize()} findings")
            lines.append("")
            for finding in section:
                lines.append(f"### `{finding.code}` — {finding.message}")
                if finding.node_id:
                    lines.append(f"- node: `{finding.node_id}`")
                if finding.path:
                    lines.append(f"- path: `{finding.path}`")
                if finding.suggested_fix:
                    lines.append(f"- suggested fix: {finding.suggested_fix}")
                if finding.auto_fixable:
                    lines.append("- auto-fixable: yes (run with `--fix-trivial`)")
                lines.append("")
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        payload = {
            "findings": [finding.to_dict() for finding in self.findings],
            "by_code": dict(sorted(self.by_code.items())),
            "by_severity": {sev: self.by_severity.get(sev, 0) for sev in SEVERITIES},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


# --------------------------------------------------------------------------- git


def _git(repo_root: Path, *args: str) -> Optional[str]:
    """Run git in ``repo_root``; stdout on success, ``None`` on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def read_git_head(repo_root: Path) -> Optional[str]:
    """Full 40-char HEAD sha of the repo at ``repo_root``, or ``None``."""
    out = _git(repo_root, "rev-parse", "HEAD")
    head = (out or "").strip()
    return head or None


# --------------------------------------------------------------------------- linter

# Markdown link patterns we scan in wiki bodies.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((papers|concepts|entities|topics|repos|sources|syntheses|questions)/([A-Za-z0-9_\-./]+?)\.md\)")
# Hrefs we scan in generated HTML.
_HTML_HREF_RE = re.compile(r'href="([^"#?][^"#?]*?)"')

_FRONTMATTER_DELIM = "---"
_FRONTMATTER_END_RE = re.compile(
    r"(^|\n)" + re.escape(_FRONTMATTER_DELIM) + r"(\n|\r\n|$)", re.MULTILINE
)

# Closed metadata-key allowlists for the agent-layer node types
# (2026-07-19 layered-agent-kg spec §4). Compile determinism has broken
# repeatedly via wall-clock / counter state sneaking into graph.json
# metadata, so these schemas are enforced as an error-severity probe:
# any key outside the sets is rejected at lint time instead of surfacing
# later as a byte-idempotence diff.
_AGENT_LAYER_METADATA_ALLOWLIST: Dict[str, FrozenSet[str]] = {
    "Agent": frozenset({"agent_key", "harness", "account", "role", "label"}),
    "DistilledNote": frozenset(
        {
            "agent",
            "kind",
            "lineage_key",
            "content_hash",
            "member_count",
            "member_refs",
            "absorbed_refs",
            "distill_quality",
            "first_seen_at",
            "distilled_through",
        }
    ),
    "ExpertiseProfile": frozenset(
        {
            "agent",
            "session_count",
            "finding_counts",
            "top_concepts",
            "distilled_through",
        }
    ),
}

# The only timestamp/counter-shaped keys allowed on agent-layer nodes —
# all pure functions of the corpus (earliest member timestamp, corpus
# clock, member/session/finding tallies). Anything else matching
# ``*_at`` / ``*_time`` / ``*count*`` gets the pointed idempotence-hazard
# message below.
_AGENT_LAYER_TEMPORAL_ALLOWED: FrozenSet[str] = frozenset(
    {"first_seen_at", "distilled_through", "member_count", "session_count", "finding_counts"}
)
_AGENT_LAYER_TEMPORAL_KEY_RE = re.compile(r"(_at$|_time$|count)")

# Valid ``metadata.kind`` flavors for a DistilledNote (spec §4).
_DISTILLED_NOTE_KINDS: FrozenSet[str] = frozenset(
    {"runbook", "gotcha", "note", "index", "activity", "arbitration"}
)


class WikiLinter:
    """Run lint checks against a project's `.tesserae/` artifacts.

    Construction is cheap; calling :meth:`run` reads the graph and walks the
    wiki + site directories. ``run()`` writes ``lint-report.md`` and
    ``lint-report.json`` to the project's `.tesserae/` root regardless of
    severity floor; the floor only affects exit-code semantics for the
    caller (and which findings the colored stderr summary highlights).
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.wiki_root = self.project_root / ".tesserae"
        self.graph_path = self.wiki_root / "graph.json"
        self.wiki_dir = self.wiki_root / "wiki"
        self.site_dir = self.wiki_root / "site"
        self.build_history_path = self.wiki_root / ".build-history.jsonl"
        self.agent_writes_path = self.wiki_root / "agent-writes.jsonl"
        self.report_md_path = self.wiki_root / "lint-report.md"
        self.report_json_path = self.wiki_root / "lint-report.json"

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        fix_trivial: bool = False,
        severity_floor: str = "info",
        verify_claims: bool = False,
        claim_cap: int = 20,
        llm_client: Optional[object] = None,
    ) -> LintReport:
        if severity_floor not in _SEVERITY_RANK:
            raise ValueError(f"Unknown severity floor: {severity_floor!r}")

        graph = self._load_graph()
        nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
        edges = list(graph.get("edges", []))

        wiki_md_paths = list(sorted(self.wiki_dir.rglob("*.md"))) if self.wiki_dir.exists() else []
        site_html_paths = list(sorted(self.site_dir.rglob("*.html"))) if self.site_dir.exists() else []

        findings: List[LintFinding] = []
        findings.extend(self._check_orphan_papers(nodes_by_id, edges))
        findings.extend(self._check_missing_implemented_in(nodes_by_id, edges))
        findings.extend(self._check_stale_citations(wiki_md_paths))
        findings.extend(self._check_dangling_wiki_links(site_html_paths))
        findings.extend(self._check_drift(nodes_by_id))
        findings.extend(self._check_contradicting_claims(nodes_by_id, edges))
        findings.extend(self._check_low_title_quality(nodes_by_id))
        findings.extend(self._check_synthesis_ghost_inputs(nodes_by_id))
        findings.extend(self._check_suggested_merges(nodes_by_id))
        findings.extend(self._check_suggested_subtypes())
        findings.extend(self._check_pending_review())
        findings.extend(self._check_charter_partition(nodes_by_id))
        findings.extend(self._check_charter_fallback())
        findings.extend(self._check_stale_build_history())
        findings.extend(self._check_code_graph_staleness(nodes_by_id))
        findings.extend(self._check_agent_metadata_allowlist(nodes_by_id))
        findings.extend(self._check_agent_forget_ledger())
        findings.extend(self._check_agent_write_skips())
        findings.extend(self._check_procedural_pools(nodes_by_id, edges))
        findings.extend(self._check_undistilled_backlog(nodes_by_id, edges))
        findings.extend(self._check_reasoning_edge_ratio(edges))
        findings.extend(self._check_interval_coverage(nodes_by_id, edges))
        if verify_claims:
            findings.extend(
                self._check_claim_support(nodes_by_id, cap=claim_cap, llm_client=llm_client)
            )

        if fix_trivial:
            graph_changed = False
            for finding in findings:
                if not finding.auto_fixable:
                    continue
                if finding.code == "MISSING_IMPLEMENTED_IN":
                    if self._fix_missing_implemented_in(graph, finding):
                        graph_changed = True
                elif finding.code == "SYNTHESIS_GHOST_INPUT":
                    self._fix_synthesis_ghost_input(finding)
            if graph_changed and self.graph_path.exists():
                self.graph_path.write_text(
                    json.dumps(graph, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

        # Deterministic ordering. Counting happens after sort so the keys in
        # ``by_code`` reflect what the operator actually sees.
        findings.sort(key=LintFinding.sort_key)
        by_code: Dict[str, int] = {}
        by_severity: Dict[str, int] = {sev: 0 for sev in SEVERITIES}
        for finding in findings:
            by_code[finding.code] = by_code.get(finding.code, 0) + 1
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        report = LintReport(findings=findings, by_code=by_code, by_severity=by_severity)

        # Write artifacts unconditionally — operators want a stable file path
        # they can grep, so suppression is the caller's job (e.g. via
        # ``--severity warning`` for exit code only).
        self.wiki_root.mkdir(parents=True, exist_ok=True)
        self.report_md_path.write_text(report.to_markdown(), encoding="utf-8")
        self.report_json_path.write_text(report.to_json(), encoding="utf-8")

        self._print_summary(report, severity_floor=severity_floor)
        return report

    # ------------------------------------------------------------------
    # checks
    # ------------------------------------------------------------------

    def _check_orphan_papers(
        self,
        nodes_by_id: Dict[str, dict],
        edges: List[dict],
    ) -> Iterable[LintFinding]:
        """Paper nodes with no edges, or only incoming ``mentioned_in`` edges.

        These are the broken arXiv-only stubs the codex review (F-7, F-3)
        flagged: they show up in the graph but have no relationships that
        would make a wiki page useful.
        """
        out_degree: Dict[str, int] = {}
        in_other: Dict[str, int] = {}
        in_mentioned: Dict[str, int] = {}
        for edge in edges:
            out_degree[edge["source"]] = out_degree.get(edge["source"], 0) + 1
            if edge.get("type") == "mentioned_in":
                in_mentioned[edge["target"]] = in_mentioned.get(edge["target"], 0) + 1
            else:
                in_other[edge["target"]] = in_other.get(edge["target"], 0) + 1
        for node_id, node in nodes_by_id.items():
            if node.get("type") != "Paper":
                continue
            if out_degree.get(node_id, 0) > 0:
                continue
            if in_other.get(node_id, 0) > 0:
                continue
            # Either zero edges entirely, or only incoming ``mentioned_in``.
            yield LintFinding(
                severity="warning",
                code="ORPHAN_PAPER",
                message=f"Paper has no outgoing or non-mentioned_in edges: {node.get('name')!r}",
                node_id=node_id,
                suggested_fix="Add an `implemented_in` edge to a Repository, or remove the paper if unused.",
            )

    def _check_missing_implemented_in(
        self,
        nodes_by_id: Dict[str, dict],
        edges: List[dict],
    ) -> Iterable[LintFinding]:
        """Paper + Repository sharing an ``arxiv_id`` but no ``implemented_in`` edge."""
        papers_by_arxiv: Dict[str, str] = {}
        repos_by_arxiv: Dict[str, str] = {}
        for node_id, node in nodes_by_id.items():
            metadata = node.get("metadata") or {}
            arxiv_id = metadata.get("arxiv_id")
            if not arxiv_id:
                continue
            if node.get("type") == "Paper":
                # First-write wins; we just want any matching pair.
                papers_by_arxiv.setdefault(str(arxiv_id), node_id)
            elif node.get("type") in ("Repository", "Project"):
                repos_by_arxiv.setdefault(str(arxiv_id), node_id)
        existing_pairs = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge.get("type") == "implemented_in"
        }
        for arxiv_id, paper_id in sorted(papers_by_arxiv.items()):
            repo_id = repos_by_arxiv.get(arxiv_id)
            if not repo_id:
                continue
            if (paper_id, repo_id) in existing_pairs:
                continue
            yield LintFinding(
                severity="warning",
                code="MISSING_IMPLEMENTED_IN",
                message=(
                    f"Paper and Repository share arxiv_id={arxiv_id} but no implemented_in edge "
                    f"connects them ({paper_id} -> {repo_id})."
                ),
                node_id=paper_id,
                suggested_fix=f"Add edge {paper_id} --implemented_in--> {repo_id}.",
                auto_fixable=True,
            )

    def _check_stale_citations(self, wiki_md_paths: List[Path]) -> Iterable[LintFinding]:
        """Markdown links in wiki bodies pointing at non-existent pages."""
        for md_path in wiki_md_paths:
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _MD_LINK_RE.finditer(text):
                kind = match.group(2)
                target_slug = match.group(3)
                target_path = self.wiki_dir / kind / f"{target_slug}.md"
                if target_path.exists():
                    continue
                yield LintFinding(
                    severity="warning",
                    code="STALE_CITATION",
                    message=(
                        f"Wiki page links to {kind}/{target_slug}.md which does not exist."
                    ),
                    path=str(md_path),
                    suggested_fix=(
                        f"Either remove the link, or recompile so the target page is regenerated."
                    ),
                )

    def _check_dangling_wiki_links(self, site_html_paths: List[Path]) -> Iterable[LintFinding]:
        """`<a href="...">` references inside generated HTML pointing nowhere."""
        for html_path in site_html_paths:
            try:
                text = html_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _HTML_HREF_RE.finditer(text):
                href = match.group(1)
                # Skip protocol-qualified or anchor/query-only links (the
                # regex already excludes ``#``/``?`` starts, but absolute
                # URLs like ``https://...`` still pass through).
                if "://" in href or href.startswith("mailto:") or href.startswith("javascript:"):
                    continue
                # Resolve the target file under site/.
                if href.startswith("/"):
                    target = self.site_dir / href.lstrip("/")
                else:
                    target = (html_path.parent / href).resolve()
                # Directory references like ``concepts/`` should land on
                # ``concepts/index.html`` if the site emits one.
                candidates: List[Path] = []
                if target.suffix == "":
                    candidates.append(target / "index.html")
                    candidates.append(target.with_suffix(".html"))
                else:
                    candidates.append(target)
                if any(c.exists() for c in candidates):
                    continue
                yield LintFinding(
                    severity="warning",
                    code="DANGLING_HTML_LINK",
                    message=f"Generated HTML href does not resolve to a file: {href}",
                    path=str(html_path),
                    suggested_fix="Recompile the site, or fix the source page that produced the link.",
                )

    def _check_drift(self, nodes_by_id: Dict[str, dict]) -> Iterable[LintFinding]:
        """Public graph nodes without a ``wiki/<kind>/<slug>.md`` page (and reverse).

        We use the same kind mapping the wiki projector uses; importing it
        here would create a cycle in some test layouts, so we duplicate it
        in :data:`_KIND_FOR_TYPE` below. Drift is symmetric: a wiki page
        with no graph node is just as broken as a graph node with no wiki
        page.
        """
        wiki_pages: Dict[Tuple[str, str], Path] = {}
        if self.wiki_dir.exists():
            for kind_dir in sorted(self.wiki_dir.iterdir()):
                if not kind_dir.is_dir():
                    continue
                for md_path in sorted(kind_dir.glob("*.md")):
                    wiki_pages[(kind_dir.name, md_path.stem)] = md_path

        # Forward direction: graph -> wiki.
        # Synthesis nodes use a separate slug scheme owned by
        # ``SynthesisProjector`` (e.g. ``daily-2026-04-30`` -> wiki page
        # ``daily-digest-2026-04-30.md``). The ghost-input check validates
        # them from the other direction; mixing the two here would just
        # produce a wave of false positives.
        #
        # We also mirror :func:`tesserae.research_graph.is_public_research_node`
        # here: nodes that the projector intentionally skips (e.g. ``Paper``
        # entries whose ``title_quality`` is ``arxiv_only``/``needs_metadata``/
        # ``invalid``, or any node whose ``source_path`` is a social feed
        # capture) must not surface as drift findings.
        expected: Dict[Tuple[str, str], str] = {}
        for node_id, node in nodes_by_id.items():
            kind = _KIND_FOR_TYPE.get(node.get("type", ""))
            if kind is None or kind == "syntheses":
                continue
            if not _node_is_public(node):
                continue
            slug = _slug_for(node.get("name", "") or node_id)
            expected[(kind, slug)] = node_id

        for (kind, slug), node_id in sorted(expected.items()):
            if (kind, slug) in wiki_pages:
                continue
            yield LintFinding(
                severity="warning",
                code="GRAPH_WIKI_DRIFT",
                message=(
                    f"Graph has public node but no wiki page exists at "
                    f"wiki/{kind}/{slug}.md."
                ),
                node_id=node_id,
                suggested_fix="Recompile to regenerate the wiki page.",
            )

        # Reverse direction: wiki -> graph.
        for (kind, slug), md_path in sorted(wiki_pages.items()):
            if kind == "syntheses":
                # Synthesis pages have separate frontmatter validation in
                # ``_check_synthesis_ghost_inputs``; their slug isn't a
                # simple node-name slug.
                continue
            if (kind, slug) in expected:
                continue
            yield LintFinding(
                severity="warning",
                code="GRAPH_WIKI_DRIFT",
                message=f"Wiki page exists at wiki/{kind}/{slug}.md but no public graph node matches it.",
                path=str(md_path),
                suggested_fix="Delete the stale page, or extract the entity into the graph.",
            )

    def _check_contradicting_claims(
        self, nodes_by_id: Dict[str, dict], edges: List[dict] | None = None
    ) -> Iterable[LintFinding]:
        """Pairs of performance/comparison claims with opposite directional language.

        Precision-first heuristic: for every pair of ``PerformanceClaim`` /
        ``ComparisonClaim`` nodes from *different* sources, we flag the pair
        when one description contains ``outperforms`` and the other contains
        ``is outperformed by`` and they share at least one trigram of
        ``model+benchmark`` content. Tolerating false negatives is fine — the
        check is a sanity probe, not an oracle.

        KB-04: when the opt-in ``memory.contradiction`` pass has minted a
        ``resolved_by`` edge between a flagged pair (in either direction), the
        contradiction is considered RESOLVED and demoted to ``severity=info``
        ("resolved by <winner>"). Unresolved pairs are RAISED to
        ``severity=warning`` (formerly always ``info``). The winning claim is
        the ``resolved_by`` edge's ``target``.
        """
        # Map of unordered claim-pair -> winning (target) node id, from any
        # ``resolved_by`` edge between two flagged claims.
        resolved_winner: Dict[Tuple[str, str], str] = {}
        for edge in edges or []:
            if edge.get("type") != "resolved_by":
                continue
            src = edge.get("source")
            tgt = edge.get("target")
            if not isinstance(src, str) or not isinstance(tgt, str):
                continue
            resolved_winner[tuple(sorted([src, tgt]))] = tgt
        candidates = [
            (nid, node)
            for nid, node in nodes_by_id.items()
            if node.get("type") in ("PerformanceClaim", "ComparisonClaim")
        ]
        # Sort for determinism.
        candidates.sort(key=lambda kv: kv[0])
        seen: set[Tuple[str, str]] = set()
        for i, (left_id, left) in enumerate(candidates):
            left_text = _claim_text(left)
            if "outperforms" not in left_text.lower():
                continue
            for j in range(i + 1, len(candidates)):
                right_id, right = candidates[j]
                if left.get("source_path") and left.get("source_path") == right.get("source_path"):
                    continue
                right_text = _claim_text(right)
                if "is outperformed by" not in right_text.lower():
                    continue
                if not _share_topic(left_text, right_text):
                    continue
                pair = tuple(sorted([left_id, right_id]))
                if pair in seen:
                    continue
                seen.add(pair)
                winner_id = resolved_winner.get(pair)
                if winner_id is not None:
                    winner_name = (nodes_by_id.get(winner_id) or {}).get("name")
                    yield LintFinding(
                        severity="info",
                        code="CONTRADICTING_CLAIMS",
                        message=(
                            f"Two claims contradicted each other; resolved by "
                            f"{winner_name!r}: {left.get('name')!r} vs "
                            f"{right.get('name')!r}."
                        ),
                        node_id=left_id,
                        suggested_fix="Resolution recorded via resolved_by edge.",
                    )
                else:
                    yield LintFinding(
                        severity="warning",
                        code="CONTRADICTING_CLAIMS",
                        message=(
                            f"Two claims appear to contradict each other: "
                            f"{left.get('name')!r} vs {right.get('name')!r}."
                        ),
                        node_id=left_id,
                        suggested_fix="Manually review both source documents and reconcile.",
                    )

    def _check_low_title_quality(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """Papers whose title was scraped from arXiv stub or marked invalid."""
        for node_id, node in nodes_by_id.items():
            if node.get("type") != "Paper":
                continue
            metadata = node.get("metadata") or {}
            quality = metadata.get("title_quality")
            if quality not in ("arxiv_only", "invalid"):
                continue
            yield LintFinding(
                severity="info",
                code="LOW_TITLE_QUALITY",
                message=(
                    f"Paper has low-quality title (title_quality={quality!r}): "
                    f"{node.get('name')!r}."
                ),
                node_id=node_id,
                suggested_fix="Locate the paper.md file and verify its real title.",
            )

    def _check_agent_metadata_allowlist(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """Agent-layer nodes carrying metadata outside the closed §4 allowlist.

        Agent / DistilledNote / ExpertiseProfile metadata schemas are CLOSED
        (2026-07-19 layered-agent-kg spec §4): every key must be a pure
        function of the corpus, or byte-idempotent compiles break. Keys are
        checked in sorted order per node so the report is byte-stable;
        timestamp/counter-shaped strays get a pointed message because that
        exact class of key is how determinism has broken before.
        """
        for node_id, node in nodes_by_id.items():
            type_value = str(node.get("type") or "")
            allowed = _AGENT_LAYER_METADATA_ALLOWLIST.get(type_value)
            if allowed is None:
                continue
            metadata = node.get("metadata") or {}
            for key in sorted(metadata):
                if key in allowed:
                    continue
                if (
                    _AGENT_LAYER_TEMPORAL_KEY_RE.search(key)
                    and key not in _AGENT_LAYER_TEMPORAL_ALLOWED
                ):
                    message = (
                        f"{type_value} node carries timestamp/counter-shaped metadata "
                        f"key {key!r} outside the closed allowlist — wall-clock or "
                        f"run-varying state in graph.json breaks byte-idempotent "
                        f"compiles (CMP-03)."
                    )
                else:
                    message = (
                        f"{type_value} node carries metadata key {key!r} outside "
                        f"the closed allowlist."
                    )
                yield LintFinding(
                    severity="error",
                    code="AGENT_METADATA_KEY",
                    message=message,
                    node_id=node_id,
                    suggested_fix=(
                        "Drop the key, or extend the closed schema in the "
                        "layered-agent-kg spec first."
                    ),
                )
            if type_value == "DistilledNote":
                # ``kind`` is REQUIRED (§4: kind ∈ {runbook,...}) — a missing
                # or empty kind is as much a schema violation as an unknown one.
                kind = str(metadata.get("kind") or "")
                if kind not in _DISTILLED_NOTE_KINDS:
                    detail = (
                        f"has unknown kind {kind!r}"
                        if kind
                        else "is missing the required 'kind' metadata key"
                    )
                    yield LintFinding(
                        severity="error",
                        code="AGENT_METADATA_KEY",
                        message=(
                            f"DistilledNote node {detail}; expected "
                            f"one of {sorted(_DISTILLED_NOTE_KINDS)}."
                        ),
                        node_id=node_id,
                        suggested_fix="Use a kind from the layered-agent-kg spec §4.",
                    )

    def _check_agent_forget_ledger(self) -> Iterable[LintFinding]:
        """Surface each agent's latest forget-ledger diff (§6.2).

        Shrinkage must be visible BEFORE it costs a decision: any distill run
        that demoted or absorbed knowledge yields a warning naming the counts;
        promotion-only runs stay info-quiet (nothing became less visible).
        Reads the sidecar, not the graph — no effect on compile bytes.
        """
        try:
            from .agent_distill import DistillStateStore, _state_db_path

            db_path = _state_db_path(self.project_root)
            if not db_path.is_file():
                return
            state = DistillStateStore(db_path)
            rows = state.rows(DistillStateStore.SCOPE_FORGET_LEDGER)
        except Exception:  # noqa: BLE001 — lint never dies on sidecar trouble
            return
        latest: Dict[str, dict] = {}
        for _rowid, agent_key, _key, value in rows:
            try:
                latest[str(agent_key)] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
        for agent_key in sorted(latest):
            entry = latest[agent_key]
            demoted = list(entry.get("demoted") or [])
            absorbed = list(entry.get("absorbed") or [])
            if not demoted and not absorbed:
                continue
            yield LintFinding(
                severity="warning",
                code="AGENT_FORGET_LEDGER",
                message=(
                    f"agent {agent_key}: last distill demoted {len(demoted)} and "
                    f"absorbed {len(absorbed)} node(s) "
                    f"(through {entry.get('distilled_through', '?')}). "
                    "Demoted knowledge is index-only; absorbed knowledge lives "
                    "inside a distillate."
                ),
                suggested_fix=(
                    "Review with drill_down / include_superseded; re-run "
                    "`tesserae distill --recheck` if a demotion looks wrong."
                ),
            )

    def _check_agent_write_skips(self) -> Iterable[LintFinding]:
        """Name every agent write the overlay replay dropped.

        ``replay_agent_writes`` skips an unusable record rather than failing,
        which is right — one truncated line must not brick every future compile
        — but it says so only on stderr, so a write lost to a torn append (two
        unsynchronized appends interleaving) degraded into a message nobody
        reads. A skipped write is a claim the agent believes it filed and the
        graph does not contain; that belongs in the report.

        Reads the overlay, not the graph — no effect on compile bytes.
        """
        if not self.agent_writes_path.is_file():
            return
        skips: List[Dict[str, object]] = []
        try:
            from .agent_write import replay_agent_writes

            replay_agent_writes(self.agent_writes_path, skips)
        except Exception:  # noqa: BLE001 — lint never dies on sidecar trouble
            return
        for skip in skips:
            where = (
                f"line {skip['line']}"
                if skip.get("line") is not None
                else f"write {skip.get('write_id') or '<no id>'}"
            )
            yield LintFinding(
                severity="warning",
                code="AGENT_WRITE_SKIPPED",
                message=(
                    f"agent-writes.jsonl {where} was skipped by replay "
                    f"({skip.get('reason')}) — that write is NOT in the graph"
                ),
                path=str(self.agent_writes_path),
                suggested_fix=(
                    "Inspect the line: a truncated one is a torn concurrent "
                    "append and the agent should re-file the write; a "
                    "hand-edited one should be corrected or removed."
                ),
            )

    def _check_reasoning_edge_ratio(
        self, edges: List[dict]
    ) -> Iterable[LintFinding]:
        """Measure what fraction of the graph carries REASONING, not adjacency.

        Measured on a real 5,197-node graph: 11,250 of 15,284 edges (73%) are
        structural/membership — ``discussed_in``, ``summarizes``,
        ``authored_by``, ``part_of``, ``mentioned_in`` — every one of which
        means "X appeared near Y". Only 1,141 (7.5%) assert a reason.

        ALWAYS emits exactly one finding carrying the exact counts, so the
        number lands in ``lint-report.json`` where CI can diff it. The
        severity is the convenience; the counts are the instrument.
        """
        total = len(edges)
        if total == 0:
            return
        reasoning = sum(
            1 for edge in edges if str(edge.get("type") or "") in _REASONING_EDGE_TYPES
        )
        pct = round(reasoning * 100.0 / total, 1)
        below = pct < _REASONING_EDGE_FLOOR_PCT
        yield LintFinding(
            severity="warning" if below else "info",
            code="REASONING_EDGE_RATIO",
            message=(
                f"{reasoning} of {total} edges ({pct}%) are reasoning-bearing; "
                f"the rest are structural/membership. Floor is "
                f"{_REASONING_EDGE_FLOOR_PCT}%."
            ),
            suggested_fix=(
                # This used to advertise TESSERAE_CONTRAST_PASS=1, which has had
                # no implementation since the contrast pass was dropped for
                # measured zero yield. On BOTH real graphs the ratio is below the
                # floor (Tesserae 1141/15284 = 7.5%, ai-accounts 114/1892 = 6.0%),
                # so every real `tesserae lint` run handed the user a flag that
                # silently did nothing. Name a path that exists instead.
                "No automated pass mints these: reasoning edges come from "
                "extraction, not from a post-hoc pairing over the compiled "
                "graph (measured — only 12.4% of assertion nodes carry any "
                "subject anchor, and 16 topic hubs cover them). Add them "
                "deliberately with the typed zero-LLM `graph_write` path."
                if below
                else None
            ),
        )

    def _check_interval_coverage(
        self, nodes_by_id: Dict[str, dict], edges: List[dict]
    ) -> Iterable[LintFinding]:
        """Measure how much of the graph can actually be placed in time.

        ``TemporalFactProjector`` derives ``valid_from`` from the LATER of the
        two endpoint timestamps (:data:`temporal._TS_METADATA_KEYS`), falling
        back to the literal string ``"undated"``. Every undated fact sorts into
        one bucket in ``timeline()`` and carries no signal that it did so, so an
        agent reading an ordering cannot tell a chronology from a pile.

        Measured on this project's own compiled graph (2026-08-09): 76,095 of
        103,705 research facts (73.38%) are undated, led by ``summarizes``
        (30,752), ``evidenced_by`` (14,545) and ``supports_claim`` (4,659).

        ALWAYS info, and deliberately WITHOUT a threshold — the same
        non-strict posture :meth:`_check_code_graph_staleness` takes, for the
        same reason. That 73.38% was compiled from a session corpus that a
        later prune removed, so any floor set today would be set from an
        imagined baseline and would turn every ``compile --strict`` on this
        project red on day one. The number this probe reports is what a
        follow-up should set the floor from.

        COST: this runs at the tail of every compile, and the engine daemon
        compiles on a loop, so it counts without building the facts. Measured
        on the live 46,924-node / 103,705-edge graph: materialising every
        ``TemporalFact`` cost 4.2s and 91MB peak; the two passes below cost
        0.9s and 12MB for byte-identical output. The invalidation logic is not
        reimplemented — the projector's own helpers are imported — and
        ``test_lint_interval_coverage_matches_the_temporal_projector_exactly``
        pins the agreement so a change to the projector's dating rules turns
        that test red instead of silently drifting this number away from what
        ``timeline()`` serves.
        """
        try:
            from .research_graph import graph_from_payload
            from .temporal import (_boundary_precedes_start, _closing_roles,
                                   _end_sort_key, _latest_ts, _source_ts,
                                   _winner_precedes_loser, document_dates,
                                   first_string, graph_project_roots)

            graph = graph_from_payload(
                {"nodes": list(nodes_by_id.values()), "edges": edges}
            )
        except Exception as exc:  # noqa: BLE001
            # A probe whose whole purpose is to make a degradation loud must
            # not degrade silently. Swallowing this made "fully dated" and
            # "never ran" identical in lint-report.md.
            yield LintFinding(
                severity="info",
                code="LINT_PROBE_FAILED",
                message=(
                    f"INTERVAL_COVERAGE did not run: the graph could not be "
                    f"projected ({type(exc).__name__}: {exc}). Temporal "
                    f"coverage is unknown for this compile, not zero."
                ),
                suggested_fix=(
                    "Usually a node type or edge type outside the schema in "
                    "graph.json — the SCHEMA_* probes name the offender."
                ),
            )
            return

        nodes = {node.id: node for node in graph.nodes}
        # Same graph-derived roots the projector uses to bound its source_path
        # rung — this probe must mirror it exactly or the number it reports is
        # not the one ``timeline()`` serves.
        roots = graph_project_roots(graph)
        # And the document rung's index, for the same reason: a probe that
        # reads a NARROWER ladder than the projector reports facts as undated
        # that timeline() orders perfectly well, which is a lint finding
        # nobody can act on.
        doc_dates = document_dates(graph)
        ts_cache: Dict[str, Optional[str]] = {}

        def source_ts(node_id: str) -> Optional[str]:
            if node_id not in ts_cache:
                ts_cache[node_id] = _source_ts(nodes.get(node_id), roots, doc_dates)
            return ts_cache[node_id]

        # Pass 1, mirroring TemporalFactProjector.project: one fact per edge
        # whose endpoints both resolve, valid_from = latest of the two endpoint
        # timestamps and the edge's own analysis_date.
        derived: List[Tuple[str, str, str, Optional[str]]] = []
        for edge in graph.edges:
            if edge.source not in nodes or edge.target not in nodes:
                continue
            meta = edge.metadata or {}
            derived.append(
                (
                    edge.source,
                    edge.target,
                    edge.type,
                    _latest_ts(
                        (
                            source_ts(edge.source),
                            source_ts(edge.target),
                            first_string(meta.get("analysis_date")) if meta else None,
                        )
                    ),
                )
            )

        total = len(derived)
        if total == 0:
            return

        # Pass 2: a node ends when its EARLIEST dated superseder was observed.
        # Which endpoint lost is orientation-dependent (``supersedes`` kills
        # its target, ``resolved_by`` its own source), so ask _closing_roles
        # rather than assuming a side — assuming one here would report the
        # winner's interval as closed.
        ended_by: Dict[str, Tuple[str, str, str]] = {}
        for subject_id, object_id, predicate, _vf in derived:
            roles = _closing_roles(predicate, subject_id, object_id)
            if roles is None:
                continue
            loser_id, winner_id = roles
            stamp = source_ts(winner_id)
            if stamp is None:
                continue
            if _winner_precedes_loser(stamp, source_ts(loser_id)):
                continue
            entry = (stamp, predicate, winner_id)
            prior = ended_by.get(loser_id)
            if prior is None or _end_sort_key(entry) < _end_sort_key(prior):
                ended_by[loser_id] = entry

        undated = 0
        # ``valid_to_basis`` is non-null exactly when ``valid_to`` is, so
        # "open" counts the intervals nothing has closed.
        basis: Dict[str, int] = {}
        for subject_id, object_id, predicate, valid_from in derived:
            if (valid_from or "undated") == "undated":
                undated += 1
            roles = _closing_roles(predicate, subject_id, object_id)
            endpoints = [roles[1]] if roles is not None else [subject_id, object_id]
            ends = [ended_by[e] for e in endpoints if e in ended_by]
            if ends:
                best = min(ends, key=_end_sort_key)
                valid_to, key = best[0], best[1]
            else:
                valid_to, key = None, None
            if _boundary_precedes_start(valid_from, valid_to):
                key = None
            bucket = key or "open"
            basis[bucket] = basis.get(bucket, 0) + 1

        pct = round(undated * 100.0 / total, 1)
        histogram = ", ".join(f"{key}={basis[key]}" for key in sorted(basis))
        yield LintFinding(
            severity="info",
            code="INTERVAL_COVERAGE",
            message=(
                f"{undated} of {total} facts ({pct}%) have no valid_from, so "
                f"timeline() cannot order them and buckets them behind every "
                f"dated fact (reported there as 'undated_events'). "
                f"valid_to_basis: {histogram}."
            ),
            suggested_fix=(
                "Undated facts come from endpoints where the whole "
                "temporal._source_ts ladder misses: none of "
                "_TS_METADATA_KEYS (first_seen_at, analysis_date, ended_at, "
                "started_at, updated_at, created), no leading date in the "
                "node name, no whole dated DIRECTORY segment in the "
                "project-root-relative part of its source_path, and no date "
                "stated by the document at that source_path (document_dates). "
                "Stamp one at "
                "extraction time on the node types that dominate the count, "
                "or ingest their sources under a dated directory inside the "
                "project. A path outside every root a Session node declares "
                "is undated by design — it is not this project's ingest "
                "layout, so no segment of it names its observation day."
            ),
        )

    def _check_procedural_pools(
        self, nodes_by_id: Dict[str, dict], edges: List[dict]
    ) -> Iterable[LintFinding]:
        """How much of each producer-owned type a pool can actually serve.

        ``compile_context(multi_pool=True)`` reserves a budget slot per
        procedural pool, and since roadmap step 4 only a node carrying producer
        provenance can take one. Document extraction mints the same type names,
        so a graph can hold hundreds of ``Event`` nodes — conference deadlines,
        typically — and still have a pool that reserves nothing. That is the
        correct outcome and an invisible one: the caller sees procedural memory
        working. This states the ratio instead.

        Two counts, not one, because a census alone reports the WORST pool as
        the best. Reservation picks from the PPR neighbourhood of the query's
        seeds, so a producer-made node with no edges at all is not in any
        neighbourhood it did not seed itself — a type whose only real members
        are isolated reads as fully populated while being unservable. Hence
        ``reachable/producer-made/total``, reachable first. ("Reachable" here is
        has-at-least-one-edge. An isolated node can still be returned as a
        direct hybrid-search hit; what it cannot be is *reserved*, which is what
        this probe is about.)

        INFO, always, with no threshold — the posture ``INTERVAL_COVERAGE``
        already ships. ``compile --strict`` maps warnings to exit 1, and the
        projects that trip this are exactly the ones whose producers have never
        run: a documentation corpus with no agent sessions has nothing to fix in
        the graph in front of it. A gate whose remedy is "record some sessions"
        is not a gate, it is a broken build. Silent when the graph has no nodes
        of these types at all — there is no pool to misread.
        """
        from .research_graph import PROCEDURAL_POOL_TYPES, has_producer_provenance

        pool_values = sorted(item.value for item in PROCEDURAL_POOL_TYPES)
        made: Dict[str, int] = {value: 0 for value in pool_values}
        total: Dict[str, int] = {value: 0 for value in pool_values}
        reachable: Dict[str, int] = {value: 0 for value in pool_values}
        incident: Set[str] = set()
        for edge in edges:
            incident.add(str(edge.get("source") or ""))
            incident.add(str(edge.get("target") or ""))
        for node_id, node in nodes_by_id.items():
            type_value = str(node.get("type") or "")
            if type_value not in total:
                continue
            total[type_value] += 1
            if not has_producer_provenance(type_value, node.get("metadata") or {}):
                continue
            made[type_value] += 1
            if node_id in incident:
                reachable[type_value] += 1

        populated = [value for value in pool_values if total[value]]
        if not populated:
            return

        histogram = ", ".join(
            f"{value}={reachable[value]}/{made[value]}/{total[value]}"
            for value in populated
        )
        unearned = [value for value in populated if made[value] == 0]
        # Real members, every one of them isolated: the pool the census would
        # have called healthiest and the one that can never be served.
        unreachable = [
            value
            for value in populated
            if made[value] and reachable[value] == 0
        ]
        all_total = sum(total[value] for value in populated)
        all_made = sum(made[value] for value in populated)
        all_reachable = sum(reachable[value] for value in populated)
        yield LintFinding(
            severity="info",
            code="PROCEDURAL_POOLS",
            message=(
                f"{all_made} of {all_total} nodes on producer-owned types were "
                f"made by their producer, {all_reachable} of those carry an edge "
                f"and can be reserved (reachable/producer-made/total: "
                f"{histogram})."
                + (
                    f" {', '.join(unearned)} hold only document extractions, so "
                    f"multi_pool reserves nothing for them."
                    if unearned
                    else ""
                )
                + (
                    f" {', '.join(unreachable)} hold real producer output that is "
                    f"unreachable — every member has degree 0, so no query "
                    f"neighbourhood contains one and the pool cannot be served."
                    if unreachable
                    else ""
                )
            ),
            suggested_fix=(
                "A node earns a reserved procedural slot by provenance, not by "
                "type: metadata['extractor'] naming a producer pass, producer "
                "provenance (member_ids / member_refs / lineage_key / "
                "distill_quality / distilled_through / agent_write_id), or "
                "metadata['agent'] on an ExpertiseProfile. A wholly unearned "
                "pool means the type is populated only by document extraction — "
                "the vocabulary is offered to the extraction LLM by "
                "EXTRACTABLE_NODE_TYPES, so a call-for-papers becomes an Event; "
                "run the producer (the session-event pass, distillation, or "
                "graph_write) to fill it. An unreachable pool is a different "
                "problem: the producer ran but wired no edges, so link its "
                "output into the graph. Neither is a failure to fix before "
                "shipping — these nodes stay reachable through search_nodes / "
                "graph_map / node_context, which do not apply this rule."
            ),
        )

    def _check_undistilled_backlog(
        self, nodes_by_id: Dict[str, dict], edges: List[dict]
    ) -> Iterable[LintFinding]:
        """Per-agent undistilled-backlog metric (§6.3).

        Scope findings referenced by no distillate's ``member_refs`` are the
        knowledge sitting below the distillation waterline — measured, not
        invisible. Info severity: backlog is normal; the metric exists so its
        growth is watchable.
        """
        try:
            from .agent_distill import undistilled_slice_chars
            from .research_graph import graph_from_payload

            graph = graph_from_payload(
                {"nodes": list(nodes_by_id.values()), "edges": edges}
            )
        except Exception:  # noqa: BLE001 — a malformed payload is other probes' job
            return
        agent_keys = sorted(
            str((node.get("metadata") or {}).get("agent_key") or "")
            for node in nodes_by_id.values()
            if str(node.get("type") or "") == "Agent"
            and str((node.get("metadata") or {}).get("agent_key") or "")
            and str((node.get("metadata") or {}).get("agent_key") or "") != "org:root"
        )
        for agent_key in agent_keys:
            try:
                chars = undistilled_slice_chars(graph, agent_key, self.project_root)
            except Exception:  # noqa: BLE001
                continue
            if chars <= 0:
                continue
            yield LintFinding(
                severity="info",
                code="AGENT_UNDISTILLED_BACKLOG",
                message=(
                    f"agent {agent_key}: ~{chars} chars of scope findings are "
                    "covered by no distillate (undistilled backlog)."
                ),
                suggested_fix=f"tesserae distill --agent {agent_key}",
            )

    def _check_synthesis_ghost_inputs(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """Synthesis pages whose ``inputs:`` reference node ids not in the graph.

        Each ghost id triggers exactly one finding, keyed on
        ``(synthesis_path, ghost_id)`` so removing them via ``--fix-trivial``
        rewrites the frontmatter once per page.
        """
        synth_dir = self.wiki_dir / "syntheses"
        if not synth_dir.exists():
            return
        valid_ids = set(nodes_by_id.keys())
        for md_path in sorted(synth_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter, _body = _split_frontmatter(text)
            inputs = frontmatter.get("inputs") or []
            if not isinstance(inputs, list):
                continue
            for raw in inputs:
                input_id = str(raw)
                if input_id in valid_ids:
                    continue
                yield LintFinding(
                    severity="warning",
                    code="SYNTHESIS_GHOST_INPUT",
                    message=(
                        f"Synthesis frontmatter references missing node id: {input_id}."
                    ),
                    node_id=input_id,
                    path=str(md_path),
                    suggested_fix="Prune the missing input, or restore the node.",
                    auto_fixable=True,
                )

    def _check_suggested_merges(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """Two Repositories with the same ``github_repo`` URL, or two Persons identical."""
        repo_groups: Dict[str, List[str]] = {}
        person_groups: Dict[Tuple[str, str], List[str]] = {}
        for node_id, node in nodes_by_id.items():
            metadata = node.get("metadata") or {}
            if node.get("type") in ("Repository", "Project"):
                url = metadata.get("github_repo")
                if url:
                    repo_groups.setdefault(str(url), []).append(node_id)
            elif node.get("type") == "Person":
                affiliation = str(metadata.get("affiliation") or "")
                key = (str(node.get("name") or "").strip().lower(), affiliation.strip().lower())
                if key[0]:
                    person_groups.setdefault(key, []).append(node_id)

        for url, ids in sorted(repo_groups.items()):
            if len(ids) < 2:
                continue
            yield LintFinding(
                severity="info",
                code="SUGGESTED_MERGE",
                message=(
                    f"{len(ids)} Repository nodes share github_repo={url}; consider merging: "
                    + ", ".join(sorted(ids))
                ),
                node_id=sorted(ids)[0],
                suggested_fix="Run canonicalization, or merge the Repository nodes by id.",
            )
        for (name, affiliation), ids in sorted(person_groups.items()):
            if len(ids) < 2:
                continue
            yield LintFinding(
                severity="info",
                code="SUGGESTED_MERGE",
                message=(
                    f"{len(ids)} Person nodes share name={name!r} affiliation={affiliation!r}; "
                    f"consider merging: " + ", ".join(sorted(ids))
                ),
                node_id=sorted(ids)[0],
                suggested_fix="Run canonicalization, or merge the Person nodes by id.",
            )

    def _check_suggested_subtypes(self) -> Iterable[LintFinding]:
        """Surface pending sub-type proposals from the schema-drift ledger.

        The last rung of the ontology-growth loop, and deliberately the
        quietest one: clustering plus an LLM can NOTICE that a host type is
        carrying two populations, but promoting a name into
        ``ResearchNodeType`` stays a human edit to ``research_graph.py``. An
        LLM-minted enum member would break two of the things this project is
        genuinely ahead on — ``ResearchEdge.__post_init__``'s fail-loud
        contract and ``verify_claim``'s ``predicate_not_in_ontology`` refusal.

        Reads ONE sidecar file: no graph traversal, no LLM, no network, so
        ``tesserae lint`` stays deterministic, offline and free. Absent file =
        silence (schema drift is opt-in, and a "never ran" row in every
        project is noise); present-but-unreadable is a DIFFERENT state and
        says so, rather than being collapsed into the same silence.
        """
        try:
            from .schema_drift import PROPOSAL_LEDGER_NAME

            ledger_path = self.wiki_root / PROPOSAL_LEDGER_NAME
            if not ledger_path.is_file():
                return
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — lint never dies on sidecar trouble
            yield LintFinding(
                severity="info",
                code="LINT_PROBE_FAILED",
                message=(
                    f"SUGGESTED_SUBTYPE did not run: the schema-drift ledger "
                    f"could not be read ({type(exc).__name__}: {exc}). Pending "
                    f"proposals are unknown for this project, not zero."
                ),
                suggested_fix=(
                    "Re-run `tesserae schema-drift` to rewrite "
                    f".tesserae/{PROPOSAL_LEDGER_NAME}, or delete it."
                ),
            )
            return
        if not isinstance(payload, list):
            return
        # The ledger is a HUMAN-EDITABLE file, so every field is untrusted:
        # numeric node ids (a plausible hand-edit) would make a mixed-type
        # sort raise, and a scalar node_ids would not iterate — either one
        # escaping this method takes the WHOLE lint run down with it, which
        # `compile --strict` then reports as "lint did not run".
        pending = []
        for record in payload:
            if not isinstance(record, dict) or record.get("approved"):
                continue
            raw_ids = record.get("node_ids")
            node_ids = sorted(str(i) for i in raw_ids) if isinstance(raw_ids, list) else []
            pending.append((record, node_ids))
        pending.sort(
            key=lambda pair: (
                str(pair[0].get("host_type") or ""),
                str(pair[0].get("proposed_type") or ""),
                pair[1][0] if pair[1] else "",
            )
        )
        for record, node_ids in pending[:30]:
            name = str(record.get("proposed_type") or record.get("name") or "")
            host_type = str(record.get("host_type") or "")
            if not name or not node_ids:
                continue
            sample = ", ".join(node_ids[:3])
            yield LintFinding(
                severity="info",
                code="SUGGESTED_SUBTYPE",
                message=(
                    f"{len(node_ids)} {host_type} nodes cluster as candidate "
                    f"sub-type {name!r}: {sample}"
                    + ("…" if len(node_ids) > 3 else "")
                ),
                node_id=node_ids[0],
                path=str(ledger_path),
                suggested_fix=(
                    "Promotion is a human edit — add the member to "
                    "ResearchNodeType in tesserae/research_graph.py, then set "
                    f'"approved": true in .tesserae/{PROPOSAL_LEDGER_NAME}.'
                ),
            )

    def _check_pending_review(self) -> Iterable[LintFinding]:
        """Report unanswered candidate-merge pairs from the candidate ledger.

        The durable half of the review queue: once a verdict is remembered,
        "how much review work is outstanding" stops being a function of corpus
        size and becomes a real number, which is the only reason it is worth
        reporting at all.

        Reads ONE sidecar file — no graph traversal, no LLM, no network — so
        ``tesserae lint`` stays deterministic, offline and free. Absent file =
        silence (a project that has never run the review workflow has nothing
        to say); present-but-unreadable is a DIFFERENT state and says so, since
        collapsing it into silence makes "no pending pairs" and "the ledger is
        corrupt" the same report — the mistake ``LINT_PROBE_FAILED`` exists for.
        """
        from .candidate_ledger import (
            CANDIDATE_LEDGER_FILENAME,
            CANDIDATE_LEDGER_SCHEMA_VERSION,
            STATUS_PENDING,
        )

        ledger_path = self.wiki_root / CANDIDATE_LEDGER_FILENAME
        try:
            if not ledger_path.is_file():
                return
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != CANDIDATE_LEDGER_SCHEMA_VERSION
                or not isinstance(payload.get("records"), list)
            ):
                raise ValueError(
                    f"unrecognised ledger shape (expected schema_version "
                    f"{CANDIDATE_LEDGER_SCHEMA_VERSION} and a records list)"
                )
        except Exception as exc:  # noqa: BLE001 — lint never dies on sidecar trouble
            yield LintFinding(
                severity="info",
                code="LINT_PROBE_FAILED",
                message=(
                    f"PENDING_REVIEW did not run: the candidate ledger could not "
                    f"be read ({type(exc).__name__}: {exc}). Unanswered merge "
                    f"candidates are unknown for this project, not zero."
                ),
                path=str(ledger_path),
                suggested_fix=(
                    f"Re-run `tesserae extract --canonicalize` to rewrite "
                    f".tesserae/{CANDIDATE_LEDGER_FILENAME}, or delete it — deleting "
                    f"discards every recorded human verdict."
                ),
            )
            return
        # Every field is untrusted: the ledger is a HUMAN-EDITABLE file (flipping
        # a status by hand is the supported way to answer the queue), and a
        # hand-edited row that does not iterate would take the WHOLE lint run
        # down, which `compile --strict` then reports as "lint did not run".
        pending = sorted(
            (str(row.get("a") or ""), str(row.get("b") or ""))
            for row in payload["records"]
            if isinstance(row, dict) and row.get("status") == STATUS_PENDING
        )
        pending = [pair for pair in pending if pair[0] and pair[1]]
        if not pending:
            return
        sample = "; ".join(f"{a} ↔ {b}" for a, b in pending[:3])
        yield LintFinding(
            severity="info",
            code="PENDING_REVIEW",
            message=(
                f"{len(pending)} candidate merge pair(s) await a human verdict: "
                f"{sample}" + ("…" if len(pending) > 3 else "")
            ),
            node_id=pending[0][0],
            path=str(ledger_path),
            suggested_fix=(
                "Review the pairs and apply a decision file with `tesserae extract "
                "--apply-review-decisions … --reviewed-by <you>`, or set a row's "
                f'"status" to "rejected" in .tesserae/{CANDIDATE_LEDGER_FILENAME}. '
                "A rejected pair is never re-surfaced."
            ),
        )

    def _read_charter_for_lint(
        self, code: str
    ) -> Tuple[Optional[dict], Optional[LintFinding]]:
        """``(charter, None)``, ``(None, None)`` for no charter, or a probe failure.

        Shared by the two charter probes so each reads ONE JSON file and no
        graph traversal, LLM or network is involved — ``tesserae lint`` stays
        deterministic, offline and free.

        Absent = silence. A project under :func:`charter.worth_chartering`'s
        founding bound never gets a ``.tesserae/charter/`` at all, so a "no
        charter" row would appear in every small project's report forever.
        Present-but-unreadable is a DIFFERENT state and says so: collapsing it
        into silence would make "the partition is intact" and "the charter is
        truncated" the same report, which is the mistake ``LINT_PROBE_FAILED``
        exists for — and it is the more dangerous half here, because
        ``read_charter`` raises on a mangled charter precisely so nothing
        downstream mistakes it for a project that never had one.
        """
        from .charter import charter_path, read_charter

        path = charter_path(self.project_root)
        try:
            payload = read_charter(self.project_root)
            if payload is None:
                return None, None
            # Shape is checked here rather than trusted: charter.json is a file
            # on disk that a bad hand-merge can leave in any state, and a
            # non-dict ``domains`` escaping this method takes the WHOLE lint run
            # down, which `compile --strict` then reports as "lint did not run".
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("domains"), dict)
                or not isinstance(payload.get("member_index"), dict)
            ):
                raise ValueError(
                    "unrecognised charter shape (expected 'domains' and "
                    "'member_index' objects)"
                )
        except Exception as exc:  # noqa: BLE001 — lint never dies on sidecar trouble
            return None, LintFinding(
                severity="info",
                code="LINT_PROBE_FAILED",
                message=(
                    f"{code} did not run: the charter could not be read "
                    f"({type(exc).__name__}: {exc}). The state of the "
                    f"institution is unknown for this project, not healthy."
                ),
                path=str(path),
                suggested_fix=(
                    "Restore .tesserae/charter/charter.json from version control. "
                    "Deleting it and recompiling re-founds the charter, which "
                    "mints new slugs and breaks every pinned attach path."
                ),
            )
        return payload, None

    def _check_charter_partition(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """CH-01 checked against ``graph.json`` — the half runtime cannot check.

        ``charter._verify_partition`` already raises inside ``build_charter``,
        so this probe deliberately does NOT re-assert what it covers. Two gaps
        are left, and both are about the file on disk rather than the object in
        memory:

        **The ordering window.** ``_write_charter_sidecar`` runs at
        ``project.py:1573``, after which ``compile()`` rebinds ``graph``
        through ``_merge_community_summaries``, ``_merge_distillation`` and
        ``_write_artifacts``'s own passes before ``graph.json`` is written. A
        pass in that window that changes a node's ``type`` moves it across
        :func:`partition_graph`'s split without touching its id — concretely
        ``apply_schema_drift`` (opt-in, ``TESSERAE_SCHEMA_DRIFT_APPLY``) — so
        ``member_index`` can name an id ``graph.json`` does not carry. That is
        the same systematic cause behind the measured 169/1360 hierarchy-sidecar
        skew, and ``_verify_partition``'s docstring hands this case to lint by
        name because no fixture can produce it.

        **Succession.** ``_verify_partition`` runs on the FRESH charter inside
        ``build_charter``; what a reorg writes is ``succeed``'s output, which is
        never re-verified. ``succeed`` remaps ``domains`` and ``member_index``
        through ``rename`` in independent passes, and its own docstring records
        the review finding where exactly that let ``member_index`` keep pointing
        members at a slug that no longer held them.

        ``error`` because the remedy is in the tripping project's control and a
        void partition routes agents into domains that hold nothing: a
        recompile re-derives the charter from the graph on disk, and the one
        known in-project trigger is an opt-in env flag. It is not a gate whose
        remedy is a human editing the Tesserae package.

        Only ``live`` domains are examined. A tombstone keeps the
        ``direct_member_ids`` it had when it was last live — deliberately, so an
        old citation still resolves — and those same members are held by live
        domains now, so counting tombstones would report the whole institution
        as duplicated on every healthy charter.
        """
        payload, failure = self._read_charter_for_lint("CHARTER_PARTITION")
        if failure is not None:
            yield failure
            return
        if payload is None:
            return
        charter_file = str(self.wiki_root / "charter" / "charter.json")
        if not nodes_by_id:
            # ``_load_graph`` returns an EMPTY graph for a missing or corrupt
            # graph.json, which would make every member_index id read as
            # unresolved — thousands of errors describing a graph nobody could
            # parse. A charter cannot exist for a genuinely empty graph
            # (``worth_chartering`` requires ARTIFACT_CHAR_BUDGET of mass), so
            # this is the unreadable case and has to say so.
            yield LintFinding(
                severity="info",
                code="LINT_PROBE_FAILED",
                message=(
                    "CHARTER_PARTITION did not run: a charter exists but "
                    "graph.json carries no nodes, so member ids cannot be "
                    "resolved. The partition is unknown for this project, "
                    "not intact."
                ),
                path=str(self.graph_path),
                suggested_fix="Recompile the project to rewrite .tesserae/graph.json.",
            )
            return

        domains = payload["domains"]
        # Every field is untrusted for the same reason the shape is: this file
        # can be hand-merged, and a scalar where a list belongs must not take
        # the lint run down.
        live = {
            str(slug): entry
            for slug, entry in sorted(domains.items(), key=lambda kv: str(kv[0]))
            if isinstance(entry, dict) and entry.get("status") == "live"
        }
        member_index = {
            str(mid): str(slug) for mid, slug in payload["member_index"].items()
        }

        unresolved = sorted(mid for mid in member_index if mid not in nodes_by_id)
        if unresolved:
            yield LintFinding(
                severity="error",
                code="CHARTER_PARTITION",
                message=(
                    f"{len(unresolved)} charter member id(s) do not resolve in "
                    f"graph.json: {', '.join(unresolved[:3])}"
                    + ("…" if len(unresolved) > 3 else "")
                    + ". The charter routes agents to nodes that are not there."
                ),
                node_id=unresolved[0],
                path=charter_file,
                suggested_fix=(
                    "Recompile to re-derive the charter from the graph on disk. "
                    "If it survives a recompile with TESSERAE_SCHEMA_DRIFT_APPLY "
                    "unset, charter.json and graph.json were written by different "
                    "compiles."
                ),
            )

        held: Dict[str, str] = {}
        duplicated: Set[str] = set()
        for slug, entry in live.items():
            direct = entry.get("direct_member_ids")
            for member_id in direct if isinstance(direct, list) else []:
                member_id = str(member_id)
                if member_id in held:
                    duplicated.add(member_id)
                held[member_id] = slug
        if duplicated:
            sample = sorted(duplicated)
            yield LintFinding(
                severity="error",
                code="CHARTER_PARTITION",
                message=(
                    f"{len(sample)} member id(s) are held by more than one live "
                    f"domain: {', '.join(sample[:3])}"
                    + ("…" if len(sample) > 3 else "")
                    + ". CH-01 requires exactly one."
                ),
                node_id=sample[0],
                path=charter_file,
                suggested_fix=(
                    "Recompile to re-derive the charter; a partition this broken "
                    "cannot be repaired by hand."
                ),
            )
        disagreeing = sorted(
            mid for mid, slug in member_index.items() if held.get(mid) != slug
        )
        if disagreeing:
            yield LintFinding(
                severity="error",
                code="CHARTER_PARTITION",
                message=(
                    f"{len(disagreeing)} member id(s) are indexed to a live domain "
                    f"whose direct block does not hold them: "
                    f"{', '.join(disagreeing[:3])}"
                    + ("…" if len(disagreeing) > 3 else "")
                    + ". member_index and the domains disagree about where they live."
                ),
                node_id=disagreeing[0],
                path=charter_file,
                suggested_fix=(
                    "Recompile to re-derive the charter; member_index is written "
                    "from the direct blocks and cannot be re-aligned by hand."
                ),
            )

        # ``union(child members) ∪ direct == own members``, in the only form the
        # record can express it: the tree carries per-domain counts, not per-domain
        # member lists. A dangling child is reported separately and excluded from
        # the arithmetic, because "child_slugs names a domain that does not exist"
        # and "the counts disagree" have different remedies and one causes the
        # other. ``succeed`` preserves an unmapped child VERBATIM rather than
        # raising, so this state is reachable by design and lint is where it lands.
        dangling: List[Tuple[str, str]] = []
        mismatched: List[str] = []
        for slug, entry in live.items():
            raw_children = entry.get("child_slugs")
            children = (
                [str(c) for c in raw_children] if isinstance(raw_children, list) else []
            )
            absent = [child for child in children if child not in live]
            if absent:
                dangling.extend((slug, child) for child in sorted(absent))
                continue
            direct = entry.get("direct_member_ids")
            own = entry.get("member_count")
            if not isinstance(own, int) or not isinstance(direct, list):
                continue
            covered = len(direct)
            for child in children:
                child_count = live[child].get("member_count")
                if not isinstance(child_count, int):
                    covered = -1
                    break
                covered += child_count
            if covered >= 0 and covered != own:
                mismatched.append(slug)
        if dangling:
            sample = "; ".join(f"{slug} → {child}" for slug, child in dangling[:3])
            yield LintFinding(
                severity="error",
                code="CHARTER_PARTITION",
                message=(
                    f"{len(dangling)} child_slugs entr(ies) name a domain no live "
                    f"charter domain defines: {sample}"
                    + ("…" if len(dangling) > 3 else "")
                    + ". Agents descending the charter reach a dead end."
                ),
                path=charter_file,
                suggested_fix="Recompile to re-derive the charter.",
            )
        if mismatched:
            yield LintFinding(
                severity="error",
                code="CHARTER_PARTITION",
                message=(
                    f"{len(mismatched)} domain(s) claim a member_count that is not "
                    f"their direct block plus their children's counts: "
                    f"{', '.join(sorted(mismatched)[:3])}"
                    + ("…" if len(mismatched) > 3 else "")
                    + ". The tree does not cover what it says it covers."
                ),
                path=charter_file,
                suggested_fix="Recompile to re-derive the charter.",
            )

    def _check_charter_fallback(self) -> Iterable[LintFinding]:
        """CH-06 — how much of the institution is frozen at structural quality.

        ``materialize_community_summary`` never raises and caches nothing on
        failure, and no later compile revisits a scope whose cache is cold. One
        LLM outage during a materialization pass therefore leaves a slice of the
        charter with a structural card and nothing to retry it, indefinitely and
        invisibly. A count is the entire mechanism: ``info`` severity, because a
        cold cache is the normal state of a lazy cache and the number — not the
        severity — is the instrument, exactly as ``REASONING_EDGE_RATIO`` puts
        its counts into lint-report.json for CI to diff.

        Deliberately NOT a threshold. The spec asks for "exit non-zero above a
        threshold" and gives no value, no default and no config key anywhere;
        inventing one would gate every project's build on a number nobody chose.

        Silent unless there is a frozen slice. "0 of 780 domains are warm" is
        true of every project that never ran the pass — the never-ran noise
        absent optional state must not generate — and "0 of 780 are cold" is a
        row saying nothing is wrong. A cold remainder is CH-06's subject only
        once at least one domain is warm, because that is what proves a pass ran
        and stopped partway. The undated count is reported on its own terms: it
        is a property of the corpus rather than of the cache, so it does not
        wait on a materialization pass.

        Warmth is decided by :func:`charter.brief_cache_path`, never by a
        filename spelled out here — see the comment on the loop for what
        spelling one out cost.
        """
        from .charter import brief_cache_path

        payload, failure = self._read_charter_for_lint("CHARTER_FALLBACK")
        if failure is not None:
            yield failure
            return
        if payload is None:
            return
        cache_dir = self.wiki_root / "community_summaries"
        live = {
            str(slug): entry
            for slug, entry in payload["domains"].items()
            if isinstance(entry, dict) and entry.get("status") == "live"
        }
        if not live:
            return
        # The path is ASKED FOR rather than restated. ``brief_cache_path``
        # composes the cid namespace and the level exactly as
        # ``read_domain_brief`` does, so "warm" here means the one file that
        # reader will open — no other file can serve a brief, and a rename on
        # the writer's side moves the probe with it. Restating the name is how
        # this probe was dead on arrival: it keyed on
        # ``CommunitySummary_<slug>.json``, the namespace charter briefs
        # deliberately avoid, so no domain was ever warm and the frozen branch
        # below could not be reached.
        #
        # One level per domain, not a scan of all of them. A brief is written
        # at the domain's OWN tier and nowhere else, and a copy left at another
        # level by a tier move is residue ``read_domain_brief`` will not find
        # (``_brief_level``'s docstring calls that residue intended). Counting
        # it warm would tell an operator a domain has prose when every reader
        # of it gets a structural card.
        cold: List[str] = []
        for slug in live:
            try:
                path = brief_cache_path(payload, slug, cache_dir=cache_dir)
                warm = path is not None and path.is_file()
            except (TypeError, ValueError, OSError):
                # A hand-merged charter can carry a non-numeric ``tier``.
                # Unknown is not warm, and it must not take the lint run down.
                warm = False
            if not warm:
                cold.append(slug)
        cold.sort()
        # ``quality`` is written by the domain clock when no member of a domain
        # carries a resolvable timestamp on any rung of ``temporal._source_ts``'s
        # ladder — ``charter.py:790``/``:858`` write exactly ``"undated"`` or
        # ``"dated"`` on every live record, so an ABSENT key means a charter
        # written before that clock shipped rather than a healthy domain. Not
        # counting it is still right: the count is a rising extraction-quality
        # signal, and an old charter has no undated domains to report rather
        # than an unknown number of them.
        undated = sorted(
            slug for slug, entry in live.items() if entry.get("quality") == "undated"
        )
        frozen = bool(cold) and len(cold) < len(live)
        if not frozen and not undated:
            return
        message = ""
        if frozen:
            message = (
                f"{len(cold)} of {len(live)} live charter domain(s) have no warm "
                f"summary and fall back to a structural card: "
                f"{', '.join(cold[:3])}" + ("…" if len(cold) > 3 else "") + "."
            )
        if undated:
            message += (
                f"{' ' if message else ''}{len(undated)} domain(s) are undated "
                f"(no member carries a resolvable timestamp): "
                f"{', '.join(undated[:3])}"
                + ("…" if len(undated) > 3 else "")
                + "."
            )
        yield LintFinding(
            severity="info",
            code="CHARTER_FALLBACK",
            message=message,
            path=str(cache_dir),
            suggested_fix=(
                "Cold domains materialize lazily on first read; a count that "
                "rises across compiles means an LLM outage froze a slice of the "
                "institution and nothing is retrying it."
            ),
        )

    def _check_stale_build_history(self) -> Iterable[LintFinding]:
        """Build-history entries older than 90 days (oldest 30 are reported)."""
        if not self.build_history_path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        stale: List[Tuple[datetime, dict]] = []
        try:
            for line in self.build_history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                built_at_raw = entry.get("built_at")
                if not isinstance(built_at_raw, str):
                    continue
                try:
                    built_at = datetime.strptime(
                        built_at_raw, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if built_at < cutoff:
                    stale.append((built_at, entry))
        except OSError:
            return
        # Surface the oldest 30 (the spec asks for "last 30 days listed in
        # report" — we read that as cap=30; oldest first puts the worst at
        # the top of the report).
        stale.sort(key=lambda tup: tup[0])
        for built_at, entry in stale[:30]:
            yield LintFinding(
                severity="info",
                code="STALE_BUILD_HISTORY",
                message=f"Build-history entry older than 90 days: built_at={built_at.isoformat()}",
                path=str(self.build_history_path),
                suggested_fix="Trim `.build-history.jsonl` to recent entries.",
            )

    def _check_code_graph_staleness(
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """``SourceFile`` nodes whose backing files changed since last compile.

        Diffs the git HEAD recorded in the build-history ledger against the
        current HEAD and reports which changed files back ``SourceFile``
        nodes. Read-only over git and the graph — a staleness *report*, never
        auto-regeneration. Every finding is ``info``: a repo that merely
        advanced by a commit must not fail ``compile --strict``. No wall
        clock, no config-dependent git output (``--no-renames``,
        ``core.quotepath=false``, full shas sliced in Python), so the report
        stays a pure function of (graph, ledger, git object state).
        """
        if not self.build_history_path.exists():
            return
        recorded: Optional[str] = None
        try:
            for line in self.build_history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                git_head = entry.get("git_head")
                if isinstance(git_head, str) and git_head:
                    recorded = git_head
        except OSError:
            return
        if recorded is None:
            # Pre-feature ledgers and non-code projects produce no noise.
            return
        head = read_git_head(self.project_root)
        if head is None or head == recorded:
            return
        if _git(self.project_root, "rev-parse", "--verify", "--quiet", recorded + "^{commit}") is None:
            yield LintFinding(
                severity="info",
                code="CODE_GRAPH_HEAD_UNRESOLVED",
                message=(
                    f"Recorded compile head {recorded[:12]} is not resolvable in "
                    "this repo (history rewritten or pruned); staleness unknown"
                ),
                path=str(self.build_history_path),
                suggested_fix="Run `tesserae compile` to re-anchor the graph to the current HEAD.",
            )
            return
        out = _git(self.project_root, "rev-list", "--count", f"{recorded}..HEAD")
        n_commits = int(out.strip()) if out else 0
        # Two-dot snapshot diff (not ``log``) so merges/reverts net out;
        # ``--relative`` re-roots paths at project_root when the workspace is
        # a repo subdirectory, matching SourceFile node names.
        diff_out = _git(
            self.project_root,
            "-c",
            "core.quotepath=false",
            "diff",
            "--no-renames",
            "--relative",
            "--name-status",
            recorded,
            "HEAD",
        )
        changes: List[Tuple[str, str]] = []
        for raw in (diff_out or "").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            status, _, path = raw.partition("\t")
            if path:
                changes.append((status[:1], path))
        if not changes and n_commits == 0:
            # Diverged-but-identical trees (e.g. reset + recommit).
            return
        source_files = {
            node["name"]: node_id
            for node_id, node in nodes_by_id.items()
            if node.get("type") == "SourceFile"
        }
        matched = sorted((path, status) for status, path in changes if path in source_files)
        yield LintFinding(
            severity="info",
            code="CODE_GRAPH_BEHIND",
            message=(
                f"Code graph compiled at {recorded[:12]} is {n_commits} commit(s) behind HEAD {head[:12]} "
                f"({len(changes)} changed file(s), {len(matched)} tracked in graph)"
            ),
            suggested_fix="Run `tesserae compile` to refresh the graph from the current working tree.",
        )
        # Lexicographic path order = deterministic cap. Deletions matched to a
        # node are the strongest signal (the graph cites a file that no longer
        # exists); added files have no node and only count toward the summary.
        for path, status in matched[:20]:
            yield LintFinding(
                severity="info",
                code="CODE_GRAPH_STALE_FILE",
                message=f"Source file changed since last compile ({status}): {path}",
                node_id=source_files[path],
                path=path,
                suggested_fix="Run `tesserae compile` to refresh the graph from the current working tree.",
            )

    def _check_claim_support(
        self,
        nodes_by_id: Dict[str, dict],
        *,
        cap: int,
        llm_client: Optional[object] = None,
    ) -> Iterable[LintFinding]:
        """Opt-in (``verify_claims=True``): LLM-judge sampled cited claims.

        Samples up to ``cap`` cited claims from synthesis pages
        (deterministically, by content hash — no RNG, no wall clock) and asks
        the configured JSON client, in ONE batched call, whether each cited
        source node's text supports the claim. Any failure (no candidates, no
        client, LLM error, unparsable output) degrades to a single
        ``CLAIM_SUPPORT_SKIPPED`` info finding — never an exception. Writes
        nothing; no finding is ``auto_fixable``.
        """
        sampled = _sample_claims(_iter_claim_candidates(self.wiki_root, nodes_by_id), cap)
        if not sampled:
            yield LintFinding(
                severity="info",
                code="CLAIM_SUPPORT_SKIPPED",
                message="claim support: no cited claims found in wiki/syntheses — nothing to verify.",
            )
            return
        client = llm_client
        if client is None:
            # Lazy import keeps the default lint path stdlib-only.
            from .llm_json import build_default_json_client

            client = build_default_json_client()
        if client is None:
            yield LintFinding(
                severity="info",
                code="CLAIM_SUPPORT_SKIPPED",
                message=(
                    "claim support: no LLM backend available "
                    "(claude/codex CLI or ANTHROPIC_API_KEY); skipped."
                ),
            )
            return
        user = json.dumps(
            {
                "items": [
                    {
                        "index": i,
                        "claim": candidate.claim_text[:_CLAIM_MAX_CHARS],
                        "source": _claim_text(nodes_by_id[candidate.node_id])[:_SOURCE_MAX_CHARS],
                    }
                    for i, candidate in enumerate(sampled)
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            payload = client.complete_json(
                system=_CLAIM_SUPPORT_SYSTEM,
                user=user,
                schema_name="claim_support_v1",
            )
        except Exception:
            payload = None
        if payload is None:
            yield LintFinding(
                severity="info",
                code="CLAIM_SUPPORT_SKIPPED",
                message="claim support: LLM call failed or returned unparsable output; skipped.",
            )
            return
        raw_verdicts = payload.get("verdicts") if isinstance(payload, dict) else payload
        if not isinstance(raw_verdicts, list):
            raw_verdicts = []
        counts = {"supported": 0, "partial": 0, "unsupported": 0, "unverifiable": 0}
        for i, candidate in enumerate(sampled):
            verdict = str(raw_verdicts[i]).strip().lower() if i < len(raw_verdicts) else ""
            if verdict not in _CLAIM_VERDICTS:
                verdict = "unverifiable"
            counts[verdict] += 1
            if verdict in ("supported", "unverifiable"):
                continue
            snippet = candidate.claim_text[:160]
            if verdict == "unsupported":
                severity = "warning"
                code = "CLAIM_UNSUPPORTED"
                message = f"Cited source does not support the claim: {snippet!r}"
            else:
                severity = "info"
                code = "CLAIM_PARTIAL"
                message = f"Cited source only partially supports the claim: {snippet!r}"
            yield LintFinding(
                severity=severity,
                code=code,
                message=message,
                node_id=candidate.node_id,
                path=str(self.wiki_root / candidate.page_relpath),
                suggested_fix="Re-run synthesis for this page, or correct/remove the citation.",
            )
        pages = len({candidate.page_relpath for candidate in sampled})
        yield LintFinding(
            severity="info",
            code="CLAIM_SUPPORT_SUMMARY",
            message=(
                f"claim support: sampled {len(sampled)} claims across {pages} pages — "
                f"{counts['supported']} supported, {counts['partial']} partial, "
                f"{counts['unsupported']} unsupported, {counts['unverifiable']} unverifiable."
            ),
        )

    # ------------------------------------------------------------------
    # auto-fix helpers
    # ------------------------------------------------------------------

    def _fix_missing_implemented_in(
        self, graph: Dict[str, object], finding: LintFinding
    ) -> bool:
        """Insert the ``implemented_in`` edge directly into the graph payload.

        Returns ``True`` iff the graph was mutated. We only fix when the
        suggested-fix string we authored above is present, because that
        string is the only place we encode the canonical (paper, repo) ids.
        """
        if not finding.suggested_fix:
            return False
        # Trailing period is part of the human-facing sentence, not the node
        # id; strip before parsing so node ids that *do* contain dots survive.
        suggested = finding.suggested_fix.rstrip(".")
        match = re.match(
            r"Add edge (?P<src>\S+) --implemented_in--> (?P<tgt>\S+)$",
            suggested,
        )
        if not match:
            return False
        source = match.group("src")
        target = match.group("tgt")
        edges = graph.setdefault("edges", [])
        if not isinstance(edges, list):
            return False
        for edge in edges:
            if (
                edge.get("source") == source
                and edge.get("target") == target
                and edge.get("type") == "implemented_in"
            ):
                return False
        edges.append(
            {
                "source": source,
                "target": target,
                "type": "implemented_in",
                "evidence": "auto-fixed by project lint --fix-trivial",
                "metadata": {"auto_fixed": True},
            }
        )
        return True

    def _fix_synthesis_ghost_input(self, finding: LintFinding) -> None:
        """Remove the offending ``inputs:`` entry from a synthesis page.

        The wiki store hashes the *body*, not the frontmatter, when deciding
        whether to write — so a frontmatter rewrite leaves the next compile's
        idempotence guarantees intact.
        """
        if not finding.path or not finding.node_id:
            return
        path = Path(finding.path)
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        frontmatter, body = _split_frontmatter(text)
        inputs = frontmatter.get("inputs")
        if not isinstance(inputs, list):
            return
        new_inputs = [item for item in inputs if str(item) != finding.node_id]
        if len(new_inputs) == len(inputs):
            return
        frontmatter["inputs"] = new_inputs
        path.write_text(
            _render_with_frontmatter(frontmatter, body),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # IO helpers
    # ------------------------------------------------------------------

    def _load_graph(self) -> Dict[str, object]:
        if not self.graph_path.exists():
            return {"nodes": [], "edges": []}
        try:
            return json.loads(self.graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"nodes": [], "edges": []}

    def _print_summary(self, report: LintReport, *, severity_floor: str) -> None:
        floor_rank = _SEVERITY_RANK[severity_floor]
        no_color = bool(os.environ.get("NO_COLOR")) or not sys.stderr.isatty()
        bold = "" if no_color else "\033[1m"
        reset = "" if no_color else "\033[0m"
        red = "" if no_color else "\033[31m"
        yellow = "" if no_color else "\033[33m"
        cyan = "" if no_color else "\033[36m"
        green = "" if no_color else "\033[32m"
        if not report.findings:
            print(f"{bold}{green}lint: clean — no findings{reset}", file=sys.stderr)
            return
        print(
            f"{bold}lint: {len(report.findings)} findings"
            f" (errors={report.by_severity.get('error', 0)},"
            f" warnings={report.by_severity.get('warning', 0)},"
            f" info={report.by_severity.get('info', 0)}){reset}",
            file=sys.stderr,
        )
        for code in sorted(report.by_code):
            print(f"  - {code}: {report.by_code[code]}", file=sys.stderr)
        # Highlight the floor: anything at or above the floor is colored.
        worst = max(_SEVERITY_RANK[f.severity] for f in report.findings)
        if worst < floor_rank:
            return
        if report.has_errors():
            color = red
        elif report.has_warnings():
            color = yellow
        else:
            color = cyan
        print(
            f"{color}lint report written to {self.report_md_path}{reset}",
            file=sys.stderr,
        )


# --------------------------------------------------------------------------- helpers

# Mirrors ``tesserae.wiki_projector._KIND_FOR_TYPE`` for nodes whose enum
# value the linter sees as a plain string in graph.json. Kept as a flat dict
# (rather than importing) so the linter has zero dependencies on projector
# internals — that keeps ``project lint`` runnable against arbitrary graphs.
_KIND_FOR_TYPE: Dict[str, str] = {
    "SourceDocument": "sources",
    "Paper": "papers",
    "Repository": "repos",
    "Project": "repos",
    "Concept": "concepts",
    "TechnicalTerm": "concepts",
    "MathematicalConcept": "concepts",
    "MethodologicalConcept": "concepts",
    "Algorithm": "concepts",
    "ObjectiveFunction": "concepts",
    "ArchitecturePattern": "concepts",
    "TrainingParadigm": "concepts",
    "InferenceStrategy": "concepts",
    "EvaluationProtocol": "concepts",
    "Task": "concepts",
    "Capability": "concepts",
    "Model": "entities",
    "Dataset": "entities",
    "Benchmark": "entities",
    "Metric": "entities",
    "Result": "entities",
    "Organization": "entities",
    "Person": "entities",
    "ResearchField": "topics",
    "ResearchTopic": "topics",
    "ProblemArea": "topics",
    "ApproachFamily": "topics",
    "Trend": "topics",
    "OpenQuestion": "questions",
    "Synthesis": "syntheses",
    "CommunitySummary": "communities",
}


# Same set used by ``research_graph.VERIFIED_PAPER_TITLE_QUALITIES``. Kept as
# a flat tuple so the linter has zero import-time dependency on
# ``research_graph`` (which keeps lint runnable against arbitrary graphs).
_VERIFIED_PAPER_TITLE_QUALITIES: FrozenSet[str] = frozenset(
    {"paper_file", "verified", "reference_context"}
)


# Mirror of :data:`research_graph._DAILY_FEEDS_RE` — recognises the social
# feed capture sub-tree that the projector deliberately omits from public
# wiki pages. Anything under ``data/research/daily/<date>/feeds/`` is treated
# as private evidence rather than a public research entity.
_LINT_FEED_PATH_RE = re.compile(r"data/research/daily/[^/]+/feeds/")


def _node_is_public(node: Dict[str, object]) -> bool:
    """True iff ``node`` would survive ``is_public_research_node()``.

    Mirrors ``research_graph.is_public_research_node`` against the JSON
    payload shape (no enum types). Two gates: source-path social-feed filter
    and the paper title-quality gate.
    """
    source_path = node.get("source_path") or ""
    if isinstance(source_path, str) and _LINT_FEED_PATH_RE.search(
        source_path.replace("\\", "/")
    ):
        return False
    if node.get("type") == "Paper":
        metadata = node.get("metadata") or {}
        if isinstance(metadata, dict):
            quality = str(metadata.get("title_quality") or "")
            if quality and quality not in _VERIFIED_PAPER_TITLE_QUALITIES:
                return False
    return True


def _slug_for(name: str) -> str:
    """Canonical slug — byte-identical to :func:`WikiPageStore.slug_for`.

    Mirrors the algorithm in ``tesserae.wiki_store._canonical_slug`` (and
    ``tesserae.site.pages._canonical_slug``) so the linter computes the same
    on-disk filename that the projector would write. The previous
    implementation was ASCII-only via ``[^a-z0-9]+``, which silently produced
    different slugs for names containing CJK / accented characters and
    therefore reported phantom forward + reverse drift.
    """
    import hashlib

    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if len(safe.encode("utf-8")) > 96:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        safe = (
            safe.encode("utf-8")[:80].decode("utf-8", errors="ignore").strip("-")
            + "-"
            + digest
        )
    return safe or hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def _claim_text(node: Dict[str, object]) -> str:
    parts: List[str] = []
    name = node.get("name")
    if isinstance(name, str):
        parts.append(name)
    description = node.get("description")
    if isinstance(description, str):
        parts.append(description)
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        evidence = metadata.get("evidence") or metadata.get("text")
        if isinstance(evidence, str):
            parts.append(evidence)
    return " ".join(parts)


def _share_topic(left: str, right: str) -> bool:
    """Cheap substring overlap heuristic for the contradiction check.

    We tokenize both strings to lowercase words, drop common stopwords, and
    require at least two shared tokens. This is intentionally crude: the
    finding has ``severity=info`` and the operator is expected to manually
    confirm.
    """
    left_tokens = set(_topic_tokens(left))
    right_tokens = set(_topic_tokens(right))
    return len(left_tokens & right_tokens) >= 2


_TOPIC_STOPWORDS = {
    "outperforms",
    "is",
    "outperformed",
    "by",
    "the",
    "a",
    "an",
    "on",
    "of",
    "in",
    "and",
    "or",
    "with",
    "to",
    "for",
    "claim",
    "performance",
    "comparison",
}


def _topic_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]+", text.lower())
    return [t for t in tokens if t not in _TOPIC_STOPWORDS and len(t) >= 3]


# --------------------------------------------------------------------------- claim support (opt-in)


@dataclass(frozen=True)
class _ClaimCandidate:
    """A cited claim extracted from a synthesis page (``verify_claims=True``)."""

    page_relpath: str  # "wiki/syntheses/<slug>.md" (posix, relative to wiki root)
    node_id: str  # resolved graph node id
    claim_text: str  # the paragraph containing the citation, stripped


_CLAIM_SUPPORT_SYSTEM = (
    "You judge whether a SOURCE text supports a CLAIM. For each item, answer "
    "exactly one of: supported, partial, unsupported. Judge only from the "
    "given source text — use no outside knowledge. Respond as JSON "
    '{"verdicts": ["supported", ...]} with one entry per item, in the same '
    "order as the items."
)

# Fixed truncation widths keep the prompt bytes deterministic for fixed artifacts.
_CLAIM_MAX_CHARS = 600
_SOURCE_MAX_CHARS = 1200
_CLAIM_VERDICTS: FrozenSet[str] = frozenset({"supported", "partial", "unsupported"})


def _iter_claim_candidates(
    wiki_root: Path, nodes_by_id: Dict[str, dict]
) -> List[_ClaimCandidate]:
    """Extract paragraph-level cited claims from ``wiki/syntheses/*.md``.

    A citation marker is ``[<node id>]`` or ``[<node name>]`` for any of the
    page's resolved frontmatter ``inputs:`` — except when immediately followed
    by ``(`` (a markdown link, not a citation). Deterministic by construction:
    sorted pages, sorted markers, first-seen dedupe on the candidate tuple.
    """
    synth_dir = wiki_root / "wiki" / "syntheses"
    if not synth_dir.exists():
        return []
    candidates: List[_ClaimCandidate] = []
    seen: set = set()
    for md_path in sorted(synth_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, body = _split_frontmatter(text)
        inputs = frontmatter.get("inputs") or []
        if not isinstance(inputs, list):
            continue
        # Ghost inputs are ``_check_synthesis_ghost_inputs``'s job — skip them.
        resolved = sorted({str(raw) for raw in inputs if str(raw) in nodes_by_id})
        if not resolved:
            continue
        # Marker -> node id. Raw ids first, then display names; a name shared
        # by several inputs maps to the sorted-first id (setdefault over the
        # sorted id list keeps this deterministic).
        markers: Dict[str, str] = {}
        for node_id in resolved:
            markers.setdefault(node_id, node_id)
        for node_id in resolved:
            name = nodes_by_id[node_id].get("name")
            if not isinstance(name, str) or not name:
                continue
            if "]" in name or "\n" in name:
                continue
            markers.setdefault(name, node_id)
        page_relpath = f"wiki/syntheses/{md_path.name}"
        for paragraph in re.split(r"\n\s*\n", body):
            claim_text = paragraph.strip()
            if len(claim_text) < 40:
                continue
            if claim_text.startswith(("#", "|", "```")):
                continue
            for marker in sorted(markers):
                if not re.search(re.escape(f"[{marker}]") + r"(?!\()", claim_text):
                    continue
                key = (page_relpath, markers[marker], claim_text)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(_ClaimCandidate(*key))
    return candidates


def _sample_claims(candidates: List[_ClaimCandidate], cap: int) -> List[_ClaimCandidate]:
    """Deterministic content-hash sample: same artifacts → same sample bytes."""
    import hashlib

    def _key(candidate: _ClaimCandidate) -> Tuple[str, str, str, str]:
        digest = hashlib.sha256(
            f"{candidate.page_relpath}\x00{candidate.node_id}\x00{candidate.claim_text}".encode("utf-8")
        ).hexdigest()
        return (digest, candidate.page_relpath, candidate.node_id, candidate.claim_text)

    return sorted(candidates, key=_key)[: max(cap, 0)]


def _split_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
    """Lightweight frontmatter parser sufficient for synthesis pages.

    Mirrors ``tesserae.wiki_store._parse_frontmatter`` for the subset we
    need (scalar keys, multi-line ``- "value"`` lists). Kept local so the
    linter has no soft dependency on synthesis internals.
    """
    if not text.startswith(_FRONTMATTER_DELIM):
        return {}, text
    rest = text[len(_FRONTMATTER_DELIM):]
    if rest.startswith("\n"):
        rest = rest[1:]
    elif rest.startswith("\r\n"):
        rest = rest[2:]
    end_match = _FRONTMATTER_END_RE.search(rest)
    if not end_match:
        return {}, text
    fm_text = rest[: end_match.start()]
    body = rest[end_match.end():]
    if body.startswith("\n"):
        body = body[1:]
    return _parse_frontmatter(fm_text), body


def _parse_frontmatter(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value:
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                items = [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
                out[key] = items
            else:
                out[key] = _unquote(value)
            i += 1
            continue
        items: List[object] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            stripped = nxt.lstrip()
            if not stripped:
                j += 1
                continue
            if not stripped.startswith("- "):
                break
            items.append(_unquote(stripped[2:]))
            j += 1
        if items:
            out[key] = items
            i = j
        else:
            out[key] = ""
            i += 1
    return out


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _format_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    needs_quotes = (
        text == ""
        or text.strip() != text
        or text.lower() in {"true", "false", "null", "~"}
        or any(ch in text for ch in (":", "#", "[", "]", "{", "}", ","))
    )
    if needs_quotes:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _render_with_frontmatter(frontmatter: Dict[str, object], body: str) -> str:
    lines = [_FRONTMATTER_DELIM]
    for key in sorted(frontmatter):
        value = frontmatter[key]
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    lines.append(_FRONTMATTER_DELIM)
    rendered = "\n".join(lines) + "\n"
    if not body.endswith("\n"):
        body = body + "\n"
    return rendered + body


__all__ = [
    "LintFinding",
    "LintReport",
    "WikiLinter",
    "SEVERITIES",
]
