"""Per-project LLM-Wiki workspace helpers.

A project wiki lives under ``<project>/.llm-wiki`` and keeps all generated
artifacts for that project together: graph JSON, batch manifest, SQLite store,
markdown projection, Cognee export bundle, report, and MCP config snippet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional

from .batch import BatchIngestRunner
from .cognee_adapter import CogneeResearchGraphAdapter
from .markdown_projection import GraphMarkdownProjector
from .persistence import SQLiteResearchGraphStore
from .report import GraphReporter
from .research_graph import ResearchCorpusAnalyzer, ResearchEdge, ResearchGraph, ResearchGraphExtractor, ResearchNode, ResearchNodeType
from .temporal import TemporalFactProjector, render_competitive_report


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config: Path
    graph: Path
    manifest: Path
    sqlite: Path
    markdown_projection: Path
    cognee_bundle: Path
    report: Path
    temporal_facts: Path
    competitive_report: Path


class ProjectWiki:
    """Manage a self-contained ``.llm-wiki`` workspace inside a project."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".llm-wiki"
        self.paths = ProjectPaths(
            root=self.root,
            config=self.root / "config.json",
            graph=self.root / "graph.json",
            manifest=self.root / "manifest.json",
            sqlite=self.root / "sqlite.db",
            markdown_projection=self.root / "markdown_projection",
            cognee_bundle=self.root / "cognee_bundle",
            report=self.root / "report.md",
            temporal_facts=self.root / "temporal_facts.jsonl",
            competitive_report=self.root / "competitive_report.md",
        )

    @classmethod
    def init(cls, project_root: str | Path = ".", name: Optional[str] = None, source_kind: str = "SourceDocument", sources: Optional[Iterable[str | Path]] = None) -> "ProjectWiki":
        wiki = cls(project_root)
        wiki.root.mkdir(parents=True, exist_ok=True)
        wiki.paths.markdown_projection.mkdir(parents=True, exist_ok=True)
        wiki.paths.cognee_bundle.mkdir(parents=True, exist_ok=True)
        if not wiki.paths.graph.exists():
            wiki.paths.graph.write_text(ResearchGraph().to_json(indent=2) + "\n", encoding="utf-8")
        if not wiki.paths.manifest.exists():
            wiki.paths.manifest.write_text(json.dumps({"files": {}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        config = {
            "name": name or sanitize_server_name(wiki.project_root.name),
            "project_root": str(wiki.project_root),
            "created": date.today().isoformat(),
            "source_kind": source_kind,
            "sources": [str(source) for source in (sources or [])],
            "graph_path": ".llm-wiki/graph.json",
            "manifest_path": ".llm-wiki/manifest.json",
            "sqlite_path": ".llm-wiki/sqlite.db",
            "markdown_projection_path": ".llm-wiki/markdown_projection",
            "cognee_bundle_path": ".llm-wiki/cognee_bundle",
            "report_path": ".llm-wiki/report.md",
            "temporal_facts_path": ".llm-wiki/temporal_facts.jsonl",
            "competitive_report_path": ".llm-wiki/competitive_report.md",
        }
        wiki.paths.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return wiki

    @classmethod
    def load(cls, project_root: str | Path = ".") -> "ProjectWiki":
        wiki = cls(project_root)
        if not wiki.paths.config.exists():
            raise FileNotFoundError(f"Project wiki is not initialized: {wiki.root}. Run `python3 -m llm_wiki.cli project init` first.")
        return wiki

    def config(self) -> dict:
        return json.loads(self.paths.config.read_text(encoding="utf-8"))

    def ingest(
        self,
        inputs: Iterable[str | Path],
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
    ) -> dict:
        cfg = self.config()
        kind = source_kind or cfg.get("source_kind", "SourceDocument")
        input_paths = [resolve_project_input(self.project_root, item) for item in inputs]
        markdown_files: List[Path] = []
        for input_path in input_paths:
            markdown_files.extend(iter_markdown_files(input_path))
        extractor = ResearchGraphExtractor()
        batch = BatchIngestRunner(extractor=extractor, manifest_path=self.paths.manifest).run(
            markdown_files,
            source_kind=kind,
            changed_only=changed_only,
            limit=limit,
        )
        graphs = batch.graphs or [batch.graph]
        graph = ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=min_trend_sources) if trends else batch.graph
        if changed_only and batch.processed == 0 and self.paths.graph.exists():
            graph = load_graph_file(self.paths.graph)
        self._write_artifacts(graph)
        return {
            "project_root": str(self.project_root),
            "wiki_root": str(self.root),
            "source_kind": kind,
            "processed_files": batch.processed,
            "skipped_files": batch.skipped,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "graph_path": str(self.paths.graph),
            "mcp_server_name": cfg.get("name", sanitize_server_name(self.project_root.name)),
        }

    def compile(
        self,
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
    ) -> dict:
        cfg = self.config()
        sources = cfg.get("sources") or ["."]
        return self.ingest(
            sources,
            source_kind=source_kind,
            changed_only=changed_only,
            limit=limit,
            trends=trends,
            min_trend_sources=min_trend_sources,
        )

    def render_mcp_config(self, server_name: Optional[str] = None, pythonpath: Optional[str] = None) -> str:
        cfg = self.config() if self.paths.config.exists() else {}
        name = sanitize_server_name(server_name or cfg.get("name") or self.project_root.name)
        python_path = pythonpath or str(Path(__file__).resolve().parents[1])
        graph_path = str(self.paths.graph.resolve())
        return (
            "mcp_servers:\n"
            f"  {name}:\n"
            "    command: \"python3\"\n"
            "    args:\n"
            "      - \"-m\"\n"
            "      - \"llm_wiki.mcp_server\"\n"
            "      - \"--graph\"\n"
            f"      - \"{graph_path}\"\n"
            "    env:\n"
            f"      PYTHONPATH: \"{python_path}\"\n"
        )

    def _write_artifacts(self, graph: ResearchGraph) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.paths.graph.write_text(graph.to_json(indent=2) + "\n", encoding="utf-8")
        SQLiteResearchGraphStore(self.paths.sqlite).write_graph(graph, replace=True)
        GraphMarkdownProjector().write_projection(graph, self.paths.markdown_projection)
        CogneeResearchGraphAdapter().write_bundle(graph, self.paths.cognee_bundle)
        report = GraphReporter().render_markdown(GraphReporter().summarize(graph))
        self.paths.report.write_text(report, encoding="utf-8")
        TemporalFactProjector().write_jsonl(graph, self.paths.temporal_facts)
        self.paths.competitive_report.write_text(render_competitive_report(), encoding="utf-8")


def load_graph_file(path: str | Path) -> ResearchGraph:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id=str(raw["id"]),
                name=str(raw["name"]),
                type=ResearchNodeType(str(raw["type"])),
                aliases=[str(alias) for alias in raw.get("aliases", [])],
                description=str(raw.get("description") or ""),
                source_path=raw.get("source_path"),
                metadata=dict(raw.get("metadata") or {}),
            )
            for raw in payload.get("nodes", [])
        ],
        edges=[
            ResearchEdge(
                source=str(raw["source"]),
                target=str(raw["target"]),
                type=str(raw["type"]),
                evidence=raw.get("evidence"),
                metadata=dict(raw.get("metadata") or {}),
            )
            for raw in payload.get("edges", [])
        ],
    )


def resolve_project_input(project_root: Path, item: str | Path) -> Path:
    raw = Path(item)
    return raw if raw.is_absolute() else project_root / raw


def iter_markdown_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".md" else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    files = []
    for child in sorted(path.rglob("*.md")):
        rel = child.relative_to(path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        files.append(child)
    return files


def sanitize_server_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "llm_wiki_project"
