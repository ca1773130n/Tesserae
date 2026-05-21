"""EDC-style schema-drift pass over a compiled Tesserae graph.

Given the compiled ``.tesserae/graph.json``, this module clusters
member nodes of a configured "host" type (default ``SourceDocument``)
by Jaccard similarity over their name tokens, and asks an LLM via
:class:`tesserae.llm_json.LLMJsonClient` to propose 1-3 candidate
sub-types per cluster — PascalCase enum name, one-line description,
and three example member node ids.

Output:

* A human-readable markdown report at ``.tesserae/schema-drift.md``
  with one section per host type, listing proposed sub-types, member
  previews, and a copy-pasteable ``Suggested enum additions`` block.
* A per-host cache at ``.tesserae/schema_drift_cache/<TYPE>.json``
  keyed by the SHA-256 of the sorted member-id list of each cluster,
  so re-running on an unchanged graph skips the LLM entirely.

This is a *reporting* layer — it never mutates ``ResearchNodeType``
or the graph. Promoting an entry to the enum is a human edit on
``tesserae/research_graph.py``.

Designed for the EDC blueprint (Zhang et al., EMNLP 2024) but
scaled down to a single-host pass that fits in a quick win.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .llm_json import LLMJsonClient
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType


# ---------------------------------------------------------------------------
# Tokenization + clustering
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on",
        "with", "by", "is", "are", "be", "this", "that", "from", "as",
        "at", "it", "its", "into", "via", "using",
    }
)


def _tokenize(name: str) -> frozenset[str]:
    """Lowercase alphanumeric tokens with stopwords removed."""
    return frozenset(
        tok.lower()
        for tok in _TOKEN_RE.findall(name)
        if tok.lower() not in _STOPWORDS and len(tok) > 1
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cluster_nodes_by_jaccard(
    nodes: Sequence[ResearchNode],
    threshold: float = 0.34,
    min_cluster_size: int = 5,
) -> List[List[ResearchNode]]:
    """Single-link agglomerative clustering over name tokens.

    Two nodes share a cluster if the Jaccard similarity of their
    token sets is at least ``threshold``. Order-independent: nodes
    are processed in id-sorted order so callers get stable output.
    Clusters smaller than ``min_cluster_size`` are dropped from the
    returned list.
    """
    items = sorted(nodes, key=lambda n: n.id)
    token_cache: Dict[str, frozenset[str]] = {n.id: _tokenize(n.name) for n in items}

    # Union-find
    parent: Dict[str, str] = {n.id: n.id for n in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, n1 in enumerate(items):
        t1 = token_cache[n1.id]
        if not t1:
            continue
        for n2 in items[i + 1 :]:
            t2 = token_cache[n2.id]
            if not t2:
                continue
            if _jaccard(t1, t2) >= threshold:
                union(n1.id, n2.id)

    buckets: Dict[str, List[ResearchNode]] = {}
    for n in items:
        buckets.setdefault(find(n.id), []).append(n)

    clusters = [c for c in buckets.values() if len(c) >= min_cluster_size]
    # Stable order: largest first, ties broken by id of the seed.
    clusters.sort(key=lambda c: (-len(c), c[0].id))
    return clusters


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cluster_cache_key(cluster: Sequence[ResearchNode]) -> str:
    """SHA-256 over the sorted member ids — stable across re-runs."""
    payload = "\n".join(sorted(n.id for n in cluster))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write with PID+random tmp suffix (matches batch manifest pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f".{os.getpid()}.{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}.tmp"
    tmp = path.with_name(path.name + suffix)
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _load_cache(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_cache(path: Path, cache: Dict[str, dict]) -> None:
    _atomic_write(path, json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# LLM proposal
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are an ontology engineer assisting the Tesserae knowledge-graph "
    "compiler. The user will show you a cluster of nodes that all share "
    "the same coarse type. Propose 1 to 3 candidate sub-types using the "
    "EDC (Extract-Define-Canonicalize) pattern: each sub-type must be a "
    "PascalCase enum name, a one-line description (<= 100 chars), and "
    "three example member node ids drawn from the cluster."
)


def _build_user_prompt(host_type: str, cluster: Sequence[ResearchNode]) -> str:
    preview = []
    for node in cluster[:25]:
        desc = (node.description or "").splitlines()[0] if node.description else ""
        if len(desc) > 120:
            desc = desc[:117] + "..."
        preview.append(
            f"- id={node.id} name={node.name!r}"
            + (f" desc={desc!r}" if desc else "")
        )
    members_block = "\n".join(preview)
    return (
        f"Host type: {host_type}\n"
        f"Cluster size: {len(cluster)} members\n"
        f"Members (up to 25 shown):\n"
        f"{members_block}\n\n"
        f"Return a JSON object: "
        f'{{"sub_types": [{{"name": "PascalCase", "description": "...", '
        f'"examples": ["id1", "id2", "id3"]}}]}}'
    )


def _coerce_proposals(payload: object, valid_ids: set[str]) -> List[dict]:
    """Validate and clean an LLM proposal payload."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("sub_types") or payload.get("subtypes") or []
    if not isinstance(raw, list):
        return []
    cleaned: List[dict] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or not name[:1].isalpha():
            continue
        # PascalCase guard: strip whitespace/punct, capitalize segments.
        name = "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", name) if part)
        if not name:
            continue
        description = str(item.get("description") or "").strip()[:200]
        examples_raw = item.get("examples") or []
        examples = [str(e) for e in examples_raw if isinstance(e, (str, int))]
        # Only keep example ids that actually exist in the cluster.
        examples = [e for e in examples if e in valid_ids][:3]
        cleaned.append({"name": name, "description": description, "examples": examples})
    return cleaned


