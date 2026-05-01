"""Karpathy-style ``project lint`` for LLM-Wiki.

The linter walks the graph and the rendered wiki/site artifacts and produces
:class:`LintFinding` objects. Findings are sorted deterministically and the
report is byte-stable on identical input.

Severity ladder (3 levels): ``info`` < ``warning`` < ``error``.

Each check is a private method ``_check_*`` returning an iterable of
:class:`LintFinding`. The public entry point is :class:`WikiLinter` which
loads the graph + wiki + site, runs every check, optionally applies safe
auto-fixes (``fix_trivial=True``), and writes ``lint-report.md`` /
``lint-report.json`` next to the project graph.

Stdlib only — no LLM, no network. The report is intended to flag the kinds
of corruption documented in ``docs/superpowers/codex-extraction-review.md``
(orphan papers, stale citations, ghost synthesis inputs, drift, etc.) so the
operator can fix them cheaply.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# --------------------------------------------------------------------------- types

SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")
_SEVERITY_RANK: Dict[str, int] = {name: idx for idx, name in enumerate(SEVERITIES)}


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


# --------------------------------------------------------------------------- linter

# Markdown link patterns we scan in wiki bodies.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((papers|concepts|entities|topics|repos|sources|syntheses|questions)/([A-Za-z0-9_\-./]+?)\.md\)")
# Hrefs we scan in generated HTML.
_HTML_HREF_RE = re.compile(r'href="([^"#?][^"#?]*?)"')

_FRONTMATTER_DELIM = "---"


class WikiLinter:
    """Run lint checks against a project's `.llm-wiki/` artifacts.

    Construction is cheap; calling :meth:`run` reads the graph and walks the
    wiki + site directories. ``run()`` writes ``lint-report.md`` and
    ``lint-report.json`` to the project's `.llm-wiki/` root regardless of
    severity floor; the floor only affects exit-code semantics for the
    caller (and which findings the colored stderr summary highlights).
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.wiki_root = self.project_root / ".llm-wiki"
        self.graph_path = self.wiki_root / "graph.json"
        self.wiki_dir = self.wiki_root / "wiki"
        self.site_dir = self.wiki_root / "site"
        self.build_history_path = self.wiki_root / ".build-history.jsonl"
        self.report_md_path = self.wiki_root / "lint-report.md"
        self.report_json_path = self.wiki_root / "lint-report.json"

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def run(self, *, fix_trivial: bool = False, severity_floor: str = "info") -> LintReport:
        if severity_floor not in _SEVERITY_RANK:
            raise ValueError(f"Unknown severity floor: {severity_floor!r}")

        graph = self._load_graph()
        nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
        edges = list(graph.get("edges", []))

        findings: List[LintFinding] = []
        findings.extend(self._check_orphan_papers(nodes_by_id, edges))
        findings.extend(self._check_missing_implemented_in(nodes_by_id, edges))
        findings.extend(self._check_stale_citations())
        findings.extend(self._check_dangling_wiki_links())
        findings.extend(self._check_drift(nodes_by_id))
        findings.extend(self._check_contradicting_claims(nodes_by_id))
        findings.extend(self._check_low_title_quality(nodes_by_id))
        findings.extend(self._check_synthesis_ghost_inputs(nodes_by_id))
        findings.extend(self._check_suggested_merges(nodes_by_id))
        findings.extend(self._check_stale_build_history())

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

    def _check_stale_citations(self) -> Iterable[LintFinding]:
        """Markdown links in wiki bodies pointing at non-existent pages."""
        if not self.wiki_dir.exists():
            return
        for md_path in sorted(self.wiki_dir.rglob("*.md")):
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

    def _check_dangling_wiki_links(self) -> Iterable[LintFinding]:
        """`<a href="...">` references inside generated HTML pointing nowhere."""
        if not self.site_dir.exists():
            return
        for html_path in sorted(self.site_dir.rglob("*.html")):
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
        expected: Dict[Tuple[str, str], str] = {}
        for node_id, node in nodes_by_id.items():
            kind = _KIND_FOR_TYPE.get(node.get("type", ""))
            if kind is None or kind == "syntheses":
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
        self, nodes_by_id: Dict[str, dict]
    ) -> Iterable[LintFinding]:
        """Pairs of performance/comparison claims with opposite directional language.

        Precision-first heuristic: for every pair of ``PerformanceClaim`` /
        ``ComparisonClaim`` nodes from *different* sources, we flag the pair
        when one description contains ``outperforms`` and the other contains
        ``is outperformed by`` and they share at least one trigram of
        ``model+benchmark`` content. Tolerating false negatives is fine — the
        check is a sanity probe, not an oracle.
        """
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
                yield LintFinding(
                    severity="info",
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

# Mirrors ``llm_wiki.wiki_projector._KIND_FOR_TYPE`` for nodes whose enum
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
}


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug_for(name: str) -> str:
    cleaned = _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-")
    return cleaned or "node"


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


def _split_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
    """Lightweight frontmatter parser sufficient for synthesis pages.

    Mirrors ``llm_wiki.wiki_store._parse_frontmatter`` for the subset we
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
    end_match = re.search(r"(^|\n)" + re.escape(_FRONTMATTER_DELIM) + r"(\n|\r\n|$)", rest)
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
