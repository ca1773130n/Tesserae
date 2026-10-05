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

0.42 (T7) widens it in four ways, all opt-in, the default run unchanged:

* ``kind="edge"`` clusters the edges of a host EDGE type by their relation
  text (``metadata.relation_label`` / ``relation`` / ``raw_type``, else the
  evidence) and proposes snake_case sub-relations.
* ``embedder=<EmbeddingBackend>`` replaces name-token Jaccard with cosine
  single-link clustering over embeddings — Jaccard cannot see that "SE(3)
  equivariant" and "rotation equivariance" are one idea.
* Every ledger record carries ``kind`` and ``gate`` metrics measured without
  an LLM (instances, distinct sources, cohesion, clustering method), which is
  what a host's promotion gate reads.
* :func:`apply_ledger_to_registry` writes approved proposals into a type
  registry JSON (:mod:`tesserae.type_registry`) as ``shadow`` types under
  their host type. It never retypes a node and never touches ``graph.json``;
  promotion and veto are the host's lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .llm_json import LLMJsonClient
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


_LOG = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class DriftItem:
    """A clusterable member: a node, or an edge seen as ``source|type|target``.

    Duck-types the three :class:`ResearchNode` attributes the clustering and
    prompt code read (``id``, ``name``, ``description``), so edges flow through
    the same functions as nodes. ``source`` is the provenance key the gate
    metrics count distinct values of.
    """

    id: str
    name: str
    description: str = ""
    source: str = ""


def _cosine_unit(vec: Sequence[float]) -> List[float]:
    norm = sum(float(x) * float(x) for x in vec) ** 0.5
    return [float(x) / norm for x in vec] if norm > 0 else [0.0 for _ in vec]


