"""Research-domain literature intelligence graph primitives.

This module is intentionally independent from Cognee/Graphiti. It defines the
controlled research ontology and a deterministic baseline extractor that can be
used in tests and as a guardrail around future Claude/Cognee extraction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


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


ALLOWED_NODE_TYPES: Set[str] = {item.value for item in ResearchNodeType}

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
    ) -> ResearchNode:
        canonical_name = normalize_display_name(name)
        node_id = stable_id(node_type.value, canonical_name)
        existing = self._nodes.get(node_id)
        if existing:
            merged_aliases = sorted(set(existing.aliases) | set(aliases or []))
            if merged_aliases == existing.aliases:
                return existing
            node = ResearchNode(
                id=existing.id,
                name=existing.name,
                type=existing.type,
                aliases=merged_aliases,
                description=existing.description or description,
                source_path=existing.source_path or source_path,
                metadata={**existing.metadata, **(metadata or {})},
            )
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


@dataclass(frozen=True)
class TermRule:
    canonical_name: str
    node_type: ResearchNodeType
    aliases: Tuple[str, ...] = ()
    approach_family: Optional[str] = None

    def patterns(self) -> Tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


DEFAULT_TERM_RULES: Tuple[TermRule, ...] = (
    TermRule("Gaussian Splatting", ResearchNodeType.METHODOLOGICAL_CONCEPT, ("3D Gaussian Splatting", "3DGS", "GS"), "Gaussian Splatting Reconstruction"),
    TermRule("Geometry-Grounded Gaussian Splatting", ResearchNodeType.APPROACH_FAMILY, ("Geometry-Grounded GS",), "Geometry-Grounded Gaussian Splatting"),
    TermRule("Novel View Synthesis", ResearchNodeType.TASK, ("new view synthesis",)),
    TermRule("Shape Reconstruction", ResearchNodeType.TASK, ("형상 재구성", "geometry extraction", "기하 추출")),
    TermRule("Stochastic Solid", ResearchNodeType.MATHEMATICAL_CONCEPT, ("stochastic solid",)),
    TermRule("Volumetric Rendering", ResearchNodeType.METHODOLOGICAL_CONCEPT, ("volumetric 특성", "volumetric")),
    TermRule("Depth Map", ResearchNodeType.TECHNICAL_TERM, ("depth map", "depth maps")),
    TermRule("Multi-View Consistency", ResearchNodeType.EVALUATION_PROTOCOL, ("multi-view consistency",)),
    TermRule("Floaters", ResearchNodeType.LIMITATION_CLAIM, ("floaters",)),
    TermRule("4D Gaussian Splatting", ResearchNodeType.METHODOLOGICAL_CONCEPT, ("4DGS", "4D Gaussian Splatting"), "Dynamic Gaussian Splatting"),
    TermRule("Video Diffusion", ResearchNodeType.METHODOLOGICAL_CONCEPT, ("video diffusion",)),
    TermRule("Point Cloud", ResearchNodeType.TECHNICAL_TERM, ("point cloud", "4D point cloud")),
    TermRule("Image-to-3D", ResearchNodeType.TASK, ("image-to-3D",)),
    TermRule("World Model", ResearchNodeType.RESEARCH_TOPIC, ("world model", "world models")),
    TermRule("Pseudo-Mask", ResearchNodeType.TECHNICAL_TERM, ("pseudo-mask", "pseudo mask")),
    TermRule("Object-Level Prior", ResearchNodeType.METHODOLOGICAL_CONCEPT, ("object-level prior", "object-level pseudo-mask")),
    TermRule("Visual SLAM", ResearchNodeType.RESEARCH_TOPIC, ("Visual SLAM", "SLAM")),
    TermRule("Multi-Robot Cooperative Mapping", ResearchNodeType.TASK, ("multi-robot cooperative mapping",)),
)


class ResearchGraphExtractor:
    """Deterministic baseline extractor for research-literature intelligence graphs.

    The long-term extractor should be LLM-backed, but this baseline enforces the
    domain ontology and provides stable tests/evaluation fixtures.
    """

    def __init__(self, term_rules: Sequence[TermRule] = DEFAULT_TERM_RULES) -> None:
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
        title = extract_title(text, source_path)
        source_type = source_kind_to_node_type(source_kind, source_path)
        paper_metadata = {"source_kind": source_kind, **extract_source_metadata(text, source_path)}
        paper = builder.add_node(title, source_type, source_path=source_path, metadata=paper_metadata)

        if source_type in {ResearchNodeType.SOURCE_DOCUMENT, ResearchNodeType.REPOSITORY, ResearchNodeType.PROJECT}:
            self._add_document_structure(builder, paper, text, source_path)
            return builder.build()

        field = builder.add_node(infer_research_field(text), ResearchNodeType.RESEARCH_FIELD)
        builder.add_edge(paper, "part_of", field)

        matched_terms: List[ResearchNode] = []
        for rule in self.term_rules:
            evidence = find_evidence(text, rule.patterns())
            if not evidence:
                continue
            node = builder.add_node(
                rule.canonical_name,
                rule.node_type,
                aliases=list(rule.aliases),
                source_path=source_path,
            )
            matched_terms.append(node)
            relation = relation_for_node_type(rule.node_type)
            builder.add_edge(paper, relation, node, evidence=evidence)

            span = self._add_evidence(builder, paper, evidence, source_path)
            claim = self._add_claim_for_term(builder, paper, node, evidence, source_path)
            builder.add_edge(claim, "evidenced_by", span, evidence=evidence)
            builder.add_edge(claim, "mentioned_in", paper, evidence=evidence)

            if rule.approach_family:
                family = builder.add_node(rule.approach_family, ResearchNodeType.APPROACH_FAMILY)
                builder.add_edge(paper, "belongs_to_approach_family", family, evidence=evidence)
                if node.type != ResearchNodeType.APPROACH_FAMILY:
                    builder.add_edge(family, "uses", node, evidence=evidence)

        self._add_comparative_claims(builder, paper, text, source_path)
        self._connect_related_terms(builder, matched_terms, text)
        return builder.build()

    def _add_document_structure(self, builder: ResearchGraphBuilder, document: ResearchNode, text: str, source_path: Optional[str]) -> None:
        for heading in extract_markdown_headings(text)[:24]:
            if heading.lower() == document.name.lower():
                continue
            concept = builder.add_node(
                heading,
                ResearchNodeType.CONCEPT,
                description=f"Section heading in {document.name}",
                source_path=source_path,
                metadata={"source_kind": "document_heading"},
            )
            builder.add_edge(document, "documents", concept)

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

    def _add_comparative_claims(
        self, builder: ResearchGraphBuilder, paper: ResearchNode, text: str, source_path: Optional[str]
    ) -> None:
        sentences = split_sentences(text)
        for sentence in sentences:
            if any(marker in sentence.lower() for marker in ["우수", "outperform", "better", "improve", "성능"]):
                claim = builder.add_node(
                    "Performance claim: " + truncate(sentence, 96),
                    ResearchNodeType.PERFORMANCE_CLAIM,
                    description=sentence,
                    source_path=source_path,
                )
                span = self._add_evidence(builder, paper, sentence, source_path)
                builder.add_edge(paper, "supports_claim", claim, evidence=sentence)
                builder.add_edge(claim, "evidenced_by", span, evidence=sentence)
                if "dataset" in sentence.lower() or "데이터셋" in sentence:
                    dataset = builder.add_node("Public Datasets", ResearchNodeType.DATASET)
                    builder.add_edge(claim, "evaluated_on", dataset, evidence=sentence)

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


def source_kind_to_node_type(source_kind: str, source_path: Optional[str]) -> ResearchNodeType:
    lowered = (source_kind or "").lower()
    path = (source_path or "").lower()
    if "paper" in lowered or "/papers/" in path or path.endswith("paper.md") or "arxiv" in path:
        return ResearchNodeType.PAPER
    if "repo" in lowered or "repo" in path or "github" in path:
        return ResearchNodeType.REPOSITORY
    return ResearchNodeType.SOURCE_DOCUMENT


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


def extract_title(text: str, source_path: Optional[str]) -> str:
    """Extract the human paper title, not scraper scaffolding headings.

    The papers.cool notes often start with ``# 논문 분석: <arxiv_id>`` and then
    ``## <arxiv_id>`` before the real title. Those are metadata headings, not the
    research artifact title.
    """
    metadata = extract_source_metadata(text, source_path)
    arxiv_id = str(metadata.get("arxiv_id", ""))
    candidates: List[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("# ").strip()
        if not stripped or stripped.startswith(">"):
            continue
        if stripped.startswith("논문 분석:"):
            continue
        if arxiv_id and stripped == arxiv_id:
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        if stripped in {"총계: 1", "검색", "필터", "하이라이트", "내보내기", "저장"}:
            continue
        if " | Cool Papers" in stripped:
            stripped = stripped.split(" | Cool Papers", 1)[0].strip()
        if stripped.endswith("  "):
            stripped = stripped.rstrip()
        if looks_like_research_title(stripped):
            candidates.append(stripped)
    if candidates:
        return candidates[0]
    if source_path:
        return Path(source_path).stem
    return "Untitled Source"


def looks_like_research_title(text: str) -> bool:
    if len(text) < 3:
        return False
    lowered = text.lower()
    if any(skip in lowered for skip in ["designed by", "powered by", "github:", "disclaimer"]):
        return False
    return bool(re.search(r"[A-Za-z가-힣]", text))


def extract_source_metadata(text: str, source_path: Optional[str]) -> Dict[str, object]:
    metadata: Dict[str, object] = {}
    arxiv_match = re.search(r"arxiv(?:\.org/abs/|:\s*)(\d{4}\.\d{4,5})", text, flags=re.IGNORECASE)
    if not arxiv_match and source_path:
        arxiv_match = re.search(r"(\d{4}\.\d{4,5})", source_path)
    if arxiv_match:
        metadata["arxiv_id"] = arxiv_match.group(1)
    date_match = re.search(r"분석일:\s*(\d{4}-\d{2}-\d{2})", text)
    if not date_match and source_path:
        date_match = re.search(r"daily/(\d{4}-\d{2}-\d{2})/", source_path)
    if date_match:
        metadata["analysis_date"] = date_match.group(1)
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
