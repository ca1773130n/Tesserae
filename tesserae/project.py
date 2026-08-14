"""Per-project Tesserae workspace helpers.

A project wiki lives under ``<project>/.tesserae`` and keeps all generated
artifacts for that project together: graph JSON, batch manifest, SQLite store,
markdown projection, report, and MCP config snippet.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import sys
from dataclasses import dataclass, field, replace as dataclasses_replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .agent_harness import AgentHarnessAdapter, SUPPORTED_AGENT_HARNESSES, install_instruction_pointer
from .agent_write import align_overlay, replay_agent_writes
from .batch import BatchIngestRunner, read_markdown_text, sha256_text
from .code_graph import (
    CodeGraphExtractor,
    extractor_fingerprint,
    manifest_delta,
    read_code_graph_cache,
    stat_manifest,
    write_code_graph_cache,
)
from .deploy import GitHubPagesDeployer
from .graph_stores import SqliteGraphStore
from .karpathy_layer import KarpathyLayerWriter
from .lint import LintReport, WikiLinter, read_git_head
from .locking import compile_lock
from .merge_ledger import collect_merges, publish_merge_ledger
from .output_snapshot import snapshot_output, write_state
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
from .research_graph import ResearchCorpusAnalyzer, ResearchGraph, ResearchGraphExtractor, ResearchNode, ResearchNodeType, filter_filename_shaped_concepts, graph_from_payload, link_paper_repo_pairs, prefer_research_node
from .temporal import TemporalFactProjector, render_competitive_report
from .temporal_observed import record_fact_observations, transaction_now
from .raganything_adapter import merge_raganything_graph
from .wiki_projector import partition_graph

logger = logging.getLogger("tesserae.project")

# Removed-backend tolerance (mirrors the understand-anything pattern): OLD
# configs may still carry a ``memory_backends.cognee`` section. It is ignored
# with ONE stderr note per process — never an error — so those configs keep
# loading unchanged.
_COGNEE_REMOVAL_NOTED = False


def _note_removed_cognee_section(cfg: object) -> None:
    """Print a one-per-process stderr note when a legacy config still carries
    ``memory_backends.cognee`` (the backend was removed in 0.19)."""
    global _COGNEE_REMOVAL_NOTED
    if _COGNEE_REMOVAL_NOTED:
        return
    backends = cfg.get("memory_backends") if isinstance(cfg, dict) else None
    if isinstance(backends, dict) and "cognee" in backends:
        print(
            "note: cognee backend was removed in 0.19 — section ignored",
            file=sys.stderr,
        )
        _COGNEE_REMOVAL_NOTED = True


def _env_truthy(name: str) -> bool:
    """True when env var ``name`` is set to a documented truthy value.

    Used to gate opt-in, destructive / credential-dependent compile passes
    (schema-drift apply, ambient-LLM passes) so the default compile stays
    deterministic and credential-free.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def _recompile_note(limit: Optional[int], corpus_size: int) -> str:
    """The tail of every completeness-guard warning: what the recompile will do.

    Every one of those warnings used to end "re-extracting the whole corpus",
    which is a promise the run cannot keep under ``--limit``. ``--limit`` cuts
    the work-list before the deferred documents, and a full (demoted) run
    rebuilds graph.json from the processed ones ALONE — so the deferred
    document stays uncovered, loses its ``graphed`` stamp again, and the very
    same warning fires on the next run. Measured, 3 docs and a RETAINED
    ``--limit=2``: four consecutive ``--changed-only`` runs each warned and each
    re-extracted a.md+b.md, and c.md never re-stamped.

    That livelock is a real CEILING, not something this note fixes: a limited
    run is a partial compile by construction, and the guard demands totality,
    so nothing short of accumulating into the prior graph instead of replacing
    it (the per-origin graph layer named elsewhere in this module, a schema
    migration) can satisfy both. What the note does is stop the warning lying
    about it and name the one action that clears the guard.

    ``corpus_size`` is always known here: the one caller is the filesystem
    completeness guard, which has already walked ``markdown_files``.
    """
    if limit is not None:
        if limit < corpus_size:
            return (
                f"re-extracting up to --limit={limit} of {corpus_size} — which cannot "
                "restore full coverage, so this warning repeats every run until you "
                "compile WITHOUT --limit."
            )
    return "re-extracting the whole corpus."