def propose_subtypes_for_cluster(
    cluster: Sequence[ResearchNode],
    *,
    host_type: str,
    llm: LLMJsonClient,
    cache: Dict[str, dict],
) -> List[dict]:
    """Look up or fetch sub-type proposals for ``cluster``.

    Returns a list of ``{"name", "description", "examples"}`` dicts.
    Mutates ``cache`` in-place; caller is responsible for persisting.
    """
    key = _cluster_cache_key(cluster)
    cached = cache.get(key)
    if isinstance(cached, dict):
        proposals = cached.get("proposals")
        if isinstance(proposals, list):
            return proposals  # cache hit — skip LLM
    valid_ids = {n.id for n in cluster}
    payload = llm.complete_json(
        system=_SYSTEM_PROMPT,
        user=_build_user_prompt(host_type, cluster),
        schema_name="schema-drift-subtypes-v1",
        cache_key=f"schema-drift:{host_type}",
    )
    proposals = _coerce_proposals(payload, valid_ids)
    cache[key] = {
        "host_type": host_type,
        "cluster_size": len(cluster),
        "proposals": proposals,
    }
    return proposals


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


@dataclass
class HostTypeReport:
    host_type: str
    member_count: int
    clusters: List[Tuple[List[ResearchNode], List[dict]]] = field(default_factory=list)


def render_report(reports: Sequence[HostTypeReport]) -> str:
    """Render the human-readable schema-drift markdown report."""
    lines: List[str] = [
        "# Schema-Drift Report",
        "",
        "EDC-style sub-type proposals for high-volume host types.",
        "Each cluster was grouped by Jaccard similarity on node-name tokens, then an LLM proposed candidate PascalCase sub-types.",
        "",
        "Promotion is a human decision — copy entries from the `Suggested enum additions` block at the end into `tesserae/research_graph.py:ResearchNodeType` to adopt.",
        "",
    ]
    all_additions: List[Tuple[str, str, str]] = []  # (host_type, name, description)
    for report in reports:
        lines.append(f"## {report.host_type} ({report.member_count} members)")
        lines.append("")
        if not report.clusters:
            lines.append("_No clusters of size >= 5 found; skipping._")
            lines.append("")
            continue
        for cluster_idx, (cluster, proposals) in enumerate(report.clusters, start=1):
            preview = ", ".join(f"`{n.name}`" for n in cluster[:5])
            more = "" if len(cluster) <= 5 else f" (+{len(cluster) - 5} more)"
            lines.append(f"### Cluster {cluster_idx} ({len(cluster)} members)")
            lines.append("")
            lines.append(f"Members: {preview}{more}")
            lines.append("")
            if not proposals:
                lines.append("_LLM returned no usable proposals._")
                lines.append("")
                continue
            for prop in proposals:
                name = prop.get("name", "")
                desc = prop.get("description", "")
                examples = prop.get("examples") or []
                lines.append(f"- **{name}** — {desc}")
                if examples:
                    ex_str = ", ".join(f"`{e}`" for e in examples)
                    lines.append(f"  - Examples: {ex_str}")
                all_additions.append((report.host_type, name, desc))
            lines.append("")
    lines.append("## Suggested enum additions")
    lines.append("")
    if not all_additions:
        lines.append("_No candidate sub-types were proposed in this run._")
        lines.append("")
    else:
        lines.append("Copy into `tesserae/research_graph.py:ResearchNodeType`:")
        lines.append("")
        lines.append("```python")
        seen: set[str] = set()
        for host, name, desc in all_additions:
            if name in seen:
                continue
            seen.add(name)
            screaming = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
            comment = f"  # proposed by schema-drift under {host}: {desc}" if desc else f"  # proposed under {host}"
            lines.append(f'    {screaming} = "{name}"{comment}')
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _nodes_of_type(graph: ResearchGraph, host_type: ResearchNodeType) -> List[ResearchNode]:
    return [n for n in graph.nodes if n.type == host_type]


def analyze_schema_drift(
    graph: ResearchGraph,
    *,
    tesserae_dir: Path,
    llm: LLMJsonClient,
    host_types: Optional[Iterable[ResearchNodeType]] = None,
    min_volume: int = 10,
    top_k_clusters: int = 5,
    jaccard_threshold: float = 0.34,
    min_cluster_size: int = 5,
) -> Tuple[Path, List[HostTypeReport]]:
    """Run the EDC pass and write the report to ``schema-drift.md``.

    Returns ``(report_path, host_type_reports)``.
    """
    tesserae_dir = Path(tesserae_dir)
    cache_dir = tesserae_dir / "schema_drift_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    hosts: List[ResearchNodeType]
    if host_types is None:
        hosts = [ResearchNodeType.SOURCE_DOCUMENT]
    else:
        hosts = list(host_types)

    reports: List[HostTypeReport] = []
    for host in hosts:
        members = _nodes_of_type(graph, host)
        report = HostTypeReport(host_type=host.value, member_count=len(members))
        if len(members) < min_volume:
            reports.append(report)
            continue
        clusters = cluster_nodes_by_jaccard(
            members,
            threshold=jaccard_threshold,
            min_cluster_size=min_cluster_size,
        )[:top_k_clusters]
        cache_path = cache_dir / f"{host.value}.json"
        cache = _load_cache(cache_path)
        for cluster in clusters:
            proposals = propose_subtypes_for_cluster(
                cluster, host_type=host.value, llm=llm, cache=cache
            )
            report.clusters.append((cluster, proposals))
        _save_cache(cache_path, cache)
        reports.append(report)

    report_path = tesserae_dir / "schema-drift.md"
    _atomic_write(report_path, render_report(reports) + "\n")
    return report_path, reports
