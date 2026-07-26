"""Canonicalization and review queue utilities for ResearchGraph.

This module keeps ontology extraction and duplicate management separate:
automatic canonicalization handles high-confidence alias matches, while ambiguous
similar concepts are emitted as review items instead of being silently merged.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


CANONICALIZABLE_TYPES = {
    ResearchNodeType.RESEARCH_FIELD,
    ResearchNodeType.RESEARCH_TOPIC,
    ResearchNodeType.PROBLEM_AREA,
    ResearchNodeType.APPROACH_FAMILY,
    ResearchNodeType.MODEL,
    ResearchNodeType.DATASET,
    ResearchNodeType.BENCHMARK,
    ResearchNodeType.METRIC,
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


@dataclass(frozen=True)
class ReviewItem:
    id: str
    left_node_id: str
    right_node_id: str
    left_name: str
    right_name: str
    node_type: str
    reason: str
    score: float
    status: str = "pending"

    def model_dump(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewDecision:
    item_id: str
    action: str  # merge | keep_separate
    canonical_node_id: Optional[str] = None


@dataclass
class CanonicalizationResult:
    graph: ResearchGraph
    merged_nodes: Dict[str, str] = field(default_factory=dict)
    review_items: List[ReviewItem] = field(default_factory=list)
    # Why the semantic pass did / did not contribute. Same vocabulary as
    # federation.add_semantic_links so a skip always says WHY rather than
    # looking like "ran and found nothing".
    stats: Dict[str, object] = field(default_factory=dict)

    def review_queue(self) -> "ReviewQueue":
        return ReviewQueue(self.review_items)


class GraphCanonicalizer:
    def __init__(
        self,
        similarity_threshold: float = 0.60,
        *,
        semantic: bool = False,
        embedding_backend: Optional[object] = None,
        embedding_min_cosine: float = 0.60,
        embedding_top_k: int = 2,
        max_semantic_items: int = 200,
        max_block: int = 1500,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.semantic = semantic
        self.embedding_backend = embedding_backend
        self.embedding_min_cosine = embedding_min_cosine
        self.embedding_top_k = embedding_top_k
        self.max_semantic_items = max_semantic_items
        self.max_block = max_block

    def canonicalize(self, graph: ResearchGraph) -> CanonicalizationResult:
        canonical_for = self._build_alias_canonical_map(graph.nodes)
        merged_nodes: Dict[str, str] = {}
        canonical_groups: Dict[str, List[ResearchNode]] = {}

        for node in graph.nodes:
            canonical_id = canonical_for.get(node.id, node.id)
            if canonical_id != node.id:
                merged_nodes[node.id] = canonical_id
            canonical_groups.setdefault(canonical_id, []).append(node)

        new_nodes = [merge_node_group(canonical_id, group) for canonical_id, group in canonical_groups.items()]
        node_ids = {node.id for node in new_nodes}
        new_edges = rewire_edges(graph.edges, {node_id: canonical_for.get(node_id, node_id) for node_id in [node.id for node in graph.nodes]}, node_ids)
        canonicalized_graph = ResearchGraph(nodes=new_nodes, edges=new_edges)
        review_items = self._build_review_items(canonicalized_graph.nodes)
        stats: Dict[str, object] = {}
        if self.semantic:
            semantic_items, stats = self._build_embedding_review_items(canonicalized_graph.nodes, review_items)
            # APPENDED, never interleaved: today's string items keep today's
            # bytes and order, so turning the flag on is purely additive.
            review_items = review_items + semantic_items
        return CanonicalizationResult(
            graph=canonicalized_graph,
            merged_nodes=merged_nodes,
            review_items=review_items,
            stats=stats,
        )

    def _build_alias_canonical_map(self, nodes: Sequence[ResearchNode]) -> Dict[str, str]:
        alias_owner: Dict[Tuple[ResearchNodeType, str], ResearchNode] = {}
        canonical_for: Dict[str, str] = {}
        # Prefer richer canonical nodes (nodes that already carry aliases) over
        # short alias-only nodes such as `3DGS`.
        ordered_nodes = sorted(nodes, key=lambda node: (node.type.value, -len(node.aliases), len(node.name), node.name.lower()))

        for node in ordered_nodes:
            if node.type not in CANONICALIZABLE_TYPES:
                continue
            own_terms = [node.name, *node.aliases]
            matched_owner: Optional[ResearchNode] = None
            for term in own_terms:
                owner = alias_owner.get((node.type, normalize_key(term)))
                if owner and owner.id != node.id:
                    matched_owner = owner
                    break
            if matched_owner:
                canonical_for[node.id] = matched_owner.id
                for term in own_terms:
                    alias_owner.setdefault((node.type, normalize_key(term)), matched_owner)
            else:
                canonical_for[node.id] = node.id
                for term in own_terms:
                    alias_owner.setdefault((node.type, normalize_key(term)), node)
        return canonical_for

    def _build_review_items(self, nodes: Sequence[ResearchNode]) -> List[ReviewItem]:
        items: List[ReviewItem] = []
        comparable = [node for node in nodes if node.type in CANONICALIZABLE_TYPES]

        # Build inverted index: token -> list of (index, node) for O(1) candidate lookup.
        token_to_indices: Dict[str, List[int]] = {}
        for idx, node in enumerate(comparable):
            for word in node.name.lower().split():
                if len(word) >= 3:
                    token_to_indices.setdefault(word, []).append(idx)

        # Restrict comparisons to pairs sharing at least one significant token.
        candidate_pairs: set = set()
        for indices in token_to_indices.values():
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    i, j = indices[a], indices[b]
                    if i > j:
                        i, j = j, i
                    candidate_pairs.add((i, j))

        for i, j in candidate_pairs:
            left, right = comparable[i], comparable[j]
            if left.type != right.type:
                continue
            score = name_similarity(left.name, right.name)
            if score < self.similarity_threshold:
                continue
            if normalize_key(left.name) == normalize_key(right.name):
                continue
            items.append(
                ReviewItem(
                    id=stable_review_id(left.id, right.id, "similar_name"),
                    left_node_id=left.id,
                    right_node_id=right.id,
                    left_name=left.name,
                    right_name=right.name,
                    node_type=left.type.value,
                    reason="similar_name",
                    score=round(score, 4),
                )
            )
        return sorted(items, key=_review_sort_key)

    def _build_embedding_review_items(
        self,
        nodes: Sequence[ResearchNode],
        string_items: Sequence[ReviewItem],
    ) -> Tuple[List[ReviewItem], Dict[str, object]]:
        """Second CANDIDATE source for the review queue: embedding kNN.

        Emits review items only — never a merge. The token-overlap pass above
        cannot pair 'Edwin Aldrin' with 'Buzz Aldrin' (no shared token), which
        is exactly the duplicate class a human reviewer needs surfaced.

        ponytail: candidates only, verdicts stay human — and that is a measured
        decision, not caution. Against the backend this repo ships
        (model2vec:minishlab/potion-base-8M), name-only cosines are
        'Edwin Aldrin'~'Buzz Aldrin' 0.665 (the TRUE merge) versus 'GPT-4'~'GPT-3'
        0.959, 'Llama 2'~'Llama 3' 0.957, 'BERT-base'~'BERT-large' 0.689 (all
        FALSE merges). Adding descriptions makes it worse:
        'Buzz Aldrin'~'Neil Armstrong' 0.792 > 'Edwin Aldrin'~'Buzz Aldrin' 0.758.
        Every dangerous pair outranks the target pair, so NO cosine threshold
        admits the case we want and excludes version/family siblings — a static
        token-mean embedding encodes "same family", and intra-family variants are
        precisely what must never fuse. Ceiling: static embeddings rank topical
        proximity, not identity. Upgrade path: LLM adjudication of this <=200-item
        band, ONE pair per call with clean context (evidence originating outside
        the graph), not a raw-cosine auto-merge band.
        """

        # numpy is imported only AFTER the stub-skip so `--canonicalize-semantic`
        # on a base install degrades cleanly instead of crashing on the import.
        from .retrieval.hybrid import HashEmbeddingBackend, active_embedding_backend

        backend = self.embedding_backend or active_embedding_backend()
        backend_name = getattr(backend, "name", type(backend).__name__)
        stats: Dict[str, object] = {"semantic_backend": backend_name, "semantic_added": 0}
        if isinstance(backend, HashEmbeddingBackend):
            stats["semantic_skipped"] = "no real embedding backend (install tesserae[semantic])"
            return [], stats
        try:
            import numpy as np
        except ImportError:
            stats["semantic_skipped"] = "numpy not available (install tesserae[semantic])"
            return [], stats

        # Pairs the string pass already emitted, keyed on the node pair (the
        # reason differs, so stable_review_id alone would not collide).
        seen: Set[Tuple[str, str]] = {
            tuple(sorted((item.left_node_id, item.right_node_id))) for item in string_items  # type: ignore[misc]
        }

        # Block by node type: cross-type pairs are already ineligible above, so
        # blocking is free and turns one N^2 matrix into several small ones.
        blocks: Dict[str, List[ResearchNode]] = {}
        for node in nodes:
            if node.type not in CANONICALIZABLE_TYPES:
                continue
            blocks.setdefault(node.type.value, []).append(node)

        capped = False
        items: List[ReviewItem] = []
        node_by_id = {node.id: node for node in nodes}
        for type_value in sorted(blocks):
            block = sorted(blocks[type_value], key=lambda node: node.id)
            if len(block) > self.max_block:
                block = block[: self.max_block]  # truncate by sorted id, never by iteration order
                capped = True
            if len(block) < 2:
                continue
            vectors = np.asarray(
                backend.embed([f"{n.name}. {(n.description or '').strip()}".strip() for n in block]),
                dtype="float64",
            )
            # L2-normalize defensively: EmbeddingBackend does not promise unit
            # vectors, so raw dot products could exceed 1 and fake a match.
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            unit = vectors / norms
            sims = unit @ unit.T
            for i, node in enumerate(block):
                row = sims[i]
                scored = [
                    (round(float(row[j]), 4), block[j].id)
                    for j in range(len(block))
                    if j != i and float(row[j]) >= self.embedding_min_cosine
                ]
                scored.sort(key=lambda pair: (-pair[0], pair[1]))
                for score, other_id in scored[: self.embedding_top_k]:
                    left_id, right_id = (node.id, other_id) if node.id < other_id else (other_id, node.id)
                    key = (left_id, right_id)
                    if key in seen:
                        continue
                    left, right = node_by_id[left_id], node_by_id[right_id]
                    if normalize_key(left.name) == normalize_key(right.name):
                        continue
                    seen.add(key)
                    items.append(
                        ReviewItem(
                            id=stable_review_id(left_id, right_id, "similar_embedding"),
                            left_node_id=left_id,
                            right_node_id=right_id,
                            left_name=left.name,
                            right_name=right.name,
                            node_type=type_value,
                            reason="similar_embedding",
                            score=score,
                        )
                    )

        items.sort(key=_review_sort_key)
        if len(items) > self.max_semantic_items:
            items = items[: self.max_semantic_items]
            stats["semantic_capped_at"] = self.max_semantic_items
        if capped:
            stats["semantic_block_capped_at"] = self.max_block
        stats["semantic_added"] = len(items)
        return items, stats


def _review_sort_key(item: ReviewItem) -> Tuple[float, str, str, str, str]:
    """Total order for review items.

    Node ids are part of the key because two DIFFERENT pairs can share both
    display names (same names under different types, or genuine name collisions),
    and a partial key there leaves the output at the mercy of emission order —
    the exact shape that caused prior determinism regressions.
    """

    return (-item.score, item.left_name, item.right_name, item.left_node_id, item.right_node_id)


class ReviewQueue:
    def __init__(self, items: Sequence[ReviewItem]) -> None:
        self.items = list(items)

    def model_dump(self) -> Dict[str, object]:
        return {"items": [item.model_dump() for item in self.items]}

    def apply_decisions(self, graph: ResearchGraph, decisions: Sequence[ReviewDecision]) -> ResearchGraph:
        item_by_id = {item.id: item for item in self.items}
        node_by_id = {node.id: node for node in graph.nodes}
        replacement: Dict[str, str] = {}

        for decision in decisions:
            if decision.action == "keep_separate":
                continue
            if decision.action != "merge":
                raise ValueError(f"Unsupported review decision action: {decision.action}")
            item = item_by_id.get(decision.item_id)
            if item is None:
                raise ValueError(f"Unknown review item: {decision.item_id}")
            canonical_id = decision.canonical_node_id or item.left_node_id
            if canonical_id not in {item.left_node_id, item.right_node_id}:
                raise ValueError("canonical_node_id must be one of the reviewed nodes")
            other_id = item.right_node_id if canonical_id == item.left_node_id else item.left_node_id
            if canonical_id not in node_by_id or other_id not in node_by_id:
                raise ValueError("Review decision references missing graph nodes")
            replacement[other_id] = canonical_id

        groups: Dict[str, List[ResearchNode]] = {}
        for node in graph.nodes:
            groups.setdefault(replacement.get(node.id, node.id), []).append(node)
        new_nodes = [merge_node_group(canonical_id, group) for canonical_id, group in groups.items()]
        node_ids = {node.id for node in new_nodes}
        return ResearchGraph(nodes=new_nodes, edges=rewire_edges(graph.edges, replacement, node_ids))


def merge_node_group(canonical_id: str, group: Sequence[ResearchNode]) -> ResearchNode:
    canonical = next((node for node in group if node.id == canonical_id), group[0])
    aliases: Set[str] = set(canonical.aliases)
    descriptions: List[str] = []
    metadata: Dict[str, object] = {}
    source_path = canonical.source_path
    for node in group:
        if node.id != canonical.id:
            aliases.add(node.name)
        aliases.update(node.aliases)
        if node.description:
            descriptions.append(node.description)
        metadata.update(node.metadata)
        source_path = source_path or node.source_path
    aliases.discard(canonical.name)
    return ResearchNode(
        id=canonical.id,
        name=canonical.name,
        type=canonical.type,
        aliases=sorted(aliases),
        description=canonical.description or "\n".join(dict.fromkeys(descriptions)),
        source_path=source_path,
        metadata=metadata,
    )


def rewire_edges(edges: Iterable[ResearchEdge], replacement: Mapping[str, str], node_ids: Set[str]) -> List[ResearchEdge]:
    rewritten: Dict[Tuple[str, str, str], ResearchEdge] = {}
    for edge in edges:
        source = replacement.get(edge.source, edge.source)
        target = replacement.get(edge.target, edge.target)
        if source == target:
            continue
        if source not in node_ids or target not in node_ids:
            continue
        rewritten[(source, edge.type, target)] = ResearchEdge(source=source, target=target, type=edge.type, evidence=edge.evidence, metadata=edge.metadata)
    return list(rewritten.values())


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "", text.lower())


def token_set(text: str) -> Set[str]:
    tokens = re.findall(r"[a-z0-9가-힣]+", text.lower())
    return {token for token in tokens if token not in {"3d", "4d", "the", "a", "an"}}


def name_similarity(left: str, right: str) -> float:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if left_key == right_key:
        return 1.0
    if left_key and right_key and (left_key in right_key or right_key in left_key):
        return 0.90
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap / union


def stable_review_id(left_id: str, right_id: str, reason: str) -> str:
    first, second = sorted([left_id, right_id])
    digest = hashlib.sha1(f"{first}:{second}:{reason}".encode("utf-8")).hexdigest()[:12]
    return f"review:{reason}:{digest}"
