"""Karpathy three-layer schema files.

Karpathy's LLM Wiki gist defines a three-layer structure: ``raw/`` (immutable
sources) → ``wiki/`` (LLM-generated knowledge pages) → ``schema/`` (rules and
config that govern how the wiki is built and read). Our raw layer is
``data/``, our wiki layer is ``.tesserae/wiki/<kind>/<slug>.md``, and the
schema layer was previously implicit in :mod:`tesserae.research_graph`'s
enums. This module makes the schema layer explicit by writing four
top-level wiki files that future ingest passes (and human readers) can
reference:

* ``purpose.md`` — what this wiki is *for*. Auto-seeded once; preserved on
  later compiles unless the user opts in to overwriting.
* ``schema.md`` — the controlled ontology, generated from
  :class:`ResearchNodeType` and ``ALLOWED_EDGE_TYPES`` so it stays in sync.
* ``index.md`` — wiki-layer table of contents with per-kind counts.
* ``log.md`` — chronological build log sourced from
  ``.build-history.jsonl``.

All four files are content-stable across recompiles (``index.md`` and
``log.md`` re-derive from current state; ``schema.md`` is generated;
``purpose.md`` is treated as user-owned). They live at
``.tesserae/wiki/<file>.md`` next to the kind subdirectories.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchGraph,
    ResearchNodeType,
)
from .wiki_projector import (
    ASSERTION_LAYER_TYPES,
    CODE_GRAPH_TYPES,
    kind_for_node,
)


# Public sentinel that marks the editable section of ``purpose.md``. Anything
# above the marker is owned by the generator and may be regenerated; anything
# below is preserved verbatim.
PURPOSE_MARKER = "<!-- editable below this line — your text is preserved on recompile -->"


# Ordering of node-type sections in ``schema.md``. We list each kind once;
# the kind-for-type table at the end shows the full enum.
_SCHEMA_SECTIONS: Sequence[tuple[str, str, Sequence[ResearchNodeType]]] = (
    (
        "Field / taxonomy layer",
        "Where a paper or concept *sits* in the research landscape.",
        (
            ResearchNodeType.RESEARCH_FIELD,
            ResearchNodeType.RESEARCH_TOPIC,
            ResearchNodeType.PROBLEM_AREA,
            ResearchNodeType.APPROACH_FAMILY,
            ResearchNodeType.TREND,
        ),
    ),
    (
        "Source / artifact layer",
        "Concrete things in the world — papers, code, models, datasets.",
        (
            ResearchNodeType.SOURCE_DOCUMENT,
            ResearchNodeType.PAPER,
            ResearchNodeType.REPOSITORY,
            ResearchNodeType.PROJECT,
            ResearchNodeType.MODEL,
            ResearchNodeType.DATASET,
            ResearchNodeType.BENCHMARK,
            ResearchNodeType.METRIC,
            ResearchNodeType.RESULT,
            ResearchNodeType.ORGANIZATION,
            ResearchNodeType.PERSON,
        ),
    ),
    (
        "Concept layer",
        "Reusable building blocks — definitions, algorithms, patterns.",
        (
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
        ),
    ),
    (
        "Assertion layer (private — rendered inline only)",
        "Claims and the evidence that grounds them. No dedicated URLs.",
        (
            ResearchNodeType.CLAIM,
            ResearchNodeType.CONTRIBUTION_CLAIM,
            ResearchNodeType.PERFORMANCE_CLAIM,
            ResearchNodeType.COMPARISON_CLAIM,
            ResearchNodeType.LIMITATION_CLAIM,
            ResearchNodeType.CAUSAL_CLAIM,
            ResearchNodeType.OPEN_QUESTION,
            ResearchNodeType.EVIDENCE_SPAN,
        ),
    ),
    (
        "Synthesis layer (generated)",
        "Higher-order pages produced by ``SynthesisProjector``.",
        (ResearchNodeType.SYNTHESIS,),
    ),
    (
        "Code-graph layer (private — separate artifact)",
        "Lives in ``code-graph.json``, not in the public website.",
        tuple(sorted(CODE_GRAPH_TYPES, key=lambda t: t.value)),
    ),
)


_TYPE_BLURBS: Mapping[ResearchNodeType, str] = {
    ResearchNodeType.RESEARCH_FIELD: "A broad area of research, e.g. *3D Reconstruction*.",
    ResearchNodeType.RESEARCH_TOPIC: "A topic within a field; finer-grained than a field.",
    ResearchNodeType.PROBLEM_AREA: "A problem the field is trying to solve.",
    ResearchNodeType.APPROACH_FAMILY: "A family of methods that share an approach (e.g. *Gaussian Splatting*).",
    ResearchNodeType.TREND: "A trend extracted across multiple papers/dates.",
    ResearchNodeType.SOURCE_DOCUMENT: "A markdown source — digest, raw note, doc.",
    ResearchNodeType.PAPER: "A paper, identified by arXiv id when available.",
    ResearchNodeType.REPOSITORY: "A code repository (usually GitHub).",
    ResearchNodeType.PROJECT: "Legacy alias for ``Repository``; kept for compatibility.",
    ResearchNodeType.MODEL: "A trained model (e.g. *Stable Diffusion*).",
    ResearchNodeType.DATASET: "A dataset used for training or evaluation.",
    ResearchNodeType.BENCHMARK: "A benchmark used for evaluation (e.g. *DTU*).",
    ResearchNodeType.METRIC: "An evaluation metric (e.g. *PSNR*).",
    ResearchNodeType.RESULT: "A specific numeric result on a benchmark/metric.",
    ResearchNodeType.ORGANIZATION: "An organization (lab, company, university).",
    ResearchNodeType.PERSON: "An author or contributor.",
    ResearchNodeType.CODE_PROJECT: "Code-graph: the local workspace project.",
    ResearchNodeType.SOURCE_FILE: "Code-graph: a single source file.",
    ResearchNodeType.CODE_MODULE: "Code-graph: a logical module / package.",
    ResearchNodeType.CODE_CLASS: "Code-graph: a class definition.",
    ResearchNodeType.CODE_FUNCTION: "Code-graph: a function/method definition.",
    ResearchNodeType.DEPENDENCY: "Code-graph: an external dependency.",
    ResearchNodeType.CONCEPT: "A general research concept.",
    ResearchNodeType.TECHNICAL_TERM: "A technical term, often a vocabulary item.",
    ResearchNodeType.MATHEMATICAL_CONCEPT: "A mathematical idea or construction.",
    ResearchNodeType.METHODOLOGICAL_CONCEPT: "A methodological idea (e.g. *Volumetric Rendering*).",
    ResearchNodeType.ALGORITHM: "A specific algorithm or named method.",
    ResearchNodeType.OBJECTIVE_FUNCTION: "An objective / loss function.",
    ResearchNodeType.ARCHITECTURE_PATTERN: "A model architecture pattern.",
    ResearchNodeType.TRAINING_PARADIGM: "A training paradigm (e.g. *Self-Supervised*).",
    ResearchNodeType.INFERENCE_STRATEGY: "An inference-time strategy.",
    ResearchNodeType.EVALUATION_PROTOCOL: "An evaluation protocol.",
    ResearchNodeType.TASK: "A research task (e.g. *Novel View Synthesis*).",
    ResearchNodeType.CAPABILITY: "A model capability claim.",
    ResearchNodeType.CLAIM: "A generic claim attached to a paper.",
    ResearchNodeType.CONTRIBUTION_CLAIM: "An author-stated contribution.",
    ResearchNodeType.PERFORMANCE_CLAIM: "A numeric performance claim.",
    ResearchNodeType.COMPARISON_CLAIM: "A claim that one method beats another.",
    ResearchNodeType.LIMITATION_CLAIM: "An author-stated limitation.",
    ResearchNodeType.CAUSAL_CLAIM: "A causal mechanism claim.",
    ResearchNodeType.OPEN_QUESTION: "An explicitly noted open question.",
    ResearchNodeType.EVIDENCE_SPAN: "A literal evidence span grounding a claim.",
    ResearchNodeType.SYNTHESIS: "A higher-order synthesis page (pulse, daily, weekly, topic, comparison, field overview).",
}


@dataclass(frozen=True)
class KarpathyLayerWriter:
    """Write the Karpathy schema-layer files (purpose, schema, index, log).

    Idempotent: ``purpose.md``, ``schema.md`` and ``index.md`` are
    content-stable on the same inputs and live inside the byte-idempotent
    ``wiki_root`` (the test in ``tests/test_idempotence.py`` enforces that).
    ``log.md`` derives from the volatile build-history ledger so it lives at
    ``log_root`` (default: alongside the ledger, i.e. ``.tesserae/``) instead
    of inside ``wiki_root``. ``purpose.md`` is seeded once and preserved on
    later compiles so user edits survive.
    """

    wiki_root: Path
    log_root: Optional[Path] = None
    site_title: str = "Tesserae"
    project_name: str = ""

    def write_all(self, graph: ResearchGraph, build_history_path: Optional[Path]) -> List[Path]:
        self.wiki_root.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []

        purpose = self._write_purpose_if_missing()
        if purpose:
            written.append(purpose)

        schema = self._write_schema()
        written.append(schema)

        index = self._write_index(graph)
        written.append(index)

        log = self._write_log(build_history_path)
        if log:
            written.append(log)

        return written

    # ---------------------------------------------------------------- purpose
    def _write_purpose_if_missing(self) -> Optional[Path]:
        path = self.wiki_root / "purpose.md"
        if path.exists():
            return None
        content = self._initial_purpose_body()
        path.write_text(content, encoding="utf-8")
        return path

    def _initial_purpose_body(self) -> str:
        title = self.site_title or "Tesserae"
        project = self.project_name or "this project"
        return (
            f"# Purpose — {title}\n\n"
            f"This wiki is the durable knowledge base for **{project}**. It is\n"
            "Karpathy's three-layer LLM Wiki applied here:\n\n"
            "1. **Raw** — files under `data/` (and any other configured sources).\n"
            "2. **Wiki** — markdown pages under `.tesserae/wiki/<kind>/<slug>.md`.\n"
            "3. **Schema** — `purpose.md` (this file), `schema.md`, `index.md`, `log.md`.\n\n"
            "Every ingest pass reads `purpose.md` to keep the wiki on-mission.\n\n"
            f"{PURPOSE_MARKER}\n\n"
            "## Goals\n\n"
            "_Edit this section to describe what your wiki is for._\n\n"
            "- A goal you want every future ingest to keep in mind.\n"
            "- Another goal.\n\n"
            "## Key questions\n\n"
            "_Questions the wiki should help you answer._\n\n"
            "- What are the recent advances in <topic of interest>?\n"
            "- Which papers compare <method A> against <method B>?\n\n"
            "## Out of scope\n\n"
            "_Things this wiki is intentionally not tracking._\n\n"
            "- Areas that aren't relevant to your work.\n"
        )

    # ----------------------------------------------------------------- schema
    def _write_schema(self) -> Path:
        path = self.wiki_root / "schema.md"
        body = self._render_schema()
        path.write_text(body, encoding="utf-8")
        return path

    def _render_schema(self) -> str:
        lines: List[str] = []
        lines.append("# Schema")
        lines.append("")
        lines.append(
            "This file is auto-generated from the controlled ontology in "
            "`tesserae/research_graph.py`. Editing it by hand has no effect — "
            "your changes will be overwritten on the next compile. Edit the "
            "enum or the `ALLOWED_EDGE_TYPES` set instead."
        )
        lines.append("")
        lines.append("## Layers")
        lines.append("")
        for header, blurb, types in _SCHEMA_SECTIONS:
            lines.append(f"### {header}")
            lines.append("")
            lines.append(blurb)
            lines.append("")
            for t in types:
                blurb_t = _TYPE_BLURBS.get(t, "")
                lines.append(f"- **`{t.value}`** — {blurb_t}")
            lines.append("")

        lines.append("## Edge types")
        lines.append("")
        lines.append(
            "The set of edge types is closed. New extraction logic must reuse one "
            "of these or extend the set in `research_graph.py`."
        )
        lines.append("")
        for edge_type in sorted(ALLOWED_EDGE_TYPES):
            lines.append(f"- `{edge_type}`")
        lines.append("")

        lines.append("## Public / private split")
        lines.append("")
        lines.append(
            "* **Public** — surfaces on the website at `.tesserae/site/`. Each public node "
            "type maps to one of the routes `sources` / `concepts` / `entities` / `papers` "
            "/ `repos` / `topics` / `syntheses` / `questions`."
        )
        lines.append(
            "* **Assertion-layer** types are stored in the graph but rendered inline on "
            "detail pages (no dedicated URL)."
        )
        lines.append(
            "* **Code-graph** types live in a separate artifact (`.tesserae/code-graph.json`)."
        )
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------ index
    def _write_index(self, graph: ResearchGraph) -> Path:
        path = self.wiki_root / "index.md"
        body = self._render_index(graph)
        path.write_text(body, encoding="utf-8")
        return path

    def _render_index(self, graph: ResearchGraph) -> str:
        counts: Dict[str, int] = {}
        for node in graph.nodes:
            kind = kind_for_node(node)
            if kind is None:
                continue
            counts[kind] = counts.get(kind, 0) + 1

        lines: List[str] = []
        lines.append("# Index")
        lines.append("")
        lines.append(
            "Auto-generated table of contents over the wiki layer. Each row "
            "links to the index page on the rendered site (relative paths "
            "assume the site is mounted next to this wiki)."
        )
        lines.append("")
        lines.append("| Kind | Count | Route |")
        lines.append("|---|---:|---|")
        kinds = sorted(counts.keys())
        for kind in kinds:
            lines.append(f"| {kind} | {counts[kind]} | `<site>/{kind}/index.html` |")
        if not kinds:
            lines.append("| _(empty)_ | 0 | _no public nodes yet_ |")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------ log
    def _write_log(self, build_history_path: Optional[Path]) -> Optional[Path]:
        if build_history_path is None or not build_history_path.exists():
            return None
        # Log derives from a volatile ledger (timestamps churn per compile).
        # Park it at ``log_root`` so it does not break the byte-idempotence
        # contract of the wiki dir.
        log_dir = self.log_root or self.wiki_root
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "log.md"
        body = self._render_log(build_history_path)
        path.write_text(body, encoding="utf-8")
        return path

    def _render_log(self, build_history_path: Path) -> str:
        try:
            text = build_history_path.read_text(encoding="utf-8")
        except OSError:
            return "# Log\n\n(no build history yet)\n"
        rows: deque[Dict[str, object]] = deque(maxlen=200)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        lines: List[str] = []
        lines.append("# Log")
        lines.append("")
        lines.append(
            "Chronological compile log, sourced from `.tesserae/.build-history.jsonl`. "
            "Each entry records when the wiki was rebuilt and the resulting graph "
            "size — useful when you're trying to track when a particular paper "
            "was first ingested."
        )
        lines.append("")
        lines.append("| Built at | Research nodes | Research edges | Code nodes | Code edges |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in rows:
            built_at = row.get("built_at") or row.get("timestamp") or ""
            r_nodes = row.get("research_nodes") or row.get("nodes") or 0
            r_edges = row.get("research_edges") or row.get("edges") or 0
            c_nodes = row.get("code_nodes") or 0
            c_edges = row.get("code_edges") or 0
            lines.append(f"| {built_at} | {r_nodes} | {r_edges} | {c_nodes} | {c_edges} |")
        if not rows:
            lines.append("| _(empty)_ | 0 | 0 | 0 | 0 |")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"
