"""Research-domain literature intelligence graph primitives.

This module is intentionally independent from Cognee/Graphiti. It defines the
controlled research ontology and a deterministic baseline extractor that can be
used in tests and as a guardrail around future Claude/Cognee extraction.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


class ResearchNodeType(str, Enum):
    # Field / taxonomy layer
    RESEARCH_FIELD = "ResearchField"
    RESEARCH_TOPIC = "ResearchTopic"
    PROBLEM_AREA = "ProblemArea"
    APPROACH_FAMILY = "ApproachFamily"
    TREND = "Trend"

    # Source / artifact layer
    SOURCE_DOCUMENT = "SourceDocument"
    PAPER = "Paper"
    REPOSITORY = "Repository"
    PROJECT = "Project"
    MODEL = "Model"
    DATASET = "Dataset"
    BENCHMARK = "Benchmark"
    METRIC = "Metric"
    RESULT = "Result"
    ORGANIZATION = "Organization"
    PERSON = "Person"
    CODE_PROJECT = "CodeProject"
    SOURCE_FILE = "SourceFile"
    CODE_MODULE = "CodeModule"
    CODE_CLASS = "CodeClass"
    CODE_FUNCTION = "CodeFunction"
    DEPENDENCY = "Dependency"

    # Concept layer
    CONCEPT = "Concept"
    TECHNICAL_TERM = "TechnicalTerm"
    MATHEMATICAL_CONCEPT = "MathematicalConcept"
    METHODOLOGICAL_CONCEPT = "MethodologicalConcept"
    ALGORITHM = "Algorithm"
    OBJECTIVE_FUNCTION = "ObjectiveFunction"
    ARCHITECTURE_PATTERN = "ArchitecturePattern"
    TRAINING_PARADIGM = "TrainingParadigm"
    INFERENCE_STRATEGY = "InferenceStrategy"
    EVALUATION_PROTOCOL = "EvaluationProtocol"
    TASK = "Task"
    CAPABILITY = "Capability"

    # Assertion layer
    CLAIM = "Claim"
    CONTRIBUTION_CLAIM = "ContributionClaim"
    PERFORMANCE_CLAIM = "PerformanceClaim"
    COMPARISON_CLAIM = "ComparisonClaim"
    LIMITATION_CLAIM = "LimitationClaim"
    CAUSAL_CLAIM = "CausalClaim"
    OPEN_QUESTION = "OpenQuestion"
    EVIDENCE_SPAN = "EvidenceSpan"

    # Synthesis layer (higher-order, generated)
    SYNTHESIS = "Synthesis"


ALLOWED_NODE_TYPES: Set[str] = {item.value for item in ResearchNodeType}


class TitleQuality(str, Enum):
    """Quality tier for a Paper node's display title.

    The ranking matters for de-duplication: when a digest mention and a
    full ``paper.md`` resolve to the same arXiv id, the higher-quality
    title wins.
    """

    INVALID = "invalid"
    NEEDS_METADATA = "needs_metadata"
    ARXIV_ONLY = "arxiv_only"
    REFERENCE_CONTEXT = "reference_context"
    PAPER_FILE = "paper_file"
    VERIFIED = "verified"


_TITLE_QUALITY_RANK: Dict[str, int] = {
    TitleQuality.INVALID.value: -1,
    TitleQuality.NEEDS_METADATA.value: 0,
    TitleQuality.ARXIV_ONLY.value: 1,
    TitleQuality.REFERENCE_CONTEXT.value: 2,
    TitleQuality.PAPER_FILE.value: 3,
    TitleQuality.VERIFIED.value: 4,
}


VERIFIED_PAPER_TITLE_QUALITIES: Set[str] = {
    TitleQuality.PAPER_FILE.value,
    TitleQuality.VERIFIED.value,
    TitleQuality.REFERENCE_CONTEXT.value,
}

ALLOWED_EDGE_TYPES: Set[str] = {
    "is_a",
    "part_of",
    "subfield_of",
    "introduces",
    "uses",
    "extends",
    "improves_on",
    "compares_against",
    "criticizes",
    "addresses",
    "optimizes_for",
    "uses_dataset",
    "evaluated_on",
    "uses_metric",
    "reports_result",
    "achieves_score",
    "belongs_to_approach_family",
    "shares_concept_with",
    "derived_from",
    "supports_claim",
    "contradicts_claim",
    "attributes_improvement_to",
    "has_limitation",
    "evidenced_by",
    "mentioned_in",
    "authored_by",
    "released_by",
    "implemented_in",
    "rising_in",
    "declining_in",
    "emerged_after",
    "contains",
    "defines",
    "imports",
    "calls",
    "documents",
    "synthesizes",
    "summarizes",
}


@dataclass(frozen=True)
class ResearchNode:
    id: str
    name: str
    type: ResearchNodeType
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    source_path: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["type"] = self.type.value
        return payload


@dataclass(frozen=True)
class ResearchEdge:
    source: str
    target: str
    type: str
    evidence: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in ALLOWED_EDGE_TYPES:
            raise ValueError(f"Unsupported research edge type: {self.type}")

    def model_dump(self) -> Dict[str, object]:
        return asdict(self)


def is_arxiv_placeholder_name(name: str) -> bool:
    return re.fullmatch(r"arXiv:\d{4}\.\d{4,6}", name.strip(), flags=re.IGNORECASE) is not None


def prefer_research_node(existing: ResearchNode, incoming: ResearchNode) -> ResearchNode:
    """Merge duplicate nodes while preferring verified paper titles.

    Digest/source-document pages can discover papers as ``arXiv:<id>`` or weak
    context titles. When the real per-paper raw document is also ingested, keep
    the same stable node id but upgrade the display name to the paper title.
    """
    existing_quality = str(existing.metadata.get("title_quality") or "")
    incoming_quality = str(incoming.metadata.get("title_quality") or "")
    existing_placeholder = is_arxiv_placeholder_name(existing.name)
    incoming_placeholder = is_arxiv_placeholder_name(incoming.name)
    # Unknown quality tiers default to ``arxiv_only`` so unlabelled nodes never
    # silently win against a properly tagged ``paper_file`` peer.
    incoming_rank = _TITLE_QUALITY_RANK.get(
        incoming_quality, _TITLE_QUALITY_RANK[TitleQuality.ARXIV_ONLY.value]
    )
    existing_rank = _TITLE_QUALITY_RANK.get(
        existing_quality, _TITLE_QUALITY_RANK[TitleQuality.ARXIV_ONLY.value]
    )
    if incoming_rank > existing_rank:
        chosen = incoming
    elif existing_placeholder and not incoming_placeholder:
        chosen = incoming
    else:
        chosen = existing
    other = existing if chosen is incoming else incoming
    aliases = set(existing.aliases) | set(incoming.aliases)
    if other.name != chosen.name:
        aliases.add(other.name)
    aliases.discard(chosen.name)
    metadata = {**other.metadata, **chosen.metadata}
    return ResearchNode(
        id=chosen.id,
        name=chosen.name,
        type=chosen.type,
        aliases=sorted(aliases),
        description=chosen.description or other.description,
        source_path=chosen.source_path or other.source_path,
        metadata=metadata,
    )


@dataclass
class ResearchGraph:
    nodes: List[ResearchNode] = field(default_factory=list)
    edges: List[ResearchEdge] = field(default_factory=list)

    def model_dump(self) -> Dict[str, object]:
        return {
            "nodes": [node.model_dump() for node in self.nodes],
            "edges": [edge.model_dump() for edge in self.edges],
        }

    def to_json(self, **kwargs: object) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False, **kwargs)

    def has_edge_type(self, edge_type: str) -> bool:
        return any(edge.type == edge_type for edge in self.edges)


class ResearchGraphBuilder:
    def __init__(self) -> None:
        self._nodes: Dict[str, ResearchNode] = {}
        self._edges: Dict[Tuple[str, str, str], ResearchEdge] = {}

    def add_node(
        self,
        name: str,
        node_type: ResearchNodeType,
        aliases: Optional[Sequence[str]] = None,
        description: str = "",
        source_path: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        id_seed: Optional[str] = None,
    ) -> ResearchNode:
        canonical_name = normalize_display_name(name)
        # ``id_seed`` lets callers (e.g. Paper extraction) decouple the stable
        # node id from the human-readable display name. We use this so the
        # arxiv id pins the node identity while the title can still be the
        # nice title pulled from the paper file.
        node_id = stable_id(node_type.value, id_seed or canonical_name)
        existing = self._nodes.get(node_id)
        if existing:
            incoming = ResearchNode(
                id=existing.id,
                name=canonical_name,
                type=node_type,
                aliases=list(aliases or []),
                description=description,
                source_path=source_path,
                metadata=metadata or {},
            )
            node = prefer_research_node(existing, incoming)
            self._nodes[node_id] = node
            return node
        node = ResearchNode(
            id=node_id,
            name=canonical_name,
            type=node_type,
            aliases=list(aliases or []),
            description=description,
            source_path=source_path,
            metadata=metadata or {},
        )
        self._nodes[node_id] = node
        return node

    def add_edge(
        self,
        source: ResearchNode,
        edge_type: str,
        target: ResearchNode,
        evidence: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> ResearchEdge:
        edge = ResearchEdge(source=source.id, target=target.id, type=edge_type, evidence=evidence, metadata=metadata or {})
        self._edges[(edge.source, edge.type, edge.target)] = edge
        return edge

    def build(self) -> ResearchGraph:
        # Keep source artifacts last so convenience maps keyed by display name
        # prefer the concrete Paper/Repository over an identically named
        # ApproachFamily candidate.
        source_types = {ResearchNodeType.PAPER, ResearchNodeType.REPOSITORY, ResearchNodeType.SOURCE_DOCUMENT}
        nodes = sorted(self._nodes.values(), key=lambda node: node.type in source_types)
        return ResearchGraph(nodes=nodes, edges=list(self._edges.values()))


# ``TermRule`` is a backward-compat alias used by older tests. The canonical
# typed registry now lives in :mod:`llm_wiki.term_registry`.
@dataclass(frozen=True)
class TermRule:
    canonical_name: str
    node_type: ResearchNodeType
    aliases: Tuple[str, ...] = ()
    approach_family: Optional[str] = None
    relation: str = "uses"

    def patterns(self) -> Tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


# Sentinel: the default registry is built lazily from term_registry.py to avoid
# import cycles. Tests can still import ``DEFAULT_TERM_RULES`` for back-compat.
def _build_default_term_rules() -> Tuple[TermRule, ...]:
    from .term_registry import TermRegistry

    return tuple(
        TermRule(
            canonical_name=entry.canonical_name,
            node_type=entry.node_type,
            aliases=entry.aliases,
            approach_family=entry.approach_family,
            relation=entry.relation,
        )
        for entry in TermRegistry.default().entries
    )


DEFAULT_TERM_RULES: Tuple[TermRule, ...] = _build_default_term_rules()


class ResearchGraphExtractor:
    """Deterministic baseline extractor for research-literature intelligence graphs.

    The long-term extractor should be LLM-backed, but this baseline enforces the
    domain ontology and provides stable tests/evaluation fixtures.
    """

    def __init__(self, term_rules: Optional[Sequence[TermRule]] = None) -> None:
        from .term_registry import TermRegistry

        self.registry = TermRegistry.default()
        # Back-compat: tests can still construct ``TermRule`` lists.
        if term_rules is None:
            self.term_rules = DEFAULT_TERM_RULES
        else:
            self.term_rules = tuple(term_rules)

    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph:
        file_path = Path(path)
        return self.extract_text(file_path.read_text(encoding="utf-8", errors="replace"), str(file_path), source_kind)

    def extract_text(
        self,
        text: str,
        source_path: Optional[str] = None,
        source_kind: str = "SourceDocument",
    ) -> ResearchGraph:
        builder = ResearchGraphBuilder()
        source_type = source_kind_to_node_type(source_kind, source_path)
        source_metadata = extract_source_metadata(text, source_path)
        if source_type == ResearchNodeType.SOURCE_DOCUMENT and is_social_feed_source_path(source_path):
            return builder.build()
        # Pre-compute heading-derived sections + candidate paper titles. We
        # store all headings as structural metadata; concepts only get minted
        # for headings that match the typed term registry.
        sections = _collect_document_sections(text)
        registry_aliases = self.registry.all_aliases()
        candidate_paper_titles, registry_concept_headings = self._classify_document_headings(text, registry_aliases)
        paper_metadata: Dict[str, object] = {"source_kind": source_kind, **source_metadata}
        if sections:
            paper_metadata["sections"] = sections

        title = extract_title(text, source_path)
        if source_type == ResearchNodeType.PAPER:
            arxiv_id = str(source_metadata.get("arxiv_id") or "")
            if is_verified_paper_title(title, source_metadata):
                paper_metadata["title_quality"] = TitleQuality.PAPER_FILE.value
            elif arxiv_id:
                resolved = resolve_missing_paper_title(arxiv_id, text, source_metadata)
                if resolved.title and is_verified_paper_title(resolved.title, source_metadata):
                    title = resolved.title
                    paper_metadata["title_quality"] = resolved.quality.value
                else:
                    paper_metadata["title_quality"] = TitleQuality.NEEDS_METADATA.value
            else:
                paper_metadata["title_quality"] = TitleQuality.INVALID.value

        # Repository identity: prefer the GitHub URL so duplicate notes about
        # the same repo collapse on a stable id even when the markdown title
        # differs (Korean prefix, mirror notes, etc.).
        repo_identity: Optional[Tuple[str, str, str]] = None  # (owner_repo, url, display)
        if source_type == ResearchNodeType.REPOSITORY:
            github_repo = str(source_metadata.get("github_repo") or "")
            repo_url = str(source_metadata.get("repo_url") or "")
            if github_repo and repo_url:
                repo_identity = (github_repo, repo_url, title)

        # When the path identifies a Paper subfolder we want all references
        # (digest mentions, per-paper file ingest) to collapse onto the same
        # node id. We achieve that by seeding stable_id with ``arXiv:<id>``
        # while keeping the human-readable title as the display name, with
        # the arxiv id captured as an alias for cross-reference search.
        arxiv_id = str(source_metadata.get("arxiv_id", ""))
        if source_type == ResearchNodeType.PAPER and arxiv_id:
            quality = paper_metadata.get("title_quality")
            display_name = (
                title
                if quality in {TitleQuality.PAPER_FILE.value, TitleQuality.VERIFIED.value, TitleQuality.REFERENCE_CONTEXT.value}
                else f"arXiv:{arxiv_id}"
            )
            aliases: List[str] = [f"arXiv:{arxiv_id}"]
            paper = builder.add_node(
                display_name,
                source_type,
                aliases=aliases,
                source_path=source_path,
                metadata=paper_metadata,
                id_seed=f"arXiv:{arxiv_id}",
            )
        elif repo_identity is not None:
            owner_repo, repo_url, display = repo_identity
            paper = builder.add_node(
                display,
                ResearchNodeType.REPOSITORY,
                aliases=[owner_repo, repo_url],
                source_path=source_path,
                metadata=paper_metadata,
                id_seed=f"github:{owner_repo}",
            )
            # If the repo notes link to a paper via arxiv_id, upsert the
            # paper placeholder and emit an ``implemented_in`` edge. The
            # paper node may later be upgraded by a real ``paper.md``.
            repo_arxiv = str(source_metadata.get("arxiv_id") or "")
            if repo_arxiv:
                paper_placeholder = builder.add_node(
                    f"arXiv:{repo_arxiv}",
                    ResearchNodeType.PAPER,
                    aliases=[f"arXiv:{repo_arxiv}"],
                    source_path=source_path,
                    metadata={
                        "source_kind": "Paper",
                        "arxiv_id": repo_arxiv,
                        "title_quality": TitleQuality.ARXIV_ONLY.value,
                        "discovered_in": source_path,
                    },
                    id_seed=f"arXiv:{repo_arxiv}",
                )
                builder.add_edge(paper_placeholder, "implemented_in", paper)
            # The repository owner is often the releasing Organization. We
            # only mint Organization nodes for owners that look organizational
            # (multi-segment names, contains hyphen, or appears in a small
            # known list of research orgs).
            owner = owner_repo.split("/", 1)[0]
            if _looks_like_organization_owner(owner):
                org = builder.add_node(
                    owner,
                    ResearchNodeType.ORGANIZATION,
                    aliases=[],
                    source_path=source_path,
                )
                builder.add_edge(paper, "released_by", org)
        else:
            if candidate_paper_titles:
                paper_metadata["candidate_paper_titles"] = candidate_paper_titles
            paper = builder.add_node(title, source_type, source_path=source_path, metadata=paper_metadata)

        if source_type in {ResearchNodeType.SOURCE_DOCUMENT, ResearchNodeType.REPOSITORY, ResearchNodeType.PROJECT}:
            self._add_document_structure(builder, paper, registry_concept_headings, source_path)
            self._extract_paper_references(builder, paper, text, source_path)
            return builder.build()

        # Hard gate: if a paper.md failed to produce a verifiable title, we
        # still emit the paper placeholder for de-dup, but skip body
        # extraction so we don't inflate the graph with invalid claims.
        if (
            source_type == ResearchNodeType.PAPER
            and paper_metadata.get("title_quality")
            in {TitleQuality.INVALID.value, TitleQuality.NEEDS_METADATA.value}
        ):
            return builder.build()

        field = builder.add_node(infer_research_field(text), ResearchNodeType.RESEARCH_FIELD)
        builder.add_edge(paper, "part_of", field)
        research_text = strip_non_research_scaffold(text)

        matched_terms: List[ResearchNode] = []
        for entry in self.registry.entries:
            evidence = find_evidence(research_text, entry.patterns())
            if not evidence:
                continue
            node = builder.add_node(
                entry.canonical_name,
                entry.node_type,
                aliases=list(entry.aliases),
                source_path=source_path,
            )
            matched_terms.append(node)
            builder.add_edge(paper, entry.relation, node, evidence=evidence)

            span = self._add_evidence(builder, paper, evidence, source_path)
            claim = self._add_claim_for_term(builder, paper, node, evidence, source_path)
            builder.add_edge(claim, "evidenced_by", span, evidence=evidence)
            builder.add_edge(claim, "mentioned_in", paper, evidence=evidence)

            if entry.approach_family:
                family = builder.add_node(entry.approach_family, ResearchNodeType.APPROACH_FAMILY)
                builder.add_edge(paper, "belongs_to_approach_family", family, evidence=evidence)
                if node.type != ResearchNodeType.APPROACH_FAMILY:
                    builder.add_edge(family, "uses", node, evidence=evidence)

        # Typed entity extraction reads the *raw* text because the scaffold
        # stripper removes ``저자:``/``Authors:`` blocks (those lines are
        # routed straight into typed Person/Org nodes here).
        self._extract_typed_entities(builder, paper, text, source_path)
        self._add_contribution_claims(builder, paper, research_text, source_path)
        self._add_comparison_claims(builder, paper, research_text, source_path)
        self._add_open_questions(builder, paper, research_text, source_path)
        self._add_performance_claims(builder, paper, research_text, source_path)
        self._connect_related_terms(builder, matched_terms, research_text)
        return builder.build()

    def _classify_document_headings(
        self, text: str, registry_aliases: Set[str]
    ) -> Tuple[List[str], List[str]]:
        """Return (paper-title candidates, registry-aliased headings).

        The second list is intentionally small: only headings whose normalized
        text exactly matches a registered term canonical name or alias are
        promoted to ``Concept`` candidates downstream. Everything else stays
        in the source-document ``metadata["sections"]`` blob.
        """
        candidate_paper_titles: List[str] = []
        registry_headings: List[str] = []
        lowered_aliases = {alias.lower() for alias in registry_aliases}
        for raw in extract_markdown_headings(text):
            cleaned = _strip_heading_numbering(raw)
            if not cleaned:
                continue
            if _is_generic_section_heading(cleaned):
                continue
            if _looks_like_paper_title_heading(raw, cleaned):
                candidate_paper_titles.append(cleaned)
                continue
            normalized = normalize_display_name(cleaned).lower()
            if normalized in lowered_aliases:
                registry_headings.append(normalize_display_name(cleaned))
        return candidate_paper_titles, registry_headings

    def _add_document_structure(
        self,
        builder: ResearchGraphBuilder,
        document: ResearchNode,
        concept_headings: Sequence[str],
        source_path: Optional[str],
    ) -> None:
        # Only headings that exactly matched a registered term make it here.
        # Mint the concept node via the typed registry to preserve typing.
        for heading in concept_headings:
            entry = self.registry.lookup(heading)
            if entry is None:
                continue
            if heading.lower() == document.name.lower():
                continue
            concept = builder.add_node(
                entry.canonical_name,
                entry.node_type,
                aliases=list(entry.aliases),
                description=f"Registered term referenced in {document.name}",
                source_path=source_path,
                metadata={"source_kind": "document_heading"},
            )
            builder.add_edge(document, "documents", concept)

    def _extract_paper_references(
        self,
        builder: ResearchGraphBuilder,
        document: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        """Promote arxiv links inside research-corpus digest/feed bodies to Paper refs."""
        normalized_source_path = source_path.replace("\\", "/") if source_path else ""
        if not normalized_source_path or "data/research/" not in normalized_source_path:
            return
        seen: Set[str] = set()
        # arxiv URLs (http/https, abs|pdf), bare ``arXiv:NNNN.NNNNN``, and
        # relative paths to ``papers/<id>/paper.md``.
        patterns = (
            re.compile(r"https?://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,6})", re.IGNORECASE),
            re.compile(r"\barxiv\s*:\s*(\d{4}\.\d{4,6})", re.IGNORECASE),
            re.compile(r"papers/(\d{4}\.\d{4,6})/(?:paper|main|abstract)\.md", re.IGNORECASE),
        )
        for pattern in patterns:
            for match in pattern.finditer(text):
                arxiv_id = match.group(1)
                if arxiv_id in seen:
                    continue
                seen.add(arxiv_id)
                display_name = f"arXiv:{arxiv_id}"
                title_quality = "arxiv_only"
                paper = builder.add_node(
                    display_name,
                    ResearchNodeType.PAPER,
                    aliases=[f"arXiv:{arxiv_id}"],
                    source_path=source_path,
                    metadata={
                        "source_kind": "Paper",
                        "arxiv_id": arxiv_id,
                        "title_quality": title_quality,
                        "discovered_in": document.source_path or source_path,
                    },
                    id_seed=f"arXiv:{arxiv_id}",
                )
                builder.add_edge(document, "mentioned_in", paper)

    def _add_evidence(self, builder: ResearchGraphBuilder, paper: ResearchNode, evidence: str, source_path: Optional[str]) -> ResearchNode:
        name = "Evidence: " + truncate(evidence, 72)
        span = builder.add_node(name, ResearchNodeType.EVIDENCE_SPAN, description=evidence, source_path=source_path)
        builder.add_edge(span, "part_of", paper, evidence=evidence)
        return span

    def _add_claim_for_term(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        term: ResearchNode,
        evidence: str,
        source_path: Optional[str],
    ) -> ResearchNode:
        claim_type = classify_claim_type(evidence)
        claim = builder.add_node(
            "Claim: " + truncate(evidence, 96),
            claim_type,
            description=evidence,
            source_path=source_path,
        )
        builder.add_edge(paper, "supports_claim", claim, evidence=evidence)
        builder.add_edge(claim, "uses" if term.type not in {ResearchNodeType.TASK, ResearchNodeType.CAPABILITY} else "addresses", term, evidence=evidence)
        return claim

    def _extract_typed_entities(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        """Emit typed Person/Org/Dataset/Benchmark/Metric/Algorithm/Model nodes."""
        # Authors -> Person, Organization (when an Organization line is present)
        for person in extract_authors(text):
            node = builder.add_node(
                person,
                ResearchNodeType.PERSON,
                source_path=source_path,
            )
            builder.add_edge(paper, "authored_by", node)
        for org in extract_organizations(text):
            node = builder.add_node(
                org,
                ResearchNodeType.ORGANIZATION,
                source_path=source_path,
            )
            builder.add_edge(paper, "released_by", node)

        datasets, benchmarks, metrics, results = extract_eval_entities(text)
        for ds in datasets:
            ds_node = builder.add_node(ds, ResearchNodeType.DATASET, source_path=source_path)
            builder.add_edge(paper, "uses_dataset", ds_node)
        for bm in benchmarks:
            bm_node = builder.add_node(bm, ResearchNodeType.BENCHMARK, source_path=source_path)
            builder.add_edge(paper, "evaluated_on", bm_node)
        for m in metrics:
            metric_node = builder.add_node(m, ResearchNodeType.METRIC, source_path=source_path)
            builder.add_edge(paper, "uses_metric", metric_node)
        for result in results:
            result_node = builder.add_node(
                result["name"],
                ResearchNodeType.RESULT,
                description=result.get("evidence", ""),
                source_path=source_path,
                metadata={
                    "metric": result.get("metric"),
                    "value": result.get("value"),
                    "benchmark": result.get("benchmark"),
                },
            )
            builder.add_edge(paper, "reports_result", result_node)
            metric = result.get("metric")
            if metric:
                metric_node = builder.add_node(metric, ResearchNodeType.METRIC, source_path=source_path)
                builder.add_edge(result_node, "uses_metric", metric_node)
            benchmark = result.get("benchmark")
            if benchmark:
                bm_node = builder.add_node(benchmark, ResearchNodeType.BENCHMARK, source_path=source_path)
                builder.add_edge(result_node, "evaluated_on", bm_node)

        algorithms, models, training_paradigms, inference_strategies = extract_method_entities(text)
        for algo in algorithms:
            node = builder.add_node(
                algo,
                ResearchNodeType.ALGORITHM,
                source_path=source_path,
            )
            builder.add_edge(paper, "introduces", node)
        for model in models:
            node = builder.add_node(model, ResearchNodeType.MODEL, source_path=source_path)
            builder.add_edge(paper, "uses", node)
        for paradigm in training_paradigms:
            node = builder.add_node(
                paradigm, ResearchNodeType.TRAINING_PARADIGM, source_path=source_path
            )
            builder.add_edge(paper, "uses", node)
        for strategy in inference_strategies:
            node = builder.add_node(
                strategy, ResearchNodeType.INFERENCE_STRATEGY, source_path=source_path
            )
            builder.add_edge(paper, "uses", node)

    def _add_contribution_claims(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        for sentence in extract_contribution_claims(text):
            claim = builder.add_node(
                "Contribution: " + truncate(sentence, 96),
                ResearchNodeType.CONTRIBUTION_CLAIM,
                description=sentence,
                source_path=source_path,
            )
            span = self._add_evidence(builder, paper, sentence, source_path)
            builder.add_edge(paper, "supports_claim", claim, evidence=sentence)
            builder.add_edge(claim, "evidenced_by", span, evidence=sentence)

    def _add_comparison_claims(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        for sentence in extract_comparison_claims(text):
            claim = builder.add_node(
                "Comparison: " + truncate(sentence, 96),
                ResearchNodeType.COMPARISON_CLAIM,
                description=sentence,
                source_path=source_path,
            )
            span = self._add_evidence(builder, paper, sentence, source_path)
            builder.add_edge(paper, "supports_claim", claim, evidence=sentence)
            builder.add_edge(claim, "evidenced_by", span, evidence=sentence)

    def _add_open_questions(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        for sentence in extract_open_questions(text):
            question = builder.add_node(
                "Open question: " + truncate(sentence, 96),
                ResearchNodeType.OPEN_QUESTION,
                description=sentence,
                source_path=source_path,
            )
            span = self._add_evidence(builder, paper, sentence, source_path)
            builder.add_edge(paper, "supports_claim", question, evidence=sentence)
            builder.add_edge(question, "evidenced_by", span, evidence=sentence)

    def _add_performance_claims(
        self,
        builder: ResearchGraphBuilder,
        paper: ResearchNode,
        text: str,
        source_path: Optional[str],
    ) -> None:
        for sentence in extract_performance_claims(text):
            claim = builder.add_node(
                "Performance claim: " + truncate(sentence, 96),
                ResearchNodeType.PERFORMANCE_CLAIM,
                description=sentence,
                source_path=source_path,
            )
            span = self._add_evidence(builder, paper, sentence, source_path)
            builder.add_edge(paper, "supports_claim", claim, evidence=sentence)
            builder.add_edge(claim, "evidenced_by", span, evidence=sentence)

    def _connect_related_terms(self, builder: ResearchGraphBuilder, terms: Sequence[ResearchNode], text: str) -> None:
        names = {term.name: term for term in terms}
        if "Stochastic Solid" in names and "Gaussian Splatting" in names:
            builder.add_edge(names["Geometry-Grounded Gaussian Splatting"] if "Geometry-Grounded Gaussian Splatting" in names else names["Gaussian Splatting"], "uses", names["Stochastic Solid"])
        if "Depth Map" in names and "Shape Reconstruction" in names:
            builder.add_edge(names["Depth Map"], "addresses", names["Shape Reconstruction"])


class ResearchCorpusAnalyzer:
    """Corpus-level projections over already validated ResearchGraph objects.

    This deliberately consumes typed graphs instead of raw text so trend creation
    stays downstream from the controlled ontology and cannot introduce arbitrary
    node/edge types.
    """

    TREND_ELIGIBLE_TYPES = {
        ResearchNodeType.RESEARCH_TOPIC,
        ResearchNodeType.PROBLEM_AREA,
        ResearchNodeType.APPROACH_FAMILY,
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
        ResearchNodeType.TECHNICAL_TERM,
    }

    def summarize_trends(self, graphs: Sequence[ResearchGraph], min_sources: int = 2) -> ResearchGraph:
        builder = ResearchGraphBuilder()
        occurrences: Dict[str, Dict[str, object]] = {}

        for graph in graphs:
            for node in graph.nodes:
                builder.add_node(
                    node.name,
                    node.type,
                    aliases=node.aliases,
                    description=node.description,
                    source_path=node.source_path,
                    metadata=node.metadata,
                )
            nodes_by_id = {node.id: node for node in graph.nodes}
            for edge in graph.edges:
                source = nodes_by_id.get(edge.source)
                target = nodes_by_id.get(edge.target)
                if source and target:
                    merged_source = builder.add_node(source.name, source.type, aliases=source.aliases, description=source.description, source_path=source.source_path, metadata=source.metadata)
                    merged_target = builder.add_node(target.name, target.type, aliases=target.aliases, description=target.description, source_path=target.source_path, metadata=target.metadata)
                    builder.add_edge(merged_source, edge.type, merged_target, evidence=edge.evidence, metadata=edge.metadata)

            source_dates = sorted({node.metadata.get("analysis_date") for node in graph.nodes if node.metadata.get("analysis_date")})
            graph_date = str(source_dates[0]) if source_dates else None
            source_paths = sorted({node.source_path for node in graph.nodes if node.source_path})
            graph_source = source_paths[0] if source_paths else None

            for node in graph.nodes:
                if node.type not in self.TREND_ELIGIBLE_TYPES:
                    continue
                bucket = occurrences.setdefault(node.id, {"node": node, "dates": set(), "sources": set()})
                if graph_date:
                    bucket["dates"].add(graph_date)  # type: ignore[union-attr]
                if graph_source:
                    bucket["sources"].add(graph_source)  # type: ignore[union-attr]

        for bucket in occurrences.values():
            node = bucket["node"]
            dates = sorted(bucket["dates"])
            sources = sorted(bucket["sources"])
            if len(sources) < min_sources:
                continue
            trend = builder.add_node(
                f"Trend: {node.name}",
                ResearchNodeType.TREND,
                description=f"{node.name} appears across {len(sources)} research sources.",
                metadata={
                    "concept_id": node.id,
                    "source_count": len(sources),
                    "sources": sources,
                    "first_seen": dates[0] if dates else None,
                    "last_seen": dates[-1] if dates else None,
                },
            )
            merged_node = builder.add_node(node.name, node.type, aliases=node.aliases, description=node.description, source_path=node.source_path, metadata=node.metadata)
            builder.add_edge(merged_node, "rising_in", trend)

        return builder.build()


def extract_markdown_headings(text: str) -> List[str]:
    headings: List[str] = []
    for line in text.splitlines():
        match = re.match(r"^#{1,4}\s+(.+?)\s*$", line.strip())
        if not match:
            continue
        heading = re.sub(r"\s+#*$", "", match.group(1)).strip()
        if heading and heading not in headings:
            headings.append(heading)
    return headings


# Regexes for daily-digest path classification. They match anywhere inside a
# normalised forward-slash path so `data/research/<anything>/papers/<id>/...`
# windows-style or absolute prefixes both classify correctly.
_PAPER_SUBFOLDER_RE = re.compile(
    r"data/research/.+?/papers/(\d{4}\.\d{4,6})/(?:paper|main|abstract)\.md$",
    re.IGNORECASE,
)
_PAPER_REPO_FILE_RE = re.compile(
    r"data/research/.+?/papers/(\d{4}\.\d{4,6})/repo\.md$",
    re.IGNORECASE,
)
_DAILY_REPOS_RE = re.compile(r"data/research/.+?/repos/.+?\.md$", re.IGNORECASE)
_DAILY_FEEDS_RE = re.compile(r"data/research/.+?/feeds/.+?\.md$", re.IGNORECASE)


def is_social_feed_source_path(source_path: Optional[str]) -> bool:
    if not source_path:
        return False
    return _DAILY_FEEDS_RE.search(source_path.replace("\\", "/")) is not None


def is_public_research_node(node: ResearchNode) -> bool:
    """Return whether a node should appear in the public wiki/site projection.

    Social feed captures are noisy evidence inputs, not durable wiki entities.
    Likewise, arXiv mentions that have not been resolved from a real paper file
    must not become public paper pages.
    """
    if is_social_feed_source_path(node.source_path):
        return False
    if node.type == ResearchNodeType.PAPER:
        quality = str(node.metadata.get("title_quality") or "")
        if quality and quality not in VERIFIED_PAPER_TITLE_QUALITIES:
            return False
    return True


def infer_arxiv_reference_title(text: str, arxiv_match_start: int) -> Optional[str]:
    """Infer a paper title from local feed context before an arXiv URL.

    Twitter/RSS feed captures often store entries as:
    ``Title<br><br>Authors<br>https://arxiv.org/abs/...``. In that case the
    arXiv id alone is a poor display name, and the nearby title is already in
    the raw document.
    """
    window = text[max(0, arxiv_match_start - 800) : arxiv_match_start]
    window = html.unescape(window)
    window = window.replace("\\n", "\n")
    window = re.sub(r"<br\s*/?>", "\n", window, flags=re.IGNORECASE)
    window = re.sub(r"<[^>]+>", " ", window)
    segments = [segment.strip(" \t#>-*") for segment in re.split(r"\n{2,}|\r\n{2,}", window)]
    for segment in reversed([segment for segment in segments if segment.strip()]):
        lines = [line.strip(" \t#>-*") for line in segment.splitlines() if line.strip()]
        if not lines:
            continue
        candidate = lines[0]
        candidate = re.sub(r"https?://\S+", "", candidate).strip()
        candidate = strip_trailing_authors(candidate)
        candidate = normalize_display_name(candidate)
        if _is_plausible_arxiv_context_title(candidate):
            return candidate
    return None


def strip_trailing_authors(candidate: str) -> str:
    if "," not in candidate:
        return candidate
    prefix = candidate.split(",", 1)[0].strip()
    match = re.match(r"^(?P<title>.+?)\s+[A-Z][A-Za-z'\-]+\s+[A-Z][A-Za-z'\-]+$", prefix)
    if match and len(match.group("title")) >= 12:
        return match.group("title").strip()
    return candidate


def _is_plausible_arxiv_context_title(candidate: str) -> bool:
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered in {"arxiv", "본문", "feed"} or "arxiv.org" in lowered:
        return False
    if lowered.startswith(("url", "date", "author", "작성자", "논문 분석", "rt ", "tl;dr", "extract ", "📄", "논문:")):
        return False
    if any(marker in lowered for marker in ("released #", "we just released", "join our discord", "goated things", "what's the right representation")):
        return False
    if re.fullmatch(r"\d{4}\.\d{4,6}", candidate):
        return False
    if len(candidate) < 8 or len(candidate) > 220:
        return False
    if "," in candidate and not re.search(r"\b(of|for|with|in|and|to|from|via|towards?|using|learning|neural|model|models|graph|image|video|3d|4d)\b", candidate, re.IGNORECASE):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", candidate)
    if 1 <= len(tokens) <= 3 and all(token[:1].isupper() and token[1:].islower() for token in tokens):
        return False
    return True


def strip_non_research_scaffold(text: str) -> str:
    """Remove scraper/UI/provenance scaffolding before extracting claims.

    The raw paper note remains immutable; this only affects graph claim/evidence
    extraction so headings like ``# 논문 분석`` and papers.cool chrome do not
    become claims or evidence spans.
    """
    cleaned: List[str] = []
    skip_exact = {
        "search", "filter", "highlight", "export", "save", "copy", "rel",
        "include or:", "exclude:", "stared paper(s):", "magic token:",
        "english", "中文", "desc language:", "kimi language:",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned.append("")
            continue
        lowered = line.lower().strip(" -*_`#")
        if line.startswith("# 논문 분석") or re.fullmatch(r"#{1,6}\s*\d{4}\.\d{4,6}", line):
            continue
        if line.startswith("> - arxiv:") or line.startswith("> - papers.cool:") or line.startswith("> - 분석일:"):
            continue
        if re.fullmatch(r"#{1,6}\s*#?\d+", line):
            continue
        if lowered in skip_exact:
            continue
        if lowered.startswith(("designed by", "powered by", "bug report", "github:", "publish:", "subject:", "authors:", "저자:", "주제:", "게시:")):
            continue
        if lowered.startswith(("제공하신", "제공된 원문", "의미 있는 내용을", "`paper_prompt.txt`", "paper_prompt.txt", "중국어 분석")):
            continue
        cleaned.append(raw_line)
    return "\n".join(cleaned)


def source_kind_to_node_type(source_kind: str, source_path: Optional[str]) -> ResearchNodeType:
    lowered = (source_kind or "").lower()
    path = (source_path or "").replace("\\", "/")
    path_lower = path.lower()
    # Path-precise matches win over the looser keyword fallbacks below: a daily
    # feed snippet must stay a SourceDocument even though the corpus root
    # contains the substring "papers".
    if _PAPER_SUBFOLDER_RE.search(path):
        return ResearchNodeType.PAPER
    if _PAPER_REPO_FILE_RE.search(path) or _DAILY_REPOS_RE.search(path):
        return ResearchNodeType.REPOSITORY
    if _DAILY_FEEDS_RE.search(path):
        return ResearchNodeType.SOURCE_DOCUMENT
    if "paper" in lowered or "/papers/" in path_lower or path_lower.endswith("paper.md") or "arxiv" in path_lower:
        return ResearchNodeType.PAPER
    if "repo" in lowered or "repo" in path_lower or "github" in path_lower:
        return ResearchNodeType.REPOSITORY
    return ResearchNodeType.SOURCE_DOCUMENT


def extract_arxiv_id_from_path(source_path: Optional[str]) -> Optional[str]:
    """Return the arxiv id from a daily-digest paper subfolder path, if any."""
    if not source_path:
        return None
    path = source_path.replace("\\", "/")
    match = _PAPER_SUBFOLDER_RE.search(path) or _PAPER_REPO_FILE_RE.search(path)
    if match:
        return match.group(1)
    return None


# Heading-classification helpers ---------------------------------------------

# Whitelist tokens that signal a heading is a real research concept rather
# than a paper title or section marker. Keep this list short and additive.
_CONCEPT_WHITELIST_TOKENS: Tuple[str, ...] = (
    "model",
    "diffusion",
    "splatting",
    "transformer",
    "graph",
    "rendering",
    "encoder",
    "decoder",
    "embedding",
    "attention",
    "reconstruction",
    "synthesis",
    "segmentation",
    "estimation",
    "depth",
    "slam",
    "convolution",
    "kernel",
    "loss",
    "regularization",
    "tokenization",
    "agent",
    "policy",
    "reward",
    "alignment",
)

_GENERIC_SECTION_NAMES: Set[str] = {
    "intro",
    "introduction",
    "summary",
    "abstract",
    "method",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "background",
    "related work",
    "experiments",
    "evaluation",
    "limitations",
    "appendix",
    "overview",
    "highlights",
    "본문",
    "개요",
    "요약",
    "결론",
    "참고",
    "실험",
    "한계",
    "방법",
}


def _strip_heading_numbering(heading: str) -> str:
    """Strip leading numbering ("1. ", "1) ", "I. ") and trailing whitespace."""
    text = re.sub(r"\s+", " ", heading.strip())
    # 1. , 1) , 12. , 12) — Arabic numbering
    text = re.sub(r"^\d{1,3}\s*[.)]\s+", "", text)
    # I. , II) , IV. — Roman numbering (simple form)
    text = re.sub(r"^[IVXLCM]{1,4}\s*[.)]\s+", "", text)
    return text.strip()


def _is_generic_section_heading(cleaned: str) -> bool:
    return cleaned.strip().lower().rstrip(":") in _GENERIC_SECTION_NAMES


def _looks_like_paper_title_heading(raw: str, cleaned: str) -> bool:
    """True if the heading looks like a paper title / digest entry, not a concept.

    Triggers on:
      - explicit numeric section prefix in the raw heading (``### 1. …``)
      - Korean sentence-ending punctuation (``다.``, ``.`` after Hangul)
      - too many commas / em-dashes (>5) — classic Korean digest prose form
    """
    raw_stripped = raw.strip()
    # Heading started with "### 1." style numbering — that is an enumerated
    # digest entry, never a concept.
    if re.match(r"^\d{1,3}\s*[.)]\s+", raw_stripped):
        return True
    # Korean conclusion clauses or trailing periods usually mark a sentence.
    if re.search(r"[가-힣]\.\s*$", cleaned):
        return True
    if cleaned.endswith("다.") or cleaned.endswith("이다") or cleaned.endswith("했다"):
        return True
    # Comma / em-dash density implies prose, not a single concept.
    punctuation = sum(cleaned.count(ch) for ch in (",", "—", "·"))
    if punctuation > 5:
        return True
    return False


def _is_concept_shaped_heading(cleaned: str, whitelist_terms: Set[str]) -> bool:
    """True iff ``cleaned`` is short enough and looks like a concept noun phrase.

    A heading qualifies as a Concept only if it is short (< 6 words),
    free of sentence-style punctuation, and either contains a whitelisted
    technical token or matches an existing TermRule canonical name/alias.
    """
    if not cleaned:
        return False
    if ":" in cleaned:
        return False
    # Reject anything with sentence-ending punctuation or too many commas.
    if cleaned.count(",") > 0:
        return False
    if re.search(r"[!?。]", cleaned):
        return False
    word_count = len(cleaned.split())
    if word_count == 0 or word_count > 5:
        return False
    lowered = cleaned.lower()
    # Direct match against term-rule whitelist (e.g. "Volumetric Rendering").
    if lowered in whitelist_terms:
        return True
    # Whitelist-token match — any concept-flavored noun.
    for token in _CONCEPT_WHITELIST_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return True
    return False


def relation_for_node_type(node_type: ResearchNodeType) -> str:
    if node_type in {ResearchNodeType.TASK, ResearchNodeType.CAPABILITY, ResearchNodeType.PROBLEM_AREA, ResearchNodeType.RESEARCH_TOPIC}:
        return "addresses"
    if node_type in {ResearchNodeType.DATASET}:
        return "uses_dataset"
    if node_type in {ResearchNodeType.BENCHMARK}:
        return "evaluated_on"
    if node_type in {ResearchNodeType.METRIC}:
        return "uses_metric"
    if node_type in {ResearchNodeType.LIMITATION_CLAIM}:
        return "has_limitation"
    if node_type in {ResearchNodeType.APPROACH_FAMILY}:
        return "belongs_to_approach_family"
    return "uses"


def classify_claim_type(sentence: str) -> ResearchNodeType:
    lowered = sentence.lower()
    if any(marker in lowered for marker in ["outperform", "better", "improve", "성능", "우수", "달성"]):
        return ResearchNodeType.PERFORMANCE_CLAIM
    if any(marker in lowered for marker in ["문제", "limitation", "however", "그러나", "민감"]):
        return ResearchNodeType.LIMITATION_CLAIM
    if any(marker in lowered for marker in ["활용", "because", "통해", "by "]):
        return ResearchNodeType.CAUSAL_CLAIM
    return ResearchNodeType.CLAIM


_INSIDE_FENCE_PROBE = re.compile(r"^```")


def _line_is_inside_fence(text: str, target_index: int) -> bool:
    fence_count = 0
    for line in text.splitlines()[:target_index]:
        if _INSIDE_FENCE_PROBE.match(line.strip()):
            fence_count += 1
    return fence_count % 2 == 1


# Patterns that disqualify a line from ever being a paper title. These are
# anchored on common scraper / Korean-assistant chatter the corpus exposes.
_TITLE_REJECT_PREFIXES: Tuple[str, ...] = (
    "extract ",
    "rt ",
    "tl;dr",
    "📄",
    "논문:",
    "관련 링크:",
    "본 연구",
    "제공하신",
    "제공된 원문",
    "`paper_prompt.txt`",
    "paper_prompt.txt",
    "의미 있는 내용을",
    "중국어 분석",
    "희소 뷰",
    "authors:",
    "저자:",
    "subject:",
    "publish:",
)

# Substrings that mark assistant/scraper chatter; if any appears, the line is
# not a real research title regardless of where it sits.
_TITLE_REJECT_CONTAINS: Tuple[str, ...] = (
    "designed by",
    "powered by",
    "github:",
    "disclaimer",
    "번역 완료",
    "파일 생성됨",
    "파일 확인",
    "확인해야 합니다",
    "확인해보겠습니다",
    "들어있는",
    "분석 내용이 없습니다",
    "내용이 포함되어",
    "원문 웹페이지",
    "scrap",
    "[paper](",
    "[논문 분석](",
)


def classify_paper_title_candidate(line: str, *, in_fence: bool = False) -> bool:
    """Strict gate: return True iff ``line`` could plausibly be a paper title.

    This is the production replacement for ``looks_like_research_title``. It
    rejects code fences, markdown file paths, Korean assistant chatter,
    action verbs, RT/TL;DR feed lines, emoji-prefixed status messages, and
    any line that lives inside a fenced code block.
    """
    if line is None:
        return False
    if in_fence:
        return False
    cleaned = line.strip()
    if not cleaned:
        return False
    if len(cleaned) < 3 or len(cleaned) > 220:
        return False
    lowered = cleaned.lower()
    # Code fences and bare fence markers ("```markdown", "```python") are
    # never titles. We also reject inline backtick-only lines.
    if cleaned.startswith("```") or cleaned == "```" or re.fullmatch(r"`+\s*[a-zA-Z]*\s*`*", cleaned):
        return False
    if cleaned in {"```markdown", "```python", "```javascript", "```bash"}:
        return False
    # Reject literal markdown filenames or paths.
    if re.search(r"\.(?:md|txt|json|yaml|yml|csv|ipynb)\b", lowered):
        return False
    if "/" in cleaned and re.search(r"`[^`]*\.(?:md|txt|py|json|ipynb)`", cleaned):
        return False
    # Reject lines that are mostly URLs / arxiv references.
    if cleaned.lower().startswith(("http://", "https://", "arxiv.org", "doi.org")):
        return False
    if re.fullmatch(r"arXiv:\d{4}\.\d{4,6}", cleaned, flags=re.IGNORECASE):
        return False
    # Lookahead-style chatter: "RT @X:", "[Paper]", "[논문 분석]"
    if any(lowered.startswith(prefix) for prefix in _TITLE_REJECT_PREFIXES):
        return False
    if any(token in lowered for token in _TITLE_REJECT_CONTAINS):
        return False
    # Common Korean status sentences end with "~다." / "~합니다." — not titles.
    if re.search(r"[가-힣](?:다|니다|합니다|겠습니다|입니다)\.\s*$", cleaned):
        return False
    # Sentence-ish lines with multiple terminal periods are not titles.
    if cleaned.count("。") >= 1:
        return False
    # Reject anything that opens with an action verb pattern.
    if re.match(r"^(?:I |We |You |Please |Note that |Here )", cleaned, flags=re.IGNORECASE):
        return False
    return bool(re.search(r"[A-Za-z가-힣]", cleaned))


def looks_like_research_title(text: str) -> bool:
    """Backward-compatible thin wrapper over :func:`classify_paper_title_candidate`."""
    return classify_paper_title_candidate(text, in_fence=False)


def extract_title(text: str, source_path: Optional[str]) -> str:
    """Extract the human paper title, scanning multiple candidate lines.

    Strategy for ``paper.md`` files (in priority order):

    1. ``# <title>`` *after* a ``## #N`` rank marker (papers.cool layout).
    2. ``<title> | Cool Papers`` line.
    3. The first heading-level title that survives
       :func:`classify_paper_title_candidate`.
    4. arXiv placeholder fallback if nothing else qualifies.
    """
    metadata = extract_source_metadata(text, source_path)
    arxiv_id = str(metadata.get("arxiv_id", ""))
    lines = text.splitlines()

    # Pre-compute a parallel "is this line inside a fenced block?" mask.
    fence_state = False
    fence_mask: List[bool] = []
    for raw in lines:
        stripped_raw = raw.strip()
        is_fence_line = bool(_INSIDE_FENCE_PROBE.match(stripped_raw))
        fence_mask.append(fence_state)
        if is_fence_line:
            fence_state = not fence_state

    # Priority 1: title heading immediately after a ``## #N`` rank line.
    rank_marker_re = re.compile(r"^#{1,6}\s*#?\d+\s*$")
    for idx, raw in enumerate(lines):
        if not rank_marker_re.match(raw.strip()):
            continue
        # Walk forward up to 8 lines looking for the first heading.
        for follow in lines[idx + 1 : idx + 9]:
            stripped = follow.strip()
            if not stripped:
                continue
            heading_match = re.match(r"^#{1,6}\s+(.+)$", stripped)
            if heading_match:
                candidate = _strip_papers_cool_chrome(heading_match.group(1))
                if classify_paper_title_candidate(candidate, in_fence=False):
                    return candidate
            break

    # Priority 2: "<title> | Cool Papers" pattern, anywhere outside fences.
    for idx, raw in enumerate(lines):
        if fence_mask[idx]:
            continue
        if " | Cool Papers" in raw:
            candidate = raw.split(" | Cool Papers", 1)[0].strip().lstrip("# ").strip()
            candidate = _strip_papers_cool_chrome(candidate)
            if classify_paper_title_candidate(candidate, in_fence=False):
                return candidate

    # Priority 3: any other heading line that survives the title gate.
    for idx, raw in enumerate(lines):
        in_fence = fence_mask[idx]
        stripped = raw.strip().strip("# ").strip()
        if not stripped or stripped.startswith(">"):
            continue
        if stripped.startswith("논문 분석:"):
            continue
        if arxiv_id and stripped == arxiv_id:
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        if stripped in {"총계: 1", "Total: 1", "검색", "필터", "하이라이트", "내보내기", "저장"}:
            continue
        stripped = re.sub(r"^(?:#?\d+|#\d+)\s+(?=\S)", "", stripped).strip()
        stripped = _strip_papers_cool_chrome(stripped)
        if classify_paper_title_candidate(stripped, in_fence=in_fence):
            return stripped

    if source_path:
        return Path(source_path).stem
    return "Untitled Source"


def _strip_papers_cool_chrome(text: str) -> str:
    """Remove papers.cool decorations from a candidate title line."""
    out = text.strip()
    if " | Cool Papers" in out:
        out = out.split(" | Cool Papers", 1)[0].strip()
    if out.endswith("  "):
        out = out.rstrip()
    return out


def is_verified_paper_title(title: str, metadata: Dict[str, object]) -> bool:
    if not classify_paper_title_candidate(title, in_fence=False):
        return False
    arxiv_id = str(metadata.get("arxiv_id") or "")
    if title in {"paper", "main", "abstract"} or (arxiv_id and title == arxiv_id):
        return False
    return True


_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?)/([A-Za-z0-9_.\-]{1,100})",
    re.IGNORECASE,
)


# Known research / industry orgs whose GitHub handle is the org name. We use
# this as a denylist of "looks personal" rather than positive matching — most
# orgs have hyphens or multi-token names.
_KNOWN_ORG_OWNERS: Set[str] = {
    "facebookresearch",
    "google-research",
    "google",
    "googleresearch",
    "deepmind",
    "google-deepmind",
    "openai",
    "anthropic",
    "microsoft",
    "microsoftresearch",
    "nvidia",
    "nvlabs",
    "huggingface",
    "stanford-crfm",
    "stability-ai",
    "stabilityai",
    "tencent",
    "tencent-hunyuan",
    "alibaba-research",
    "bytedance",
    "salesforceresearch",
    "apple",
    "intel-isl",
    "iclr",
    "neurips",
    "skalskip",
    "url-kaist",
}


def _looks_like_organization_owner(owner: str) -> bool:
    """Heuristic: does a GitHub ``owner`` slug look like an org rather than a person?

    Heuristics:
      * Known org slugs always qualify.
      * Hyphenated names (>= 2 tokens) usually indicate an org (``foo-research``,
        ``meta-llama``).
      * Camel-cased multi-word names (``FacebookResearch``) qualify.
      * Anything else (single Latin name, e.g. ``alice123``) is treated as
        personal and skipped.
    """
    if not owner:
        return False
    lowered = owner.lower()
    if lowered in _KNOWN_ORG_OWNERS:
        return True
    if "-" in owner and len(owner.split("-")) >= 2:
        return True
    if re.search(r"[a-z][A-Z]", owner):
        return True
    if owner.lower().endswith(("research", "labs", "ai", "team")):
        return True
    return False


def extract_source_metadata(text: str, source_path: Optional[str]) -> Dict[str, object]:
    metadata: Dict[str, object] = {}
    arxiv_id = extract_arxiv_id_from_path(source_path)
    if not arxiv_id:
        arxiv_match = re.search(r"arxiv(?:\.org/abs/|:\s*)(\d{4}\.\d{4,6})", text, flags=re.IGNORECASE)
        if not arxiv_match and source_path:
            arxiv_match = re.search(r"(\d{4}\.\d{4,6})", source_path)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1)
    if arxiv_id:
        metadata["arxiv_id"] = arxiv_id
    date_match = re.search(r"분석일:\s*(\d{4}-\d{2}-\d{2})", text)
    if not date_match and source_path:
        date_match = re.search(r"daily/(\d{4}-\d{2}-\d{2})/", source_path)
    if date_match:
        metadata["analysis_date"] = date_match.group(1)
    # Repository identity: parse the *first* GitHub URL that looks like a
    # repository root. We deliberately ignore the well-known papers.cool
    # mirror which appears as a footer in every scrape.
    for match in _GITHUB_URL_RE.finditer(text):
        owner = match.group(1)
        repo = match.group(2)
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        normalized = f"{owner.lower()}/{repo.lower()}"
        if normalized in {"bojone/papers.cool"}:
            continue
        metadata["github_repo"] = normalized
        metadata["repo_url"] = f"https://github.com/{owner}/{repo}"
        break
    return metadata


def infer_research_field(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["gaussian", "splatting", "novel view", "3d", "4d", "slam"]):
        return "3D/4D Vision and Reconstruction"
    if any(term in lowered for term in ["llm", "reasoning", "language model"]):
        return "LLM Reasoning"
    return "Research Literature"


def find_evidence(text: str, patterns: Iterable[str]) -> Optional[str]:
    lowered = text.lower()
    for pattern in patterns:
        if pattern.lower() in lowered:
            for sentence in split_sentences(text):
                if pattern.lower() in sentence.lower():
                    return sentence.strip()
            return pattern
    return None


def split_sentences(text: str) -> List[str]:
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    # Korean/English mixed notes usually separate claims with periods.
    parts = re.split(r"(?<=[.!?。])\s+", cleaned)
    return [part.strip() for part in parts if part.strip()]


def normalize_display_name(name: str) -> str:
    text = re.sub(r"\s+", " ", name.strip())
    if not text:
        return "Unnamed"
    # Preserve common all-caps acronyms; otherwise title-case only all-lower labels.
    if text.islower() and " " in text and re.fullmatch(r"[a-z0-9 ]+", text):
        return text.title()
    return text


def stable_id(node_type: str, name: str) -> str:
    digest = hashlib.sha1(f"{node_type}:{name.lower()}".encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "node"
    return f"{node_type}:{slug}:{digest}"


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Document-structure helpers
# ---------------------------------------------------------------------------


def _collect_document_sections(text: str) -> List[Dict[str, object]]:
    """Return all markdown headings as structural metadata."""
    out: List[Dict[str, object]] = []
    seen: Set[Tuple[int, str]] = set()
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
        if not match:
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        if not heading:
            continue
        anchor = re.sub(r"[^A-Za-z0-9가-힣]+", "-", heading.lower()).strip("-")[:80]
        key = (level, heading)
        if key in seen:
            continue
        seen.add(key)
        out.append({"level": level, "text": heading, "anchor": anchor})
    return out


# ---------------------------------------------------------------------------
# Typed-entity extractors
# ---------------------------------------------------------------------------


_AUTHOR_BLOCK_RE = re.compile(
    r"^\s*(?:authors?|저자)\s*[:：]\s*$",
    re.IGNORECASE,
)


def extract_authors(text: str) -> List[str]:
    """Extract author Person names from ``Authors:`` / ``저자:`` blocks."""
    if not text:
        return []
    lines = text.splitlines()
    authors: List[str] = []
    seen: Set[str] = set()

    def _emit(raw_name: str) -> None:
        name = raw_name.strip(" \t,，;:•-")
        if not name:
            return
        if not re.search(r"[A-Za-z가-힣]", name):
            return
        tokens = re.findall(r"\S+", name)
        if not tokens or len(tokens) > 8:
            return
        name = re.sub(r"\s*\d+\s*$", "", name).strip()
        name = re.sub(r"\(.*?\)$", "", name).strip()
        normalized = name
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        authors.append(normalized)

    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if _AUTHOR_BLOCK_RE.match(stripped):
            idx += 1
            while idx < len(lines):
                line = lines[idx].strip().lstrip("*-•").strip()
                if not line:
                    break
                lowered = line.lower()
                if lowered.startswith(
                    (
                        "subject",
                        "publish",
                        "주제",
                        "게시",
                        "abstract",
                        "include",
                        "exclude",
                        "designed by",
                    )
                ):
                    break
                if line.startswith("#"):
                    break
                cleaned_line = re.sub(r"[\*†‡§¶]+", " ", line).strip()
                for chunk in re.split(r"\s*,\s*|\s+and\s+", cleaned_line):
                    _emit(chunk)
                idx += 1
            continue
        match = re.match(r"^(?:authors?|저자)\s*[:：]\s*(.+)$", stripped, re.IGNORECASE)
        if match:
            cleaned_inline = re.sub(r"[\*†‡§¶]+", " ", match.group(1)).strip()
            for chunk in re.split(r"\s*,\s*|\s+and\s+", cleaned_inline):
                _emit(chunk)
        idx += 1
    return sorted(authors, key=str.casefold)


_ORG_LINE_RE = re.compile(
    r"^(?:organi[sz]ation|affiliation|소속|기관)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)


def extract_organizations(text: str) -> List[str]:
    """Extract organizations from ``Organization:`` / ``Affiliation:`` lines."""
    if not text:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for line in text.splitlines():
        match = _ORG_LINE_RE.match(line.strip())
        if not match:
            continue
        for chunk in re.split(r"\s*,\s*|\s*;\s*", match.group(1)):
            org = chunk.strip()
            if not org or len(org) < 2 or len(org) > 120:
                continue
            key = org.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(org)
    return sorted(out, key=str.casefold)


def extract_eval_entities(
    text: str,
) -> Tuple[List[str], List[str], List[str], List[Dict[str, object]]]:
    """Return (datasets, benchmarks, metrics, results) extracted from ``text``."""
    from .term_registry import (
        benchmark_registry,
        dataset_registry,
        find_registry_matches,
        metric_registry,
    )

    if not text:
        return [], [], [], []
    cleaned = strip_non_research_scaffold(text)
    datasets = find_registry_matches(cleaned, dataset_registry())
    benchmarks = find_registry_matches(cleaned, benchmark_registry())
    metrics = find_registry_matches(cleaned, metric_registry())
    datasets = [d for d in datasets if d not in set(benchmarks)]

    results: List[Dict[str, object]] = []
    metric_pattern = "|".join(re.escape(m) for m in metric_registry())
    bench_pattern = "|".join(re.escape(b) for b in benchmark_registry())
    if metric_pattern and bench_pattern:
        result_re = re.compile(
            rf"({metric_pattern})\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s+(?:on|in|at)\s+({bench_pattern})",
            re.IGNORECASE,
        )
        for match in result_re.finditer(cleaned):
            metric = match.group(1)
            value = match.group(2)
            benchmark = match.group(3)
            results.append(
                {
                    "name": f"{metric}={value} on {benchmark}",
                    "metric": metric,
                    "value": value,
                    "benchmark": benchmark,
                    "evidence": match.group(0),
                }
            )
    results.sort(key=lambda r: r["name"])
    return datasets, benchmarks, metrics, results


_NOVEL_METHOD_PATTERNS: Tuple[str, ...] = (
    r"우리는\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{1,80}?)(?:라고\s*부른|를\s*제안한다|을\s*제안한다)",
    r"\bwe\s+(?:propose|introduce|present)\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{1,80}?)(?:[,.\s]|$)",
    r"본\s+논문은\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{1,80}?)(?:라고|를)",
    r"called\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{1,80}?)(?:[,.\s]|$)",
)


def extract_method_entities(
    text: str,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Return (algorithms, models, training_paradigms, inference_strategies)."""
    from .term_registry import (
        find_registry_matches,
        inference_strategy_registry,
        model_registry,
        training_paradigm_registry,
    )

    if not text:
        return [], [], [], []
    cleaned = strip_non_research_scaffold(text)

    algorithms: Set[str] = set()
    for pattern in _NOVEL_METHOD_PATTERNS:
        for match in re.finditer(pattern, cleaned, flags=re.IGNORECASE):
            name = match.group(1).strip().rstrip(".,;:")
            if not name:
                continue
            if len(name) < 2 or len(name) > 80:
                continue
            if name.lower() in {
                "the method",
                "an algorithm",
                "a method",
                "novel method",
                "the framework",
                "the approach",
                "a new",
                "a novel",
            }:
                continue
            tokens = name.split()
            if not tokens:
                continue
            head = tokens[0]
            looks_named = (
                bool(re.search(r"[A-Z][a-z0-9]*[A-Z]", head))
                or bool(re.search(r"\d", head))
                or bool(re.match(r"^[A-Z]{2,}$", head))
                or (
                    len(tokens) >= 2
                    and head[0].isupper()
                    and head.lower() not in {"a", "an", "the", "this", "that"}
                )
            )
            if not looks_named:
                continue
            algorithms.add(name)

    models = find_registry_matches(cleaned, model_registry())
    training_paradigms = find_registry_matches(cleaned, training_paradigm_registry())
    inference_strategies = find_registry_matches(cleaned, inference_strategy_registry())

    return (
        sorted(algorithms, key=str.casefold),
        models,
        training_paradigms,
        inference_strategies,
    )


# ---------------------------------------------------------------------------
# Claim extractors
# ---------------------------------------------------------------------------


_CONTRIBUTION_PATTERNS: Tuple[str, ...] = (
    r"\bwe\s+(?:propose|present|introduce|contribute)\b",
    r"\bin\s+this\s+(?:paper|work)\s*,?\s*we\s+(?:propose|present|introduce)\b",
    r"본\s*(?:논문|연구)(?:은|는|에서는)\s.{0,80}?(?:제안한다|소개한다)",
    r"우리는\s+.{0,80}?(?:제안한다|소개한다)",
    r"\b(?:our|the)\s+main\s+contribution\b",
)


def extract_contribution_claims(text: str) -> List[str]:
    return _match_sentences(text, _CONTRIBUTION_PATTERNS)


_COMPARISON_PATTERNS: Tuple[str, ...] = (
    r"\b(?:outperform|outperforms|outperformed)\b",
    r"\b(?:compared\s+with|compared\s+to)\b",
    r"\b(?:state[- ]of[- ]the[- ]art|sota)\b",
    r"\bvs\.?\s+",
    r"\+\s*\d+(?:\.\d+)?\s*%",
    r"보다\s+(?:우수|뛰어난|좋은|나은)",
    r"최신\s+(?:방법|모델|알고리즘)들?(?:과|에|보다)",
    r"기존\s+(?:방법|모델|알고리즘)들?(?:보다|에)",
)


def extract_comparison_claims(text: str) -> List[str]:
    return _match_sentences(text, _COMPARISON_PATTERNS)


_OPEN_QUESTION_PATTERNS: Tuple[str, ...] = (
    r"\bfuture\s+work\b",
    r"\bopen\s+(?:question|problem)s?\b",
    r"\bremains?\s+(?:unclear|an open question|unsolved)\b",
    r"\b(?:limitations?\s+include|main\s+limitations?)\b",
    r"추가\s+연구",
    r"향후\s+연구",
    r"한계점",
    r"해결되지\s+않(?:는|은|았)",
)


def extract_open_questions(text: str) -> List[str]:
    return _match_sentences(text, _OPEN_QUESTION_PATTERNS)


_PERFORMANCE_PATTERNS: Tuple[str, ...] = (
    r"\b(?:improves?|improved|achiev(?:e|es|ed))\b",
    r"\bbetter\b",
    r"\b(?:state[- ]of[- ]the[- ]art|sota)\b",
    r"성능",
    r"달성한다",
    r"우수한",
)


def extract_performance_claims(text: str) -> List[str]:
    return _match_sentences(text, _PERFORMANCE_PATTERNS)


def _match_sentences(text: str, patterns: Sequence[str]) -> List[str]:
    if not text:
        return []
    cleaned = strip_non_research_scaffold(text)
    sentences = split_sentences(cleaned)
    out: List[str] = []
    seen: Set[str] = set()
    for sentence in sentences:
        if any(re.search(pat, sentence, flags=re.IGNORECASE) for pat in patterns):
            key = sentence.strip()
            if not key or key.lower() in seen:
                continue
            seen.add(key.lower())
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Title resolver: handles paper.md files with a missing title but a
# resolvable arXiv id.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedTitle:
    title: Optional[str]
    quality: TitleQuality


def resolve_missing_paper_title(
    arxiv_id: str, text: str, metadata: Mapping[str, object]
) -> ResolvedTitle:
    """Resolve a paper title for ``arxiv_id`` when the body has no clear title.

    Stages:
      1. Re-parse known local scrape fields (``Title:``).
      2. Look up an offline arXiv metadata cache at
         ``.llm-wiki/arxiv-cache.json``.
      3. Emit ``NEEDS_METADATA`` if everything fails. We never invent a title
         from the abstract.
    """
    title_line_re = re.compile(r"^\s*title\s*[:：]\s*(.+)$", re.IGNORECASE)
    for line in text.splitlines():
        match = title_line_re.match(line)
        if match:
            candidate = _strip_papers_cool_chrome(match.group(1).strip())
            if classify_paper_title_candidate(candidate, in_fence=False):
                return ResolvedTitle(candidate, TitleQuality.PAPER_FILE)
    cache_title = _lookup_arxiv_cache(arxiv_id)
    if cache_title and classify_paper_title_candidate(cache_title, in_fence=False):
        return ResolvedTitle(cache_title, TitleQuality.REFERENCE_CONTEXT)
    return ResolvedTitle(None, TitleQuality.NEEDS_METADATA)


def _arxiv_cache_path() -> Path:
    """Return the offline arXiv metadata cache path."""
    override = os.environ.get("LLM_WIKI_ARXIV_CACHE")
    if override:
        return Path(override)
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / ".llm-wiki" / "arxiv-cache.json"
        if candidate.exists():
            return candidate
    return here / ".llm-wiki" / "arxiv-cache.json"


def _lookup_arxiv_cache(arxiv_id: str) -> Optional[str]:
    if not arxiv_id:
        return None
    path = _arxiv_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = payload.get(arxiv_id) if isinstance(payload, dict) else None
    if isinstance(entry, dict):
        title = entry.get("title")
        if isinstance(title, str):
            return title.strip() or None
    return None


# ---------------------------------------------------------------------------
# Post-pass: link Paper <-> Repository pairs that share an arxiv_id.
# ---------------------------------------------------------------------------


def link_paper_repo_pairs(graph: ResearchGraph) -> ResearchGraph:
    """Return a graph with ``Paper -implemented_in-> Repository`` edges added.

    Idempotent: only adds edges that aren't already present, only acts on
    Paper / Repository pairs that explicitly share an ``arxiv_id`` value.
    """
    nodes = list(graph.nodes)
    edges = list(graph.edges)
    repos_by_arxiv: Dict[str, ResearchNode] = {}
    papers_by_arxiv: Dict[str, ResearchNode] = {}
    for node in nodes:
        arxiv = str(node.metadata.get("arxiv_id") or "")
        if not arxiv:
            continue
        if node.type == ResearchNodeType.REPOSITORY:
            repos_by_arxiv.setdefault(arxiv, node)
        elif node.type == ResearchNodeType.PAPER:
            papers_by_arxiv.setdefault(arxiv, node)
    existing_edge_keys = {(edge.source, edge.type, edge.target) for edge in edges}
    for arxiv, paper in papers_by_arxiv.items():
        repo = repos_by_arxiv.get(arxiv)
        if not repo:
            continue
        key = (paper.id, "implemented_in", repo.id)
        if key in existing_edge_keys:
            continue
        edges.append(
            ResearchEdge(
                source=paper.id,
                target=repo.id,
                type="implemented_in",
                evidence=None,
                metadata={},
            )
        )
        existing_edge_keys.add(key)
    return ResearchGraph(nodes=nodes, edges=edges)