def cluster_by_embedding(
    items: Sequence[Any],
    embedder: Any,
    threshold: float = 0.8,
    min_cluster_size: int = 5,
    text_of=None,
) -> List[List[Any]]:
    """Single-link clustering on cosine similarity of embeddings (0.42, T7).

    Same contract as :func:`cluster_nodes_by_jaccard` — id-sorted processing,
    clusters below ``min_cluster_size`` dropped, largest first — so either can
    feed the proposal step. ``embedder`` is any object with
    ``embed(texts) -> List[List[float]]`` (an ``EmbeddingBackend``). O(n²)
    pairs: callers cap the host's member count (``min_volume`` / top-k keep it
    to a host type at a time).
    """
    ordered = sorted(items, key=lambda n: n.id)
    if not ordered:
        return []
    text_fn = text_of or (lambda n: n.name)
    vectors = [_cosine_unit(v) for v in embedder.embed([text_fn(n) for n in ordered])]
    if len(vectors) != len(ordered):
        raise ValueError(
            f"embedder returned {len(vectors)} vectors for {len(ordered)} texts"
        )
    parent = list(range(len(ordered)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(ordered)):
        vi = vectors[i]
        for j in range(i + 1, len(ordered)):
            sim = sum(a * b for a, b in zip(vi, vectors[j]))
            if sim >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    buckets: Dict[int, List[Any]] = {}
    for i, item in enumerate(ordered):
        buckets.setdefault(find(i), []).append(item)
    clusters = [c for c in buckets.values() if len(c) >= min_cluster_size]
    clusters.sort(key=lambda c: (-len(c), c[0].id))
    return clusters


def edge_drift_items(graph: ResearchGraph, edge_type: str) -> List[DriftItem]:
    """The edges of ``edge_type`` as clusterable items.

    The text is the most specific relation label the edge carries — an open
    label an extractor attached (``metadata.relation_label``), a registry
    relation (``metadata.relation``), the type a lenient load mapped away
    (``metadata.raw_type``) — else its evidence. Edges with no text at all
    carry no signal about a finer relation and are skipped.
    """
    items: List[DriftItem] = []
    for edge in graph.edges:
        if edge.type != edge_type:
            continue
        md = edge.metadata or {}
        label = ""
        for key in ("relation_label", "relation", "raw_type"):
            if str(md.get(key) or "").strip():
                label = str(md[key]).strip()
                break
        text = label or str(edge.evidence or "").strip()
        if not text:
            continue
        items.append(
            DriftItem(
                id=f"{edge.source}|{edge.type}|{edge.target}",
                name=text[:200],
                description=str(edge.evidence or "")[:200] if label else "",
                source=edge.source,
            )
        )
    return items


def gate_metrics(
    cluster: Sequence[Any],
    *,
    clustering: str,
    embedder: Any = None,
    cohesion_sample: int = 60,
) -> Dict[str, Any]:
    """LLM-free evidence for a promotion gate, stored on each ledger record.

    ``n_instances`` and ``n_sources`` (distinct source documents for nodes,
    distinct source nodes for edges) are the volume test; ``cohesion`` is the
    mean pairwise similarity under the clustering's own metric over the first
    ``cohesion_sample`` members (id order), a cheap signal of how much one
    definition can cover the cluster.
    """
    members = sorted(cluster, key=lambda n: n.id)
    sources = {
        str(getattr(n, "source", "") or getattr(n, "source_path", "") or "")
        for n in members
    }
    sources.discard("")
    sample = members[: max(2, int(cohesion_sample))]
    sims: List[float] = []
    if clustering == "embedding" and embedder is not None and len(sample) > 1:
        vecs = [_cosine_unit(v) for v in embedder.embed([n.name for n in sample])]
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                sims.append(sum(a * b for a, b in zip(vecs[i], vecs[j])))
    elif len(sample) > 1:
        toks = [_tokenize(n.name) for n in sample]
        for i in range(len(toks)):
            for j in range(i + 1, len(toks)):
                sims.append(_jaccard(toks[i], toks[j]))
    return {
        "clustering": clustering,
        "cohesion": round(sum(sims) / len(sims), 4) if sims else None,
        "n_instances": len(members),
        "n_sources": len(sources),
    }


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


_EDGE_SYSTEM_PROMPT = (
    "You are an ontology engineer assisting the Tesserae knowledge-graph "
    "compiler. The user will show you a cluster of RELATIONS (edges) that all "
    "share the same coarse edge type, each given by its relation label or "
    "evidence. Propose 1 to 3 candidate sub-relations using the EDC "
    "(Extract-Define-Canonicalize) pattern: each must be a snake_case "
    "relation name, a one-line definition (<= 100 chars), and three example "
    "member ids drawn from the cluster."
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


def _snake_case(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return "_".join(p.lower() for p in re.split(r"[^A-Za-z0-9]+", spaced) if p)


def _coerce_proposals(
    payload: object, valid_ids: set[str], kind: str = "node"
) -> List[dict]:
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
        if kind == "edge":
            # Edge vocabulary is snake_case (``uses_metric``, ``improves_on``).
            name = _snake_case(name)
        else:
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
    kind: str = "node",
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
        system=_EDGE_SYSTEM_PROMPT if kind == "edge" else _SYSTEM_PROMPT,
        user=_build_user_prompt(host_type, cluster),
        schema_name=(
            "schema-drift-subrelations-v1" if kind == "edge" else "schema-drift-subtypes-v1"
        ),
        # The cluster hash is no longer load-bearing: llm_json now digests the
        # prompt itself, so two clusters cannot collide however the key is
        # spelled. It stays because it was RIGHT — this caller was the only one
        # that saw the old hazard ("a host-only key serves cluster 1's answer to
        # every other cluster of the same host, and _coerce_proposals then
        # strips the foreign example ids, leaving proposals that look plausible
        # and cite nothing") and defended against it by hand. Ten callers that
        # did not are what this fix is for. Now it reads as a namespace.
        cache_key=(
            f"schema-drift:edge:{host_type}:{key}"
            if kind == "edge"
            else f"schema-drift:{host_type}:{key}"
        ),
    )
    if payload is None:
        # Transient LLM failure (backend error / unparseable JSON). Do NOT
        # cache the empty result — otherwise the next run treats this cluster
        # as "already processed" and skips the LLM forever until a human
        # deletes the cache file. Return [] so this run still renders.
        _LOG.warning(
            "schema-drift: LLM returned no payload for %s cluster %s "
            "(size=%d); skipping cache write so the next run retries.",
            host_type,
            key[:12],
            len(cluster),
        )
        return []
    proposals = _coerce_proposals(payload, valid_ids, kind)
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
    #: Why this host produced no clusters, when the reason is not "clustering
    #: ran and found none". Empty means clustering actually ran.
    skipped_reason: str = ""
    #: ``node`` or ``edge`` (0.42, T7) — what ``host_type`` names.
    kind: str = "node"
    #: ``jaccard`` or ``embedding`` — how ``clusters`` were formed.
    clustering: str = "jaccard"
    #: Per-cluster gate metrics, parallel to ``clusters``.
    gates: List[Dict[str, Any]] = field(default_factory=list)


def render_report(
    reports: Sequence[HostTypeReport],
    *,
    min_cluster_size: int = 5,
) -> str:
    """Render the human-readable schema-drift markdown report.

    ``min_cluster_size`` is interpolated into the "no clusters found"
    message so the report stays truthful when callers pass a non-default
    threshold (or when a host gets filtered out for other reasons).
    """
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
            # Distinguish "clustering ran and found nothing" from "clustering
            # never ran". A host below --min-volume is skipped BEFORE
            # clustering, and reporting it as "no clusters of size >= N found"
            # was a statement about a computation that never happened — which
            # the same run's own ledger then contradicted.
            lines.append(
                f"_{report.skipped_reason}_"
                if report.skipped_reason
                else f"_No clusters of size >= {min_cluster_size} found; skipping._"
            )
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


def apply_schema_drift(
    graph: ResearchGraph,
    proposals: Sequence[dict],
) -> ResearchGraph:
    """Rename ``node.type`` for APPROVED schema-drift proposals only.

    ``proposals`` are proposal dicts shaped like those produced inside
    :class:`HostTypeReport` clusters (``{"name", "description", "examples"}``).
    For the apply path each proposal additionally carries:

      * an explicit ``approved`` key — destructive type changes are opt-in
        (Pitfall 4); a missing or falsy ``approved`` means NOT approved and the
        proposal is skipped.
      * the target node ids — taken from ``examples`` (the member node ids the
        proposal applies to) or an explicit ``node_ids`` / ``ids`` list.
      * the new type — ``proposed_type`` (preferred) or ``name``; it must be a
        real :class:`ResearchNodeType` enum value (matched by enum value or
        name). Proposals naming an unknown type are skipped + logged.

    Pure + deterministic: returns a NEW :class:`ResearchGraph` with renamed node
    types (edges unchanged). An empty list — or one with no approved, resolvable
    proposals — returns ``graph`` unchanged (byte-identical no-op).

    The compile-time wiring + the ``TESSERAE_SCHEMA_DRIFT_APPLY`` env gate live
    in ``project.py`` (plan 05-03); this function only provides the transform.
    """
    if not proposals:
        return graph

    # Build id -> new ResearchNodeType from approved, resolvable proposals.
    valid_by_value = {t.value: t for t in ResearchNodeType}
    valid_by_name = {t.name: t for t in ResearchNodeType}
    retype: Dict[str, ResearchNodeType] = {}
    skipped = 0
    for prop in proposals:
        if not isinstance(prop, dict) or not prop.get("approved"):
            continue
        raw_type = prop.get("proposed_type") or prop.get("name")
        if not raw_type:
            skipped += 1
            continue
        new_type = valid_by_value.get(str(raw_type)) or valid_by_name.get(str(raw_type))
        if new_type is None:
            _LOG.warning(
                "apply_schema_drift: proposed type %r is not a ResearchNodeType "
                "enum value; skipping.",
                raw_type,
            )
            skipped += 1
            continue
        ids = prop.get("node_ids") or prop.get("ids") or prop.get("examples") or []
        for node_id in ids:
            retype[str(node_id)] = new_type

    if not retype:
        return graph

    from dataclasses import replace

    new_nodes: List[ResearchNode] = []
    for node in graph.nodes:
        target = retype.get(node.id)
        if target is not None and target != node.type:
            new_nodes.append(replace(node, type=target))
        else:
            new_nodes.append(node)

    return ResearchGraph(nodes=new_nodes, edges=list(graph.edges))


#: Where human-reviewable sub-type proposals live. A SIDECAR, deliberately not
#: ``graph.json`` node metadata: compile republishes graph.json wholesale from
#: the producer-built graph, while the incremental path carries non-stale prior
#: nodes through verbatim — so a metadata key written out-of-band would survive
#: an incremental compile and vanish on a full one. Mode-dependent presence of
#: an LLM-derived field is exactly the byte-idempotence blind spot this repo
#: has hit four times. The key NAME the roadmap asked for (``proposed_type``)
#: lives inside each record.
PROPOSAL_LEDGER_NAME = "schema-drift-proposals.json"


def build_proposal_ledger(reports: Sequence["HostTypeReport"]) -> List[dict]:
    """Flatten host reports into records ``apply_schema_drift`` consumes AS-IS.

    Each record carries exactly the keys the apply pass already reads —
    ``approved`` (the human gate), ``proposed_type`` (the human-editable
    target) and ``node_ids`` (the WHOLE cluster, not the <=3 LLM examples, so
    approving one record retypes the cluster it was derived from rather than
    three samples of it). ``(host_type, cluster_key, name)`` is the merge
    identity.
    """
    records: List[dict] = []
    for report in reports:
        kind = getattr(report, "kind", "node")
        gates = getattr(report, "gates", None) or []
        for index, (cluster, proposals) in enumerate(report.clusters):
            cluster_key = _cluster_cache_key(cluster)
            node_ids = sorted(n.id for n in cluster)
            for proposal in proposals:
                name = str(proposal.get("name") or "").strip()
                if not name:
                    continue
                record = {
                    "approved": False,
                    "cluster_key": cluster_key,
                    "description": str(proposal.get("description") or ""),
                    "host_type": report.host_type,
                    "name": name,
                    "node_ids": node_ids,
                    "proposed_type": name,
                }
                # Only stamped for the 0.42 shapes, so a default node run's
                # ledger keeps exactly its 0.41 bytes.
                if kind != "node":
                    record["kind"] = kind
                if index < len(gates) and gates[index]:
                    record["gate"] = dict(gates[index])
                records.append(record)
    records.sort(key=lambda r: (r["host_type"], r["cluster_key"], r["name"]))
    return records


def _merge_proposal_ledger(existing: List[dict], fresh: List[dict]) -> List[dict]:
    """Fresh findings, with every human decision preserved.

    A re-run must never silently revert an ``approved: true`` — the human's
    edit and the enum edit it pairs with are separated in time, so the revert
    would be invisible until a later compile quietly retyped nothing. Records
    this run did not rediscover are RETAINED rather than dropped: the cluster
    key is a hash over member ids, so ingesting one document remints it and
    would otherwise orphan the decision attached to the old key.
    """
    by_identity = {
        (str(r.get("host_type")), str(r.get("cluster_key")), str(r.get("name"))): dict(r)
        for r in existing
        if isinstance(r, dict)
    }
    for record in fresh:
        identity = (record["host_type"], record["cluster_key"], record["name"])
        prior = by_identity.get(identity)
        if prior is None:
            by_identity[identity] = dict(record)
            continue
        merged = dict(record)
        # The two human-editable fields win over anything this run produced.
        merged["approved"] = bool(prior.get("approved"))
        if prior.get("proposed_type"):
            merged["proposed_type"] = prior["proposed_type"]
        by_identity[identity] = merged
    return [by_identity[k] for k in sorted(by_identity)]


def write_proposal_ledger(tesserae_dir: Path, reports: Sequence["HostTypeReport"]) -> Path:
    """Merge this run's proposals into the ledger and persist it."""
    path = Path(tesserae_dir) / PROPOSAL_LEDGER_NAME
    # Same read contract as ``read_proposal_ledger`` — an unreadable ledger
    # must not abort a drift run that has already written schema-drift.md.
    existing: List[dict] = read_proposal_ledger(tesserae_dir) if path.exists() else []
    merged = _merge_proposal_ledger(existing, build_proposal_ledger(reports))
    _atomic_write(
        path,
        json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return path


def read_proposal_ledger(tesserae_dir: Path) -> List[dict]:
    """The ledger, or ``[]`` when absent/unreadable. Never raises.

    ``UnicodeDecodeError`` is caught explicitly: it is NOT an ``OSError``, and
    a ledger saved as UTF-16 by an editor — or half-written — would otherwise
    raise straight out of a function whose whole contract is that it does not.
    """
    path = Path(tesserae_dir) / PROPOSAL_LEDGER_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []


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
    kind: str = "node",
    host_edge_types: Optional[Iterable[str]] = None,
    embedder: Any = None,
    cosine_threshold: float = 0.8,
) -> Tuple[Path, List[HostTypeReport]]:
    """Run the EDC pass and write the report to ``schema-drift.md``.

    ``kind="edge"`` (0.42, T7) analyzes ``host_edge_types`` (default
    ``["references"]``, the generic fallback edge) instead of node types.
    ``embedder`` switches clustering from name-token Jaccard to embedding
    cosine at ``cosine_threshold``. Every report carries LLM-free gate
    metrics, which the ledger records keep.

    Returns ``(report_path, host_type_reports)``.
    """
    if kind not in ("node", "edge"):
        raise ValueError(f"schema drift kind must be 'node' or 'edge', got {kind!r}")
    clustering = "embedding" if embedder is not None else "jaccard"
    tesserae_dir = Path(tesserae_dir)
    cache_dir = tesserae_dir / "schema_drift_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    host_names: List[str]
    if kind == "edge":
        host_names = sorted(set(host_edge_types or ["references"]))
        unknown = [h for h in host_names if h not in ALLOWED_EDGE_TYPES]
        if unknown:
            raise ValueError(f"unknown host edge type(s): {unknown}")
    elif host_types is None:
        host_names = [ResearchNodeType.SOURCE_DOCUMENT.value]
    else:
        host_names = [ResearchNodeType(h).value if not isinstance(h, ResearchNodeType) else h.value for h in host_types]

    reports: List[HostTypeReport] = []
    for host_name in host_names:
        if kind == "edge":
            members: List[Any] = edge_drift_items(graph, host_name)
        else:
            members = _nodes_of_type(graph, ResearchNodeType(host_name))
        report = HostTypeReport(
            host_type=host_name,
            member_count=len(members),
            kind=kind,
            clustering=clustering,
        )
        if len(members) < min_volume:
            report.skipped_reason = (
                f"Not clustered: {len(members)} member(s) is below --min-volume "
                f"{min_volume}, so no clustering was attempted for this host type."
            )
            reports.append(report)
            continue
        if embedder is not None:
            clusters = cluster_by_embedding(
                members,
                embedder,
                threshold=cosine_threshold,
                min_cluster_size=min_cluster_size,
            )[:top_k_clusters]
        else:
            clusters = cluster_nodes_by_jaccard(
                members,
                threshold=jaccard_threshold,
                min_cluster_size=min_cluster_size,
            )[:top_k_clusters]
        cache_name = f"edge.{host_name}.json" if kind == "edge" else f"{host_name}.json"
        cache_path = cache_dir / cache_name
        cache = _load_cache(cache_path)
        for cluster in clusters:
            proposals = propose_subtypes_for_cluster(
                cluster, host_type=host_name, llm=llm, cache=cache, kind=kind
            )
            report.clusters.append((cluster, proposals))
            report.gates.append(
                gate_metrics(cluster, clustering=clustering, embedder=embedder)
            )
        _save_cache(cache_path, cache)
        reports.append(report)

    report_path = tesserae_dir / "schema-drift.md"
    _atomic_write(
        report_path,
        render_report(reports, min_cluster_size=min_cluster_size) + "\n",
    )
    # The machine-readable half of the same run: the markdown is for a human
    # to read, the ledger is what the lint probe surfaces and what the apply
    # pass consumes once a human sets ``approved``.
    write_proposal_ledger(tesserae_dir, reports)
    return report_path, reports


# ---------------------------------------------------------------------------
# Registry apply (0.42, T7)
# ---------------------------------------------------------------------------


def apply_ledger_to_registry(
    records: Sequence[dict],
    registry_path: Union[str, Path],
) -> List[dict]:
    """Write APPROVED ledger proposals into a type-registry JSON file.

    Each approved record becomes (or updates) a :class:`TypeSpec` named by its
    ``proposed_type`` under ``core_parent = host_type``, with status
    ``shadow`` — rows may be written under it, and it walks at its parent's
    weight until the host's gate promotes it. A spec the file already has keeps
    its status unless that status is ``proposed`` (a later lifecycle decision —
    promoted, vetoed, retired — is the host's, and a re-run must never revert
    it, exactly as :func:`_merge_proposal_ledger` never reverts ``approved``).

    It NEVER retypes a node and never writes ``graph.json``. A proposal whose
    name is already core vocabulary is skipped with a warning: promoting into
    the enum is a release, not a ledger edit. Returns the specs written, as
    dicts. The file is written atomically.
    """
    from .type_registry import TypeRegistry, TypeSpec, register_types

    path = Path(registry_path)
    registry = TypeRegistry()
    if path.exists():
        register_types(path, registry=registry)
    written: List[dict] = []
    for record in records:
        if not isinstance(record, dict) or not record.get("approved"):
            continue
        name = str(record.get("proposed_type") or record.get("name") or "").strip()
        host = str(record.get("host_type") or "").strip()
        kind = str(record.get("kind") or "node")
        if not name or not host:
            continue
        core = ALLOWED_EDGE_TYPES if kind == "edge" else ALLOWED_NODE_TYPES
        if name in core:
            _LOG.warning(
                "apply_ledger_to_registry: %r is already a core %s type; "
                "skipping (core promotion is a release, not a registry edit).",
                name,
                kind,
            )
            continue
        prior = registry.get(name)
        status = "shadow"
        if prior is not None and prior.status != "proposed":
            status = prior.status
        spec = TypeSpec(
            name=name,
            kind=kind,
            core_parent=host,
            definition=str(record.get("description") or (prior.definition if prior else "")),
            view=prior.view if prior else None,
            ppr_weight=prior.ppr_weight if prior else None,
            extractable=prior.extractable if prior else True,
            inverse_of=prior.inverse_of if prior else None,
            symmetric=prior.symmetric if prior else False,
            acyclic=prior.acyclic if prior else False,
            status=status,
        )
        registry.register(spec)
        written.append(spec.to_dict())
    _atomic_write(
        path,
        json.dumps(registry.to_payload(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return written
