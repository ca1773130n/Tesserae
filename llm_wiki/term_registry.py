"""Typed research-term registry.

Replaces the legacy flat ``DEFAULT_TERM_RULES`` tuple in :mod:`research_graph`.
Each entry pins a controlled-vocabulary term to a specific
:class:`ResearchNodeType` and a specific edge ``relation`` so the extractor
can never silently fall through to a generic ``uses`` edge for a typed term.

The registry is intentionally small and curated. Recall comes from typed
extractors (authors, datasets, benchmarks, metrics, methods, claims) — not
from blowing up the keyword list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from .research_graph import ResearchNodeType


# Edges that the registry is allowed to emit. ``uses`` is the generic
# fallback — a registry entry with ``relation="uses"`` must declare
# ``allow_generic_relation=True`` or registry construction fails.
_TYPED_RELATIONS: FrozenSet[str] = frozenset(
    {
        "uses",
        "addresses",
        "uses_dataset",
        "evaluated_on",
        "uses_metric",
        "belongs_to_approach_family",
        "has_limitation",
        "implemented_in",
    }
)


@dataclass(frozen=True)
class TermEntry:
    """A registered controlled-vocabulary term.

    Attributes:
      canonical_name: display name; how the node is rendered in the wiki.
      node_type: the typed node this term materializes as.
      aliases: other strings that refer to the same canonical term.
      match_scope: ``"any"``, ``"paper_only"``, or ``"digest_only"``. Controls
        which source kinds the entry is matched against.
      relation: the edge type to draw from the source ``Paper`` / document
        to this term. Cannot fall through to generic ``uses`` unless
        ``allow_generic_relation=True``.
      approach_family: optional approach family this term belongs to.
      public: whether the resulting node is allowed on public wiki pages.
      owner: free-form owner tag (team / area) for registry hygiene.
      allow_generic_relation: explicit opt-in for ``relation="uses"``.
    """

    canonical_name: str
    node_type: ResearchNodeType
    aliases: Tuple[str, ...] = ()
    match_scope: str = "any"
    relation: str = "uses"
    approach_family: Optional[str] = None
    public: bool = True
    owner: str = "research"
    allow_generic_relation: bool = False

    def patterns(self) -> Tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


def _validate_entry(entry: TermEntry) -> None:
    if entry.match_scope not in {"any", "paper_only", "digest_only"}:
        raise ValueError(
            f"Invalid match_scope on TermEntry {entry.canonical_name!r}: {entry.match_scope!r}"
        )
    if entry.relation not in _TYPED_RELATIONS:
        raise ValueError(
            f"Invalid relation on TermEntry {entry.canonical_name!r}: {entry.relation!r}"
        )
    if entry.relation == "uses" and not entry.allow_generic_relation:
        raise ValueError(
            f"TermEntry {entry.canonical_name!r} falls through to generic 'uses'. "
            f"Set a typed relation or pass allow_generic_relation=True."
        )


# ---------------------------------------------------------------------------
# Default registry. The set is small on purpose — typed extractors generate
# the majority of nodes. New terms must be added with a typed relation.
# ---------------------------------------------------------------------------


def _default_entries() -> Tuple[TermEntry, ...]:
    return (
        TermEntry(
            "Gaussian Splatting",
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=("3D Gaussian Splatting", "3DGS", "Gaussian splatting"),
            relation="uses",
            approach_family="Gaussian Splatting Reconstruction",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Geometry-Grounded Gaussian Splatting",
            ResearchNodeType.APPROACH_FAMILY,
            aliases=("Geometry-Grounded GS",),
            relation="belongs_to_approach_family",
            approach_family="Geometry-Grounded Gaussian Splatting",
        ),
        TermEntry(
            "Novel View Synthesis",
            ResearchNodeType.TASK,
            aliases=("new view synthesis", "novel view synthesis"),
            relation="addresses",
        ),
        TermEntry(
            "Shape Reconstruction",
            ResearchNodeType.TASK,
            aliases=("형상 재구성", "geometry extraction", "기하 추출"),
            relation="addresses",
        ),
        TermEntry(
            "Stochastic Solid",
            ResearchNodeType.MATHEMATICAL_CONCEPT,
            aliases=("stochastic solid",),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Volumetric Rendering",
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=("volumetric 특성", "volumetric"),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Depth Map",
            ResearchNodeType.TECHNICAL_TERM,
            aliases=("depth map", "depth maps"),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Multi-View Consistency",
            ResearchNodeType.EVALUATION_PROTOCOL,
            aliases=("multi-view consistency",),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Floaters",
            ResearchNodeType.LIMITATION_CLAIM,
            aliases=("floaters",),
            relation="has_limitation",
        ),
        TermEntry(
            "4D Gaussian Splatting",
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=("4DGS", "4D Gaussian splatting"),
            relation="uses",
            approach_family="Dynamic Gaussian Splatting",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Video Diffusion",
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=("video diffusion",),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Point Cloud",
            ResearchNodeType.TECHNICAL_TERM,
            aliases=("point cloud", "4D point cloud"),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Image-to-3D",
            ResearchNodeType.TASK,
            aliases=("image-to-3D",),
            relation="addresses",
        ),
        TermEntry(
            "World Model",
            ResearchNodeType.RESEARCH_TOPIC,
            aliases=("world model", "world models"),
            relation="addresses",
        ),
        TermEntry(
            "Pseudo-Mask",
            ResearchNodeType.TECHNICAL_TERM,
            aliases=("pseudo-mask", "pseudo mask"),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Object-Level Prior",
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=("object-level prior", "object-level pseudo-mask"),
            relation="uses",
            allow_generic_relation=True,
        ),
        TermEntry(
            "Visual SLAM",
            ResearchNodeType.RESEARCH_TOPIC,
            aliases=("Visual SLAM", "SLAM"),
            relation="addresses",
        ),
        TermEntry(
            "Multi-Robot Cooperative Mapping",
            ResearchNodeType.TASK,
            aliases=("multi-robot cooperative mapping",),
            relation="addresses",
        ),
    )


@dataclass(frozen=True)
class TermRegistry:
    entries: Tuple[TermEntry, ...]

    def __post_init__(self) -> None:
        for entry in self.entries:
            _validate_entry(entry)

    @classmethod
    def default(cls) -> "TermRegistry":
        return cls(entries=_default_entries())

    def for_scope(self, scope: str) -> Tuple[TermEntry, ...]:
        if scope not in {"paper", "digest"}:
            raise ValueError(f"Unknown match scope: {scope!r}")
        match_scope = "paper_only" if scope == "paper" else "digest_only"
        return tuple(
            entry for entry in self.entries if entry.match_scope in {"any", match_scope}
        )

    def canonical_names(self) -> Set[str]:
        return {entry.canonical_name for entry in self.entries}

    def all_aliases(self) -> Set[str]:
        out: Set[str] = set()
        for entry in self.entries:
            out.add(entry.canonical_name)
            out.update(entry.aliases)
        return out

    def lookup(self, name: str) -> Optional[TermEntry]:
        """Case-insensitive lookup against canonical names and aliases."""
        lowered = name.strip().lower()
        for entry in self.entries:
            if entry.canonical_name.lower() == lowered:
                return entry
            if any(alias.lower() == lowered for alias in entry.aliases):
                return entry
        return None


# ---------------------------------------------------------------------------
# Curated typed-extractor registries. Kept here so the research-graph module
# stays focused on extraction control flow.
# ---------------------------------------------------------------------------


_DATASETS: Tuple[str, ...] = (
    "DTU",
    "Tanks and Temples",
    "Mip-NeRF360",
    "Mip-NeRF 360",
    "ShapeNet",
    "ImageNet",
    "COCO",
    "MS COCO",
    "ScanNet",
    "Replica",
    "TUM RGB-D",
    "Objaverse",
    "LLFF",
    "Blender",
    "NeRF Synthetic",
    "KITTI",
    "Waymo",
    "Cityscapes",
    "ADE20K",
    "Pascal VOC",
    "Open Images",
    "MotionX",
    "AMASS",
    "HumanML3D",
    "Replica Dataset",
)

_BENCHMARKS: Tuple[str, ...] = (
    "DTU",
    "Tanks and Temples",
    "Mip-NeRF360",
    "Mip-NeRF 360",
    "ScanNet",
    "Replica",
    "ImageNet",
    "MS COCO",
    "COCO",
    "Pascal VOC",
    "Cityscapes",
    "KITTI",
    "MMLU",
    "GSM8K",
    "MATH",
    "HumanEval",
    "MMBench",
    "ARC-Challenge",
    "HellaSwag",
)

_METRICS: Tuple[str, ...] = (
    "PSNR",
    "SSIM",
    "LPIPS",
    "FID",
    "KID",
    "IoU",
    "mIoU",
    "mAP",
    "AP",
    "BLEU",
    "ROUGE",
    "ROUGE-L",
    "METEOR",
    "CIDEr",
    "AbsRel",
    "RMSE",
    "Chamfer Distance",
    "F1",
    "F-score",
    "Recall",
    "Precision",
    "Top-1",
    "Top-5",
    "ATE",
    "RPE",
    "Accuracy",
)

# Curated foundation-model / pretrained-model names. Typed as Model nodes.
_MODELS: Tuple[str, ...] = (
    "Stable Diffusion",
    "Stable Diffusion XL",
    "SDXL",
    "DALL-E",
    "DALL-E 3",
    "Imagen",
    "Midjourney",
    "Sora",
    "Runway Gen-2",
    "Gen-2",
    "GPT-4",
    "GPT-4o",
    "GPT-4 Turbo",
    "GPT-3.5",
    "Claude",
    "Claude Opus",
    "Claude Sonnet",
    "Claude Haiku",
    "Gemini",
    "Gemini Pro",
    "Gemini Ultra",
    "LLaMA",
    "LLaMA 2",
    "LLaMA 3",
    "Mistral",
    "Mixtral",
    "Phi-3",
    "Qwen",
    "DeepSeek",
    "DINO",
    "DINOv2",
    "CLIP",
    "SAM",
    "Segment Anything",
    "Whisper",
    "BERT",
    "T5",
    "ViT",
    "PaLM",
    "Flamingo",
    "BLIP",
    "BLIP-2",
)

# Light-touch training-paradigm registry. These are coarse-grained because
# the paradigm names themselves are the canonical strings.
_TRAINING_PARADIGMS: Tuple[str, ...] = (
    "Supervised Learning",
    "Self-Supervised Learning",
    "Self-supervised learning",
    "Contrastive Learning",
    "Reinforcement Learning",
    "RLHF",
    "DPO",
    "Instruction Tuning",
    "Knowledge Distillation",
    "Fine-tuning",
    "Pretraining",
    "Pre-training",
    "Meta Learning",
    "Few-shot Learning",
    "Zero-shot Learning",
)

_INFERENCE_STRATEGIES: Tuple[str, ...] = (
    "Chain-of-Thought",
    "Tree-of-Thought",
    "Self-Consistency",
    "Beam Search",
    "Top-k Sampling",
    "Top-p Sampling",
    "Nucleus Sampling",
    "Speculative Decoding",
    "MCTS",
    "Monte Carlo Tree Search",
)


def dataset_registry() -> Tuple[str, ...]:
    return _DATASETS


def benchmark_registry() -> Tuple[str, ...]:
    return _BENCHMARKS


def metric_registry() -> Tuple[str, ...]:
    return _METRICS


def model_registry() -> Tuple[str, ...]:
    return _MODELS


def training_paradigm_registry() -> Tuple[str, ...]:
    return _TRAINING_PARADIGMS


def inference_strategy_registry() -> Tuple[str, ...]:
    return _INFERENCE_STRATEGIES


def find_registry_matches(text: str, registry: Iterable[str]) -> List[str]:
    """Return canonical names from ``registry`` whose patterns appear in ``text``.

    Matching is case-insensitive. Multi-word patterns require a word-boundary
    match on each side. Order is sorted, deduplicated, deterministic.
    """
    if not text:
        return []
    matched: Set[str] = set()
    for canonical in registry:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(canonical)}(?![A-Za-z0-9_])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            matched.add(canonical)
    return sorted(matched)