def _publish_atomically(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` via a temp file no other writer can share.

    The graph publishes used to go through ``target.with_suffix(".tmp")``, and
    ``with_suffix`` REPLACES the suffix rather than appending — so every
    publish of ``graph.json`` ran through the one fixed path
    ``.tesserae/graph.tmp``. Two publishers hitting that path at once (two
    hosts on a shared disk, or the engine daemon and an interactive compile on
    one host) interleave their writes into that single file and then rename the
    mixture into place, so what lands in ``graph.json`` is neither writer's
    graph and parses as truncated or duplicated JSON.

    PID + random suffix — the same shape ``batch.py::_write_manifest`` and
    ``session_graph.py::_write_cache`` already use — gives each writer its own
    scratch file, so the only thing they contend over is the ``os.replace``,
    which is atomic: the loser publishes a whole graph, just not the last one.
    The ``finally`` unlink matters because without it a failed write leaves the
    scratch file behind forever — a fixed name was at least reused.
    """
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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
    # Descent hierarchy sidecar (§3): every Louvain dendrogram level
    # (finest→coarsest, ``community_id`` → sorted member ids) plus the
    # high-degree hub ids, written by ``_write_hierarchy_sidecar`` right
    # after graph canonicalization. A pure function of graph content —
    # deterministic, byte-idempotent, never shipped to an LLM. Follows the
    # established sidecar idiom (``discovered_links.json``, ``sqlite.db``).
    hierarchy: Path = Path(".tesserae/hierarchy.json")
    # Extraction-feedback loop (docs/superpowers/specs/2026-05-26-...). Human
    # corrections captured during vault overlay / review-apply are appended to
    # ``extraction_feedback`` (JSONL, deduped). ``tesserae evolve`` distills them
    # into ``extraction_guidance`` (human-curatable markdown), caching each
    # cluster's LLM-phrased bullet under ``extraction_guidance_cache``.
    extraction_feedback: Path = Path(".tesserae/extraction-feedback.jsonl")
    extraction_guidance: Path = Path(".tesserae/extraction-guidance.md")
    extraction_guidance_cache: Path = Path(".tesserae/extraction_guidance_cache")
    # Compile-output snapshot state (no-op detector + byte-idempotence
    # tripwire; see ``tesserae.output_snapshot``). Hex digests + a bool only —
    # never timestamps — and always excluded from the hash by construction.
    output_snapshot: Path = Path(".tesserae/output-snapshot.json")
    # Code-graph extraction cache (delta-scoped regeneration; see
    # ``tesserae.code_graph``). INPUT state, not a compiled artifact: it
    # carries a stat manifest (``mtime_ns`` — volatile across checkouts), so
    # it must stay excluded from every output-hash scope — the
    # ``output_snapshot`` allowlists never include it.
    code_graph_cache: Path = Path(".tesserae/code-graph-cache.json")
    # Daily session-chunk store (see ``tesserae.session_chunks``): normalised
    # turns bucketed by KST day + a day_coverage gate, written live by the
    # engine daemon's tailer and by ``tesserae sessions chunk-backfill``. The
    # ``.lock`` is the backfill's non-blocking skip-if-held flock. INPUT-side
    # state (like ``harness_sessions``), never part of any output-hash scope.
    session_chunks: Path = Path(".tesserae/session_chunks.db")
    session_chunks_lock: Path = Path(".tesserae/session_chunks.lock")
    # Agent-authored graph overlay (see ``tesserae.agent_write``). Append-only
    # JSONL replayed as a 5th producer (``__agent_write__``) on every compile,
    # which is what keeps agent writes from being erased by recompilation.
    # INPUT state like ``extraction_feedback`` — never part of any output-hash
    # scope, so ``output_snapshot``'s allowlists deliberately exclude it.
    agent_writes: Path = Path(".tesserae/agent-writes.jsonl")
    # Merge ledger (see :mod:`tesserae.merge_ledger`): ``loser_id ->
    # survivor_id`` for every node the compile's three merge passes absorbed,
    # so a stale node id resolves to its survivor instead of reading as
    # not-found. DERIVED state: every ingest revalidates it against the graph
    # it just published and prunes what no longer lands there, so it stays a
    # statement about the current graph rather than merge history. Out of every
    # output-hash scope, and deliberately not node metadata — an out-of-band
    # metadata key would survive an incremental compile, vanish on a full one,
    # and land in ``graph.json`` bytes.
    merge_ledger: Path = Path(".tesserae/merge-ledger.json")


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
            hierarchy=self.root / "hierarchy.json",
            extraction_feedback=self.root / "extraction-feedback.jsonl",
            extraction_guidance=self.root / "extraction-guidance.md",
            extraction_guidance_cache=self.root / "extraction_guidance_cache",
            output_snapshot=self.root / "output-snapshot.json",
            code_graph_cache=self.root / "code-graph-cache.json",
            session_chunks=self.root / "session_chunks.db",
            session_chunks_lock=self.root / "session_chunks.lock",
            agent_writes=self.root / "agent-writes.jsonl",
            merge_ledger=self.root / "merge-ledger.json",
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
           persisted by ``init --obsidian-vault``.
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
        # Lets `tesserae vault set-root <PATH>` configure many
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
    def init(
        cls,
        project_root: str | Path = ".",
        name: Optional[str] = None,
        source_kind: str = "SourceDocument",
        sources: Optional[Iterable[str | Path]] = None,
        llm_provider: Optional[str] = None,
        llm_claude_config_dirs: Optional[List[str]] = None,
        llm_codex_home: Optional[str] = None,
    ) -> "ProjectWiki":
        wiki = cls(project_root)
        wiki.root.mkdir(parents=True, exist_ok=True)
        wiki.paths.markdown_projection.mkdir(parents=True, exist_ok=True)
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
            "report_path": ".tesserae/report.md",
            "temporal_facts_path": ".tesserae/temporal_facts.jsonl",
            "competitive_report_path": ".tesserae/competitive_report.md",
            "graphiti_episodes_path": ".tesserae/graphiti_episodes.jsonl",
            "agent_harness_path": ".tesserae/agent_harness",
            "harness_sessions_path": ".tesserae/harness_sessions",
            "obsidian_vault_path": ".tesserae/obsidian_vault",
            "site_path": ".tesserae/site",
        }
        # Durable LLM backend preference for the synthesis/insights JSON
        # client ("use codex instead of claude"). Only persisted when set so
        # existing configs stay byte-identical.
        if llm_provider:
            config["llm_provider"] = llm_provider
        if llm_claude_config_dirs:
            config["llm_claude_config_dirs"] = [str(d) for d in llm_claude_config_dirs]
        if llm_codex_home:
            config["llm_codex_home"] = str(llm_codex_home)
        wiki.paths.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return wiki

    @classmethod
    def load(cls, project_root: str | Path = ".") -> "ProjectWiki":
        wiki = cls(project_root)
        if not wiki.paths.config.exists():
            raise FileNotFoundError(f"Project wiki is not initialized: {wiki.root}. Run `tesserae init` first.")
        return wiki

    def config(self) -> dict:
        cfg = json.loads(self.paths.config.read_text(encoding="utf-8"))
        _note_removed_cognee_section(cfg)
        return cfg

    def _compile_options(self) -> dict:
        """Return the ``compile_options`` block from config.json.

        Holds the dieted (non-everyday) compile knobs that used to be CLI
        flags — each removed flag's old help text becomes the matching
        ``compile_options.<dest>`` key's documentation. Missing/invalid ⇒
        empty dict so callers fall back to the old argparse defaults.
        """
        try:
            cfg = self.config() if self.paths.config.exists() else {}
        except Exception:  # pragma: no cover — corrupt config must not crash
            cfg = {}
        opts = cfg.get("compile_options")
        return dict(opts) if isinstance(opts, dict) else {}

    def _build_json_client(self, model: Optional[str] = None):
        """Build the synthesis/insights JSON client honoring project config.

        Resolves ``llm_provider`` / ``llm_claude_config_dirs`` /
        ``llm_codex_home`` from ``config.json`` with env vars (i.e. CLI
        flags) taking precedence — see
        :func:`tesserae.llm_json.resolve_llm_client_settings`.
        """
        from .llm_json import build_default_json_client, resolve_llm_client_settings

        try:
            cfg = self.config() if self.paths.config.exists() else {}
        except Exception:  # pragma: no cover — corrupt config must not crash
            cfg = {}
        settings = resolve_llm_client_settings(cfg)
        return build_default_json_client(
            model=model,
            # Without ``settings=`` the factory re-resolves against env + the
            # GLOBAL config only, so a project-level llm_model / llm_base_url /
            # llm_api_key was dropped on the primary compile path — a custom
            # endpoint configured in .tesserae/config.json never reached the
            # client and the run silently used the default backend instead.
            settings=settings,
            provider=settings["provider"],
            claude_config_dirs=settings["claude_config_dirs"],
            # codex_homes, not codex_home: the scalar is a one-element list by
            # the time it reaches the client, which pins it verbatim and turns
            # rotation off for compile/session/community/distillation — the
            # exact defect this branch exists to remove, on its primary path.
            codex_homes=settings.get("codex_homes"),
            codex_reasoning_effort=settings.get("codex_reasoning_effort"),
        )

    def ingest(
        self,
        inputs: Iterable[str | Path],
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
        loader: Optional[SourceLoader] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        session_options: Optional[SessionExtractionOptions] = None,
        use_extraction_feedback: bool = False,
        doc_extractor: Optional[object] = None,
        changed_paths: Optional[List[Path]] = None,
        llm_passes_client: Optional["LLMJsonClient"] = None,
        progress: Optional["CompileProgress"] = None,
        incremental_override: Optional[bool] = None,
        retry_fallbacks: bool = False,
    ) -> dict:
        """Run :meth:`_ingest` with a merge collector open, then publish the ledger.

        The split exists only so the collector's scope is a ``with`` block
        around the WHOLE pipeline. The three merge passes fire from roughly ten
        call sites inside it, and a node absorbed at the first is long gone by
        the last, so collecting anywhere narrower — around the final
        ``merge_graphs([graph])``, say — yields a ledger that is empty on every
        real corpus. Both entry points that publish ``graph.json`` route
        through here (``compile`` and the standalone ``ingest`` verb), which is
        what keeps the ledger from going stale against the graph beside it.

        The ledger write is best-effort: it is a read-convenience sidecar, and
        a failure to write one must not fail a compile that produced a correct
        graph. It is logged rather than swallowed, matching the
        output-snapshot write at the tail of :meth:`compile`.
        """
        self._published_node_ids: Set[str] = set()
        with collect_merges() as merges:
            result = self._ingest(
                inputs,
                source_kind=source_kind,
                changed_only=changed_only,
                limit=limit,
                trends=trends,
                min_trend_sources=min_trend_sources,
                loader=loader,
                store=store,
                vault_pull=vault_pull,
                session_options=session_options,
                use_extraction_feedback=use_extraction_feedback,
                doc_extractor=doc_extractor,
                changed_paths=changed_paths,
                llm_passes_client=llm_passes_client,
                progress=progress,
                incremental_override=incremental_override,
                retry_fallbacks=retry_fallbacks,
            )
        try:
            result["merges_recorded"] = publish_merge_ledger(
                self.paths.merge_ledger, merges, self._published_node_ids
            )
        except OSError:
            logger.exception(
                "merge-ledger write failed; compile artifacts are unaffected, but a "
                "node id absorbed by this compile will read as not-found"
            )
        return result

    def _ingest(
        self,
        inputs: Iterable[str | Path],
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
        loader: Optional[SourceLoader] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        session_options: Optional[SessionExtractionOptions] = None,
        use_extraction_feedback: bool = False,
        doc_extractor: Optional[object] = None,
        changed_paths: Optional[List[Path]] = None,
        llm_passes_client: Optional["LLMJsonClient"] = None,
        progress: Optional["CompileProgress"] = None,
        incremental_override: Optional[bool] = None,
        retry_fallbacks: bool = False,
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

        # ------------------------------------------------------------ Phase 5
        # SINGLE gate for the LLM graph-mutating passes (KB-03 / KB-04
        # consistency). Both the session ``supersede`` pass and the
        # ``contradiction`` / schema-drift passes are graph-mutating and
        # credential-dependent, so they must run TOGETHER or NOT AT ALL — never
        # one without the other. Resolve ONE compile-level client here, gated by
        # a single explicit opt-in: an injected ``llm_passes_client`` OR the
        # documented ``TESSERAE_ENABLE_LLM_PASSES`` env var. When the gate is
        # OFF (the default) the client is ``None`` and NEITHER supersede NOR
        # contradiction runs, so the default compile is deterministic,
        # credential-free, and byte-idempotent. When ON, the SAME client is
        # threaded into both passes; their content-keyed disk caches keep warm
        # reruns byte-stable. (This decouples the graph-mutating passes from the
        # session FINDING-extraction client, which stays gated by
        # ``sessions.llm_enabled`` — that one only feeds extraction prompts and
        # never mutates graph.json edges.)
        llm_passes_client = self._resolve_llm_passes_client(llm_passes_client)

        # Extraction-feedback guidance (feature G). Collection of feedback
        # events is unconditional; only *injection* into prompts is gated by
        # ``use_extraction_feedback``. When the flag is off, both slices stay
        # "" so every extractor prompt is byte-identical to the legacy path.
        doc_guidance, session_guidance = self._load_extraction_guidance(
            use_extraction_feedback
        )

        # ``doc_extractor`` is an injection seam for tests; the default
        # deterministic extractor ignores guidance, but a Claude/Selective
        # extractor will pick up ``doc_guidance`` via its ``guidance`` attr.
        extractor = doc_extractor if doc_extractor is not None else ResearchGraphExtractor()
        if doc_guidance and hasattr(extractor, "guidance"):
            extractor.guidance = doc_guidance
        code_inputs: List[Path] = list(input_paths)
        markdown_source_kind = "SourceDocument" if kind in {"CodeProject", "Repository", "Project"} else kind

        # ------------------------------------------------------------ B3 / B4
        # Decide UP FRONT whether a provenance-driven incremental compile is
        # admissible. The precondition check must happen BEFORE we touch the
        # extractor batch AND before any ``SqliteGraphStore(...)`` constructor
        # runs (its ``create table if not exists`` would mint an empty
        # node_provenance sidecar and make an old/no-sidecar DB look
        # provenance-ready — Codex B3). When the incremental flag is OFF, or
        # the sidecar is missing / does not cover the prior graph, we degrade
        # to a TRUE full recompile: re-extract the WHOLE corpus with
        # changed_only=False, so the default path is byte-identical to a
        # from-scratch compile (Codex B4) — never a prior+delta merge.
        incremental_active = False
        prior_graph_for_diff: Optional[ResearchGraph] = None
        if changed_only and self.paths.graph.exists():
            incremental_enabled = (
                incremental_override
                if incremental_override is not None
                else bool(cfg.get("incremental_compile", False))
            )
            if incremental_enabled:
                # EXPERIMENTAL — incremental is byte-identical to a full compile
                # for the parity-gated edit shapes (additive K=1/5/21,
                # content-reduction, file-deletion, RENAME, alias-identity change,
                # both-endpoints-move), but a Codex re-review found remaining
                # divergence for MULTI-OWNER payload (re-extraction only re-runs
                # the canonical co-owner, not the union), producer-layer removal
                # (producer-owned nodes aren't tombstoned on incremental), the
                # over-cap fallback (not a true full compile), and untracked
                # post-pass edges. Tracked in 04.1-FOLLOWUP. The flag stays OFF
                # by default (full recompile = correct); do not enable in
                # production until those close.
                logger.warning(
                    "incremental_compile is ENABLED but EXPERIMENTAL: byte-parity "
                    "with a full compile holds for the parity-gated edit shapes, "
                    "but known gaps remain (multi-owner payload, producer-layer "
                    "removal, over-cap fallback). The default (flag off) full "
                    "recompile is the safe, supported path."
                )
                _prior = _strip_generated_layer(load_graph_file(self.paths.graph))
                if _prior.nodes or _prior.edges:
                    _prior_edge_triples = {
                        (e.source, e.type, e.target) for e in _prior.edges
                    }
                    if store is not None:
                        # Injected store: trust its provenance surface +
                        # coverage of the prior graph (no schema-creation risk
                        # from our side). Edge coverage required (Codex #5).
                        incremental_active = self._provenance_ready(
                            store,
                            [n.id for n in _prior.nodes],
                            prior_edge_triples=_prior_edge_triples,
                        )
                    else:
                        # Default SQLite path: inspect the on-disk db WITHOUT
                        # constructing the schema-creating store. Edge coverage
                        # required (Codex #2).
                        incremental_active = self._sqlite_provenance_ready(
                            self.paths.sqlite,
                            [n.id for n in _prior.nodes],
                            prior_edge_triples=_prior_edge_triples,
                        )
                    if incremental_active:
                        prior_graph_for_diff = _prior
        # Effective changed-only for the EXTRACTION batch: only re-extract a
        # subset when an incremental compile is genuinely active. Otherwise
        # re-extract everything (true full recompile, Codex B4) — UNLESS the
        # corpus is byte-for-byte unchanged, which is the idempotent no-op case
        # handled separately below. The experimental provenance differ (gated on
        # ``incremental_active``) is the ONLY path allowed to re-extract a
        # *subset*; a plain ``changed_only`` rerun either no-ops (nothing
        # changed) or falls back to a full recompile (something changed), so it
        # never produces a partial graph.json.
        # Both this and ``incremental_active`` can still be DEMOTED below, when
        # the manifest shows ``graph.json`` is not known to cover every tracked
        # document: the differ would reuse that partial graph as the corpus.
        effective_changed_only = changed_only and incremental_active
        # ...with ONE narrow exception, decided below: ``--changed-only
        # --retry-fallbacks`` on a corpus that is byte-for-byte unchanged AND
        # whose prior ``graph.json`` is known-complete. That is the one shape
        # where re-extracting a SUBSET is safe without the provenance differ.
        # Note what the no-op short-circuit's predicate does NOT prove: matching
        # manifest keys + shas say the manifest is self-consistent, not that
        # ``graph.json`` was ever written from it. The manifest is persisted
        # per-document inside the batch worker; ``graph.json`` only at the end of
        # ``compile()``. So the subset path additionally requires the ``graphed``
        # completeness marker (stamped after ``_write_artifacts`` returns) and a
        # provenance sidecar that covers the prior graph (so the retried docs'
        # degraded nodes can actually be tombstoned). Every other shape still
        # takes the full recompile (Codex B4).
        fallback_only = False
        prior_graph_for_fallback: Optional[ResearchGraph] = None
        # One reason string for every reuse path that refuses to trust
        # ``graph.json``: the filesystem no-op / scoped-retry / differ gates
        # below.
        ungraphed_reason = (
            "graph.json is not known to cover every tracked document "
            "(interrupted compile, a document deferred past --limit, or a "
            "manifest predating the completeness marker)"
        )

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

            # Idempotent no-op short-circuit (changed-only). When the caller asks
            # for ``changed_only`` and EVERY candidate markdown file is unchanged
            # versus the manifest (same sha256) AND ``graph.json`` is known to
            # cover every tracked document, there is nothing to re-extract: the
            # existing ``graph.json`` IS the full corpus. Re-running a full
            # recompile here would be wasted work and — more importantly —
            # would report ``processed_files`` for files that did not change,
            # defeating ``--changed-only``. We detect this BEFORE the batch so a
            # genuine no-op reports ``processed=0, skipped=N`` and reuses the
            # prior graph. This is distinct from the experimental provenance
            # differ (``incremental_active``): it never re-extracts a subset, it
            # only skips the whole batch when nothing changed at all. If ANY file
            # changed, we fall through to the full recompile below (Codex B4).
            #
            # SUBTRACTIVE guard: matching shas for the surviving candidates is
            # NOT enough — a DELETED (or renamed-away) file leaves a manifest
            # entry behind (``BatchIngestRunner`` merges, never prunes) while the
            # prior ``graph.json`` still carries its source node + incident
            # edges. Reusing that graph would resurrect the deleted file's nodes
            # (test_incremental_parity deletion gate). The no-op therefore also
            # requires the manifest's tracked-path set to be IDENTICAL to the
            # current candidate set; any extra manifest entry means the corpus
            # shrank or shifted, and we fall through to the full recompile —
            # the always-correct path.
            #
            # COMPLETENESS guard: matching shas do not prove ``graph.json`` was
            # ever written from them either. The manifest is persisted
            # per-document INSIDE the batch worker; ``graph.json`` only at the
            # end of ``compile()``. Kill a compile between the two and every sha
            # matches while the graph is missing the docs that run added — and
            # then this short-circuit would report a clean ``processed=0,
            # skipped=N`` forever, on a permanently partial graph, until a
            # doc's bytes change. Same predicate the ``fallback_only`` path
            # already requires (``graph_covers_corpus``); it belongs here too.
            noop_skip = False
            # When the operator asked for the scoped recovery and does not get
            # it, NAME the disqualifying condition. Silence here is how the
            # 137->35 bound evaporates in the normal workflow: a ``refresh`` /
            # ``sessions-import`` writes today's session markdown into the same
            # tracked set, ``corpus_unchanged`` goes False, and the run quietly
            # re-extracts all 137 (the reported 1.75h non-completion). The CLI's
            # warning only covers a missing ``--changed-only``; these are the
            # other four exits.
            fallback_blocked: Optional[str] = None
            # Same disqualifier, reached on the two ``--changed-only`` paths that
            # reuse the prior graph without a pending fallback: the plain no-op
            # skip, and the experimental differ (demoted below). Either way the
            # operator is about to pay for a full recompile they did not ask for
            # and must be told why. Also fires for a workspace whose manifest
            # predates the ``graphed`` marker — ONCE if that recompile is
            # unlimited, because it re-stamps every entry and the next
            # ``--changed-only`` reuses again. Under a retained ``--limit`` it
            # re-stamps only the docs it reached, so it fires EVERY run:
            # measured with the pre-marker manifest as the ONLY cause, 3 docs
            # and ``--limit=2``, four consecutive runs each warned, each
            # re-extracted a.md+b.md, and c.md was never stamped.
            noop_blocked: Optional[str] = None
            if changed_only and markdown_files and self.paths.graph.exists():
                manifest_files = self._load_manifest()
                candidate_keys = {str(md) for md in markdown_files}
                # ``_ingest_via_loader`` shares this manifest under ``source:``
                # keys, and both predicates below deliberately COUNT them. A
                # ``source:`` entry is never a filesystem candidate and nothing
                # stamps it ``graphed``, so one loader run makes
                # ``corpus_unchanged`` and ``graph_covers_corpus`` permanently
                # false and every later filesystem ``--changed-only`` re-extracts
                # the whole corpus.
                #
                # That cost is DELIBERATE, and excluding them is not a cheaper
                # spelling of the same rule — it is a hole. Both origins rebuild
                # graph.json from their OWN sources alone, so a loader run
                # genuinely removes the filesystem documents from the graph while
                # their entries still match by sha. Exclude ``source:`` keys and
                # this predicate calls that graph complete, takes the no-op, and
                # serves a loader-only graph until a document's bytes change —
                # which for an untouched corpus is never. Counting them is what
                # keeps the dangerous no-op unreachable in a mixed workspace.
                #
                # ponytail: ceiling — the price is that a workspace which has
                # EVER run a loader loses the scoped fast path (137->35) and
                # pays a full re-extract on every filesystem ``--changed-only``.
                # Buying it back needs per-origin coverage tracking: graph.json
                # accumulated by origin instead of replaced, with each origin's
                # reuse judged against the layer it actually built. That is a
                # schema migration, and it is only worth building once
                # ``compile(loader=...)`` has a caller — today it has none (no
                # CLI path and no daemon path passes a loader), so the scoped
                # path is unreachable-in-practice and the conservative rule
                # costs real workspaces nothing.
                #
                # The completeness marker (see the COMPLETENESS guard above).
                # ``graphed`` is stamped only after ``_write_artifacts``
                # returned, so its absence has three possible causes — an
                # interrupted compile, a document DEFERRED past ``--limit`` at
                # the stamp block below, or a manifest written before the marker
                # existed. Those are the three ``ungraphed_reason`` names. Two of
                # them clear after one recompile; the DEFERRED one does not while
                # the ``--limit`` is retained (see the demotion just below).
                #
                # ALL THREE reuse paths need the marker, because each hands back
                # the prior ``graph.json`` as if it were the whole corpus: the
                # scoped fallback merge, the plain no-op skip, and — reached
                # through the demotion just below — the experimental differ's
                # own ``processed == 0`` reuse.
                graph_covers_corpus = all(
                    entry.get("graphed") is True for entry in manifest_files.values()
                )
                if incremental_active:
                    if not graph_covers_corpus:
                        # This demotion is the differ's ONLY consultation of the
                        # marker; downstream it just skips an unstamped doc by
                        # sha, hits ``processed == 0``, and returns the prior
                        # graph verbatim — the graph missing exactly the doc the
                        # killed run added. Nothing in that run re-stamps, so it
                        # would stay missing until the doc's bytes change. Demote
                        # to the full recompile, which re-extracts and re-stamps.
                        #
                        # CORRECT, but not terminating under a retained
                        # ``--limit``: the demoted run is limited too, so it
                        # rebuilds graph.json from the docs it reached ALONE,
                        # defers the same doc, unstamps it again, and the next
                        # run demotes again. Measured, 3 docs and ``--limit=2``:
                        # five consecutive ``--changed-only`` runs each warned
                        # and each re-extracted a.md+b.md, and c.md never
                        # re-stamped. The reason is structural — a limited run is
                        # a partial compile by construction while the guard
                        # demands totality. ``_recompile_note`` stops the warning
                        # claiming otherwise and names the exit (compile WITHOUT
                        # ``--limit``); actually making progress under a limit
                        # needs the per-origin accumulate-instead-of-replace
                        # graph layer this module books as a ceiling.
                        incremental_active = False
                        prior_graph_for_diff = None
                        effective_changed_only = False
                        noop_blocked = ungraphed_reason
                    # else: coverage is complete, so the differ's reuse is sound
                    # and the scoped run proceeds — no standing full-recompile
                    # tax on incremental workspaces.
                else:
                    # ``--retry-fallbacks`` has work to do precisely when nothing
                    # changed, so a pending fallback entry must beat the no-op —
                    # otherwise the flag is swallowed here and never reaches the
                    # batch runner that acts on it.
                    has_pending_fallback = retry_fallbacks and any(
                        manifest_files.get(str(md), {}).get("fallback") is True
                        for md in markdown_files
                    )
                    corpus_unchanged = set(manifest_files.keys()) == candidate_keys and all(
                        manifest_files.get(str(md), {}).get("sha256")
                        == sha256_text(read_markdown_text(md))
                        for md in markdown_files
                    )
                    if has_pending_fallback and not corpus_unchanged:
                        fallback_blocked = (
                            "the tracked corpus changed since the last compile (a "
                            "document was added, edited or removed)"
                        )
                    elif corpus_unchanged and has_pending_fallback:
                        if trends:
                            # ``trends`` recomputes the trend layer from THIS run's
                            # per-doc graphs (``summarize_trends(graphs, ...)``); a
                            # scoped batch only holds the retried ones, which would
                            # silently shrink that layer. Keep trends users on the
                            # full recompile.
                            fallback_blocked = (
                                "this project computes trends, which are derived "
                                "from every document's per-doc graph"
                            )
                        elif not graph_covers_corpus:
                            # Merging one retried doc over a graph that predates the
                            # docs the killed run added drops those docs
                            # permanently: this run also CLEARS the fallback marker,
                            # so every later ``--changed-only`` no-ops and the
                            # corpus stays partial forever. Full recompile — which
                            # re-stamps.
                            fallback_blocked = ungraphed_reason
                        else:
                            # The retried docs' DEGRADED nodes have to LEAVE the
                            # prior graph before the recovered ones merge in, or the
                            # "recovery" contaminates instead of repairing — see the
                            # tombstone at the merge below. That tombstone reads the
                            # provenance sidecar, and (exactly like the differ) is
                            # only trustworthy when the sidecar covers the whole
                            # prior graph: a node co-owned by an UNCHANGED doc whose
                            # row is missing would be deleted outright.
                            _prior_fb = _strip_generated_layer(
                                load_graph_file(self.paths.graph)
                            )
                            _fb_ids = [n.id for n in _prior_fb.nodes]
                            _fb_triples = {
                                (e.source, e.type, e.target) for e in _prior_fb.edges
                            }
                            if (
                                self._provenance_ready(
                                    store, _fb_ids, prior_edge_triples=_fb_triples
                                )
                                if store is not None
                                else self._sqlite_provenance_ready(
                                    self.paths.sqlite,
                                    _fb_ids,
                                    prior_edge_triples=_fb_triples,
                                )
                            ):
                                fallback_only = True
                                prior_graph_for_fallback = _prior_fb
                            else:
                                fallback_blocked = (
                                    "the provenance sidecar does not cover the prior "
                                    "graph, so the degraded nodes could not be "
                                    "tombstoned before the retry merged in"
                                )
                    elif corpus_unchanged:
                        if graph_covers_corpus:
                            noop_skip = True
                        else:
                            # Nothing changed, but the graph we would have reused is
                            # not known to be whole. A kill inside the scoped-retry
                            # window below lands here too: it rewrites the retried
                            # doc's manifest entry from scratch, clearing BOTH its
                            # fallback marker and its ``graphed`` stamp, so
                            # ``has_pending_fallback`` is False on the next run and
                            # this is the only guard left to notice that
                            # ``graph.json`` still carries the degraded nodes.
                            noop_blocked = ungraphed_reason

            recompile_note = _recompile_note(limit, len(markdown_files))
            if fallback_blocked is not None:
                print(
                    "warning: --retry-fallbacks could not scope this run to the "
                    f"marked document(s) — {fallback_blocked}; {recompile_note}",
                    file=sys.stderr,
                )
            elif noop_blocked is not None:
                print(
                    "warning: --changed-only could not reuse the prior graph — "
                    f"{noop_blocked}; {recompile_note}",
                    file=sys.stderr,
                )

            if noop_skip:
                # Nothing changed: the prior graph.json is the corpus. Re-derive
                # the union graph from disk and report a pure skip. Downstream
                # artifact writes are byte-idempotent for graph.json / vault /
                # site, so re-emitting them from the prior graph leaves those
                # unchanged. NOT for the provenance sidecar: this run extracts
                # nothing, so ``extraction_prov`` is empty and a reconcile would
                # REPLACE the row-set with it — which is exactly why the
                # ``full_compile`` argument below is gated on
                # ``extracted_graphs`` being non-empty.
                base_graph = _strip_generated_layer(load_graph_file(self.paths.graph))
                graphs = []
                processed = 0
                skipped = len(markdown_files)
                fallback_files: List[str] = []
                batch = None
            else:
                batch = BatchIngestRunner(extractor=extractor, manifest_path=self.paths.manifest).run(
                    markdown_files,
                    source_kind=markdown_source_kind,
                    changed_only=effective_changed_only or fallback_only,
                    limit=limit,
                    progress=progress,
                    retry_fallbacks=retry_fallbacks,
                )
                graphs = batch.graphs or [batch.graph]
                processed = batch.processed
                skipped = batch.skipped
                # Documents whose LLM extraction failed and were served by the
                # deterministic baseline. The count reached only the batch's own
                # stderr note, so a compile that lost most of its concept layer
                # still printed a clean summary and exited 0 (issue #92).
                fallback_files = list(batch.fallback_paths)
                if fallback_only:
                    # The corpus is byte-unchanged AND the prior graph is
                    # marked complete, so it carries every doc the batch just
                    # skipped; merging the retried docs over it keeps graph.json
                    # WHOLE (never the partial graph Codex B4 guards against).
                    # ``graphs`` deliberately stays the batch's per-doc list —
                    # feeding the prior graph into
                    # ``compute_extraction_provenance`` would attribute the
                    # whole prior corpus to one file.
                    #
                    # TOMBSTONE FIRST, with the differ's own helper and in its
                    # order (nodes, then edges whose only asserting file was
                    # retried). A plain prior-first merge does not repair, it
                    # contaminates: ``prefer_research_node`` defaults to
                    # ``chosen = existing``, so a same-id node keeps the
                    # DEGRADED name/description/metadata, and a node the
                    # deterministic pass named differently simply survives
                    # alongside its typed replacement. Either way the fallback
                    # marker is then cleared and a second ``--retry-fallbacks``
                    # is a no-op, so the operator never learns.
                    #
                    # Keys are ``batch.processed_paths`` VERBATIM (no
                    # ``resolve()``): ``BatchIngestRunner`` uses one
                    # ``str(file_path)`` for the manifest key, for the
                    # ``source_path`` it hands the extractor, and hence for the
                    # rows ``compute_extraction_provenance`` wrote — so these
                    # are the sidecar's own keys by construction.
                    #
                    # ponytail: ceiling — a node CO-OWNED by an unchanged doc
                    # survives the tombstone (correctly: that doc still asserts
                    # it) carrying the payload the degraded extraction may have
                    # won, and we do not re-extract the surviving co-owner to
                    # re-derive the winner. Upgrade path is the differ's Blocker
                    # #3 machinery (``surviving_source_paths`` + co-owner
                    # re-extraction), which costs exactly the extra extractions
                    # this mode exists to avoid.
                    fb_store = (
                        store if store is not None else SqliteGraphStore(self.paths.sqlite)
                    )
                    removed_ids, removed_edges = fb_store.delete_nodes_by_source_with_edges(
                        set(batch.processed_paths)
                    )
                    kept_prior = ResearchGraph(
                        nodes=[
                            n
                            for n in prior_graph_for_fallback.nodes
                            if n.id not in removed_ids
                        ],
                        edges=[
                            e
                            for e in prior_graph_for_fallback.edges
                            if e.source not in removed_ids
                            and e.target not in removed_ids
                            and (e.source, e.type, e.target) not in removed_edges
                        ],
                    )
                    base_graph = merge_graphs([kept_prior, batch.graph])
                else:
                    base_graph = batch.graph
        else:
            # Injected loader path: ``Source`` records carry their own content,
            # so we extract from text and bookkeep changed-only against a
            # source-id-keyed manifest. The on-disk manifest format stays the
            # same JSON dict; entries are merged so a future FS-loader run
            # does not erase loader-keyed entries (and vice versa).
            #
            # ponytail: ceiling — this path has NO completeness guard, and gets
            # none until it has a caller. ``compile(loader=...)`` is a
            # library-only API today: neither the CLI nor ``engine/daemon.py``
            # ever passes a loader, and the daemon's ``changed_paths`` reaches
            # the filesystem branch. The guard's mirror over ``source:`` keys
            # existed here and was removed with the rest of the cross-origin
            # machinery, because defending an uncalled API cost two silent
            # data-loss defects and one regression against v0.25.1. What is
            # left uncovered is real and worth naming: an interrupted
            # ``compile(loader=..., changed_only=True)`` under the experimental
            # differ leaves ``_ingest_via_loader``'s ``finally``-persisted sha
            # on disk while graph.json never got the nodes, so later runs
            # sha-skip it. Building the guard properly means per-origin coverage
            # tracking (graph.json accumulated by origin rather than replaced) —
            # a schema migration, correctly deferred until a caller exists.
            graphs, processed, skipped, fallback_files = self._ingest_via_loader(
                loader=loader,
                extractor=extractor,
                source_kind=markdown_source_kind,
                changed_only=effective_changed_only,
                limit=limit,
            )
            # An empty ``graphs`` leaves the base EMPTY on purpose. When the
            # differ owns the reuse it detects ``processed == 0 and not
            # graph.nodes`` below and rebinds ``graph`` to its own
            # ``prior_graph_for_diff``; handing it a POPULATED base instead
            # looks equivalent and stops being so the moment something is
            # TOMBSTONED — a deleted ``changed_paths`` entry fails that
            # ``not deleted_changed`` test and takes the differ's ``else`` arm,
            # which appends ``graph`` to ``merge_inputs`` AFTER the tombstone,
            # merging the deleted document's nodes straight back in. Measured:
            # ``Paper:a`` resurrected.
            base_graph = merge_graphs(graphs) if graphs else ResearchGraph()

        # Per-file extraction graphs from THIS run, the authoritative source of
        # provenance (Codex B2). On a full compile they cover the whole corpus;
        # on an active incremental compile they cover only the re-extracted
        # changed files. ``compute_extraction_provenance`` attributes each
        # node/edge to the file whose extraction actually produced it.
        extracted_graphs: List[ResearchGraph] = list(graphs)

        # Extraction is done; everything below (trends, code graph, memory
        # passes, community summaries, vault, site) is the "finalize" phase.
        if progress is not None:
            progress.finalize("community summaries, vault, site")

        # ``graphs`` is empty on the ``noop_skip`` short-circuit above, which
        # reuses the prior graph from DISK — and ``summarize_trends([])`` returns
        # an EMPTY graph. Recomputing the trend layer there would therefore
        # overwrite graph.json with nothing while the manifest still marks every
        # doc ``graphed``, which makes the loss PERMANENT: the next
        # ``--changed-only`` no-ops again on the wreck. Reusing ``base_graph`` is
        # also the CORRECT answer, not merely the safe one: that arm set it to
        # the prior graph loaded from disk, and it still carries its TREND nodes
        # (``_strip_generated_layer`` removes only SYNTHESIS and
        # COMMUNITY_SUMMARY).
        #
        # Two more shapes leave ``graphs`` empty with a deliberately EMPTY
        # ``base_graph``, and they end differently:
        #
        # * The differ owns the reuse. The prior graph survives by ONE OF TWO
        #   routes, not just the rebind: that rebind is gated on
        #   ``not graph.nodes``, and between here and there ``graph`` picks up
        #   the code-graph merge (CodeProject/Repository/Project) and
        #   ``_merge_configured_raganything_graph``. Either can make it
        #   non-empty, and the run then takes the differ's ``else`` arm instead
        #   — where the prior graph survives by being MERGED in, after a
        #   tombstone that removes nothing on an empty changed-set. Measured
        #   both ways: a plain kind rebinds, a CodeProject merges (3 code
        #   nodes), and graph.json keeps the prior graph either way.
        # * A loader run whose ``discover()`` yielded nothing, with the differ
        #   off. That one DOES overwrite graph.json with an empty graph. It is
        #   the ceiling booked at the loader branch above, not an oversight:
        #   without the differ ``effective_changed_only`` is False, so the next
        #   loader run re-extracts everything and heals it — and no shipped
        #   caller reaches the branch at all.
        graph = (
            ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=min_trend_sources)
            if trends and graphs
            else base_graph
        )
        cfg = self.config()
        code_graph_report: Optional[Dict[str, object]] = None
        if self._code_layer_enabled(cfg):
            # Delta-scoped regeneration gate (whole-layer grain — reuse
            # everything or re-extract everything, never a partial graph).
            # When the stat manifest of the walked file list AND the extractor
            # fingerprint match the cache, rehydrate the cached extractor
            # output instead of re-parsing every code file. Cache failures of
            # any kind degrade to the always-correct full extraction.
            cg_extractor = CodeGraphExtractor(self.project_root)
            cg_files = cg_extractor.iter_code_files(code_inputs)
            manifest = stat_manifest(cg_files, self.project_root)
            fingerprint = extractor_fingerprint()
            cached = read_code_graph_cache(self.paths.code_graph_cache)
            code_graph: Optional[ResearchGraph] = None
            if (
                manifest is not None
                and fingerprint is not None
                and cached is not None
                and cached.fingerprint == fingerprint
                and cached.manifest == manifest
            ):
                try:
                    code_graph = graph_from_payload(cached.graph_payload)
                except Exception:
                    logger.exception("code-graph cache rehydration failed; re-extracting")
                    code_graph = None
            reused = code_graph is not None
            if code_graph is None:
                code_graph = cg_extractor.extract_files(cg_files)
                if manifest is not None and fingerprint is not None:
                    write_code_graph_cache(
                        self.paths.code_graph_cache, code_graph, manifest, fingerprint
                    )
            code_graph_report = {
                "reused": reused,
                "files": len(cg_files),
                "delta": None
                if reused or manifest is None
                else manifest_delta(cached.manifest if cached else None, manifest),
            }
            logger.info(
                "code graph %s (%d files)",
                "reused — tree unchanged" if reused else "re-extracted",
                len(cg_files),
            )
            _before_code = graph
            graph = merge_graphs([graph, code_graph])
            # Codex #6: code-graph re-derives its nodes/edges from the repo every
            # compile, so they carry no extraction-file provenance. Attribute the
            # ids it minted this compile to "__code_graph__".
            self._record_producer_provenance("__code_graph__", _before_code, graph)
        _before_rag = graph
        graph = self._merge_configured_raganything_graph(graph, cfg)
        self._record_producer_provenance("__raganything__", _before_rag, graph)
        # Provenance-driven incremental differ (Codex B1/B2/B3/B4). Only runs
        # when an incremental compile is genuinely admissible — decided up
        # front in ``incremental_active`` (flag on + sidecar present + covers
        # the prior graph). The batch above re-extracted ONLY the changed files
        # in this case; we now tombstone what those files solely owned (nodes
        # AND edges) and merge the survivors with the fresh extraction.
        #
        # When the incremental flag is OFF or the preconditions failed,
        # ``effective_changed_only`` was forced False, the batch re-extracted
        # the WHOLE corpus, and ``incremental_active`` is False — so we skip
        # this block entirely and ``graph`` already equals a from-scratch full
        # compile (Codex B4). No prior+delta merge, no partial graph. (The
        # ``fallback_only`` retry also lands here with ``incremental_active``
        # False; it re-extracted a subset but already tombstoned and merged it
        # over the prior graph, which ``corpus_unchanged`` + the ``graphed``
        # marker prove is complete — see the ceiling noted at that merge.)
        # Resolved paths whose prior nodes this run TOMBSTONED. The ``graphed``
        # stamping block below needs it: a ``--limit``-deferred doc normally
        # keeps its stamp because nothing touched its nodes, but an explicit
        # ``changed_paths`` tombstones by CALLER intent rather than by what the
        # batch actually processed — so a deferred doc on that path really did
        # lose its nodes and must not stay stamped.
        tombstoned_resolved: set[str] = set()
        if incremental_active and prior_graph_for_diff is not None:
            prior_graph = prior_graph_for_diff
            # A DELETED changed_path no longer exists on disk, so the batch
            # never re-extracts it: ``processed == 0`` and the fresh ``graph``
            # is empty even though the corpus genuinely shrank. That is NOT the
            # idempotent no-op case — we must still tombstone what the deleted
            # file solely owned. Detect deletions explicitly so the no-op branch
            # only fires when nothing changed AND nothing was removed.
            deleted_changed = bool(
                changed_paths is not None
                and any(not Path(p).exists() for p in changed_paths)
            )
            if processed == 0 and not graph.nodes and not deleted_changed:
                # Nothing actually changed this run: the prior graph IS the
                # corpus. (Empty incremental run — byte-idempotent no-op.)
                graph = prior_graph
            else:
                # Effective changed-file set: trust the caller's explicit
                # ``changed_paths`` over the manifest re-scan when provided
                # (04-RESEARCH.md Pitfall 3). Otherwise fall back to the
                # manifest-derived processed-paths set.
                if changed_paths is not None:
                    changed_set = {str(Path(p).resolve()) for p in changed_paths}
                else:
                    changed_set = {
                        str(Path(p).resolve())
                        for p in (batch.processed_paths if loader is None else [])
                    }
                inc_store = store if store is not None else SqliteGraphStore(self.paths.sqlite)
                # Tombstone NODES whose provenance set became empty after
                # removing the changed files (cross-file nodes co-owned by an
                # unchanged file survive — no 2400->1700 collapse), then EDGES
                # whose only asserting file changed and stopped emitting them
                # even though both endpoints survive (Codex B1 stale edge).
                removed_ids, removed_edges = inc_store.delete_nodes_by_source_with_edges(
                    changed_set
                )
                tombstoned_resolved = changed_set
                kept_nodes = [n for n in prior_graph.nodes if n.id not in removed_ids]
                # Re-point stale ``source_path`` scalars on surviving cross-file
                # nodes (Phase-4 subtractive gate). A shared author/field node
                # survives because an UNCHANGED file still asserts it, but the
                # prior node we keep may carry a ``source_path`` pointing at the
                # now-changed file that originally won attribution. A full
                # compile re-derives ``source_path`` from the lowest sorted-path
                # surviving owner (``prefer_research_node`` keeps the first-merged
                # one, and ``iter_markdown_files`` is sorted). We reproduce that
                # exact choice from the post-tombstone provenance sidecar so the
                # incremental node scalars match a full compile byte-for-byte.
                # Blocker #3 (Plan 03): surviving-owner RE-EXTRACTION. A
                # cross-file node survives the subtractive edit because an
                # UNCHANGED co-owner file still asserts it — but the prior node
                # we kept carries the PRIOR merged payload (name / aliases /
                # description / metadata), and surviving cross-file EDGES keep
                # their prior evidence. When the changed file is the one that
                # WON attribution (e.g. it carried the title-case alias that
                # ``prefer_research_node`` chose), the kept payload diverges
                # from a full compile of the post-edit corpus. Re-pointing
                # ``source_path`` alone (below) is not enough. We RE-EXTRACT
                # the surviving co-owner files and re-merge through the exact
                # ``merge_graphs`` / ``prefer_research_node`` /
                # ``_merge_same_type_aliased_duplicates`` code a full compile
                # uses, so the canonical winner — and its edge evidence — is
                # re-derived byte-identically. (04.1-RESEARCH.md
                # Recommendation 1; fragment store explicitly rejected.)
                reextract_graph: Optional[ResearchGraph] = None
                stale_ids: Set[str] = set()
                full_fallback = False
                if hasattr(inc_store, "surviving_source_paths"):
                    # Only nodes the changed files STOPPED asserting are stale.
                    # If the fresh extraction still emits the node (additive
                    # edit, or the changed file still mentions it), the merge
                    # below carries the correct fresh ``source_path`` — leave it.
                    fresh_node_ids = {n.id for n in graph.nodes}
                    stale_ids = {
                        n.id
                        for n in kept_nodes
                        if n.id not in fresh_node_ids
                        and n.source_path
                        and str(Path(n.source_path).resolve()) in changed_set
                    }
                    canonical = inc_store.surviving_source_paths(stale_ids)
                    # EXCLUDE producer-owned nodes (Plan 02 contract): a node
                    # whose only surviving provenance source is a ``__``-prefixed
                    # producer sentinel (``__code_graph__`` / ``__session_graph__``
                    # / ``__raganything__`` /
                    # ``__vault_overlay__``) is regenerated by its producer every
                    # compile — there is no markdown file to re-extract it from.
                    # ``min(source_path)`` returns a real path over a ``__``
                    # sentinel when both exist (``/`` < ``_``), so a ``__`` value
                    # here means the node has NO surviving file owner.
                    producer_only = {
                        nid
                        for nid, src in canonical.items()
                        if src.startswith("__")
                    }
                    stale_ids -= producer_only
                    canonical = {
                        nid: src
                        for nid, src in canonical.items()
                        if nid not in producer_only
                    }
                    if canonical:
                        kept_nodes = [
                            dataclasses_replace(n, source_path=canonical[n.id])
                            if n.id in canonical
                            else n
                            for n in kept_nodes
                        ]
                    # Surviving co-owner FILES to re-extract: the canonical
                    # winner file per stale node (a real markdown path that
                    # still exists on disk). The merge re-runs the dedup rule,
                    # so the canonical-path set is sufficient — the changed
                    # file's contribution is already in the fresh ``graph``.
                    co_owner_files = sorted(
                        {
                            src
                            for src in canonical.values()
                            if not src.startswith("__") and Path(src).exists()
                        }
                    )
                    if co_owner_files:
                        # Safety CAP: bound re-extraction cost for dense-sharing
                        # corpora. Over the cap we cannot cheaply re-derive the
                        # surviving payload, so we fall back to a FULL compile
                        # of the whole corpus (still byte-identical, just not
                        # incremental). (04.1-RESEARCH.md Scaling concern.)
                        reextract_cap = int(cfg.get("incremental_reextract_cap", 50))
                        if len(co_owner_files) > reextract_cap:
                            logger.warning(
                                "incremental re-extraction would touch %d surviving "
                                "co-owner files (> cap %d); falling back to a full "
                                "compile for byte-parity.",
                                len(co_owner_files),
                                reextract_cap,
                            )
                            re_batch = BatchIngestRunner(
                                extractor=extractor,
                                manifest_path=self.paths.manifest,
                            ).run(
                                markdown_files if loader is None else [],
                                source_kind=markdown_source_kind,
                                changed_only=False,
                                limit=limit,
                            )
                            full_graphs = re_batch.graphs or [re_batch.graph]
                            graph = (
                                ResearchCorpusAnalyzer().summarize_trends(
                                    full_graphs, min_sources=min_trend_sources
                                )
                                if trends
                                else re_batch.graph
                            )
                            # Re-derived the whole corpus: skip the prior+delta
                            # merge entirely (the full graph IS the result).
                            full_fallback = True
                        else:
                            # Re-extract ONLY the surviving co-owner files via
                            # the deterministic runner (changed_only=False so it
                            # re-runs even though their sha256 is unchanged; the
                            # manifest write is idempotent). No wall-clock, no
                            # new sidecar table.
                            re_batch = BatchIngestRunner(
                                extractor=extractor,
                                manifest_path=self.paths.manifest,
                            ).run(
                                [Path(p) for p in co_owner_files],
                                source_kind=markdown_source_kind,
                                changed_only=False,
                            )
                            reextract_graph = (
                                merge_graphs(re_batch.graphs)
                                if re_batch.graphs
                                else re_batch.graph
                            )
                if full_fallback:
                    # Full-compile fallback already replaced ``graph``.
                    pass
                else:
                    # Replace ONLY the stale nodes/edges. Drop the stale prior
                    # nodes (their payload is re-derived by re-extraction), keep
                    # every other survivor, and feed the re-extracted co-owner
                    # fragments through the NORMAL merge so the canonical winner
                    # + edge evidence is re-derived exactly as a full compile.
                    # Extra non-stale nodes a co-owner re-extraction emits merge
                    # normally and must not corrupt other nodes (Pitfall 2).
                    kept_nodes = [n for n in kept_nodes if n.id not in stale_ids]
                    kept_ids = {n.id for n in kept_nodes}
                    kept_edges = [
                        e for e in prior_graph.edges
                        if e.source in kept_ids
                        and e.target in kept_ids
                        and (e.source, e.type, e.target) not in removed_edges
                    ]
                    prior_kept_graph = ResearchGraph(nodes=kept_nodes, edges=kept_edges)
                    merge_inputs = [prior_kept_graph]
                    if reextract_graph is not None:
                        merge_inputs.append(reextract_graph)
                    merge_inputs.append(graph)
                    graph = merge_graphs(merge_inputs)
        # Bug A guard: after every merge — native FS extractor, code graph,
        # RAG-Anything, prior incremental graph — strip any concept-layer node
        # whose name is a filename or path; we don't want those duplicating
        # SourceDocument pages in the visual graph. See
        # ``filter_filename_shaped_concepts``.
        graph = filter_filename_shaped_concepts(graph)
        # Session graph extraction. Runs unconditionally when enabled (the
        # default); produces a slice of Session + SessionDecision nodes plus
        # discussed_in / derived_from_session edges that link the agent's
        # historical conversations into the doc graph. The structural pass
        # is the only thing Phase 3 wires in; the LLM pass arrives in
        # Phase 5 of the session-graph plan.
        _before_session = graph
        graph = self._merge_session_graph(
            graph,
            cfg,
            override=session_options,
            guidance=session_guidance,
            llm_passes_client=llm_passes_client,
        )
        # Codex #6: session graph re-derives Session/SessionDecision nodes +
        # discussed_in/derived_from_session edges from the harness transcripts
        # every compile. Attribute the minted ids to "__session_graph__".
        self._record_producer_provenance("__session_graph__", _before_session, graph)
        # Agent-write overlay (5th producer). Placement is load-bearing three
        # times: AFTER the session graph so the ``Agent`` anchor node already
        # exists and merges by id instead of forking; BEFORE the canonicalizing
        # ``merge_graphs([graph])`` below so agent nodes get the same dedup /
        # canonicalization every other node gets and Louvain sees them; and
        # extraction-FIRST in the merge list so ``prefer_research_node``'s
        # ``chosen = existing`` default means extraction wins payload conflicts
        # — an agent must not be able to rename a curated node out from under
        # it. This block sits outside the batch path on purpose, so it also
        # runs on the ``noop_skip`` arm: ``compile --changed-only`` over an
        # unchanged corpus still materializes new agent writes with zero
        # extraction.
        _before_agent = graph
        # ``align_overlay`` first: an agent name that hashes to a different
        # stable id than the extracted node (e.g. a Paper whose id is seeded
        # from its arXiv id) would otherwise FORK, and the same-type collapse
        # picks its winner with ``max((len(name), id))`` — a lexicographic
        # coin-flip the agent's node can win, renaming a curated node.
        overlay = align_overlay(
            replay_agent_writes(self.paths.agent_writes), graph
        )
        if overlay.nodes:
            graph = merge_graphs([graph, overlay])
            # Codex #6: without this the reconcile strips the rows and every
            # later incremental compile tombstones the agent nodes as stale.
            self._record_producer_provenance(
                "__agent_write__", _before_agent, graph
            )
        # Canonicalize the merged graph BEFORE the community-summary pass.
        # ``merge_graphs`` re-runs the same-type/cross-type dedup and edge
        # redirection over the whole node universe, so an incremental compile
        # (prior-graph nodes + freshly re-extracted changed files, assembled in
        # a different order than a full compile) converges on the SAME canonical
        # node ids AND the SAME redirected edge set as a full compile. Without
        # this, Louvain runs over a transient graph whose author/edge surface
        # ids differ between the two arms and mints a different partition
        # (e.g. 3 vs 2 COMMUNITY_SUMMARY nodes) — the CMP-03 community-drift
        # bug. It is an idempotent no-op for a full compile (the graph is
        # already canonical), so byte-idempotence is preserved.
        graph = merge_graphs([graph])
        # Descent hierarchy sidecar (PR4): persist the full Louvain dendrogram
        # + hub list to ``.tesserae/hierarchy.json``. Runs on the canonical
        # graph (the same ordering CMP-03 requires) and BEFORE the community-
        # summary merge so Louvain never sees a COMMUNITY_SUMMARY node. The
        # sidecar clusters THIS graph whole — there is no code layer left for
        # it to partition off first. The ordering window that split used to
        # sit in outlives it: the passes below, and those inside
        # ``_write_artifacts``, rebind ``graph`` afterwards, so a member named
        # here can be gone by the time ``graph.json`` is written; see the
        # ordering window in that method's docstring.
        # Returns the all-level live-cid manifest used to prune stale summary
        # caches after that pass (§9.5).
        live_community_ids = self._write_hierarchy_sidecar(graph)
        # The chartered institution (.tesserae/charter/charter.json), derived
        # from the SAME graph at the SAME moment and for the same reason: this
        # is the one point where a canonicalized research-layer partition
        # exists and Louvain has not yet seen a COMMUNITY_SUMMARY node. Its
        # ordering window is the one _write_hierarchy_sidecar documents, and
        # it applies verbatim to ``member_index``.
        self._write_charter_sidecar(graph)
        # Community-summary pass (Microsoft GraphRAG playbook applied to
        # the typed graph). Opt-in via ``TESSERAE_COMMUNITY_SUMMARIES=true``
        # so quiet ``compile`` runs stay free of incremental LLM
        # cost. Runs AFTER merge/dedup so cluster membership reflects the
        # canonical graph and BEFORE ``_write_artifacts`` so the new
        # COMMUNITY_SUMMARY nodes flow through vault projection,
        # graph.json persistence, MCP, and site builds in one pass.
        graph = self._merge_community_summaries(graph, cfg)
        # §9.5 cache pruning: with this compile's caches written, delete
        # summary-cache files whose cid appears at NO dendrogram level.
        # Live-but-unvisited caches are kept (pruning on the coarsest level
        # alone would delete valid fine-level caches).
        from .community_summaries import prune_stale_summary_caches

        pruned_caches = prune_stale_summary_caches(
            self.paths.community_summaries, live_community_ids
        )
        if pruned_caches:
            print(
                f"[tesserae] community summaries: pruned {len(pruned_caches)} "
                "stale cache file(s).",
                flush=True,
            )
        # AgentRunbook Runbook/Gotcha distillation (opt-in). Runs after
        # merge/dedup + community summaries so it clusters the canonical
        # session findings, and before ``_write_artifacts`` so the minted
        # distilled-memory nodes flow into graph.json / MCP / retrieval.
        graph = self._merge_distillation(graph, cfg)
        # Extraction-derived provenance for the files extracted THIS run
        # (Codex B2). On a full / true-full-recompile this covers the whole
        # corpus; on an active incremental run only the re-extracted changed
        # files. ``_write_artifacts`` records these rows (preserving
        # first_seen_at) and then reconciles the sidecar against the final
        # graph (Codex M5), so stale rows for dropped nodes/edges are purged.
        extraction_prov = compute_extraction_provenance(extracted_graphs)
        self._write_artifacts(
            graph,
            store=store,
            vault_pull=vault_pull,
            extraction_prov=extraction_prov,
            json_client=llm_passes_client,
            # Reconcile the FULL provenance row-set only when this was a full
            # compile (Codex #1). ``incremental_active`` is True only when the
            # incremental differ actually ran (flag on + sidecar covers prior
            # graph); otherwise the batch re-extracted the whole corpus, so the
            # row-set is authoritative and safe to reconcile. ``fallback_only``
            # is the other subset case: it re-extracted only the MARKED docs, so
            # ``extraction_prov`` covers a subset and a reconcile would delete
            # every unchanged doc's row.
            #
            # ``extracted_graphs`` empty is the THIRD subset case, and the one
            # that quietly disarmed the scoped ``--retry-fallbacks`` bound. The
            # ``noop_skip`` short-circuit reuses the prior graph from DISK
            # without extracting anything (a loader run that discovered nothing
            # leaves it empty too, without reusing anything: it writes an empty
            # graph and takes this same additive path) — and with the
            # differ off (the default) that landed here as
            # ``full_compile=True`` carrying an EMPTY row-set. Reconcile then
            # deleted every per-document row, and the NEXT
            # ``--changed-only --retry-fallbacks`` failed ``_provenance_ready``
            # and re-extracted the whole corpus. Measured, 3 docs: seed 7 rows ->
            # one plain ``--changed-only`` no-op -> 1 row (``__synthesis__``
            # alone) -> retry processed 3, not 1. The bound survived only if the
            # retry was the LITERAL next compile. A run that extracted NOTHING
            # has nothing to reconcile against, so it takes the additive path
            # (record + ``prune_provenance_to_graph``, which still drops rows
            # whose node/edge left the final graph). Note this is NOT the same
            # as "the corpus is empty": a real full compile of an emptied corpus
            # still runs the batch, so ``graphs`` is ``[batch.graph]`` — truthy —
            # and reconcile still prunes what genuinely left.
            full_compile=bool(extracted_graphs) and not incremental_active and not fallback_only,
        )
        # graph.json now exists on disk for the docs this run extracted, so —
        # and ONLY now — stamp them ``graphed``. This is the completeness marker
        # the ``fallback_only`` subset path above requires: ``BatchIngestRunner``
        # writes a FRESH entry dict per re-extracted doc, which drops any prior
        # stamp, so a run killed before this line leaves exactly the docs whose
        # extraction never reached an artifact unstamped. What is guaranteed is
        # therefore narrow and exact: ``graphed`` means "the last successful
        # ``_write_artifacts`` wrote a graph built from THIS entry's content".
        #
        # FILESYSTEM ONLY. ``batch`` exists only on that branch, and the loader
        # path has no completeness marker of its own — see the ceiling booked
        # there. ``loader is None`` is the branch test; ``batch is not None``
        # additionally excludes the ``noop_skip`` short-circuit, which extracted
        # nothing and therefore has nothing to re-stamp.
        if loader is None and batch is not None:
            processed_keys = set(batch.processed_paths)
            # Did graph.json get rebuilt from THIS run's extractions alone, or
            # was the prior graph merged into it? Both stamp decisions below
            # turn on that one difference, and so does the prune further down.
            full_run = not effective_changed_only and not fallback_only
            candidate_keys = {str(md) for md in markdown_files}
            # A SKIPPED doc keeps whatever stamp it had — its nodes reached this
            # graph only via a prior-graph merge, so its stamp is the honest
            # record of that. A doc that is neither processed nor skipped is one
            # of two different things, and they get opposite answers:
            #
            # * DEFERRED — still a candidate, but ``--limit`` cut the batch's
            #   work-list before it (``BatchIngestRunner`` BREAKS out of the
            #   scan, so everything past the cut is neither processed nor
            #   skipped). Its manifest entry was not rewritten and, on a SCOPED
            #   run, the prior graph was merged in — so graph.json still holds
            #   exactly what that entry describes and the stamp is still true,
            #   UNLESS this run tombstoned its nodes anyway. It can: an explicit
            #   ``changed_paths`` tombstones by caller intent, not by what the
            #   batch got to, so a doc past the ``--limit`` cut can be both
            #   deferred and gutted. ``tombstoned_resolved`` is subtracted for
            #   exactly that case. Dropping the stamp unconditionally made the
            #   next ``--changed-only --retry-fallbacks`` see incomplete
            #   coverage and re-extract the whole corpus: the 137->35 bound,
            #   gone after one ``--limit`` (Codex A1).
            # * GONE — no longer a candidate at all (deleted, renamed away, or
            #   outside a narrower compile's inputs). Nothing vouches for the
            #   coverage any more, so the stamp has to go; that is what keeps
            #   the completeness guard from trusting a graph the corpus has
            #   moved out from under.
            #
            # On a FULL run there is no prior graph to inherit from — a
            # deferred doc is genuinely uncovered, so it falls through to the
            # unstamp with the departed ones.
            carried = processed_keys | set(batch.skipped_paths)
            if not full_run:
                carried |= {
                    key
                    for key in candidate_keys
                    if str(Path(key).resolve()) not in tombstoned_resolved
                }
            # ...and an entry whose file left the corpus entirely gets PRUNED,
            # not just unstamped. ``BatchIngestRunner`` only ever merges into the
            # manifest, so a deleted or renamed document leaves its key behind
            # forever — and ``corpus_unchanged`` (manifest keys == candidate
            # keys, above) is then permanently False, which disables BOTH the
            # ``--changed-only`` no-op and the scoped ``--retry-fallbacks`` path
            # for the life of the workspace. That is the whole 137->35 bound,
            # gone after one ``rm``.
            #
            # Pruning is only safe on a run that rebuilt graph.json from THIS
            # run's extractions alone: ``base_graph`` is then ``batch.graph``, so
            # the departed document's nodes are already absent from the graph the
            # next run would reuse and the stale key has done its job. On a
            # SCOPED run — the incremental differ (``effective_changed_only``) or
            # ``fallback_only`` — the prior graph IS merged in, so that key is
            # exactly the signal the subtractive guard needs; keep it.
            stamped = self._load_manifest()
            for key in list(stamped):
                # ``_ingest_via_loader`` shares this manifest under ``source:``
                # keys; an FS run must not erase them (and vice versa). The
                # prune below would, since they are never candidates.
                #
                # It leaves them otherwise UNTOUCHED — no stamp, no sha edit.
                # A ``full_run`` did rebuild graph.json from ``batch.graph``
                # alone, so the loader's nodes really are gone from it while its
                # ``sha256`` still claims otherwise, and a cross-origin fixup
                # here is the machinery this PR removed: every version of it
                # traded one origin's silent loss for the other's. The reuse
                # predicates above make the trade unnecessary instead — they
                # COUNT ``source:`` keys, so a mixed workspace never takes the
                # filesystem no-op at all and the stale claim cannot be acted
                # on from this side.
                #
                # ponytail: ceiling — the remaining exposure is loader-side and
                # loader-only: the next ``compile(loader=...)`` under the
                # experimental differ still sha-skips its sources onto the
                # FS-rebuilt graph. Closing it needs per-origin coverage
                # tracking (see the ceiling on the reuse predicates above), not
                # another cross-origin stamp edit. Unreachable today —
                # ``compile(loader=...)`` has no caller.
                if key.startswith("source:"):
                    continue
                if full_run and key not in candidate_keys:
                    del stamped[key]
                    continue
                entry = stamped[key]
                if key in processed_keys:
                    entry["graphed"] = True
                elif key not in carried:
                    entry.pop("graphed", None)
            self._write_manifest(stamped)
        result = {
            "project_root": str(self.project_root),
            "wiki_root": str(self.root),
            "source_kind": kind,
            "processed_files": processed,
            "skipped_files": skipped,
            "fallback_files": len(fallback_files),
            "fallback_paths": fallback_files,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "graph_path": str(self.paths.graph),
            "graphiti_episodes_path": str(self.paths.graphiti_episodes),
            "agent_harness_path": str(self.paths.agent_harness),
            "obsidian_vault_path": str(self.effective_obsidian_vault()),
            "site_path": str(self.paths.site),
            "mcp_server_name": cfg.get("name", sanitize_server_name(self.project_root.name)),
        }
        if code_graph_report is not None:
            # Present only when the code branch ran: reuse/extraction signal +
            # the code-tree delta (surgicality observability, result-dict and
            # logs only — never serialized into artifacts).
            result["code_graph_cache"] = code_graph_report
        return result

    def _merge_session_graph(
        self,
        graph: ResearchGraph,
        cfg: dict,
        override: Optional[SessionExtractionOptions] = None,
        guidance: str = "",
        llm_passes_client: Optional["LLMJsonClient"] = None,
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
        # multi-minute latency to every ``compile``.
        # Prefer the live SQLite store (SESS-03): the daemon's SessionTailer
        # upserts sessions there per turn. Fall back to the legacy
        # ``.tesserae/harness_sessions/`` glob store for back-compat (the
        # ``sessions discover --import`` path) when the DB is absent/empty.
        in_project: List[HarnessSession] = []
        live_db_path = self.project_root / ".tesserae" / "harness_sessions.db"
        if live_db_path.exists():
            from .harness_sessions_db import HarnessSessionsDB

            try:
                db = HarnessSessionsDB(live_db_path)
                cached = db.list_for_project(self.project_root)
                # Distinguish a legitimately EMPTY db (quiet legacy-glob
                # fallback, back-compat) from a real READ ERROR. An empty
                # store is normal before the first session lands; a corrupt
                # or locked db must NOT silently degrade compile to stale
                # context — log it loudly so the regression is visible
                # (Codex #7).
                if not cached and db.count_sessions() == 0:
                    logger.debug(
                        "live sessions db %s is empty; falling back to "
                        "legacy harness_sessions glob store",
                        live_db_path,
                    )
            except Exception:  # noqa: BLE001 - corrupt/locked db: fall back loudly
                logger.warning(
                    "failed to read live sessions db %s; falling back to "
                    "legacy harness_sessions glob store (compile may use "
                    "stale session context)",
                    live_db_path,
                    exc_info=True,
                )
                cached = []
            in_project = [
                s for s in cached
                if session_matches_project(s, self.project_root)
            ]
        if not in_project:
            if not self.paths.harness_sessions.exists():
                return graph
            store = HarnessSessionStore(self.paths.harness_sessions)
            cached = store.list_sessions()
            in_project = [
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
            json_client = self._build_json_client(model=opts.model)
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
            guidance=guidance,
        )
        session_slice = extractor.extract()

        # AgentRunbook Event layer. For each session, mint per-transition
        # ``Event`` nodes from its turns and link this session's findings to
        # them via ``derived_from``. LLM-free and template-only, so additive and
        # byte-idempotent — ``json_client`` is accepted for API symmetry and
        # never used. See ``tesserae.session_event``.
        #
        # DEFAULT-ON for session-bearing projects (roadmap step 4): control
        # flow only reaches here when ``in_project`` is non-empty. It used to
        # share ``distillation_enabled`` with the LLM Runbook/Gotcha pass, which
        # made this deterministic layer unavailable without also buying an LLM
        # pass and its cache; ``event_pass_enabled`` is its own opt-out.
        from .session_event import event_pass_enabled

        if event_pass_enabled(cfg):
            from .session_event import extract_events
            from .session_recovery import detect_recoveries

            findings_by_session: Dict[str, List[ResearchNode]] = {}
            for _node in session_slice.nodes:
                _sid = (_node.metadata or {}).get("session_id")
                if _sid:
                    findings_by_session.setdefault(str(_sid), []).append(_node)
            for _session in in_project:
                _ev_nodes, _ev_edges = extract_events(
                    _session,
                    findings=findings_by_session.get(str(_session.id), []),
                    json_client=json_client,
                )
                session_slice.nodes.extend(_ev_nodes)
                session_slice.edges.extend(_ev_edges)
                # The causal layer (roadmap step 6). ``recovers`` edges join two
                # of the Events just minted — a failure and the success that
                # fixed it — so this runs here, on the same list, and is gated
                # by the same switch: without Events there is nothing to join.
                # LLM-free, session-local and deterministic, like the pass above.
                session_slice.edges.extend(detect_recoveries(_session, _ev_nodes))

        if not session_slice.nodes and not session_slice.edges:
            return graph
        # Drop the prior copy of anything this session pass just re-derived.
        #
        # On the incremental path ``graph`` already carries ``prior_kept_graph``,
        # and producer-owned nodes are deliberately exempted from ``stale_ids``
        # above (:1305, ``stale_ids -= producer_only``) because their producer
        # re-derives them. But ``merge_graphs`` resolves an id collision through
        # ``prefer_research_node``, which for two session nodes of equal
        # title-quality returns the one already in the dict — the PRIOR copy. So
        # a correction the producer made this run was silently discarded.
        #
        # Measured on the home-path redaction: ~4,885 of 4,917 leaking Event
        # nodes keep their id under redaction (a tool turn keys on the tool
        # name, not the text), so the redaction ran, produced the right bytes,
        # and lost the merge. A default ``tesserae compile`` rebuilds from the
        # doc batch and masked it; only ``--changed-only`` was affected, which
        # is why nothing caught it.
        #
        # Deliberately NOT fixed by merging the slice first. That would also
        # swap ``existing``/``incoming`` for every OTHER node type, and
        # ``prefer_research_node``'s placeholder rule is asymmetric — it would
        # quietly change which paper title wins. Dropping exactly the ids this
        # pass re-derived keeps the blast radius to the nodes in question.
        _fresh_ids = {n.id for n in session_slice.nodes}
        if _fresh_ids:
            graph = ResearchGraph(
                nodes=[n for n in graph.nodes if n.id not in _fresh_ids],
                edges=list(graph.edges),
            )
        merged = merge_graphs([graph, session_slice])

        # A-MEM-style ``superseded_by`` edges between near-duplicate session
        # findings. As of Plan 01 the supersede pass is DEFAULT-ON with a
        # deterministic, credential-free verdict, so it runs whenever
        # ``supersede_pass_enabled()`` is true (the default) — NO longer gated on
        # ``llm_passes_client``. On the default path json_client is None and the
        # verdict is deterministic; an explicit client overrides the verdict.
        # ``supersede_pass_enabled`` still honours an explicit opt-OUT via
        # ``TESSERAE_SUPERSEDE_PASS=false``. Contradiction/schema-drift passes
        # remain gated on ``llm_passes_client`` separately.
        from .memory.supersede import run_supersede_pass, supersede_pass_enabled

        if supersede_pass_enabled():
            cache_dir = self.paths.root / "supersede_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            merged = run_supersede_pass(
                merged, json_client=llm_passes_client, cache_dir=cache_dir
            )

        # Opt-in post-pass: feature H — link each session finding to the
        # code symbols (CodeFunction / CodeClass / CodeMethod) it
        # mentions, by scanning finding bodies for backticked
        # identifiers and dotted ``Class.method`` paths and resolving
        # them against ``.tesserae/code-graph.json`` (produced by
        # ``tesserae code ingest``). Guarded by env flag so the
        # default compile path doesn't depend on the code graph
        # existing. Purely additive: mints only ``discusses`` edges.
        from .memory.insight_symbol_link import (
            insight_symbol_link_enabled,
            run_insight_symbol_link_pass,
        )

        if insight_symbol_link_enabled():
            merged = run_insight_symbol_link_pass(
                merged, code_graph_path=self.paths.code_graph
            )
        return merged

    def _write_hierarchy_sidecar(self, graph: ResearchGraph) -> Set[str]:
        """Write ``.tesserae/hierarchy.json`` — the Descent hierarchy sidecar.

        Runs immediately after the ``merge_graphs([graph])`` canonicalization
        and BEFORE ``_merge_community_summaries``, so the dendrogram is a pure
        function of the canonical graph content (identical for full vs
        incremental compiles — the CMP-03 parity surface) and Louvain never
        sees a COMMUNITY_SUMMARY node. Payload::

            {"schema_version": 1,
             "levels": [{cid: [sorted member ids], ...}, ...],  # finest→coarsest
             "hubs": [node ids with undirected degree > 200]}

        ``cid`` is the existing :func:`community_id` scheme, so the coarsest
        level's keys are exactly the COMMUNITY_SUMMARY node ids the summary
        pass mints. Deterministic (fixed seed, sorted construction, no wall
        clock, no LLM); atomic tmp + ``os.replace`` with sorted keys, matching
        the sidecar idiom of ``output_snapshot.write_state``.

        Built over the RESEARCH layer only (:func:`partition_graph`) — the same
        split ``_write_artifacts`` uses to decide what goes to ``graph.json``
        rather than ``code-graph.json``. What that guarantees, exactly: no
        member id here was in the CODE layer of the graph HANDED TO THIS CALL.
        That is the systematic cause of the observed skew (measured: 169/1360
        ai-accounts sidecar members absent from ``graph.json``, 100% of them in
        ``code-graph.json``) and it is closed. Kept in lockstep with
        :meth:`_merge_community_summaries`, which partitions identically so its
        minted cids are exactly this sidecar's coarsest-level keys.

        It is NOT closed by construction, and the residue is an ORDERING window,
        not an id-rewrite: ``compile()`` calls this on the freshly canonicalized
        graph, then rebinds ``graph`` through ``_merge_community_summaries`` and
        ``_merge_distillation``, and ``_write_artifacts`` rebinds it again
        through ``_apply_vault_overlay``, ``SynthesisProjector.project`` and
        ``_run_memory_passes`` before its own ``partition_graph`` call. A pass in
        that window that changes a node's ``type`` moves it across the split
        without touching its id — concretely ``apply_schema_drift`` inside
        ``_run_memory_passes`` (opt-in, ``TESSERAE_SCHEMA_DRIFT_APPLY``), which
        renames types, and :func:`is_code_graph_node` dispatches on type. Such a
        node is named here and absent from ``graph.json``.

        ponytail: ceiling — writing this from the object ``_write_artifacts``
        actually partitions is not a small move: the returned live-cid manifest
        is consumed by ``prune_stale_summary_caches`` BEFORE
        ``_write_artifacts`` runs, and the dendrogram has to predate
        ``_merge_community_summaries`` so Louvain never clusters a
        COMMUNITY_SUMMARY node and the coarsest cids stay equal to the minted
        summary ids. Upgrade path if a retyping pass ever ships ON by default:
        have ``_write_artifacts`` re-emit the sidecar's member lists filtered to
        its own research layer, keeping the cids this pass minted.

        The partition is unconditional even though the code layer is now
        OPT-IN (``tools`` entry ``codegraph``, default off). It has to be:
        with the layer disabled the split is a no-op over a graph that has no
        code nodes, and a graph compiled while it was ENABLED keeps its code
        nodes across later disabled compiles until they are pruned. Gating the
        partition on the current config would name those stale members here and
        reopen exactly the skew above, on the one graph least likely to be
        looked at — the one whose owner just turned the feature off.

        That does NOT make this sidecar and ``graph.json`` the same node
        universe, and reading it that way is how the pruning key gets deleted
        as redundant. ``_write_artifacts`` rebinds ``graph`` through
        ``_apply_vault_overlay``, which harvests vault-page deletions, so a
        member named here can still be absent from the published graph — a
        much narrower window than the code split opened, but the same shape.
        :func:`hierarchy.live_member_count` is what reports it.

        Returns the live-cid manifest across ALL levels — the §9.5 pruning
        key: a community-summary cache file is stale only when its cid appears
        at no level.
        """
        from .community_summaries import (
            community_id,
            detect_community_levels,
            hub_node_ids,
        )

        # F-11: ``graph.json`` carries the RESEARCH layer only — ``partition_graph``
        # routes CodeProject/SourceFile/CodeClass/Dependency/... into
        # ``code-graph.json`` and ``_write_artifacts`` writes the two separately.
        # The dendrogram exists to navigate ``graph.json``, so it must be built
        # over exactly that node universe: a code-layer member is an id
        # ``graph_map`` can NEVER resolve (dead "Untitled community" cards,
        # live_member_count=0). Partition here rather than at the call site so the
        # writer cannot emit an unresolvable id no matter what a caller hands it —
        # and so hub degrees come off the same projection PPR actually walks.
        # ponytail: ceiling — a user who opts into ``combined-graph.json``
        # (off by default) and points ``graph_map --graph-path`` at it no longer
        # sees code-layer communities in the map. They were never navigable from
        # the default artifact anyway. Upgrade path if that surface is ever
        # wanted: a SECOND, code-layer sidecar, not a union dendrogram.
        research_layer, _code_layer = partition_graph(graph)
        levels = detect_community_levels(research_layer)
        logger.info(
            "hierarchy sidecar: %d dendrogram level(s) over %d research-layer "
            "node(s) (%d code-layer node(s) excluded — they live in "
            "code-graph.json, never in graph.json)",
            len(levels),
            len(research_layer.nodes),
            len(graph.nodes) - len(research_layer.nodes),
        )
        levels_payload = [
            {community_id(members): members for members in level}
            for level in levels
        ]
        payload = {
            "schema_version": 1,
            "levels": levels_payload,
            "hubs": hub_node_ids(research_layer),
        }
        path = self.paths.hierarchy
        path.parent.mkdir(parents=True, exist_ok=True)
        # Published through the shared helper for the same reason the graph
        # artifacts are: ``with_suffix(".tmp")`` gave every writer the one path
        # ``.tesserae/hierarchy.tmp``, and ``.tesserae/`` is on the disk the
        # whole fleet shares, so two hosts compiling the same project at once
        # interleaved their dendrograms into it and renamed the mixture here.
        _publish_atomically(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return {cid for level_map in levels_payload for cid in level_map}

    def _write_charter_sidecar(self, graph: ResearchGraph) -> Optional[Path]:
        """Derive ``.tesserae/charter/charter.json`` — the chartered institution.

        Returns the path written, or ``None`` when this compile deliberately
        wrote nothing (below the founding bound, nothing to divide, no reorg,
        or a charter on disk that could not be read).

        **Not gated on an env flag, and that is a decision rather than an
        oversight.** :func:`_env_truthy`'s own docstring scopes the convention
        it implements — gating "destructive / credential-dependent compile
        passes ... so the default compile stays deterministic and
        credential-free". ``build_charter`` is neither: it is a pure function
        of graph content, LLM-free, credential-free and measured
        byte-identical across runs, which is the profile of the passes this
        compile already runs unconditionally rather than the profile of the
        ones it gates. Putting it behind ``TESSERAE_*`` would also make every
        reader downstream of it conditional on an env var — an entry point
        that is a size rank unless you knew to opt out of it.

        The cost is real and is bounded by the founding test instead. Measured
        on the 47,132-node live graph: 8.2s, against 2.7s for
        ``detect_community_levels`` in :meth:`_write_hierarchy_sidecar`, which
        this compile already pays unconditionally on the same graph.
        :func:`charter.worth_chartering` is a cheap ``mass`` pass, so a
        project small enough to be one read pays that and nothing else — the
        Louvain work only happens on corpora big enough to need routing.

        Partitions the research layer itself rather than taking
        ``_write_hierarchy_sidecar``'s, following the same two-independent-
        ``partition_graph``-calls idiom :meth:`_merge_community_summaries`
        already uses: O(n) twice on an in-memory graph, and each function then
        defends its own invariant. That invariant here is CH-01 against
        ``graph.json`` specifically — ``member_index`` maps node ids to slugs,
        and a code-layer id is one ``graph.json`` never carries, which is the
        measured 169/1360 skew arriving through a third door.
        """
        from .charter import (
            _INTAKE_SLUG,
            CharterUnreadable,
            build_charter,
            is_noop_reorg,
            read_charter,
            succeed,
            write_charter,
            worth_chartering,
        )

        try:
            prior = read_charter(self.project_root)
        except CharterUnreadable as exc:
            # Do NOT fall through to the founding path. ``read_charter``
            # raises precisely so that "unreadable" cannot be mistaken for
            # "absent", and founding here would re-mint every slug, break
            # every pinned attach path and leave no tombstone explaining
            # where the old ones went — with the operator's only clue being
            # a charter file that silently changed. Skipping keeps the
            # damaged file intact for them to restore, and the compile
            # itself is not failed over a sidecar.
            logger.error("charter: %s", exc)
            print(f"[tesserae] charter: {exc}", flush=True)
            return None

        research_layer, _code_layer = partition_graph(graph)
        if prior is None and not worth_chartering(research_layer):
            # Below the one-read bound: never create .tesserae/charter/. The
            # bound is a FOUNDING test only — an existing institution keeps
            # being maintained even if the corpus later shrinks, because its
            # slugs are pinned paths and must not be abandoned by a corpus
            # oscillating around the budget.
            return None

        fresh = build_charter(research_layer)
        real_domains = [slug for slug in fresh["domains"] if slug != _INTAKE_SLUG]
        if prior is None and len(real_domains) < 2:
            # Detection found nothing to divide, so there is no institution to
            # charter — one division plus intake is a rename of the graph, not
            # a structure an agent can route through.
            return None

        if prior is None:
            print(
                f"[tesserae] charter: founded {len(fresh['domains'])} domain(s) "
                f"over {len(fresh['member_index'])} member(s).",
                flush=True,
            )
            return write_charter(self.project_root, fresh)

        merged = succeed(prior, fresh)
        if is_noop_reorg(prior, merged):
            # Nothing reorganised, so nothing is written and charter.json stays
            # byte-identical. Without this every compile would bump reorg_seq
            # and re-stamp all 780 domains ``stable``, making the one file the
            # rest of the system keys on the only output that churns on an
            # unchanged corpus. See ``is_noop_reorg``.
            return None
        retired = sum(
            1 for e in merged["domains"].values() if e.get("status") == "retired"
        )
        founded = sum(
            1 for e in merged["domains"].values() if e.get("transition") == "founded"
        )
        print(
            f"[tesserae] charter: reorg {merged['reorg_seq']} — "
            f"{len(merged['domains'])} domain(s), {founded} founded, "
            f"{retired} retired.",
            flush=True,
        )
        return write_charter(self.project_root, merged)

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
        # Both gates default to True (post-PR #14 env flip and the project-config
        # ``enabled`` key has always defaulted to True when unset). Use AND so
        # either side can opt out independently: project owners can set
        # ``community_summaries.enabled: false`` in tesserae.toml without
        # needing to also unset TESSERAE_COMMUNITY_SUMMARIES, and vice versa.
        # Codex PR #14 P2 fix — previously OR allowed env-default to override
        # an explicit config opt-out.
        env_enabled = is_enabled_via_env()
        cfg_enabled = bool(community_cfg.get("enabled", True))
        if not (env_enabled and cfg_enabled):
            return graph
        json_client = _get_community_summaries_test_client()
        if json_client is None:
            # Provider-aware: honors llm_provider / llm_codex_home /
            # llm_claude_config_dirs from project + global config (else this
            # always defaulted to the Claude CLI, ignoring llm_provider=codex).
            json_client = self._build_json_client(
                model=community_cfg.get("model") if isinstance(community_cfg.get("model"), str) else None
            )
        # No LLM client: do NOT skip outright. Run in CACHE-ONLY mode so
        # previously-minted, membership-stable summaries are re-emitted from
        # ``self.paths.community_summaries/`` instead of vanishing when the LLM
        # is momentarily unavailable. ``compile_community_summaries`` re-emits a
        # cached summary for every unchanged cluster and skips only the clusters
        # that have no cache entry (those genuinely need an LLM). A re-emitted
        # node is byte-identical to its original LLM mint (no build-provenance in
        # node metadata), so recompiles stay stable across LLM-availability flux.
        if json_client is None:
            print(
                "[tesserae] community summaries: no LLM client — re-emitting "
                "cached summaries only.",
                flush=True,
            )
        # Lockstep with ``_write_hierarchy_sidecar``: cluster the RESEARCH layer
        # only. ``cid = community_id(member_ids)`` — cluster the union here and
        # the minted COMMUNITY_SUMMARY ids stop matching the sidecar's
        # coarsest-level keys, which (a) kills ``community_card``'s
        # quality="llm" lookup and (b) makes ``prune_stale_summary_caches`` in
        # ``compile()`` delete every cache file this pass just wrote. It would
        # also spend LLM calls summarizing code-only clusters whose members
        # ``graph.json`` never carries.
        # ponytail: two independent ``partition_graph()`` calls rather than a
        # threaded view — O(n) twice on an already-in-memory graph, and each
        # function then defends its own invariant. If a third producer ever
        # needs the same universe, hoist it to one local in ``compile()`` and
        # pass it down.
        research_layer, _code_layer = partition_graph(graph)
        slice_graph = compile_community_summaries(
            research_layer,
            cache_dir=self.paths.community_summaries,
            json_client=json_client,
            min_size=int(community_cfg.get("min_size") or 5),
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

    def _merge_distillation(self, graph: ResearchGraph, cfg: dict) -> ResearchGraph:
        """Mint ``Runbook``/``Gotcha`` distilled-memory nodes (opt-in).

        The cross-session half of the AgentRunbook memory layer: clusters
        session-finding nodes and mints one ``Runbook`` (procedure) or
        ``Gotcha`` (failure-mode) node per recurring cluster, with
        ``derived_from`` edges to its members. Skipped unless
        ``distillation.enabled`` in config or ``TESSERAE_RUNBOOK_DISTILLATION``
        is truthy. Unlike community summaries this runs DETERMINISTICALLY with
        no LLM — an ``LLMJsonClient`` only enriches titles/bodies — so it never
        skips for lack of a client. See ``tesserae.memory.distill``.
        """
        from .memory.distill import (
            _get_distillation_test_client,
            distillation_enabled,
            run_distillation_pass,
        )

        if not distillation_enabled(cfg):
            return graph
        distill_cfg = cfg.get("distillation") if isinstance(cfg.get("distillation"), dict) else {}
        layers = distill_cfg.get("layers")
        if not isinstance(layers, (list, tuple)) or not layers:
            layers = ("runbook", "gotcha")
        json_client = _get_distillation_test_client()
        if json_client is None:
            json_client = self._build_json_client(
                model=distill_cfg.get("model") if isinstance(distill_cfg.get("model"), str) else None
            )
        cache_dir = self.paths.root / "distillation_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        before = len(graph.nodes)
        merged = run_distillation_pass(
            graph,
            json_client=json_client,
            cache_dir=cache_dir,
            layers=tuple(str(x) for x in layers),
            min_cluster_size=int(distill_cfg.get("min_cluster_size") or 2),
            min_sessions=int(distill_cfg.get("min_sessions") or 2),
        )
        minted = len(merged.nodes) - before
        if minted:
            print(
                f"[tesserae] distillation: minted {minted} Runbook/Gotcha node(s).",
                flush=True,
            )
        return merged

    def _code_layer_enabled(self, cfg: dict) -> bool:
        """Is the code layer switched on for this project?

        OPT-IN, and absence is the off state: a project gets a code layer only
        by carrying an ``external_tools`` entry whose ``id`` is ``codegraph``.
        This reuses the shape ``_merge_configured_raganything_graph`` already
        reads rather than minting a second, differently-spelled switch — an
        entry that exists is on unless it says ``"enabled": false``, exactly as
        raganything behaves.

        What this REPLACES is the important part. The layer used to fire on
        ``kind in {"CodeProject", "Repository", "Project"}``, which is a
        description of what a project IS, not a statement that its owner wanted
        their source parsed. Every repository-shaped project got a code layer
        whether or not anything read it, and the only way out was to lie about
        the kind. The cost was never visible in ``graph.json`` either, because
        ``_write_artifacts`` partitions the code nodes back out to
        ``code-graph.json`` — it landed in the SQLite projection and the
        generated pages, roughly 220k rows and 220k pages on this repo, in
        artifacts nobody thinks to attribute to a setting they never set.

        Project kind is therefore NOT consulted here, deliberately. A
        ``CodeProject`` with no entry compiles no code layer. Turning it on for
        a project whose kind has nothing to do with code is equally allowed:
        the switch means what it says.
        """
        for tool in cfg.get("external_tools", []) or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("id") != "codegraph":
                continue
            return tool.get("enabled", True) is not False
        return False

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
    ) -> tuple[List[ResearchGraph], int, int, List[str]]:
        """Drive extraction from a :class:`SourceLoader` instead of the FS walker.

        Manifest bookkeeping mirrors :class:`BatchIngestRunner`: entries are
        keyed on ``source.id`` (rather than file path), value carries the
        content sha256 and source kind. Skipping is an exact-hash match.

        Note the sha is persisted in a ``finally``, BEFORE ``_write_artifacts``
        runs — so a killed loader compile leaves the claim on disk without the
        nodes. Nothing on this path stamps completeness the way the filesystem
        path does; see the ceiling booked at the loader branch in ``compile``.
        """
        manifest = self._load_manifest()
        graphs: List[ResearchGraph] = []
        processed = 0
        skipped = 0
        fallback_paths: List[str] = []
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
                # Same duck-typed thread-local BatchIngestRunner reads. Without
                # it this arm reported fallbacks=0 no matter how much of the
                # concept layer it lost — #92's silent failure, rebuilt.
                if bool(getattr(extractor, "last_was_fallback", False)):
                    fallback_paths.append(key)
                    manifest[key]["fallback"] = True
        finally:
            self._write_manifest(manifest)
        return graphs, processed, skipped, fallback_paths

    def _load_manifest(self) -> dict:
        if not self.paths.manifest.exists():
            return {}
        payload = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        files = payload.get("files", payload if isinstance(payload, dict) else {})
        return files if isinstance(files, dict) else {}

    def _write_manifest(self, manifest: dict) -> None:
        self.paths.manifest.parent.mkdir(parents=True, exist_ok=True)
        # This one is the least forgiving of the fixed-``.tmp`` writers, which
        # is why it goes through the same helper: ``_load_manifest`` above
        # calls ``json.loads`` with no guard, so a manifest two hosts
        # interleaved into the shared ``.tesserae/manifest.tmp`` does not
        # degrade to a full re-extraction — it raises ``JSONDecodeError`` out
        # of every subsequent compile on every host until someone deletes the
        # file by hand.
        _publish_atomically(
            self.paths.manifest,
            json.dumps({"files": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def compile(
        self,
        source_kind: Optional[str] = None,
        changed_only: bool = False,
        limit: Optional[int] = None,
        trends: bool = False,
        min_trend_sources: int = 2,
        exclude_data: bool = False,
        loader: Optional[SourceLoader] = None,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        session_options: Optional[SessionExtractionOptions] = None,
        use_extraction_feedback: bool = False,
        doc_extractor: Optional[object] = None,
        changed_paths: Optional[List[Path]] = None,
        llm_passes_client: Optional["LLMJsonClient"] = None,
        progress: Optional["CompileProgress"] = None,
        incremental_override: Optional[bool] = None,
        lock_wait: Optional[float] = None,
        retry_fallbacks: bool = False,
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
        # Per-compile producer provenance accumulator (Codex #6). Each of the 5
        # non-extraction producers records the node ids + edges it minted THIS
        # compile here; ``_record_provenance`` turns them into deterministic
        # ``__<producer>__`` sidecar rows. Reset every compile so a producer that
        # stops contributing leaves no stale rows. Sidecar-only — NEVER graph.json.
        self._producer_prov: Dict[str, Dict[str, object]] = {}
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
        # One compile per project at a time: a hook-triggered refresh that
        # fires mid-compile must fail fast (or opt into waiting) instead of
        # stacking onto the same .tesserae state.
        with compile_lock(self.paths.root, wait_seconds=lock_wait):
            # Output-snapshot bracket (no-op detector + byte-idempotence
            # tripwire, see ``tesserae.output_snapshot``): hash the allowlisted
            # artifacts before ingest and again after the post-compile lint.
            before = snapshot_output(self.root)
            result = self.ingest(
                sources,
                source_kind=source_kind,
                changed_only=changed_only,
                limit=limit,
                trends=trends,
                min_trend_sources=min_trend_sources,
                loader=loader,
                store=store,
                vault_pull=vault_pull,
                session_options=session_options,
                use_extraction_feedback=use_extraction_feedback,
                doc_extractor=doc_extractor,
                changed_paths=changed_paths,
                llm_passes_client=llm_passes_client,
                progress=progress,
                incremental_override=incremental_override,
                retry_fallbacks=retry_fallbacks,
            )
            # Tail-of-compile lint: refresh lint-report.md/json at every
            # publish so the verifier signal is never stale. The linter only
            # READS the just-written artifacts (fix_trivial=False → it never
            # touches graph.json), and its reports are byte-stable on
            # identical input. A lint bug must never fail a compile, so
            # failures are logged and swallowed.
            try:
                report = self.lint(severity_floor="warning")
            except Exception:
                logger.exception("post-compile lint failed; compile artifacts are unaffected")
            else:
                result["lint"] = {
                    "errors": report.by_severity.get("error", 0),
                    "warnings": report.by_severity.get("warning", 0),
                    "info": report.by_severity.get("info", 0),
                }
            # Close the output-snapshot bracket. Lint writes only root-level
            # reports — outside the allowlist, so ordering is cosmetic; taking
            # the snapshot last keeps the bracket honest. ``idempotence_suspect``
            # = identical graph layer but drifted projections, i.e. a projector
            # rewrote a pure function of unchanged inputs differently.
            after = snapshot_output(self.root)
            result["output_sha256"] = after.output_sha256
            result["output_changed"] = after != before
            result["idempotence_suspect"] = (
                after.graph_sha256 == before.graph_sha256
                and after.projections_sha256 != before.projections_sha256
            )
            if result["idempotence_suspect"]:
                logger.warning(
                    "byte-idempotence regression suspected: projections changed while "
                    "graph.json/config were byte-identical (before=%s after=%s)",
                    before.projections_sha256[:12], after.projections_sha256[:12],
                )
            else:
                logger.info(
                    "compile output %s (sha256 %s)",
                    "changed" if result["output_changed"] else "unchanged",
                    after.output_sha256[:12],
                )
            try:
                write_state(self.paths.output_snapshot, after, changed=result["output_changed"])
            except OSError:
                logger.exception("output-snapshot state write failed; compile artifacts are unaffected")
            return result

    def lint(
        self,
        fix_trivial: bool = False,
        severity_floor: str = "info",
        *,
        verify_claims: bool = False,
        claim_cap: int = 20,
        llm_client: Optional[object] = None,
    ) -> LintReport:
        """Run :class:`WikiLinter` against this project's compiled artifacts.

        Thin wrapper that defers all work — including artifact writes and the
        colored stderr summary — to :class:`WikiLinter`. The returned
        :class:`LintReport` lets callers inspect findings programmatically;
        the CLI uses it to derive the exit code. ``verify_claims`` opts into
        the LLM-backed claim-support check; the tail-of-compile lint never
        passes it, so compile can never pay LLM cost here.
        """
        return WikiLinter(self.project_root).run(
            fix_trivial=fix_trivial,
            severity_floor=severity_floor,
            verify_claims=verify_claims,
            claim_cap=claim_cap,
            llm_client=llm_client,
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

    def _memory_by_id(self) -> dict:
        """Load the ``node_memory`` sidecar for the temporal projector.

        Best-effort by design: a missing or locked sidecar must degrade an
        export to heuristic confidence, never fail it. Compile passes its
        in-hand rows instead of calling this, because at that point the
        sidecar has only just been written.
        """
        try:
            from .memory.store import read_memory

            return read_memory(self.paths.sqlite)
        except Exception:  # pragma: no cover — defensive
            logger.exception("read_memory failed; exporting heuristic confidence")
            return {}

    def export_graphiti(
        self,
        group_id: Optional[str] = None,
        output: Optional[str | Path] = None,
        memory_by_id: Optional[dict] = None,
    ) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(output) if output else self.paths.graphiti_episodes
        adapter = GraphitiResearchGraphAdapter(group_id=group_id or cfg.get("name") or self.project_root.name)
        # Feed the memory sidecar in, exactly as temporal_facts.jsonl does.
        # Without it the episodes carried infer_confidence()'s heuristic label
        # while temporal_facts.jsonl — same projector, same graph, same compile
        # — carried the reinforced numeric one, so the two artifacts disagreed
        # and the external consumer got the number Tesserae does not use.
        episodes = adapter.write_episodes(
            graph, target, memory_by_id=self._memory_by_id() if memory_by_id is None else memory_by_id
        )
        return {"episodes": len(episodes), "path": str(target), "group_id": adapter.group_id}

    def export_agent_harness(self, targets: Optional[Iterable[str]] = None, output: Optional[str | Path] = None, install_pointer: bool = False) -> dict:
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
        result = {"path": str(target), "files": len(written), "targets": list(targets) if targets else SUPPORTED_AGENT_HARNESSES}
        if install_pointer:
            result["pointer"] = install_instruction_pointer(self.project_root, name)
        return result

    def export_obsidian(self, vault: Optional[str | Path] = None) -> dict:
        cfg = self.config()
        graph = load_graph_file(self.paths.graph)
        target = Path(vault) if vault else self.effective_obsidian_vault()
        name = cfg.get("name") or sanitize_server_name(self.project_root.name)
        result = ObsidianVaultAdapter(vault_name=name).write_vault(graph, target)
        self._prune_orphaned_vault_pages(graph, target)
        return result

    @staticmethod
    def _prune_orphaned_vault_pages(graph: ResearchGraph, vault: Path) -> None:
        """Delete projected vault pages a rename / deletion left orphaned.

        The Obsidian vault is bidirectional, so unlike ``wiki/`` and ``site/``
        we never ``rmtree`` it — user-authored notes must survive. But the
        projector keys each generated page on the node's CURRENT canonical slug
        (``directory_for_node``/``unique_slugs``). When a node is renamed (its
        slug changes) or removed entirely, the projector simply stops writing
        the old path; the stale ``.md`` lingers on disk.

        That orphan is not harmless: it still carries the old ``title:`` in its
        frontmatter, so the next compile's vault overlay
        (:meth:`_apply_vault_overlay`) reads it, sees ``title != snapshot``, and
        mints a phantom ``name`` override that resurrects the pre-rename name —
        an incremental compile (which renamed the node) and a subsequent full
        compile then diverge on every artifact downstream of that name
        (synthesis pulse target, community partition, projections). See the
        Phase-4 subtractive-parity gate.

        A page is an orphan iff it carries a ``node_id:`` frontmatter key (i.e.
        it is projector-generated, not a hand-written user note) AND that node
        no longer projects to this exact path. User notes (no ``node_id:``) and
        the structural files (``index.md``, ``_bridges.md``, ``README.md``,
        ``_meta/``, ``.obsidian/``) are left untouched. SQLite/graph.json are
        never affected.
        """
        if not vault.exists():
            return
        from .markdown_projection import (
            directory_for_node,
            unique_slugs,
        )
        from .research_graph import is_public_research_node

        slug_by_id = unique_slugs(graph.nodes)
        # The canonical (current) projected path for every node that owns a
        # page. Matches the projector's own path computation so a freshly
        # written page is never mistaken for an orphan.
        canonical_paths: set[Path] = set()
        for node in graph.nodes:
            if node.type == ResearchNodeType.STUB or not is_public_research_node(node):
                continue
            slug = slug_by_id.get(node.id)
            if slug is None:
                continue
            canonical_paths.add(
                (vault / directory_for_node(node) / f"{slug}.md").resolve()
            )
        for md in vault.rglob("*.md"):
            try:
                head = md.read_text(encoding="utf-8")[:512]
            except OSError:
                continue
            # Only projector-generated pages carry a ``node_id:`` frontmatter
            # key (written first by ``render_node_page``); user notes never do.
            if "\nnode_id:" not in ("\n" + head):
                continue
            if md.resolve() not in canonical_paths:
                md.unlink(missing_ok=True)

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
        force_no_llm: bool = False,
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
            force_no_llm=force_no_llm,
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
            memory_by_id=self._memory_by_id(),
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
                f"No graph at {self.paths.graph}; run `tesserae compile` first."
            )
        graph = load_graph_file(self.paths.graph)

        before_node_count = len(graph.nodes)
        before_edge_count = len(graph.edges)
        graph = self._apply_vault_overlay(graph)
        new_stubs = sum(1 for n in graph.nodes[before_node_count:] if n.type == ResearchNodeType.STUB)

        # Re-project: markdown + the obsidian vault itself. The
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

    def _apply_vault_overlay(self, graph: ResearchGraph) -> ResearchGraph:  # noqa: C901
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
        # Codex #4: orphan-prune ordering. Filter out vault pages whose
        # ``node_id`` frontmatter no longer maps to a LIVE graph node BEFORE
        # compute_overrides / compute_user_link_changes — otherwise an orphan
        # page could emit a node_id override (or a user_link edge) and mutate
        # the graph before its deletion. Pages with no node_id are non-node
        # pages (index/dashboard) and are kept; ``_load_vault_files`` already
        # only returns files carrying a ``node_id`` key, so the ``is None`` arm
        # is a defensive no-op for that loader.
        live_ids = set(node_by_id.keys())
        vault_files = [
            f
            for f in vault_files
            if (nid := _vault_file_node_id(f)) is None or nid in live_ids
        ]
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

        # Extraction-feedback collection (UNCONDITIONAL — see spec flag boundary).
        # Capture node_type / source_path AT EVENT TIME from the current graph;
        # never cluster on node_id, which renames/merges after projection.
        from .extraction_feedback import events_from_vault_overlay, append_events
        node_types = {n.id: n.type.value for n in graph.nodes}
        source_paths = {n.id: (n.source_path or "") for n in graph.nodes}
        events = events_from_vault_overlay(
            overrides, user_link_changes, node_types, source_paths
        )
        if events:
            append_events(self.paths.extraction_feedback, events)

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

        _before_overlay = graph
        graph = apply_overrides(graph, overrides)
        graph = apply_user_link_changes(graph, user_link_changes)
        # Codex #6: the vault overlay mints ``user_link`` edges from the user's
        # wikilinks every compile (overrides mutate existing nodes in place and
        # add no new ids). Attribute the edges it introduced to
        # "__vault_overlay__" so they carry a deterministic sidecar row.
        self._record_producer_provenance("__vault_overlay__", _before_overlay, graph)
        return graph

    def _load_extraction_guidance(self, enabled: bool) -> tuple[str, str]:
        """Load + slice ``.tesserae/extraction-guidance.md`` for this compile.

        Returns ``(doc_guidance, session_guidance)`` as newline-joined bullet
        texts ready to drop into the two extractor prompts. Returns ``("", "")``
        when the flag is off, no guidance file exists, or it has no bullets —
        which keeps every prompt byte-identical to the legacy path.

        v1 routing is EXTRACTOR-LEVEL only: we inject *all* bullets whose
        ``extractor`` matches (``doc_graph`` vs ``session_findings``),
        regardless of ``node_type``. The doc extractor emits many node types
        in one call, so per-node-type slicing at this boundary is impractical;
        node-type correctness is already enforced at write-time by
        ``extraction_feedback._route()`` (and again when guidance is distilled
        per ``(extractor, node_type, field, source)`` cluster). We therefore
        pass ``node_types`` = the full set present for that extractor.
        """
        if not enabled:
            return "", ""
        path = self.paths.extraction_guidance
        if not path.exists():
            return "", ""
        from .guidance_markdown import parse_guidance, slice_guidance

        bullets = parse_guidance(path.read_text(encoding="utf-8"))
        if not bullets:
            return "", ""

        def _join(extractor: str) -> str:
            node_types = {b.node_type for b in bullets if b.extractor == extractor}
            sliced = slice_guidance(bullets, extractor=extractor, node_types=node_types)
            return "\n".join(f"- {b.text}" for b in sliced)

        return _join("doc_graph"), _join("session_findings")

    def evolve(self, json_client=None) -> dict:
        """Distill collected feedback into extraction-guidance.md.

        Reads the append-only feedback corpus, clusters + LLM-phrases each
        cluster (cached, with a deterministic fallback when no LLM is
        reachable), and writes the human-curatable guidance markdown. Returns
        a small summary dict for the CLI to print.
        """
        from .extraction_feedback import read_events
        from .extraction_guidance import build_guidance, cache_hash_ledger
        from .guidance_markdown import parse_guidance, render_guidance
        events = read_events(self.paths.extraction_feedback)

        # Preserve human curation: parse the CURRENT guidance file (user edits +
        # what survived prior deletions) and snapshot the "ever generated"
        # ledger (one cache file per cluster_hash ever phrased) BEFORE building
        # — so a brand-new cluster phrased this run is not mistaken for a
        # previously-deleted one. ``build_guidance`` then KEEPs existing bullets,
        # SKIPs user-deleted ones, and ADDs only genuinely-new clusters.
        existing = (
            parse_guidance(self.paths.extraction_guidance.read_text(encoding="utf-8"))
            if self.paths.extraction_guidance.exists()
            else []
        )
        ever_generated = cache_hash_ledger(self.paths.extraction_guidance_cache)

        bullets = build_guidance(
            events,
            cache_dir=self.paths.extraction_guidance_cache,
            json_client=json_client,
            existing=existing,
            ever_generated=ever_generated,
        )
        self.paths.extraction_guidance.parent.mkdir(parents=True, exist_ok=True)
        self.paths.extraction_guidance.write_text(
            render_guidance(bullets), encoding="utf-8")
        return {"events": len(events), "bullets": len(bullets),
                "guidance_path": str(self.paths.extraction_guidance)}

    @staticmethod
    def _has_provenance_table(store: Optional[GraphStore]) -> bool:
        """True when ``store`` is backed by a SQLite db carrying the
        ``node_provenance`` sidecar (Plan 01). Non-SQLite stores degrade to
        False so the caller takes the safe full-recompile fallback."""
        if store is None:
            return False
        if not hasattr(store, "delete_nodes_by_source") or not hasattr(store, "record_provenance_many"):
            return False
        db_path = getattr(store, "path", None)
        if db_path is None or not Path(db_path).exists():
            return False
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as con:
                row = con.execute(
                    "select name from sqlite_master where type='table' and name='node_provenance'"
                ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001 - missing table => safe fallback
            return False

    @staticmethod
    def _provenance_ready(
        store: object,
        prior_node_ids: List[str],
        prior_edge_triples: Optional[Iterable[Tuple[str, str, str]]] = None,
    ) -> bool:
        """True when an INJECTED ``store`` exposes the FULL edge-aware provenance
        surface AND its sidecar covers every prior-graph node id and edge triple
        (Codex B3 + #5).

        Coverage matters: a store with the table but missing rows for some
        prior nodes/edges would leave those un-tombstoned, so the caller must
        fall back to a full recompile rather than emit a stale partial graph.

        The injected store must expose the complete edge-aware surface the
        incremental differ relies on — node deletion, edge-aware deletion,
        node + edge provenance recording, and node + edge coverage. A node-only
        store (missing ``record_edge_provenance_many`` /
        ``provenance_covers_edges`` / ``delete_nodes_by_source_with_edges``)
        cannot tombstone stale edges, so it is NOT incremental-ready.

        ``provenance_covers_nodes`` is REQUIRED, not optional: absence of the
        coverage API is not evidence of coverage. This predicate no longer gates
        only the experimental differ — it also gates the ``fallback_only`` scoped
        retry, which is reachable in the DEFAULT config on
        ``--changed-only --retry-fallbacks`` and whose first act is a
        ``delete_nodes_by_source_with_edges`` tombstone. Trusting a store that
        cannot answer "do you cover these nodes?" there deletes co-owned nodes
        outright instead of falling back to the always-correct full recompile.
        """
        required = (
            "delete_nodes_by_source",
            "record_provenance_many",
            "delete_nodes_by_source_with_edges",
            "record_edge_provenance_many",
            "provenance_covers_edges",
            "provenance_covers_nodes",
        )
        for method in required:
            if not hasattr(store, method):
                return False
        if not hasattr(store, "has_node_provenance_rows") or not store.has_node_provenance_rows():
            return False
        if not bool(store.provenance_covers_nodes(prior_node_ids)):
            return False
        if prior_edge_triples and not store.provenance_covers_edges(prior_edge_triples):
            return False
        return True

    @staticmethod
    def _sqlite_provenance_ready(
        db_path: Path,
        prior_node_ids: List[str],
        prior_edge_triples: Optional[Iterable[Tuple[str, str, str]]] = None,
    ) -> bool:
        """True when the on-disk SQLite db at ``db_path`` carries a NON-EMPTY
        ``node_provenance`` sidecar that covers every prior-graph node id AND an
        ``edge_provenance`` sidecar covering every prior-graph edge triple
        (Codex #2/#5) — checked WITHOUT constructing the schema-creating
        ``SqliteGraphStore`` (Codex B3: that constructor would create an empty
        table and make an old/no-sidecar DB falsely look provenance-ready).

        Edge coverage is required because the incremental differ tombstones
        stale edges via the ``edge_provenance`` sidecar; if that sidecar does
        not cover the prior edges (e.g. an older DB written before edge
        provenance, or a producer that minted edges without provenance), the
        differ could leave stale edges and diverge from a full compile — so we
        fall back to a safe full recompile.
        """
        if db_path is None or not Path(db_path).exists():
            return False
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as con:
                has_table = con.execute(
                    "select name from sqlite_master where type='table' and name='node_provenance'"
                ).fetchone()
                if has_table is None:
                    return False
                row = con.execute("select 1 from node_provenance limit 1").fetchone()
                if row is None:
                    return False  # existing but EMPTY sidecar — not ready.
                covered = {
                    r[0]
                    for r in con.execute(
                        "select distinct node_id from node_provenance"
                    ).fetchall()
                }
                if not all(nid in covered for nid in dict.fromkeys(prior_node_ids)):
                    return False
                if prior_edge_triples:
                    has_edge_table = con.execute(
                        "select name from sqlite_master where type='table' "
                        "and name='edge_provenance'"
                    ).fetchone()
                    if has_edge_table is None:
                        return False
                    edge_covered = {
                        (r[0], r[1], r[2])
                        for r in con.execute(
                            "select source, type, target from edge_provenance"
                        ).fetchall()
                    }
                    if not set(prior_edge_triples).issubset(edge_covered):
                        return False
        except Exception:  # noqa: BLE001 - any error => safe full recompile
            return False
        return True

    def _record_producer_provenance(
        self,
        source_label: str,
        before: ResearchGraph,
        after: ResearchGraph,
    ) -> None:
        """Capture the node ids + edges a producer minted THIS compile (Codex #6).

        Diffs ``after`` against ``before`` (the graph as it entered the producer)
        and stashes the NEW node ids and NEW edge triples under ``source_label``
        in ``self._producer_prov``. Only ids the producer actually introduced are
        recorded — never a blanket fallback for every uncovered node (Pitfall
        1/2). ``_collect_producer_provenance`` later turns these into
        deterministic ``det:`` rows; ``_record_provenance`` filters them to the
        FINAL graph so canonicalization/dedup drops never leave over-coverage.
        """
        # ``_producer_prov`` is reset per compile() (~1232), but producers also
        # run via direct ingest()/_merge_session_graph()/_write_artifacts() calls
        # (e.g. in tests) that bypass that reset. Initialise defensively so the
        # record path tolerates any entry point — mirrors the guarded
        # ``getattr(self, "_producer_prov", {})`` in _collect_producer_provenance.
        if not hasattr(self, "_producer_prov"):
            self._producer_prov = {}
        before_node_ids = {n.id for n in before.nodes}
        before_edge_keys = {(e.source, e.type, e.target) for e in before.edges}
        minted_nodes = [n.id for n in after.nodes if n.id not in before_node_ids]
        minted_edges = [
            (e.source, e.type, e.target)
            for e in after.edges
            if (e.source, e.type, e.target) not in before_edge_keys
        ]
        if not minted_nodes and not minted_edges:
            return
        bucket = self._producer_prov.setdefault(
            source_label, {"nodes": set(), "edges": set()}
        )
        bucket["nodes"].update(minted_nodes)  # type: ignore[union-attr]
        bucket["edges"].update(minted_edges)  # type: ignore[union-attr]

    def _collect_producer_provenance(
        self,
    ) -> Tuple[
        List[Tuple[str, str, str]], List[Tuple[str, str, str, str, str]]
    ]:
        """Flatten ``self._producer_prov`` into deterministic provenance rows.

        Node rows: ``(node_id, source_label, "det:"+sha256(node_id|label)[:16])``.
        Edge rows: ``(source, type, target, source_label, det_ts)``.
        Deterministic content-derived timestamps only (never wall-clock) so the
        sidecar stays byte-stable. Returns empty lists when no producer ran.
        """
        node_rows: List[Tuple[str, str, str]] = []
        edge_rows: List[Tuple[str, str, str, str, str]] = []
        prov = getattr(self, "_producer_prov", {}) or {}
        for source_label in sorted(prov):
            bucket = prov[source_label]
            for node_id in sorted(bucket.get("nodes", set())):  # type: ignore[union-attr]
                det_ts = "det:" + sha256_text(f"{node_id}|{source_label}")[:16]
                node_rows.append((node_id, source_label, det_ts))
            for (src, etype, tgt) in sorted(bucket.get("edges", set())):  # type: ignore[union-attr]
                det_ts = "det:" + sha256_text(
                    f"{src}|{etype}|{tgt}|{source_label}"
                )[:16]
                edge_rows.append((src, etype, tgt, source_label, det_ts))
        return node_rows, edge_rows

    @staticmethod
    def _record_provenance(
        store: object,
        graph: ResearchGraph,
        extraction_prov: Optional[
            Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str, str]]]
        ],
        producer_prov: Optional[
            Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str, str]]]
        ] = None,
        full_compile: bool = True,
    ) -> None:
        """Record extraction-derived node + edge provenance, then reconcile.

        ``extraction_prov`` carries the rows produced by the files actually
        extracted this run (the WHOLE corpus on a full compile, only the
        changed files on an incremental one). When absent (legacy callers /
        tests that build the graph by hand) we fall back to deriving rows from
        the final graph's own ``source_path`` scalars.

        ``producer_prov`` carries the deterministic ``__producer__`` rows for
        the non-extraction graph producers (session-graph, code-graph,
        raganything, vault-overlay) — see
        :meth:`_record_producer_provenance`. Each producer re-derives its
        nodes/edges from a STABLE source every compile, so they would otherwise
        carry NO provenance row and permanently force the readiness gate to
        fall back to a full recompile (Codex #6).

        ``full_compile`` selects the persistence strategy (Codex #1):

        * **Full compile** — ``extraction_prov`` covers the WHOLE corpus, so
          the full row-set is authoritative. We call
          ``store.reconcile_provenance(node_rows, edge_rows)`` which REPLACES
          the exact provenance row-set (preserving ``first_seen_at``, deleting
          any row absent from the fresh set). This kills the M5 false-keeper:
          a stale ``(node, b.md)`` row from a previous compile where b.md
          contributed is removed even though the node is still LIVE (owned by
          another file).
        * **Incremental compile** — ``extraction_prov`` covers ONLY the changed
          files. Reconcile would wrongly delete every unchanged-file row, so we
          keep the additive path: ``record_provenance_many`` +
          ``record_edge_provenance_many`` + ``prune_provenance_to_graph``
          (which only drops rows whose node/edge left the final graph).

        All SQLite-only; graph.json is never touched.
        """
        if not hasattr(store, "record_provenance_many"):
            return
        if extraction_prov is None:
            node_rows, edge_rows = compute_extraction_provenance([graph])
        else:
            node_rows, edge_rows = extraction_prov
        # Projector-generated nodes/edges (SYNTHESIS, COMMUNITY_SUMMARY and
        # their ``synthesizes``/``summarizes`` edges) are minted AFTER
        # extraction, so the per-file extraction graphs never carry them. They
        # are regenerated every compile (``_strip_generated_layer``), so they
        # need no real file provenance — but the sidecar invariant is "every
        # final-graph node/edge has at least one provenance row". Record ONLY
        # these generated rows under "__synthesis__" (NOT every uncovered node:
        # in an incremental run unchanged-file nodes are uncovered here yet
        # already carry real rows in the sidecar, so a blanket fallback would
        # diverge from a full compile and re-introduce over-attribution).
        node_rows = list(node_rows)
        edge_rows = list(edge_rows)
        generated_node_ids = {
            n.id
            for n in graph.nodes
            if n.type in {ResearchNodeType.SYNTHESIS, ResearchNodeType.COMMUNITY_SUMMARY}
        }
        for node_id in sorted(generated_node_ids):
            det_ts = "det:" + sha256_text(f"{node_id}|__synthesis__")[:16]
            node_rows.append((node_id, "__synthesis__", det_ts))
        for edge in graph.edges:
            if (
                edge.source in generated_node_ids
                or edge.target in generated_node_ids
                or edge.type in {"synthesizes", "summarizes"}
            ):
                det_ts = "det:" + sha256_text(
                    f"{edge.source}|{edge.type}|{edge.target}|__synthesis__"
                )[:16]
                edge_rows.append((edge.source, edge.type, edge.target, "__synthesis__", det_ts))
        # Producer-minted rows (Codex #6): the 5 non-extraction producers record
        # deterministic ``__<producer>__`` rows for ONLY the ids they minted this
        # compile. Filter to ids/edges still present in the FINAL graph so a
        # producer node later dropped by canonicalization/dedup never leaves an
        # over-coverage row (Pitfall 1/2: never a blanket fallback). Plan 03's
        # incremental differ can recognise producer-owned nodes as those whose
        # only sidecar source starts with ``"__"`` — these are regenerated by
        # their producer every compile and must NOT be tombstoned as stale
        # surviving cross-file nodes.
        if producer_prov is not None:
            final_node_ids = {n.id for n in graph.nodes}
            final_edge_keys = {(e.source, e.type, e.target) for e in graph.edges}
            prod_node_rows, prod_edge_rows = producer_prov
            for row in prod_node_rows:
                if row[0] in final_node_ids:
                    node_rows.append(row)
            for row in prod_edge_rows:
                if (row[0], row[1], row[2]) in final_edge_keys:
                    edge_rows.append(row)
        if full_compile and hasattr(store, "reconcile_provenance"):
            # Full compile: the fresh row-set is authoritative for the WHOLE
            # corpus — replace exactly (first_seen_at preserved, stale source
            # rows for still-live nodes deleted). Reconcile must NEVER run on an
            # incremental compile (Pitfall 4): there ``extraction_prov`` covers
            # only the changed files, so it would delete every unchanged-file row.
            store.reconcile_provenance(node_rows, edge_rows)
        else:
            store.record_provenance_many(node_rows)
            if hasattr(store, "record_edge_provenance_many"):
                store.record_edge_provenance_many(edge_rows)
            if hasattr(store, "prune_provenance_to_graph"):
                store.prune_provenance_to_graph(graph)

    def _resolve_llm_passes_client(
        self, explicit: Optional["LLMJsonClient"]
    ) -> Optional["LLMJsonClient"]:
        """Resolve the ONE client that gates the LLM graph-mutating passes.

        Single opt-in (KB-03/KB-04 consistency): an explicit client wins; else
        we build the best-effort default ONLY when ``TESSERAE_ENABLE_LLM_PASSES``
        is truthy. Otherwise return ``None`` — the default compile then runs
        NEITHER the session ``supersede`` pass NOR the ``contradiction`` /
        schema-drift passes, staying deterministic, credential-free, and
        byte-idempotent. This is the SAME gate ``_run_memory_passes`` documents,
        lifted to the compile level so supersede and contradiction can never
        disagree about whether the LLM ran.
        """
        if explicit is not None:
            return explicit
        if _env_truthy("TESSERAE_ENABLE_LLM_PASSES"):
            try:
                return self._build_json_client()
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "phase5: build_default_json_client failed; LLM passes off"
                )
                return None
        return None

    def _compile_reference_timestamp(self, graph: ResearchGraph) -> "datetime":
        """A FIXED, content-derived decay reference instant (never now()).

        Decay must be byte-stable across identical-source compiles, so the
        reference timestamp cannot be wall-clock ``datetime.now()`` (05-RESEARCH
        Pitfall 1). We anchor on the LATEST ``last_accessed_at`` /
        ``first_seen_at`` present in node metadata — a deterministic function of
        the source corpus. When no node carries a parseable timestamp we fall
        back to a fixed UTC epoch so the value is still deterministic.
        """
        from datetime import datetime, timezone

        from .memory.decay import _parse_ts

        latest: Optional[datetime] = None
        for node in graph.nodes:
            meta = getattr(node, "metadata", None) or {}
            if not isinstance(meta, dict):
                continue
            for key in ("last_accessed_at", "first_seen_at"):
                ts = _parse_ts(meta.get(key))
                if ts is not None and (latest is None or ts > latest):
                    latest = ts
        if latest is not None:
            return latest
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _run_memory_passes(
        self,
        graph: ResearchGraph,
        json_client: Optional["LLMJsonClient"],
        store: Optional[GraphStore] = None,
    ) -> Tuple[ResearchGraph, List["NodeMemoryRow"]]:
        """Run the Phase-5 self-improvement passes at the compile choke point.

        Order (05-RESEARCH "Compile Pass Order"): restore MCP-accumulated access
        state -> [schema-drift apply if opted-in] -> contradiction resolution
        (mints ``resolved_by`` edges into graph.json) -> recurring-insight
        reinforcement -> compute decay -> stage node_memory rows. Returns the
        (possibly edge-augmented) graph plus the staged rows; the caller writes
        graph.json from the returned graph and persists the rows to the
        node_memory sidecar AFTER the sqlite write.

        Mutable scalars (decay_score / access_count / confidence / superseded)
        go to node_memory ONLY. The only graph.json delta from a fresh project
        is the deterministic ``resolved_by`` / ``supersedes`` edges.
        """
        from .memory.contradiction import run_contradiction_resolution
        from .memory.decay import compute_decay_score
        from .memory.reinforce import compute_recurring_confidence
        from .memory.store import NodeMemoryRow, read_memory

        # The LLM-arbitrated passes (contradiction, schema-drift) MUTATE
        # graph.json (resolved_by / supersedes edges) and depend on ambient
        # credentials, so we do NOT silently build a default client here:
        # building one inside compile makes ordinary, credential-free compiles
        # depend on a configured LLM backend and can trigger a surprise COLD
        # arbitration call. Instead the LLM passes run ONLY when the caller
        # hands us an explicit ``json_client`` (e.g. via session extraction) OR
        # the documented ``TESSERAE_ENABLE_LLM_PASSES`` env gate is set — in
        # which case we build the best-effort default. Otherwise they are
        # skipped entirely and the default path stays deterministic and
        # credential-free. Either way graph.json is byte-idempotent (the
        # content-keyed disk caches keep warm LLM reruns byte-stable).
        if json_client is None and _env_truthy("TESSERAE_ENABLE_LLM_PASSES"):
            try:
                # Provider-aware (honors llm_provider=codex), same as the
                # community-summaries and session-extraction paths.
                json_client = self._build_json_client()
            except Exception:  # pragma: no cover — defensive
                json_client = None

        # (1) Single FIXED reference timestamp for ALL decay computations.
        reference_dt = self._compile_reference_timestamp(graph)
        reference_iso = reference_dt.isoformat()

        # (2) Load MCP-accumulated access state from the node_memory sidecar so
        # decay reflects reads recorded since the last compile. CRITICAL: we do
        # NOT stamp these sidecar fields (access_count / last_accessed_at) onto
        # ``node.metadata`` — ``ResearchNode.model_dump`` serializes the ENTIRE
        # metadata dict into graph.json, so mutating it would leak wall-clock
        # sidecar state into graph.json and break byte-idempotence (a read bump
        # would change the NEXT compile's bytes). The access state is fed to the
        # decay computation in step (7) via a COPIED metadata view; graph node
        # metadata is never touched. (05-RESEARCH Pitfall 2: sidecar-only.)
        # The node_memory sidecar lives in the DEFAULT ``sqlite.db``. When an
        # alternate ``store`` is injected (HypePaper Postgres adapter, test
        # doubles), that store owns persistence and the default SQLite path must
        # stay UNTOUCHED — ``read_memory`` opens a ``SqliteGraphStore`` whose
        # ``CREATE TABLE IF NOT EXISTS`` would otherwise mint ``sqlite.db`` on
        # disk, breaking the injected-store contract. Skip the sidecar read in
        # that case and treat prior access state as empty.
        prior: Dict[str, "NodeMemoryRow"] = {}
        if store is None:
            try:
                prior = read_memory(self.paths.sqlite)
            except Exception:  # pragma: no cover — defensive; missing/locked db
                logger.exception("phase5: read_memory failed; treating as empty")
                prior = {}

        # (3) Schema-drift apply — OPT-IN, destructive (Pitfall 4). Default
        # (env unset/falsy) => skipped entirely, graph.json byte-identical.
        if _env_truthy("TESSERAE_SCHEMA_DRIFT_APPLY"):
            try:
                from .schema_drift import apply_schema_drift, read_proposal_ledger

                # Read the human-reviewed LEDGER; never re-run the analysis
                # here. Two things follow, and both are the point of step 12:
                # the compile-time apply is now LLM-free and deterministic
                # (no ``json_client`` requirement), and a proposal can only be
                # applied after a human set ``approved`` — which the fresh
                # analyze path could never produce, since its proposals carry
                # only {name, description, examples} and no approval field at
                # all. That is why this pass had never once fired.
                approved: List[dict] = [
                    record
                    for record in read_proposal_ledger(self.paths.root)
                    if record.get("approved")
                ]
                if approved:
                    before = len(approved)
                    graph = apply_schema_drift(graph, approved)
                    logger.info(
                        "phase5 schema-drift apply: applied %d approved proposal(s)",
                        before,
                    )
            except Exception:  # pragma: no cover — defensive
                logger.exception("phase5 schema-drift apply failed; continuing")

        # (4) Contradiction resolution (KB-04): mints deterministic
        # ``resolved_by`` edges into graph.json; conf_map -> node_memory.
        conf_map: Dict[str, str] = {}
        if json_client is not None:
            try:
                graph, conf_map = run_contradiction_resolution(
                    graph,
                    llm=json_client,
                    cache_dir=self.paths.root / "contradiction_cache",
                )
            except Exception:  # pragma: no cover — defensive
                logger.exception("phase5 contradiction resolution failed")
                conf_map = {}


        # (5) Recurring-insight reinforcement (KB-05): confidence -> node_memory
        # (NEVER graph.json). Reinforced "high" wins over contradiction's map.
        try:
            recur = compute_recurring_confidence(graph)
        except Exception:  # pragma: no cover — defensive
            logger.exception("phase5 reinforcement failed")
            recur = {}
        confidence_by_id: Dict[str, str] = dict(conf_map)
        # ``recur`` carries NUMERIC scores (0->1); store them as deterministic
        # text ("0.5"/"0.75"/"1") so the SQLite round-trip and the temporal
        # projector emit byte-identical values. Reinforced numeric wins over the
        # contradiction map. NEVER stamped onto node.metadata / graph.json.
        for nid, score in recur.items():
            confidence_by_id[nid] = f"{float(score):.2f}".rstrip("0").rstrip(".")

        # (6) Superseded targets — a node pointed AT by a ``supersedes`` edge is
        # obsolete. Flag drives the MCP fresh-insights filter; node_memory only.
        superseded_ids: Set[str] = {
            e.target for e in graph.edges if e.type == "supersedes"
        }

        # (7) Stage one NodeMemoryRow per node: deterministic decay at the fixed
        # reference, carrying forward MCP access state from ``prior``. The MCP
        # access fields (access_count / last_accessed_at) are fed to the decay
        # computation via a COPIED metadata view — NEVER stamped back onto
        # ``node.metadata`` — so graph.json carries no sidecar/memory state and
        # stays byte-identical even after an MCP read bumps the sidecar.
        rows: List["NodeMemoryRow"] = []
        for node in graph.nodes:
            prev = prior.get(node.id)
            try:
                decay_node = node
                if prev is not None:
                    base_meta = getattr(node, "metadata", None)
                    merged_meta = dict(base_meta) if isinstance(base_meta, dict) else {}
                    if prev.access_count:
                        merged_meta["access_count"] = prev.access_count
                    if prev.last_accessed_at:
                        merged_meta["last_accessed_at"] = prev.last_accessed_at
                    decay_node = SimpleNamespace(metadata=merged_meta)
                decay_score = compute_decay_score(decay_node, reference_dt)
            except Exception:  # pragma: no cover — defensive
                decay_score = 1.0
            rows.append(
                NodeMemoryRow(
                    node_id=node.id,
                    decay_score=decay_score,
                    access_count=(prev.access_count if prev else 0),
                    last_accessed_at=(prev.last_accessed_at if prev else None),
                    confidence=confidence_by_id.get(node.id),
                    superseded=(node.id in superseded_ids),
                    updated_at=reference_iso,
                )
            )
        return graph, rows

    def _write_artifacts(
        self,
        graph: ResearchGraph,
        store: Optional[GraphStore] = None,
        vault_pull: bool = True,
        extraction_prov: Optional[
            Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str, str]]]
        ] = None,
        json_client: Optional["LLMJsonClient"] = None,
        full_compile: bool = True,
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
        # Canonicalize node/edge order ONCE here so every downstream artifact
        # (graph.json via ``to_json``, temporal facts,
        # Graphiti episodes, markdown/obsidian projections, the provenance
        # sidecar) derives from the SAME content-derived order. A full compile
        # builds the graph in insertion order; an incremental compile appends
        # re-extracted changed-file nodes after the prior corpus. Without this,
        # the two arms carry identical node/edge SETS but different LIST order,
        # so the projections diverge byte-wise even when the graph is logically
        # equal (CMP-03). Idempotent no-op for an already-canonical graph.
        graph = graph.canonicalized()

        # ------------------------------------------------------------ Phase 5
        # Self-improvement passes (KB-01/03/04/05/06). Node ids are now final
        # (canonicalized above) but graph.json has NOT been written yet — the
        # right choke point to (a) mint the deterministic graph.json edges
        # (resolved_by / supersedes already present) and (b) compute the
        # mutable memory state that goes EXCLUSIVELY to the node_memory sidecar
        # (decay_score / access_count / confidence / superseded). graph.json
        # must stay byte-identical across identical-source compiles, so NOTHING
        # below writes a mutable scalar into the node serialization — decay and
        # confidence land in node_memory only; resolved_by edges are
        # deterministic (content-keyed warm cache). 05-RESEARCH "Compile Pass
        # Order"; Pitfalls 1 (fixed reference timestamp), 2 (sidecar-only),
        # 4 (schema-drift apply is opt-in/destructive).
        #
        # The whole block is best-effort: a missing/locked sidecar db or an
        # absent LLM client must degrade gracefully and never fail a compile.
        memory_rows: List["NodeMemoryRow"] = []
        try:
            graph, memory_rows = self._run_memory_passes(graph, json_client, store=store)
        except Exception:  # pragma: no cover — defensive; never fail compile
            logger.exception("phase5 memory passes failed; continuing")
            memory_rows = []

        # The contradictions page was projected above from the PRE-memory-pass
        # graph; the passes may have just minted resolved_by / supersedes
        # edges. Re-emit only that page from the post-pass graph so a
        # resolution minted during THIS compile lands in THIS compile's page —
        # otherwise it surfaces one compile late and the second compile over
        # identical inputs is not byte-identical. No-op when nothing changed.
        WikiLayerProjector(wiki_store).project_contradictions(graph)

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
        cfg = self.config() if self.paths.config.exists() else {}
        # The node universe this compile is publishing, across BOTH layers.
        # ``ingest`` prunes the merge ledger against it — a redirect is only
        # worth keeping while it lands on a node that exists. Reset per compile
        # like ``_producer_prov``, so a run that publishes nothing cannot leave
        # a previous run's universe behind for the ledger to validate against.
        self._published_node_ids = {node.id for node in graph.nodes}

        _publish_atomically(self.paths.graph, research_graph.to_json(indent=2) + "\n")
        # ``code-graph.json`` follows the opt-in, and follows it in BOTH
        # directions — same shape as ``combined_graph`` below, for the same
        # reason. Writing it unconditionally left an empty artifact on every
        # project that never asked for a code layer; leaving the last one in
        # place when the switch flips off is worse, because a stale
        # ``code-graph.json`` keeps answering reads with a snapshot of a repo
        # that has since moved, and nothing in it says how old it is.
        if self._code_layer_enabled(cfg):
            _publish_atomically(self.paths.code_graph, code_graph.to_json(indent=2) + "\n")
        elif self.paths.code_graph.exists():
            self.paths.code_graph.unlink()
        include_combined = bool(
            cfg.get("combined_graph")
            or cfg.get("include_combined_graph")
            or os.environ.get("TESSERAE_INCLUDE_COMBINED_GRAPH")
        )
        if include_combined:
            _publish_atomically(self.paths.combined_graph, graph.to_json(indent=2) + "\n")
        elif self.paths.combined_graph.exists():
            # Don't let a stale combined graph survive a config flip.
            self.paths.combined_graph.unlink()

        # The downstream stores (SQLite, markdown projection,
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
            # Provenance sidecar (Plan 01) lives in the same ``sqlite.db`` via
            # the hexagonal :class:`SqliteGraphStore` (idempotent
            # ``create table if not exists`` schema — coexists with the legacy
            # research-graph table). Recorded on EVERY default compile (full +
            # incremental) so the incremental differ has a populated sidecar to
            # diff against. Deterministic timestamps; SQLite-only; graph.json
            # is untouched. See the injected-store branch for the same rows.
            prov_store = SqliteGraphStore(self.paths.sqlite)
            prov_store.upsert_many_nodes(graph.nodes)
            prov_store.upsert_many_edges(graph.edges)
            self._record_provenance(
                prov_store,
                graph,
                extraction_prov,
                producer_prov=self._collect_producer_provenance(),
                full_compile=full_compile,
            )
        else:
            # Injected store path: drive the union graph through the
            # :class:`GraphStore` port. The Postgres adapter (HypePaper-side)
            # and any test-double share this code path.
            #
            # Bulk upsert (04-RESEARCH.md Pattern 3) — fixes the
            # connection-per-call throughput problem. Fall back to per-row
            # upserts for stores that don't expose the bulk surface.
            if hasattr(store, "upsert_many_nodes"):
                store.upsert_many_nodes(graph.nodes)
            else:
                for node in graph.nodes:
                    store.upsert_node(node)
            if hasattr(store, "upsert_many_edges"):
                store.upsert_many_edges(graph.edges)
            else:
                for edge in graph.edges:
                    store.upsert_edge(edge)
            # Record node + edge provenance on EVERY compile (full +
            # incremental). Derived from PER-FILE extraction output (Codex B2),
            # NOT graph adjacency: each node/edge is attributed to the file
            # whose extraction actually produced it, so a single-file change
            # tombstones exactly what that file solely owned. Deterministic
            # content-derived timestamps (never datetime.now(), 04-RESEARCH
            # Pitfall 1); SQLite-only, never in graph.json.
            self._record_provenance(
                store,
                graph,
                extraction_prov,
                producer_prov=self._collect_producer_provenance(),
                full_compile=full_compile,
            )
        # Phase 5 (KB-01/04/05): persist mutable memory state to the
        # node_memory sidecar AFTER graph.json + sqlite writes, so node ids are
        # final. decay_score / access_count / last_accessed_at / confidence /
        # superseded live HERE ONLY — never in graph.json. Best-effort: a
        # locked/missing sidecar must not fail the compile.
        #
        # Sidecar lives in the DEFAULT ``sqlite.db``. When an alternate ``store``
        # is injected it owns persistence and the default SQLite path must stay
        # untouched (same contract as the ``read_memory`` skip in
        # ``_run_memory_passes``), so skip the sidecar write entirely.
        if memory_rows and store is None:
            try:
                from .memory.store import write_memory

                write_memory(self.paths.sqlite, memory_rows)
            except Exception:  # pragma: no cover — defensive
                logger.exception("phase5 write_memory failed; continuing")
        GraphMarkdownProjector().write_projection(graph, self.paths.markdown_projection)
        # markdown_projection/ is a one-way projection (no user notes), but like
        # the Obsidian vault it is NOT rmtree'd, so a rename / deletion leaves a
        # stale per-node page behind. Prune those orphans so an incremental and
        # a full compile project byte-identical trees (Phase-4 subtractive gate).
        self._prune_orphaned_vault_pages(graph, self.paths.markdown_projection)
        report = GraphReporter().render_markdown(GraphReporter().summarize(graph))
        self.paths.report.write_text(report, encoding="utf-8")
        mem_by_id = {r.node_id: r for r in memory_rows}
        facts = TemporalFactProjector().write_jsonl(
            graph, self.paths.temporal_facts, memory_by_id=mem_by_id
        )
        # Transaction time: stamp when we LEARNED each fact, once per compile.
        # This is the ONLY wall clock in the temporal model and it stops at
        # SQLite — never graph.json, never temporal_facts.jsonl, both of which
        # were byte-identical a line ago and must stay so. Valid time (the
        # facts' own valid_from/valid_to) is source-derived and answers a
        # different question; the two are never read off one clock.
        #
        # Same injected-store contract as the write_memory block above: an
        # alternate store owns persistence and the default sqlite.db must stay
        # untouched.
        if store is None:
            record_fact_observations(
                self.paths.sqlite, facts, transaction_now()
            )
        # Same mem_by_id temporal_facts.jsonl just used: the sidecar was written
        # moments ago, so pass the rows in hand rather than re-reading them.
        self.export_graphiti(memory_by_id=mem_by_id)
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
        head = read_git_head(self.project_root)
        if head:
            entry["git_head"] = head
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


def default_raganything_backend_config(name: str = "tesserae") -> dict:
    # ``name`` is unused for now; kept for signature symmetry with the other
    # backend-default helpers this module has carried.
    return {
        "enabled": False,
        "working_dir": ".tesserae/external/raganything/working_dir",
        "parser": "mineru",
        "parse_method": "auto",
        "query_mode": "hybrid",
        "vlm_enhanced": True,
        "llm": {
            "provider": "codex",
            "model": "gpt-5.6-luna",
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


def _vault_file_node_id(vault_file: object) -> Optional[str]:
    """Extract the ``node_id`` frontmatter key from a ``_load_vault_files`` entry.

    ``_load_vault_files`` yields ``(path, text, frontmatter)`` tuples for pages
    that carry a ``node_id`` key (Codex #4 orphan filter reads it). Returns the
    stringified ``node_id`` or ``None`` for any shape lacking one (defensive —
    a non-node page that somehow reaches here is kept by the caller).
    """
    try:
        frontmatter = vault_file[2]
    except (TypeError, IndexError, KeyError):
        return None
    if not isinstance(frontmatter, Mapping):
        return None
    node_id = frontmatter.get("node_id")
    return None if node_id is None else str(node_id)


def _provenance_source_for(graph: ResearchGraph) -> str:
    """The canonical source_path that THIS per-file extraction graph belongs to.

    A single file's extraction produces nodes that all carry the same
    ``source_path`` (the file). We pick the first non-empty one as the file's
    identity; generated/sourceless slices fall back to ``"__synthesis__"`` so
    they reconcile consistently (Open Question 3).
    """
    for node in graph.nodes:
        if node.source_path:
            return node.source_path
    return "__synthesis__"


def compute_extraction_provenance(
    graphs: Iterable[ResearchGraph],
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str, str]]]:
    """Derive node + edge provenance from PER-FILE extraction output (Codex B2).

    The authoritative answer to "which file produced this node/edge" is the
    per-file extraction graph — NOT the merged graph's adjacency. The previous
    adjacency model symmetrically attributed every adjacent node's source to a
    node, so a node from ``a.md`` gained provenance from ``b.md`` via an
    unrelated neighbour and a change to ``a.md`` could never tombstone it.

    Here, every node id and edge triple a file's standalone extraction emits is
    attributed to THAT file's ``source_path``. A cross-file concept (a
    ``ResearchField`` cited by every paper) is independently emitted by each
    paper's extraction, so it accrues one row per contributing file and
    survives any single-file change — without the over-attribution.

    Returns ``(node_rows, edge_rows)`` where
    ``node_rows = [(node_id, source_path, det_ts), ...]`` and
    ``edge_rows = [(source, type, target, source_path, det_ts), ...]``, each
    sorted canonically. Deterministic timestamps (never ``datetime.now()``):
    ``det:`` + a sha256 of the row key so the sidecar is byte-stable.
    """
    node_sources: Dict[str, Set[str]] = {}
    edge_sources: Dict[Tuple[str, str, str], Set[str]] = {}
    for graph in graphs:
        src = _provenance_source_for(graph)
        for node in graph.nodes:
            node_sources.setdefault(node.id, set()).add(node.source_path or src)
        for edge in graph.edges:
            key = (edge.source, edge.type, edge.target)
            edge_sources.setdefault(key, set()).add(src)
    node_rows: List[Tuple[str, str, str]] = []
    for node_id in sorted(node_sources):
        for source_path in sorted(node_sources[node_id] or {"__synthesis__"}):
            det_ts = "det:" + sha256_text(f"{node_id}|{source_path}")[:16]
            node_rows.append((node_id, source_path, det_ts))
    edge_rows: List[Tuple[str, str, str, str, str]] = []
    for key in sorted(edge_sources):
        source, etype, target = key
        for source_path in sorted(edge_sources[key] or {"__synthesis__"}):
            det_ts = "det:" + sha256_text(f"{source}|{etype}|{target}|{source_path}")[:16]
            edge_rows.append((source, etype, target, source_path, det_ts))
    return node_rows, edge_rows


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

    Used by changed-only ingest to avoid double-counting projector layers on
    every recompile. Two generated layers ride along with the typed graph:

    * SYNTHESIS — regenerated by :class:`tesserae.synthesis.SynthesisProjector`
      after the graph is merged.
    * COMMUNITY_SUMMARY — regenerated by
      :meth:`tesserae.project.ProjectWiki._merge_community_summaries` when the
      opt-in community-summary pass is enabled.

    Neither prior copy should survive into the merge: if we leave stale
    COMMUNITY_SUMMARY nodes in place they become members of new clusters,
    shift cluster cache ids, and cause nested/stale community pages to
    accumulate on subsequent incremental compiles.
    """
    generated_node_ids = {
        n.id
        for n in graph.nodes
        if n.type in {ResearchNodeType.SYNTHESIS, ResearchNodeType.COMMUNITY_SUMMARY}
    }
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
    # Body moved verbatim to ``research_graph.graph_from_payload`` so the
    # code-graph cache can rehydrate without importing this module (circular).
    return graph_from_payload(json.loads(Path(path).read_text(encoding="utf-8")))


def resolve_project_input(project_root: Path, item: str | Path) -> Path:
    raw = Path(item)
    return raw if raw.is_absolute() else project_root / raw


# The suffixes the default compile walker actually reads. This deliberately
# overrides ``FilesystemSourceLoader.DEFAULT_EXTENSIONS`` (which also lists
# ``.txt``/``.py``/``.rst``) — the compile pipeline has only ever extracted from
# markdown. Named here so callers that need to know what compile will pick up
# (e.g. ``tesserae.ingest.orchestrator``'s up-front validation) read the same
# set the walker uses, instead of re-hard-coding ``.md`` a third time and
# drifting from it.
COMPILE_SOURCE_EXTENSIONS: Tuple[str, ...] = (".md",)


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
        return [path] if path.suffix.lower() in COMPILE_SOURCE_EXTENSIONS else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    loader = FilesystemSourceLoader([path], extensions=COMPILE_SOURCE_EXTENSIONS)
    # We only need the absolute paths — bypass content reads by walking the
    # internal iterator directly. ``discover()`` reads file bodies eagerly,
    # which would be wasteful here since downstream consumers re-read the
    # file via :class:`BatchIngestRunner`.
    return list(loader.iter_paths(path))


def iter_source_candidates(path: Path) -> List[Path]:
    """Every file :func:`iter_markdown_files` LOOKED AT under ``path``.

    Same roots, same directory exclusions (``_EXCLUDED_TOPLEVEL_DIRS``:
    ``i18n``, ``build``, ``node_modules``, ``dist``, ...), same
    hidden-component filter — only the extension gate is dropped. The pair is
    the honest way to explain a refusal: ``iter_markdown_files`` says whether
    the walk found anything readable, and this says what the walk considered
    while deciding that.

    Re-walking with a bare ``rglob("*")`` instead describes a LARGER set than
    the decision was made on, which is how a refusal came to report
    "holds 3 file(s), none of them markdown" about three ``.md`` files living
    under ``docs/i18n/ko/``, ``docs/build/`` and ``docs/node_modules/pkg/``.
    """
    from .source_loaders import FilesystemSourceLoader

    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    return list(FilesystemSourceLoader([path], extensions=None).iter_paths(path))


def sanitize_server_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "tesserae_project"
