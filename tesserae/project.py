"""Per-project Tesserae workspace helpers.

A project wiki lives under ``<project>/.tesserae`` and keeps all generated
artifacts for that project together: graph JSON, batch manifest, SQLite store,
markdown projection, Cognee export bundle, report, and MCP config snippet.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional

from .agent_harness import AgentHarnessAdapter, SUPPORTED_AGENT_HARNESSES
from .batch import BatchIngestRunner, sha256_text
from .code_graph import CodeGraphExtractor
from .cognee_adapter import CogneeResearchGraphAdapter
from .cognee_codex import CogneeCodexPatch
from .cognee_direct import CogneeDirectImporter
from .deploy import GitHubPagesDeployer
from .graph_stores import SqliteGraphStore
from .karpathy_layer import KarpathyLayerWriter
from .lint import LintReport, WikiLinter
from .ports import GraphStore, Source, SourceLoader
from .site import StaticSiteBuilder
from .source_loaders import FilesystemSourceLoader
from .synthesis import SynthesisProjector
from .wiki_projector import WikiLayerProjector
from .wiki_store import WikiPageStore
from .graphiti_adapter import GraphitiResearchGraphAdapter
from .markdown_projection import GraphMarkdownProjector
from .obsidian_adapter import ObsidianVaultAdapter
from .persistence import SQLiteResearchGraphStore
from .report import GraphReporter
from .research_graph import ResearchCorpusAnalyzer, ResearchEdge, ResearchGraph, ResearchGraphExtractor, ResearchNode, ResearchNodeType, filter_filename_shaped_concepts, link_paper_repo_pairs, prefer_research_node
from .temporal import TemporalFactProjector, render_competitive_report
from .raganything_adapter import merge_raganything_graph
from .understand_anything_adapter import merge_understand_anything_graph
from .wiki_projector import partition_graph


# ---------------------------------------------------------------------------
# Community-summaries test seam
# ---------------------------------------------------------------------------
#
# ``_merge_community_summaries`` resolves its LLMJsonClient through this
# slot when present, falling back to ``build_default_json_client``. Tests
# call :func:`set_community_summaries_test_client` to inject a scripted
# client so they don't depend on a live LLM. Production code never calls
# the setter.
_COMMUNITY_SUMMARIES_TEST_CLIENT: Optional[object] = None


def set_community_summaries_test_client(client: Optional[object]) -> None:
    """Inject a fake LLMJsonClient for community-summary tests."""
    global _COMMUNITY_SUMMARIES_TEST_CLIENT
    _COMMUNITY_SUMMARIES_TEST_CLIENT = client


def _get_community_summaries_test_client() -> Optional[object]:
    return _COMMUNITY_SUMMARIES_TEST_CLIENT


@dataclass(frozen=True)
class CognifyOptions:
    """Optional Cognee/Codex cognify pass run after the bundle is written.

    All fields default to no-op values; the pass is a no-op when ``mode`` is
    ``"off"``. The CLI ``project compile`` builds this from --cognee-* flags;
    direct callers can construct it explicitly. Defaults mirror the legacy
    ``ingest`` subcommand at ``tesserae.cli.main``.
    """

    mode: str = "off"  # off | add | cognify | codex_cognify
    dataset: str = "tesserae_research_graph"
    codex_model: str = "gpt-4o"
    codex_timeout: int = 300
    embedding_provider: str = "deterministic"  # deterministic | ollama
    ollama_embedding_model: str = "qwen3-embedding:0.6b"
    ollama_embedding_endpoint: str = "http://127.0.0.1:11434/api/embed"
    ollama_embedding_timeout: int = 120
    local_embedding_dimensions: int = 128
    system_root: Optional[str] = None
    data_root: Optional[str] = None
    fail_fast: bool = True
    install_enabled: bool = True
    auto_install: bool = False
    install_command: str = "{python} -m pip install cognee"

    @classmethod
    def from_mapping(cls, data: dict) -> "CognifyOptions":
        install = data.get("install") or {}
        install_auto_default = bool(data.get("auto_cognify", False)) if "auto_install" not in install else bool(install.get("auto_install"))
        return cls(
            mode=str(data.get("mode") or "off"),
            dataset=str(data.get("dataset") or "tesserae_research_graph"),
            codex_model=str(data.get("codex_model") or "gpt-4o"),
            codex_timeout=int(data.get("codex_timeout") or 300),
            embedding_provider=str(data.get("embedding_provider") or "deterministic"),
            ollama_embedding_model=str(data.get("ollama_embedding_model") or "qwen3-embedding:0.6b"),
            ollama_embedding_endpoint=str(data.get("ollama_embedding_endpoint") or "http://127.0.0.1:11434/api/embed"),
            ollama_embedding_timeout=int(data.get("ollama_embedding_timeout") or 120),
            local_embedding_dimensions=int(data.get("local_embedding_dimensions") or 128),
            system_root=data.get("system_root"),
            data_root=data.get("data_root"),
            fail_fast=bool(data.get("fail_fast", False)),
            install_enabled=bool(install.get("enabled", True)),
            auto_install=install_auto_default,
            install_command=str(install.get("command") or "{python} -m pip install cognee"),
        )

    @property
    def is_active(self) -> bool:
        return self.mode in {"add", "cognify", "codex_cognify"}

    @property
    def runs_cognify(self) -> bool:
        return self.mode in {"cognify", "codex_cognify"}


@dataclass(frozen=True)
class SessionExtractionOptions:
    """Configuration for the session graph extractor.

    See ``docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md``
    for the full design. Defaults match the spec's "auto" mode: the
    structural pass runs whenever sessions exist; the LLM pass runs
    only when a backend is configured. Setting ``enabled = False``
    skips both passes entirely (graph identical to today).
    """

    enabled: bool = True
    llm_enabled: str = "auto"  # auto | true | false
    max_turns_per_chunk: int = 30
    max_tokens_per_call: int = 30000
    model: Optional[str] = None
    include_doc_id_context: int = 200

    @classmethod
    def from_mapping(cls, data: dict) -> "SessionExtractionOptions":
        return cls(
            enabled=bool(data.get("enabled", True)),
            llm_enabled=str(data.get("llm_enabled", "auto")).lower(),
            max_turns_per_chunk=int(data.get("max_turns_per_chunk", 30)),
            max_tokens_per_call=int(data.get("max_tokens_per_call", 30000)),
            model=data.get("model") or None,
            include_doc_id_context=int(data.get("include_doc_id_context", 200)),
        )


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config: Path
    graph: Path
    code_graph: Path
    combined_graph: Path
    build_history: Path
    manifest: Path
    sqlite: Path
    markdown_projection: Path
    cognee_bundle: Path
    report: Path
    temporal_facts: Path
    competitive_report: Path
    graphiti_episodes: Path
    agent_harness: Path
    harness_sessions: Path
    obsidian_vault: Path
    site: Path
    wiki: Path
    # Bidirectional Obsidian sync (Tier 1a, see docs/integrations/obsidian-sync.md):
    # vault_snapshot records what the projector last wrote per node, so the
    # next compile can diff the vault against it and surface user edits.
    # diverged_fields is the per-compile audit log of those diffs.
    vault_snapshot: Path
    diverged_fields: Path
    # Session graph extractor cache (Phase 5 populates findings.json files;
    # Phase 3 only needs the directory to exist for future writes). Default
    # supplied so existing call sites that construct ProjectPaths directly
    # (test_vault_watch.py and friends) don't need a positional update.
    session_findings: Path = Path(".tesserae/session_findings")
    # Community-summary cache (post-compile pass; opt-in via
    # ``TESSERAE_COMMUNITY_SUMMARIES=true``). One JSON file per detected
    # community keyed on the sorted-member content hash — re-runs with
    # the same membership skip the LLM call. See
    # ``tesserae.community_summaries``.
    community_summaries: Path = Path(".tesserae/community_summaries")


class ProjectWiki:
    """Manage a self-contained ``.tesserae`` workspace inside a project."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".tesserae"
        self.paths = ProjectPaths(
            root=self.root,
            config=self.root / "config.json",
            graph=self.root / "graph.json",
            code_graph=self.root / "code-graph.json",
            combined_graph=self.root / "combined-graph.json",
            # Build-history ledger lives at the project-wiki root, *not* inside
            # the wiped site directory — see F-11. ``StaticSiteBuilder`` clears
            # ``site/`` on every compile, so any ledger that lived inside would
            # be reset to one line per build (the xfail test in
            # tests/test_idempotence.py exercises this regression).
            build_history=self.root / ".build-history.jsonl",
            manifest=self.root / "manifest.json",
            sqlite=self.root / "sqlite.db",
            markdown_projection=self.root / "markdown_projection",
            cognee_bundle=self.root / "cognee_bundle",
            report=self.root / "report.md",
            temporal_facts=self.root / "temporal_facts.jsonl",
            competitive_report=self.root / "competitive_report.md",
            graphiti_episodes=self.root / "graphiti_episodes.jsonl",
            agent_harness=self.root / "agent_harness",
            harness_sessions=self.root / "harness_sessions",
            obsidian_vault=self.root / "obsidian_vault",
            site=self.root / "site",
            wiki=self.root / "wiki",
            vault_snapshot=self.root / "vault_snapshot.json",
            diverged_fields=self.root / "diverged-fields.md",
            session_findings=self.root / "session_findings",
            community_summaries=self.root / "community_summaries",
        )
        # In-memory override of the Obsidian vault location, set by
        # obsidian-sync --vault for the duration of a single CLI call.
        # The persistent override lives in .tesserae/config.json under
        # ``obsidian.vault_path``; see :meth:`effective_obsidian_vault`.
        self._vault_override: Optional[Path] = None

    def effective_obsidian_vault(self) -> Path:
        """Resolve the Obsidian vault directory the projector / watcher / overlay use.

        Resolution order:

        1. ``_vault_override`` set via :meth:`set_vault_override` (the
           per-call ``--vault`` flag on the CLI).
        2. ``obsidian.vault_path`` in ``.tesserae/config.json``,
           persisted by ``project setup --obsidian-vault``.
        3. Default ``.tesserae/obsidian_vault/`` baked into
           :class:`ProjectPaths`.

        Always returns an absolute :class:`Path` so callers don't have
        to think about cwd-relative resolution.
        """
        if self._vault_override is not None:
            return self._vault_override
        try:
            cfg = self.config() if self.paths.config.is_file() else {}
        except Exception:
            cfg = {}
        configured = (cfg.get("obsidian") or {}).get("vault_path")
        if configured:
            p = Path(configured).expanduser()
            if not p.is_absolute():
                p = (self.project_root / p).resolve()
            return p
        # Registry fallback: if the multi-project registry has a `vault_root`
        # AND this project is registered, default to `<vault_root>/<alias>/`.
        # Lets `tesserae wiki obsidian-set-root <PATH>` configure many
        # projects at once without per-project --vault setup. See
        # docs/integrations/obsidian-sync.md.
        try:
            from .mcp_server import ProjectRegistry
            registry = ProjectRegistry()
            vault_root = registry.get_vault_root()
            if vault_root is not None:
                alias = registry.alias_for_root(self.project_root)
                if alias:
                    return (vault_root / alias).expanduser()
        except Exception:
            pass
        return self.paths.obsidian_vault

    def set_vault_override(self, path: Optional[Path]) -> None:
        """Override the resolved vault path for this :class:`ProjectWiki` instance.

        Used by the CLI ``--vault`` flag to redirect the sync target for a
        single command without persisting the change. Pass ``None`` to clear.
        """
        if path is None:
            self._vault_override = None
            return
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (self.project_root / resolved).resolve()
        self._vault_override = resolved

    @classmethod
    def init(cls, project_root: str | Path = ".", name: Optional[str] = None, source_kind: str = "SourceDocument", sources: Optional[Iterable[str | Path]] = None) -> "ProjectWiki":
        wiki = cls(project_root)
        wiki.root.mkdir(parents=True, exist_ok=True)
        wiki.paths.markdown_projection.mkdir(parents=True, exist_ok=True)
        wiki.paths.cognee_bundle.mkdir(parents=True, exist_ok=True)
        wiki.paths.agent_harness.mkdir(parents=True, exist_ok=True)
        wiki.paths.harness_sessions.mkdir(parents=True, exist_ok=True)
        wiki.effective_obsidian_vault().mkdir(parents=True, exist_ok=True)
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
            "site_title": "Tesserae",
            "project_root": str(wiki.project_root),
            "created": date.today().isoformat(),
            "source_kind": source_kind,
            "sources": source_list,
            "graph_path": ".tesserae/graph.json",
            "manifest_path": ".tesserae/manifest.json",
            "sqlite_path": ".tesserae/sqlite.db",
            "markdown_projection_path": ".tesserae/markdown_projection",
            "cognee_bundle_path": ".tesserae/cognee_bundle",
            "report_path": ".tesserae/report.md",
            "temporal_facts_path": ".tesserae/temporal_facts.jsonl",
            "competitive_report_path": ".tesserae/competitive_report.md",
            "graphiti_episodes_path": ".tesserae/graphiti_episodes.jsonl",
            "agent_harness_path": ".tesserae/agent_harness",
            "harness_sessions_path": ".tesserae/harness_sessions",
            "obsidian_vault_path": ".tesserae/obsidian_vault",
            "site_path": ".tesserae/site",
            "memory_backends": {
                "cognee": default_cognee_backend_config(name or sanitize_server_name(wiki.project_root.name)),
            },
        }
        wiki.paths.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return wiki

    @classmethod
    def load(cls, project_root: str | Path = ".") -> "ProjectWiki":
        wiki = cls(project_root)
        if not wiki.paths.config.exists():
            raise FileNotFoundError(f"Project wiki is not initialized: {wiki.root}. Run `python3 -m tesserae.cli project init` first.")
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
        cognify: Optional[CognifyOptions] = None,
        loader: Optional[SourceLoader] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        session_options: Optional[SessionExtractionOptions] = None,
    ) -> dict:
        """Run the substrate-discovery + extraction pipeline for this project.

        ``loader`` and ``store`` are the hexagonal ports. When unset, defaults
        preserve the original behavior:

        * ``loader`` defaults to ``FilesystemSourceLoader`` walking the
          ``inputs`` paths under ``project_root`` (markdown only).
        * ``store`` defaults to :class:`SqliteGraphStore` pointing at
          ``self.paths.sqlite`` — writes happen at the end of compile via
          :meth:`_write_artifacts`.

        When an explicit ``loader`` is supplied, the FS walk and the
        per-file manifest dance are bypassed: each :class:`Source` from
        ``loader.discover()`` is extracted directly via
        :meth:`ResearchGraphExtractor.extract_text` and changed-only
        deduplication is keyed on the Source id + content hash.
        """
        cfg = self.config()
        kind = source_kind or cfg.get("source_kind", "SourceDocument")
        input_paths = [resolve_project_input(self.project_root, item) for item in inputs]
        extractor = ResearchGraphExtractor()
        code_inputs: List[Path] = list(input_paths)
        markdown_source_kind = "SourceDocument" if kind in {"CodeProject", "Repository", "Project"} else kind

        if loader is None:
            # Default path: filesystem walk via the legacy ``BatchIngestRunner``,
            # which preserves the changed-only manifest schema (keyed on file
            # path) used by every existing project workspace on disk.
            markdown_files: List[Path] = []
            seen_md: set[Path] = set()
            for input_path in input_paths:
                for md in iter_markdown_files(input_path):
                    resolved = md.resolve()
                    if resolved in seen_md:
                        continue
                    seen_md.add(resolved)
                    markdown_files.append(md)
            batch = BatchIngestRunner(extractor=extractor, manifest_path=self.paths.manifest).run(
                markdown_files,
                source_kind=markdown_source_kind,
                changed_only=changed_only,
                limit=limit,
            )
            graphs = batch.graphs or [batch.graph]
            processed = batch.processed
            skipped = batch.skipped
            base_graph = batch.graph
        else:
            # Injected loader path: ``Source`` records carry their own content,
            # so we extract from text and bookkeep changed-only against a
            # source-id-keyed manifest. The on-disk manifest format stays the
            # same JSON dict; entries are merged so a future FS-loader run
            # does not erase loader-keyed entries (and vice versa).
            graphs, processed, skipped = self._ingest_via_loader(
                loader=loader,
                extractor=extractor,
                source_kind=markdown_source_kind,
                changed_only=changed_only,
                limit=limit,
            )
            base_graph = merge_graphs(graphs) if graphs else ResearchGraph()

        graph = ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=min_trend_sources) if trends else base_graph
        if kind in {"CodeProject", "Repository", "Project"}:
            code_graph = CodeGraphExtractor(self.project_root).extract_paths(code_inputs)
            graph = merge_graphs([graph, code_graph])
        cfg = self.config()
        graph = self._merge_configured_understand_anything_graph(graph, cfg)
        graph = self._merge_configured_raganything_graph(graph, cfg)
        # ``--changed-only`` is supposed to be incremental: re-extract only the
        # files whose content hash changed, but keep the rest of the prior
        # corpus. The manifest stores only ``{path: sha256}``, so without this
        # merge step the result is the *delta only* — a fresh full compile of
        # 2400 nodes drops to 1700 after a 21-file edit. Fix: load the prior
        # graph, evict nodes whose ``source_path`` was just re-extracted (the
        # new extractor disagrees with the cached fragments otherwise), then
        # merge with the freshly-extracted batch.
        if changed_only and self.paths.graph.exists():
            prior_graph = load_graph_file(self.paths.graph)
            if prior_graph.nodes or prior_graph.edges:
                # Strip projector-generated nodes (Synthesis + their edges) from
                # the prior graph; ``SynthesisProjector`` regenerates them in
                # ``_write_artifacts``. Without this, every changed-only run
                # would inflate the synthesis layer.
                prior_graph = _strip_generated_layer(prior_graph)
                if processed == 0 and not graph.nodes:
                    graph = prior_graph
                else:
                    re_extracted = {str(Path(p).resolve()) for p in (batch.processed_paths if loader is None else [])}
                    if re_extracted:
                        kept_nodes = [
                            n for n in prior_graph.nodes
                            if not (n.source_path and str(Path(n.source_path).resolve()) in re_extracted)
                        ]
                        kept_ids = {n.id for n in kept_nodes}
                        kept_edges = [
                            e for e in prior_graph.edges
                            if e.source in kept_ids and e.target in kept_ids
                        ]
                        prior_graph = ResearchGraph(nodes=kept_nodes, edges=kept_edges)
                    graph = merge_graphs([prior_graph, graph])
        # Bug A guard: after every merge — native FS extractor, code graph,
        # Understand-Anything, RAG-Anything, prior incremental graph — strip
        # any concept-layer node whose name is a filename or path. UA in
        # particular tends to mint ``Concept`` nodes for documents and feed
        # entries; we don't want those duplicating SourceDocument pages in
        # the visual graph. See ``filter_filename_shaped_concepts``.
        graph = filter_filename_shaped_concepts(graph)
        # Session graph extraction. Runs unconditionally when enabled (the
        # default); produces a slice of Session + SessionDecision nodes plus
        # discussed_in / derived_from_session edges that link the agent's
        # historical conversations into the doc graph. The structural pass
        # is the only thing Phase 3 wires in; the LLM pass arrives in
        # Phase 5 of the session-graph plan.
        graph = self._merge_session_graph(graph, cfg, override=session_options)
        # Community-summary pass (Microsoft GraphRAG playbook applied to
        # the typed graph). Opt-in via ``TESSERAE_COMMUNITY_SUMMARIES=true``
        # so quiet ``project compile`` runs stay free of incremental LLM
        # cost. Runs AFTER merge/dedup so cluster membership reflects the
        # canonical graph and BEFORE ``_write_artifacts`` so the new
        # COMMUNITY_SUMMARY nodes flow through vault projection,
        # graph.json persistence, MCP, and site builds in one pass.
        graph = self._merge_community_summaries(graph, cfg)
        self._write_artifacts(graph, cognify=cognify, store=store, vault_pull=vault_pull)
        return {
            "project_root": str(self.project_root),
            "wiki_root": str(self.root),
            "source_kind": kind,
            "processed_files": processed,
            "skipped_files": skipped,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "graph_path": str(self.paths.graph),
            "graphiti_episodes_path": str(self.paths.graphiti_episodes),
            "agent_harness_path": str(self.paths.agent_harness),
            "obsidian_vault_path": str(self.effective_obsidian_vault()),
            "site_path": str(self.paths.site),
            "mcp_server_name": cfg.get("name", sanitize_server_name(self.project_root.name)),
        }

    def _merge_configured_understand_anything_graph(self, graph: ResearchGraph, cfg: dict) -> ResearchGraph:
        """Merge configured Understand Anything graph artifacts natively.

        The markdown projection remains a human-readable companion source, but
        native graph sync preserves UA node ids, edges, and concept provenance.
        """
        for tool in cfg.get("external_tools", []) or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("id") != "understand-anything" or tool.get("enabled", True) is False:
                continue
            sync_mode = str(tool.get("sync_mode") or "native_graph")
            if sync_mode not in {"native_graph", "both"}:
                continue
            artifact = self.project_root / str(tool.get("artifact") or ".understand-anything/knowledge-graph.json")
            if not artifact.exists():
                continue
            manifest = self.root / "external" / "understand-anything-sync.json"
            graph, _sync = merge_understand_anything_graph(
                graph,
                project_root=self.project_root,
                artifact=artifact,
                sync_manifest_path=manifest,
            )
        return graph

    def _merge_session_graph(
        self,
        graph: ResearchGraph,
        cfg: dict,
        override: Optional[SessionExtractionOptions] = None,
    ) -> ResearchGraph:
        """Merge the session graph extractor's slice into the doc graph.

        Phase 3 of the session-graph plan: structural pass only. Loads
        normalized HarnessSession records via ``discover_harness_sessions``
        (filtered by project_root), builds a multi-key path index from the
        live doc graph, and returns a slice of ``Session`` + structural
        ``SessionDecision`` nodes with ``discussed_in`` / ``derived_from_session``
        edges. The LLM pass is wired in Phase 5.

        The whole pass is skipped when ``sessions.enabled`` is False — either
        via the ``override`` argument (CLI flag wins) or via the
        ``sessions.enabled`` config key (fallback when no CLI override).
        """
        from .harness_sessions import (
            HarnessSession,
            HarnessSessionStore,
            discover_harness_sessions,
            session_matches_project,
        )
        from .llm_json import build_default_json_client
        from .session_graph import SessionGraphExtractor

        opts = override or SessionExtractionOptions.from_mapping(
            cfg.get("sessions") if isinstance(cfg.get("sessions"), dict) else {}
        )
        if not opts.enabled:
            return graph

        # Ensure the cache directory exists for LLM finding caches.
        self.paths.session_findings.mkdir(parents=True, exist_ok=True)

        # Source-of-truth resolution order:
        #   1. ``.tesserae/harness_sessions/`` — the normalised import the
        #      operator opted into via ``tesserae sessions discover --import``.
        #      Lets tests pre-populate this dir without depending on the
        #      caller's ``~/.claude``.
        #   2. ``discover_harness_sessions(project_root)`` — fall back to
        #      live discovery from the caller's filesystem.
        # Session source is OPT-IN by cache. We only consume
        # `.tesserae/harness_sessions/` (populated when the user runs
        # ``tesserae sessions discover --import``). The compile path does
        # NOT scan ``~/.claude/projects/`` or ``~/.codex/sessions/`` on
        # its own — that scan is multi-minute on a machine with
        # thousands of historical sessions and would silently re-add
        # multi-minute latency to every ``project compile``.
        if not self.paths.harness_sessions.exists():
            return graph
        store = HarnessSessionStore(self.paths.harness_sessions)
        cached = store.list_sessions()
        in_project: List[HarnessSession] = [
            s for s in cached
            if session_matches_project(s, self.project_root)
        ]
        if not in_project:
            return graph

        # LLM client gating: build one only when llm_enabled allows it AND
        # a backend is available. ``build_default_json_client`` returns
        # None when neither is true — keeps the no-credentials path
        # silent and structural-only.
        json_client = None
        if opts.llm_enabled != "false":
            json_client = build_default_json_client(model=opts.model)
        extractor = SessionGraphExtractor(
            project_root=self.project_root,
            cache_dir=self.paths.session_findings,
            doc_graph=graph,
            sessions=in_project,
            json_client=json_client,
            llm_enabled=opts.llm_enabled,
            max_turns_per_chunk=opts.max_turns_per_chunk,
            include_doc_id_context=opts.include_doc_id_context,
            model=opts.model,
        )
        session_slice = extractor.extract()
        if not session_slice.nodes and not session_slice.edges:
            return graph
        return merge_graphs([graph, session_slice])

    def _merge_community_summaries(self, graph: ResearchGraph, cfg: dict) -> ResearchGraph:
        """Mint COMMUNITY_SUMMARY nodes + ``summarizes`` edges (opt-in).

        Skipped unless ``TESSERAE_COMMUNITY_SUMMARIES=true`` (or
        ``community_summaries.enabled`` in config). When enabled, runs
        Louvain/label-propagation over the undirected projection of
        ``graph`` and asks the default LLMJsonClient for a per-cluster
        title/description/tags triple. Per-cluster results cache under
        ``self.paths.community_summaries/`` so membership-stable re-runs
        skip the LLM.
        """
        from .community_summaries import compile_community_summaries, is_enabled_via_env

        community_cfg = cfg.get("community_summaries") if isinstance(cfg.get("community_summaries"), dict) else {}
        if not (is_enabled_via_env() or bool(community_cfg.get("enabled"))):
            return graph
        json_client = _get_community_summaries_test_client()
        if json_client is None:
            from .llm_json import build_default_json_client
            json_client = build_default_json_client(
                model=community_cfg.get("model") if isinstance(community_cfg.get("model"), str) else None
            )
        if json_client is None:
            print("[tesserae] community summaries: no LLM client available; skipping.", flush=True)
            return graph
        slice_graph = compile_community_summaries(
            graph,
            cache_dir=self.paths.community_summaries,
            json_client=json_client,
            min_size=int(community_cfg.get("min_size") or 3),
            max_communities=int(community_cfg.get("max_communities") or 50),
        )
        if not slice_graph.nodes:
            return graph
        print(
            f"[tesserae] community summaries: minted {len(slice_graph.nodes)} "
            f"COMMUNITY_SUMMARY node(s) with {len(slice_graph.edges)} edge(s).",
            flush=True,
        )
        return merge_graphs([graph, slice_graph])

    def _merge_configured_raganything_graph(self, graph: ResearchGraph, cfg: dict) -> ResearchGraph:
        """Merge configured RAG-Anything manifest artifacts natively."""
        for tool in cfg.get("external_tools", []) or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("id") != "raganything" or tool.get("enabled", True) is False:
                continue
            sync_mode = str(tool.get("sync_mode") or "native_graph")
            if sync_mode not in {"native_graph", "both"}:
                continue
            artifact = self.project_root / str(
                tool.get("artifact") or ".tesserae/external/raganything/manifest.json"
            )
            if not artifact.exists():
                continue
            sync_path = self.project_root / ".tesserae" / "external" / "raganything-sync.json"
            graph, _ = merge_raganything_graph(
                graph,
                project_root=self.project_root,
                artifact=artifact,
                sync_manifest_path=sync_path,
            )
        return graph

    def _ingest_via_loader(
        self,
        loader: SourceLoader,
        extractor: ResearchGraphExtractor,
        source_kind: str,
        changed_only: bool,
        limit: Optional[int],
    ) -> tuple[List[ResearchGraph], int, int]:
        """Drive extraction from a :class:`SourceLoader` instead of the FS walker.

        Manifest bookkeeping mirrors :class:`BatchIngestRunner`: entries are
        keyed on ``source.id`` (rather than file path), value carries the
        content sha256 and source kind. Skipping is an exact-hash match.
        """
        manifest = self._load_manifest()
        graphs: List[ResearchGraph] = []
        processed = 0
        skipped = 0
        try:
            for source in loader.discover():
                digest = sha256_text(source.content)
                key = f"source:{source.id}"
                if changed_only and manifest.get(key, {}).get("sha256") == digest:
                    skipped += 1
                    continue
                if limit is not None and processed >= limit:
                    break
                graph = extractor.extract_text(
                    source.content,
                    source_path=source.path or source.id,
                    source_kind=source_kind,
                )
                graphs.append(graph)
                processed += 1
                manifest[key] = {"sha256": digest, "source_kind": source_kind}
        finally:
            self._write_manifest(manifest)
        return graphs, processed, skipped

    def _load_manifest(self) -> dict:
        if not self.paths.manifest.exists():
            return {}
        payload = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        files = payload.get("files", payload if isinstance(payload, dict) else {})
        return files if isinstance(files, dict) else {}

    def _write_manifest(self, manifest: dict) -> None:
        self.paths.manifest.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.paths.manifest.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"files": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.rename(self.paths.manifest)

    def compile(
        self,
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
        exclude_data: bool = False,
        cognify: Optional[CognifyOptions] = None,
        loader: Optional[SourceLoader] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        session_options: Optional[SessionExtractionOptions] = None,
    ) -> dict:
        """Compile every configured source into the .tesserae artifacts.

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
            cognify=cognify,
            loader=loader,
            store=store,
            vault_pull=vault_pull,
            session_options=session_options,
        )

    def lint(self, fix_trivial: bool = False, severity_floor: str = "info") -> LintReport:
        """Run :class:`WikiLinter` against this project's compiled artifacts.

        Thin wrapper that defers all work — including artifact writes and the
        colored stderr summary — to :class:`WikiLinter`. The returned
        :class:`LintReport` lets callers inspect findings programmatically;
        the CLI uses it to derive the exit code.
        """
        return WikiLinter(self.project_root).run(
            fix_trivial=fix_trivial,
            severity_floor=severity_floor,
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
            "      - \"tesserae.mcp_server\"\n"
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
            mcp_args=["-m", "tesserae.mcp_server", "--graph", str(self.paths.graph.resolve())],
            targets=list(targets) if targets else SUPPORTED_AGENT_HARNESSES,
        )
        return {"path": str(target), "files": len(written), "targets": list(targets) if targets else SUPPORTED_AGENT_HARNESSES}

    def export_obsidian(self, vault: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(vault) if vault else self.effective_obsidian_vault()
        name = cfg.get("name") or sanitize_server_name(self.project_root.name)
        return ObsidianVaultAdapter(vault_name=name).write_vault(graph, target)

    def build_site(self, output: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(output) if output else self.paths.site
        # The user-facing site title defaults to ``"Tesserae"``; it can be
        # overridden in ``config.json`` via the ``site_title`` field. We
        # deliberately do *not* fall back to the sanitized server name (e.g.
        # ``tesserae_self``) — that string is for MCP server identifiers, not
        # for humans reading the rendered HTML.
        site_title = cfg.get("site_title") or "Tesserae"
        # The visual graph view hides ``sources``-group nodes by default
        # (the 1000+ raganything-projected SourceDocument cloud floods the
        # canvas and obscures the concept layer). Power users can restore
        # the dense view via ``graph_view.show_sources = true`` in
        # ``.tesserae/config.json``. Only the visual payload is affected —
        # ``graph.json``, MCP, search, and per-page wiki views still see
        # every source.
        graph_view_cfg = cfg.get("graph_view") if isinstance(cfg.get("graph_view"), dict) else {}
        show_sources = bool(graph_view_cfg.get("show_sources", False))
        # Code-file links in source/raw pages (e.g. `[cli.py](../tesserae/cli.py)`)
        # point at paths the site doesn't host. When `site.github_repo_url`
        # is set in ``.tesserae/config.json``, the static builder rewrites
        # these to absolute GitHub blob URLs at compile time so clicks land
        # on real source instead of 404ing. Opt-in: no rewriting when unset.
        # ``site.github_blob_base`` can override the default ``…/blob/main``
        # when pointing at a non-main ref.
        from .site.code_link_rewriter import derive_blob_base
        site_cfg = cfg.get("site") if isinstance(cfg.get("site"), dict) else {}
        github_repo_url = site_cfg.get("github_repo_url")
        github_blob_base_cfg = site_cfg.get("github_blob_base")
        github_blob_base = derive_blob_base(
            github_repo_url=github_repo_url if isinstance(github_repo_url, str) else None,
            github_blob_base=github_blob_base_cfg if isinstance(github_blob_base_cfg, str) else None,
        )
        self.paths.wiki.mkdir(parents=True, exist_ok=True)
        return StaticSiteBuilder(
            site_title=site_title,
            show_sources=show_sources,
            github_blob_base=github_blob_base,
        ).write_site(graph, self.paths.wiki, target)

    def query(
        self,
        question: str,
        *,
        top_k: int = 8,
        kind: Optional[str] = None,
        use_llm: bool = False,
        model: str = "claude-sonnet-4-6",
    ) -> "QueryResult":
        """Convenience wrapper around :class:`tesserae.query.WikiQuery`.

        Builds a fresh :class:`WikiQuery` per call. Cheap (the search index
        is loaded lazily on the first ``search``/``answer`` call), and we
        prefer to avoid hidden global state on the project handle.
        """

        from .query import WikiQuery

        wq = WikiQuery(self.project_root, top_k=top_k, kind_filter=kind)
        return wq.answer(
            question,
            model=model,
            force_llm=use_llm,
        )

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

    def reproject_after_vault_change(self) -> "VaultWatchResult":
        """Fast path used by ``obsidian-sync --watch``: re-apply vault overlay + re-project.

        Loads the existing ``graph.json`` instead of re-extracting from
        sources, so it's seconds rather than the 30+ a full
        :meth:`compile` takes. Used by the polling watcher in
        :mod:`tesserae.vault_watch` to react to user edits live.

        Steps:

        1. Load research_graph from ``.tesserae/graph.json``.
        2. Apply vault overlay (Tier 1a + 1b — both diff streams).
        3. Re-project to markdown_projection/ + Obsidian vault/.
        4. Write fresh vault_snapshot.json so the next watch tick has a
           current baseline.

        Returns a :class:`VaultWatchResult` summarising what happened.
        """
        from .markdown_projection import GraphMarkdownProjector
        from .vault_snapshot import write_snapshot
        from .vault_watch import VaultWatchResult

        if not self.paths.graph.is_file():
            raise RuntimeError(
                f"No graph at {self.paths.graph}; run `tesserae project compile` first."
            )
        graph = load_graph_file(self.paths.graph)

        before_node_count = len(graph.nodes)
        before_edge_count = len(graph.edges)
        graph = self._apply_vault_overlay(graph)
        new_stubs = sum(1 for n in graph.nodes[before_node_count:] if n.type == ResearchNodeType.STUB)

        # Re-project: markdown + the obsidian vault itself. Cognee bundle,
        # site, harness, etc. are intentionally NOT touched here — those are
        # compile-time concerns. The watcher exists to make vault edits
        # round-trip; everything else stays static between compiles.
        GraphMarkdownProjector().write_projection(graph, self.paths.markdown_projection)
        self.export_obsidian()
        write_snapshot(graph.nodes, self.paths.vault_snapshot)

        # Count changes by re-reading the diverged-fields report that
        # _apply_vault_overlay just wrote. The report is the source of truth
        # for "what happened this round" anyway.
        return VaultWatchResult(
            overrides_applied=self._count_diverged_field_overrides(),
            user_link_changes_applied=max(0, len(graph.edges) - before_edge_count),
            stubs_minted=max(0, new_stubs),
        )

    def _count_diverged_field_overrides(self) -> int:
        """Parse diverged-fields.md to count `Field overrides — N across M node(s)`."""
        if not self.paths.diverged_fields.is_file():
            return 0
        import re
        text = self.paths.diverged_fields.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Field overrides — (\d+) across \d+ node\(s\)", text)
        return int(m.group(1)) if m else 0

    def _apply_vault_overlay(self, graph: ResearchGraph) -> ResearchGraph:
        """Read user edits out of the Obsidian vault and apply them onto the graph.

        Tier 1a + 1b of the bidirectional sync feature
        (docs/integrations/obsidian-sync.md). Two diff streams:

        1. **Frontmatter / description overrides** — computed against
           ``vault_snapshot.json``. Returns ``[]`` when the snapshot is
           missing (first-ever feature-enabled compile; the snapshot we
           write at the end of THIS compile becomes the next baseline).
        2. **user_link edges** — every ``[[wikilink]]`` inside a
           ``<!-- user-notes:start -->`` block becomes a ``user_link``
           edge. The diff is against the current graph's existing
           user_link edges, so removing a wikilink also removes the edge.
           This stream runs even on the first compile (no snapshot
           needed) because the graph itself is the baseline.

        Always emits ``.tesserae/diverged-fields.md`` so the operator can
        audit what was applied, even when both streams come back empty.
        """
        from .markdown_projection import unique_slugs
        from .vault_pull import (
            _load_vault_files,
            apply_overrides,
            apply_user_link_changes,
            compute_overrides,
            compute_user_link_changes,
            write_diverged_fields_report,
        )
        from .vault_snapshot import read_snapshot

        vault_path = self.effective_obsidian_vault()
        if not vault_path.exists():
            return graph

        node_by_id = {node.id: node for node in graph.nodes}
        slug_by_id = unique_slugs(graph.nodes)

        vault_files = _load_vault_files(vault_path)
        snapshot = read_snapshot(self.paths.vault_snapshot)
        overrides = (
            compute_overrides(vault_path, snapshot, node_by_id, vault_files=vault_files)
            if snapshot is not None
            else []
        )
        user_link_changes = compute_user_link_changes(
            vault_path, graph, slug_by_id, vault_files=vault_files
        )
        write_diverged_fields_report(
            overrides, self.paths.diverged_fields, user_link_changes
        )

        if not overrides and not user_link_changes:
            return graph

        if overrides:
            print(
                f"[tesserae] vault overlay: applying {len(overrides)} field "
                f"override(s) from {vault_path.name}/",
                flush=True,
            )
        if user_link_changes:
            adds = sum(1 for c in user_link_changes if c.action == "add")
            removes = sum(1 for c in user_link_changes if c.action == "remove")
            print(
                f"[tesserae] vault overlay: {adds} user_link add(s), "
                f"{removes} remove(s) "
                f"(see {self.paths.diverged_fields.relative_to(self.project_root)})",
                flush=True,
            )

        graph = apply_overrides(graph, overrides)
        graph = apply_user_link_changes(graph, user_link_changes)
        return graph

    def _write_artifacts(
        self,
        graph: ResearchGraph,
        cognify: Optional[CognifyOptions] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

        # Bidirectional Obsidian sync (Tier 1a). If the user has been editing
        # the projected Obsidian vault, harvest those edits and overlay them
        # on the extracted graph BEFORE we project anything new — otherwise
        # every projector (wiki/, markdown_projection/, obsidian_vault/) would
        # immediately stomp on the user's changes.
        #
        # Skipped on the first-ever compile-with-this-feature because no
        # vault_snapshot.json exists yet; the snapshot we write at the end of
        # this compile becomes the baseline for the next one. This is the
        # "free pass" the design doc relies on instead of a confirmation prompt.
        if vault_pull:
            graph = self._apply_vault_overlay(graph)

        # The wiki/site layers are generated projections. Clean them before each
        # compile so nodes that are newly filtered out (e.g. noisy social feed
        # captures) do not survive as stale public pages.
        if self.paths.wiki.exists():
            shutil.rmtree(self.paths.wiki)
        if self.paths.site.exists():
            shutil.rmtree(self.paths.site)
        self.paths.wiki.mkdir(parents=True, exist_ok=True)
        wiki_store = WikiPageStore(self.paths.wiki)
        WikiLayerProjector(wiki_store).project(graph)
        graph, _written = SynthesisProjector(wiki_store, manifest_path=self.paths.manifest).project(graph)
        # Karpathy schema layer: purpose / schema / index / log files at the
        # top of the wiki dir. ``purpose.md`` is seeded once and preserved on
        # later compiles so user edits survive; the others regenerate.
        cfg_for_layer = self.config() if self.paths.config.exists() else {}
        KarpathyLayerWriter(
            wiki_root=self.paths.wiki,
            log_root=self.root,  # log.md lives next to .build-history.jsonl, outside the byte-idempotent wiki dir
            site_title=str(cfg_for_layer.get("site_title") or "Tesserae"),
            project_name=str(cfg_for_layer.get("name") or self.project_root.name),
        ).write_all(graph, build_history_path=self.paths.build_history)

        # ------------------------------------------------------------ F-11
        # Split the union ``ResearchGraph`` into two artifacts:
        #   * ``graph.json``       — research-layer nodes/edges only (no
        #                            ``CodeProject``/``SourceFile``/etc.). MCP,
        #                            search, llms.txt, sitemap, RSS, and the
        #                            site graph payload all read this file.
        #   * ``code-graph.json``  — code-graph layer (``CodeProject``,
        #                            ``SourceFile``, ``CodeModule``,
        #                            ``CodeClass``, ``CodeFunction``,
        #                            ``Dependency``) plus any cross-layer
        #                            anchor edges so a downstream consumer can
        #                            rebuild the union if it wants one.
        #   * ``combined-graph.json`` is only written when the project config
        #                            opts in via ``combined_graph: true`` (or
        #                            the ``TESSERAE_INCLUDE_COMBINED_GRAPH``
        #                            env var is set / a future CLI flag flips
        #                            it). Default is *off* — code-graph noise
        #                            should not bloat agent-facing artifacts.
        research_graph, code_graph = partition_graph(graph)

        for target, content in (
            (self.paths.graph, research_graph.to_json(indent=2) + "\n"),
            (self.paths.code_graph, code_graph.to_json(indent=2) + "\n"),
        ):
            tmp = target.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.rename(target)

        cfg = self.config() if self.paths.config.exists() else {}
        include_combined = bool(
            cfg.get("combined_graph")
            or cfg.get("include_combined_graph")
            or os.environ.get("TESSERAE_INCLUDE_COMBINED_GRAPH")
        )
        if include_combined:
            tmp = self.paths.combined_graph.with_suffix(".tmp")
            tmp.write_text(graph.to_json(indent=2) + "\n", encoding="utf-8")
            tmp.rename(self.paths.combined_graph)
        elif self.paths.combined_graph.exists():
            # Don't let a stale combined graph survive a config flip.
            self.paths.combined_graph.unlink()

        # The downstream stores (SQLite, markdown projection, Cognee bundle,
        # report, temporal facts, Graphiti episodes, agent harness, Obsidian
        # vault) keep operating on the union so existing consumers see the
        # same structure they always did.
        if store is None:
            # Default path: keep the legacy graph-at-a-time write. This preserves
            # byte-compatibility with any existing ``.tesserae/sqlite.db`` on
            # disk — :class:`SQLiteResearchGraphStore` clears+rewrites the table
            # rather than upserting row-by-row, which is the expected behavior
            # for the standalone CLI flow.
            SQLiteResearchGraphStore(self.paths.sqlite).write_graph(graph, replace=True)
        else:
            # Injected store path: drive the union graph through the
            # :class:`GraphStore` port. The Postgres adapter (HypePaper-side)
            # and any test-double share this code path.
            for node in graph.nodes:
                store.upsert_node(node)
            for edge in graph.edges:
                store.upsert_edge(edge)
        GraphMarkdownProjector().write_projection(graph, self.paths.markdown_projection)
        CogneeResearchGraphAdapter().write_bundle(graph, self.paths.cognee_bundle)
        if cognify and cognify.is_active:
            self._run_cognify_best_effort(cognify)
        report = GraphReporter().render_markdown(GraphReporter().summarize(graph))
        self.paths.report.write_text(report, encoding="utf-8")
        TemporalFactProjector().write_jsonl(graph, self.paths.temporal_facts)
        self.export_graphiti()
        self.export_agent_harness()
        self.export_obsidian()
        self.build_site()
        self.paths.competitive_report.write_text(render_competitive_report(), encoding="utf-8")
        self._append_build_history(research_graph, code_graph)

        # Tier 1a tail: write the snapshot capturing what we just projected
        # so the next compile can diff the vault against it. Always written
        # (even when vault_pull was disabled) — disabling the overlay only
        # bypasses reading; we still want a fresh baseline for the next run.
        from .vault_snapshot import write_snapshot
        write_snapshot(graph.nodes, self.paths.vault_snapshot)

    def _run_cognify_best_effort(self, options: "CognifyOptions") -> None:
        try:
            self._run_cognify(options)
            return
        except ModuleNotFoundError as exc:
            missing_name = getattr(exc, "name", "") or ""
            message = str(exc)
            is_cognee_missing = missing_name == "cognee" or "No module named 'cognee'" in message
            if is_cognee_missing and options.install_enabled and options.auto_install:
                print("[tesserae] Cognee missing; installing configured Cognee package...", flush=True)
                try:
                    self._install_cognee(options)
                    print("[tesserae] Cognee installed; retrying cognify...", flush=True)
                    self._run_cognify(options)
                    return
                except Exception as install_exc:
                    if options.fail_fast:
                        raise
                    print(f"[tesserae] Cognee install/cognify warning; compile will continue: {install_exc}", flush=True)
                    return
            if options.fail_fast:
                raise
            print(f"[tesserae] Cognee cognify warning; compile will continue: {exc}", flush=True)
        except Exception as exc:
            if options.fail_fast:
                raise
            print(f"[tesserae] Cognee cognify warning; compile will continue: {exc}", flush=True)

    def _install_cognee(self, options: "CognifyOptions") -> dict:
        command = (options.install_command or "{python} -m pip install cognee").format(python=sys.executable)
        completed = subprocess.run(
            command,
            shell=True,
            cwd=self.project_root,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()
            detail = f": {tail[-1]}" if tail else ""
            raise RuntimeError(f"Cognee install failed ({completed.returncode}){detail}")
        return {
            "status": "installed",
            "command": command,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }

    def _run_cognify(self, options: "CognifyOptions") -> None:
        """Invoke Cognee on the freshly written bundle.

        ``add`` only loads the bundle into the Cognee dataset. ``cognify`` runs
        Cognee's full cognify pipeline (LLM + embedding calls). ``codex_cognify``
        wraps the cognify pass in :class:`CogneeCodexPatch` so Cognee's LLM
        client is patched to OAuth Codex CLI — useful when you don't have an
        OpenAI API key but do have Codex installed.
        """

        bundle = self.paths.cognee_bundle
        if not bundle.exists() or not any(bundle.iterdir()):
            print(
                "[tesserae] cognify skipped: cognee bundle is empty",
                flush=True,
            )
            return

        async def _add() -> None:
            await CogneeDirectImporter().add_bundle(
                bundle,
                dataset_name=options.dataset,
                cognify=options.runs_cognify,
                system_root=options.system_root,
                data_root=options.data_root,
            )

        if options.mode == "codex_cognify":
            with CogneeCodexPatch(
                model=options.codex_model,
                timeout=options.codex_timeout,
                deterministic_embeddings=options.embedding_provider == "deterministic",
                ollama_embeddings=options.embedding_provider == "ollama",
                ollama_model=options.ollama_embedding_model,
                ollama_endpoint=options.ollama_embedding_endpoint,
                ollama_timeout=options.ollama_embedding_timeout,
                embedding_dimensions=options.local_embedding_dimensions,
            ):
                asyncio.run(_add())
        else:
            asyncio.run(_add())

    def _append_build_history(
        self, research_graph: ResearchGraph, code_graph: ResearchGraph
    ) -> None:
        """Append one line to the project-level build-history ledger.

        Lives at ``.tesserae/.build-history.jsonl`` (next to ``manifest.json``,
        outside the wiped ``site/`` directory) so it survives across
        recompiles. Each line records the timestamp and node/edge counts for
        both partitions so an audit consumer can see the artifact split.
        """
        from datetime import datetime, timezone
        entry = {
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "research_nodes": len(research_graph.nodes),
            "research_edges": len(research_graph.edges),
            "code_nodes": len(code_graph.nodes),
            "code_edges": len(code_graph.edges),
        }
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        existing = ""
        if self.paths.build_history.exists():
            try:
                existing = self.paths.build_history.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        # Ensure trailing newline normalization so the file always ends with
        # exactly one newline after the latest entry.
        existing = existing.rstrip("\n")
        if existing:
            existing += "\n"
        self.paths.build_history.write_text(existing + line + "\n", encoding="utf-8")


def default_cognee_backend_config(name: str = "tesserae") -> dict:
    dataset_base = sanitize_server_name(name or "tesserae")
    return {
        "enabled": True,
        "mode": "codex_cognify",
        "auto_cognify": False,
        "dataset": f"{dataset_base}_memory",
        "system_root": ".tesserae/cognee_system",
        "data_root": ".tesserae/cognee_data",
        "codex_model": "gpt-5.4",
        "codex_timeout": 300,
        "embedding_provider": "deterministic",
        "local_embedding_dimensions": 128,
        "fail_fast": False,
        "install": {
            "enabled": True,
            "auto_install": False,
            "command": "{python} -m pip install cognee",
        },
    }


def default_raganything_backend_config(name: str = "tesserae") -> dict:
    # ``name`` is unused for now; kept for symmetry with default_cognee_backend_config.
    return {
        "enabled": False,
        "working_dir": ".tesserae/external/raganything/working_dir",
        "parser": "mineru",
        "parse_method": "auto",
        "query_mode": "hybrid",
        "vlm_enhanced": True,
        "llm": {
            "provider": "codex",
            "model": "gpt-5.4",
            "timeout": 300,
            "claude_config_dir": None,
        },
        "embedding": {
            "provider": "deterministic",
            "dim": 768,
        },
        "install": {
            "command": "{python} -m pip install 'raganything[all]>=1.3.0' docling",
            "auto_install": False,
        },
    }


def cognee_backend_config(config: dict) -> dict:
    defaults = default_cognee_backend_config(str(config.get("name") or "tesserae"))
    backends = config.get("memory_backends")
    if not isinstance(backends, dict) or "cognee" not in backends:
        return defaults
    configured = dict(backends.get("cognee") or {})
    merged = {**defaults, **configured}
    configured_install = configured.get("install")
    merged["install"] = {**defaults.get("install", {}), **(configured_install or {})}
    if configured_install is None and configured.get("auto_cognify"):
        merged["install"]["auto_install"] = True
    return merged


def cognify_options_from_config(config: dict) -> Optional[CognifyOptions]:
    cognee = cognee_backend_config(config)
    if not cognee.get("enabled", False) or not cognee.get("auto_cognify", False):
        return None
    options = CognifyOptions.from_mapping(cognee)
    return options if options.is_active else None


def merge_graphs(graphs: Iterable[ResearchGraph]) -> ResearchGraph:
    nodes = {}
    edges = {}
    for graph in graphs:
        for node in graph.nodes:
            existing = nodes.get(node.id)
            nodes[node.id] = prefer_research_node(existing, node) if existing else node
        for edge in graph.edges:
            edges[(edge.source, edge.type, edge.target)] = edge
    # Re-run BOTH dedup passes across the merged universe.
    # ``ResearchGraphBuilder.build()`` already runs them per extractor, but
    # two same-typed concepts spelt differently (``pre-training`` vs
    # ``pretraining``) — or a Paper + a same-named ApproachFamily — often
    # come from *different* files (different builders), so the duplicates
    # only become co-resident here.
    from .research_graph import (
        merge_cross_type_duplicates,
        merge_same_type_aliased_duplicates,
    )
    same_type_nodes, same_type_edges = merge_same_type_aliased_duplicates(
        list(nodes.values()), list(edges.values())
    )
    merged_nodes, merged_edges = merge_cross_type_duplicates(
        same_type_nodes, same_type_edges
    )
    merged = ResearchGraph(nodes=merged_nodes, edges=merged_edges)
    return link_paper_repo_pairs(merged)


def _strip_generated_layer(graph: ResearchGraph) -> ResearchGraph:
    """Remove projector-generated nodes/edges from a prior compiled graph.

    Used by changed-only ingest to avoid double-counting the synthesis layer
    on every recompile. The synthesis layer is regenerated by
    :class:`tesserae.synthesis.SynthesisProjector` after the graph is merged,
    so the prior copy of those nodes/edges should not survive into the merge.
    """
    generated_node_ids = {n.id for n in graph.nodes if n.type == ResearchNodeType.SYNTHESIS}
    if not generated_node_ids:
        return graph
    kept_nodes = [n for n in graph.nodes if n.id not in generated_node_ids]
    kept_edges = [
        e for e in graph.edges
        if e.source not in generated_node_ids
        and e.target not in generated_node_ids
        and e.type not in {"synthesizes", "summarizes"}
    ]
    return ResearchGraph(nodes=kept_nodes, edges=kept_edges)


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
    """Walk ``path`` and return the ``.md`` files inside it.

    Thin wrapper over :class:`FilesystemSourceLoader` (the hexagonal
    ``SourceLoader`` adapter) so the FS-walking logic lives in one place.
    Behavior matches the legacy inline walker:

    * Single-file ``path`` returns ``[path]`` if it is a ``.md`` file, else
      ``[]``.
    * Missing ``path`` raises :class:`FileNotFoundError` (preserved here for
      backward compatibility — the loader itself is forgiving).
    * Directory ``path`` is walked recursively; hidden components
      (dot-prefix) are skipped; results are sorted deterministically.
    """
    from .source_loaders import FilesystemSourceLoader

    if path.is_file():
        return [path] if path.suffix.lower() == ".md" else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    loader = FilesystemSourceLoader([path], extensions=(".md",))
    # We only need the absolute paths — bypass content reads by walking the
    # internal iterator directly. ``discover()`` reads file bodies eagerly,
    # which would be wasteful here since downstream consumers re-read the
    # file via :class:`BatchIngestRunner`.
    return list(loader.iter_paths(path))


def sanitize_server_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "tesserae_project"
