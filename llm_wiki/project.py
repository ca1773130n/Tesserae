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

from .agent_harness import AgentHarnessAdapter, SUPPORTED_AGENT_HARNESSES
from .batch import BatchIngestRunner
from .code_graph import CodeGraphExtractor
from .cognee_adapter import CogneeResearchGraphAdapter
from .deploy import GitHubPagesDeployer
from .site import StaticSiteBuilder
from .synthesis import SynthesisProjector
from .wiki_projector import WikiLayerProjector
from .wiki_store import WikiPageStore
from .graphiti_adapter import GraphitiResearchGraphAdapter
from .markdown_projection import GraphMarkdownProjector
from .obsidian_adapter import ObsidianVaultAdapter
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
    graphiti_episodes: Path
    agent_harness: Path
    obsidian_vault: Path
    site: Path
    wiki: Path


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
            graphiti_episodes=self.root / "graphiti_episodes.jsonl",
            agent_harness=self.root / "agent_harness",
            obsidian_vault=self.root / "obsidian_vault",
            site=self.root / "site",
            wiki=self.root / "wiki",
        )

    @classmethod
    def init(cls, project_root: str | Path = ".", name: Optional[str] = None, source_kind: str = "SourceDocument", sources: Optional[Iterable[str | Path]] = None) -> "ProjectWiki":
        wiki = cls(project_root)
        wiki.root.mkdir(parents=True, exist_ok=True)
        wiki.paths.markdown_projection.mkdir(parents=True, exist_ok=True)
        wiki.paths.cognee_bundle.mkdir(parents=True, exist_ok=True)
        wiki.paths.agent_harness.mkdir(parents=True, exist_ok=True)
        wiki.paths.obsidian_vault.mkdir(parents=True, exist_ok=True)
        wiki.paths.site.mkdir(parents=True, exist_ok=True)
        wiki.paths.wiki.mkdir(parents=True, exist_ok=True)
        for kind in ("sources", "concepts", "entities", "papers", "repos", "topics", "syntheses", "questions"):
            (wiki.paths.wiki / kind).mkdir(parents=True, exist_ok=True)
        if not wiki.paths.graph.exists():
            wiki.paths.graph.write_text(ResearchGraph().to_json(indent=2) + "\n", encoding="utf-8")
        if not wiki.paths.manifest.exists():
            wiki.paths.manifest.write_text(json.dumps({"files": {}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # When the user passes nothing for ``sources``, seed a sensible default
        # that covers the typical project layout: top-level README + docs/ and
        # data/ subtrees (the latter holds research/daily/<date>/ and friends).
        # ``compile()`` also auto-includes ``data/`` even if it wasn't listed
        # explicitly — this default keeps that visible in config.json.
        if sources is None:
            default_sources: List[str] = []
            if (wiki.project_root / "README.md").exists():
                default_sources.append("README.md")
            if (wiki.project_root / "docs").exists():
                default_sources.append("docs")
            if (wiki.project_root / "data").exists():
                default_sources.append("data")
            source_list = default_sources
        else:
            source_list = [str(source) for source in sources]
        config = {
            "name": name or sanitize_server_name(wiki.project_root.name),
            "site_title": "LLM-Wiki",
            "project_root": str(wiki.project_root),
            "created": date.today().isoformat(),
            "source_kind": source_kind,
            "sources": source_list,
            "graph_path": ".llm-wiki/graph.json",
            "manifest_path": ".llm-wiki/manifest.json",
            "sqlite_path": ".llm-wiki/sqlite.db",
            "markdown_projection_path": ".llm-wiki/markdown_projection",
            "cognee_bundle_path": ".llm-wiki/cognee_bundle",
            "report_path": ".llm-wiki/report.md",
            "temporal_facts_path": ".llm-wiki/temporal_facts.jsonl",
            "competitive_report_path": ".llm-wiki/competitive_report.md",
            "graphiti_episodes_path": ".llm-wiki/graphiti_episodes.jsonl",
            "agent_harness_path": ".llm-wiki/agent_harness",
            "obsidian_vault_path": ".llm-wiki/obsidian_vault",
            "site_path": ".llm-wiki/site",
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
        extractor = ResearchGraphExtractor()
        markdown_files: List[Path] = []
        code_inputs: List[Path] = []
        seen_md: set[Path] = set()
        for input_path in input_paths:
            for md in iter_markdown_files(input_path):
                resolved = md.resolve()
                if resolved in seen_md:
                    continue
                seen_md.add(resolved)
                markdown_files.append(md)
            code_inputs.append(input_path)
        markdown_source_kind = "SourceDocument" if kind in {"CodeProject", "Repository", "Project"} else kind
        batch = BatchIngestRunner(extractor=extractor, manifest_path=self.paths.manifest).run(
            markdown_files,
            source_kind=markdown_source_kind,
            changed_only=changed_only,
            limit=limit,
        )
        graphs = batch.graphs or [batch.graph]
        graph = ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=min_trend_sources) if trends else batch.graph
        if kind in {"CodeProject", "Repository", "Project"}:
            code_graph = CodeGraphExtractor(self.project_root).extract_paths(code_inputs)
            graph = merge_graphs([graph, code_graph])
        if changed_only and batch.processed == 0 and self.paths.graph.exists() and not graph.nodes:
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
            "graphiti_episodes_path": str(self.paths.graphiti_episodes),
            "agent_harness_path": str(self.paths.agent_harness),
            "obsidian_vault_path": str(self.paths.obsidian_vault),
            "site_path": str(self.paths.site),
            "mcp_server_name": cfg.get("name", sanitize_server_name(self.project_root.name)),
        }

    def compile(
        self,
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
        exclude_data: bool = False,
    ) -> dict:
        """Compile every configured source into the .llm-wiki artifacts.

        In addition to the ``sources`` listed in ``config.json``, the
        ``data/`` directory under ``project_root`` is auto-included when it
        exists. This is what makes ``data/research/daily/<date>/papers/<id>/``
        markdowns reachable without forcing every project to remember to add
        ``data`` to their sources list. Pass ``exclude_data=True`` to opt out
        (e.g. for projects that store unrelated binaries under ``data/``).
        """
        cfg = self.config()
        sources = list(cfg.get("sources") or ["."])
        # Auto-include the project-root ``data/`` directory if it exists and
        # isn't already part of the configured sources. ``iter_markdown_files``
        # walks recursively and ``BatchIngestRunner`` deduplicates by file
        # hash, so listing the same path twice would not double-process — but
        # we still skip the redundant entry to keep the work-list tight.
        if not exclude_data:
            data_dir = self.project_root / "data"
            if data_dir.exists():
                resolved_data = data_dir.resolve()
                already_listed = False
                for entry in sources:
                    candidate = resolve_project_input(self.project_root, entry).resolve()
                    if candidate == resolved_data:
                        already_listed = True
                        break
                if not already_listed:
                    sources.append("data")
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

    def export_graphiti(self, group_id: Optional[str] = None, output: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(output) if output else self.paths.graphiti_episodes
        adapter = GraphitiResearchGraphAdapter(group_id=group_id or cfg.get("name") or self.project_root.name)
        episodes = adapter.write_episodes(graph, target)
        return {"episodes": len(episodes), "path": str(target), "group_id": adapter.group_id}

    def export_agent_harness(self, targets: Optional[Iterable[str]] = None, output: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(output) if output else self.paths.agent_harness
        name = cfg.get("name") or sanitize_server_name(self.project_root.name)
        written = AgentHarnessAdapter(project_name=name).write_harness(
            graph,
            target,
            mcp_command="python3",
            mcp_args=["-m", "llm_wiki.mcp_server", "--graph", str(self.paths.graph.resolve())],
            targets=list(targets) if targets else SUPPORTED_AGENT_HARNESSES,
        )
        return {"path": str(target), "files": len(written), "targets": list(targets) if targets else SUPPORTED_AGENT_HARNESSES}

    def export_obsidian(self, vault: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(vault) if vault else self.paths.obsidian_vault
        name = cfg.get("name") or sanitize_server_name(self.project_root.name)
        return ObsidianVaultAdapter(vault_name=name).write_vault(graph, target)

    def build_site(self, output: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(output) if output else self.paths.site
        # The user-facing site title defaults to ``"LLM-Wiki"``; it can be
        # overridden in ``config.json`` via the ``site_title`` field. We
        # deliberately do *not* fall back to the sanitized server name (e.g.
        # ``llm_wiki_self``) — that string is for MCP server identifiers, not
        # for humans reading the rendered HTML.
        site_title = cfg.get("site_title") or "LLM-Wiki"
        self.paths.wiki.mkdir(parents=True, exist_ok=True)
        return StaticSiteBuilder(site_title=site_title).write_site(graph, self.paths.wiki, target)

    def deploy_github_pages(
        self,
        branch: str = "gh-pages",
        remote: str = "origin",
        commit_message: Optional[str] = None,
        dry_run: bool = False,
        force: bool = False,
        force_push: bool = False,
        enable_pages: bool = False,
    ) -> dict:
        """Deploy the compiled site at ``self.paths.site`` to ``branch`` on ``remote``."""
        cfg = self.config() if self.paths.config.exists() else {}
        cname = cfg.get("site_cname")
        deployer = GitHubPagesDeployer(self.project_root)
        return deployer.deploy(
            self.paths.site,
            branch=branch,
            remote=remote,
            commit_message=commit_message,
            dry_run=dry_run,
            force=force,
            force_push=force_push,
            cname=cname,
            enable_pages=enable_pages,
        )

    def sync_graphiti(
        self,
        neo4j_uri: Optional[str] = None,
        neo4j_user: Optional[str] = None,
        neo4j_password: Optional[str] = None,
        group_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        adapter = GraphitiResearchGraphAdapter(group_id=group_id or cfg.get("name") or self.project_root.name)
        return adapter.sync(
            graph,
            neo4j_uri=neo4j_uri or "bolt://localhost:7687",
            neo4j_user=neo4j_user or "neo4j",
            neo4j_password=neo4j_password or "password",
            dry_run=dry_run,
        )

    def _write_artifacts(self, graph: ResearchGraph) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.paths.wiki.mkdir(parents=True, exist_ok=True)
        wiki_store = WikiPageStore(self.paths.wiki)
        WikiLayerProjector(wiki_store).project(graph)
        graph, _written = SynthesisProjector(wiki_store, manifest_path=self.paths.manifest).project(graph)
        self.paths.graph.write_text(graph.to_json(indent=2) + "\n", encoding="utf-8")
        SQLiteResearchGraphStore(self.paths.sqlite).write_graph(graph, replace=True)
        GraphMarkdownProjector().write_projection(graph, self.paths.markdown_projection)
        CogneeResearchGraphAdapter().write_bundle(graph, self.paths.cognee_bundle)
        report = GraphReporter().render_markdown(GraphReporter().summarize(graph))
        self.paths.report.write_text(report, encoding="utf-8")
        TemporalFactProjector().write_jsonl(graph, self.paths.temporal_facts)
        self.export_graphiti()
        self.export_agent_harness()
        self.export_obsidian()
        self.build_site()
        self.paths.competitive_report.write_text(render_competitive_report(), encoding="utf-8")


def merge_graphs(graphs: Iterable[ResearchGraph]) -> ResearchGraph:
    nodes = {}
    edges = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes.setdefault(node.id, node)
        for edge in graph.edges:
            edges[(edge.source, edge.type, edge.target)] = edge
    return ResearchGraph(nodes=list(nodes.values()), edges=list(edges.values()))


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
