"""CLI for LLM-Wiki research graph extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, List

from .batch import BatchIngestRunner
from .canonicalization import GraphCanonicalizer, ReviewDecision
from .cognee_adapter import CogneeResearchGraphAdapter
from .cognee_codex import CogneeCodexPatch
from .cognee_direct import CogneeDirectImporter
from .harness_sessions import HarnessSession, HarnessSessionStore, discover_harness_sessions, session_matches_project
from .llm_extractor import ClaudeCLIResearchExtractor
from .markdown_projection import GraphMarkdownProjector
from .persistence import KuzuResearchGraphStore, SQLiteResearchGraphStore
from .graphiti_adapter import GraphitiSyncUnavailableError
from .project import CognifyOptions, ProjectWiki, cognify_options_from_config, cognee_backend_config, iter_markdown_files
from .project_setup import apply_setup_plan, build_setup_plan, interactive_setup_plan, refresh_configured_external_tools, render_setup_summary
from .report import GraphReporter
from .understand_anything_refresh import refresh_understand_anything
from .raganything_refresh import main as _raganything_refresh_main
from .research_graph import ResearchCorpusAnalyzer, ResearchGraph, ResearchGraphExtractor
from .review_workflow import ReviewQueueExporter
from .selective_extractor import SelectiveClaudeResearchExtractor


def merge_graphs(graphs: Iterable[ResearchGraph]) -> ResearchGraph:
    nodes = {}
    edges = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes[node.id] = node
        for edge in graph.edges:
            edges[(edge.source, edge.type, edge.target)] = edge
    return ResearchGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def load_review_decisions(path: Path) -> List[ReviewDecision]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_decisions = payload.get("decisions", payload if isinstance(payload, list) else [])
    if not isinstance(raw_decisions, list):
        raise ValueError("Review decision file must contain a decisions list")
    decisions = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            raise ValueError("Every review decision must be an object")
        decisions.append(
            ReviewDecision(
                item_id=str(raw["item_id"]),
                action=str(raw["action"]),
                canonical_node_id=raw.get("canonical_node_id"),
            )
        )
    return decisions


def _project_query_handler(args) -> int:
    """Handle ``project query`` (one-shot or interactive REPL).

    Lives outside ``project_main`` so the parser-vs-handler block ordering can
    stay in lockstep without inflating the dispatch ladder. Tolerant of
    missing arguments: an interactive session falls back to the REPL when
    ``question`` is empty, and one-shot prints a friendly error when the
    index isn't built yet.
    """

    from .query import QueryResult, WikiQuery

    project_root = args.project
    top_k = args.top_k
    kind_filter = args.kind
    use_llm = bool(args.llm)
    no_llm = bool(args.no_llm)
    model = args.model
    json_output = bool(args.json_output)
    interactive = bool(args.interactive)

    wq = WikiQuery(project_root, top_k=top_k, kind_filter=kind_filter)

    def run_one(question: str, history: List[dict] | None = None) -> "QueryResult":
        return wq.answer(
            question,
            model=model,
            force_llm=use_llm,
            force_no_llm=no_llm,
            history=history,
        )

    if interactive:
        return _run_query_repl(run_one, json_output=json_output, use_llm=use_llm)

    question = (args.question or "").strip()
    if not question:
        print("project query: question is required (or use --interactive)", file=sys.stderr)
        return 2

    result = run_one(question)

    if json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    _print_query_result(result)
    return 0


def _project_ask_handler(args) -> int:
    wiki = ProjectWiki.load(args.project)
    cfg = wiki.config()
    cognee_cfg = cognee_backend_config(cfg)
    backend = args.backend
    use_cognee = backend == "cognee" or (backend == "auto" and cognee_cfg.get("enabled", False))
    if use_cognee:
        from .cognee_query import search_cognee

        dataset = args.cognee_dataset or cognee_cfg.get("dataset")
        try:
            results = search_cognee(
                args.question,
                dataset=dataset,
                search_type=args.cognee_search_type,
                top_k=args.top_k,
            )
        except Exception as exc:
            if backend == "cognee":
                print(f"Cognee ask failed: {exc}", file=sys.stderr)
                return 2
            print(f"Cognee ask unavailable; falling back to compiled wiki query: {exc}", file=sys.stderr)
        else:
            if args.json_output:
                print(json.dumps({"backend": "cognee", "dataset": dataset, "question": args.question, "results": results}, ensure_ascii=False, indent=2))
            else:
                print(f"Cognee answer (dataset={dataset or 'default'}):")
                if results:
                    for idx, result in enumerate(results, start=1):
                        print(f"\n[{idx}] {result}")
                else:
                    print("No Cognee results returned.")
            return 0

    result = wiki.query(args.question, top_k=args.top_k, use_llm=False)
    if args.json_output:
        payload = result.to_dict()
        payload["backend"] = "wiki"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Compiled wiki answer:")
        _print_query_result(result)
    return 0


def _print_query_result(result) -> None:
    """Print a human-readable summary of a :class:`QueryResult`."""

    hits = result.hits
    if not hits:
        print(f"No matches for: {result.question!r}")
    else:
        print(f"Top {len(hits)} hit(s) for: {result.question!r}")
        for idx, hit in enumerate(hits, start=1):
            badge = f"[{hit.kind}]"
            path = str(hit.page_path) if hit.page_path else "(no page)"
            print(f"  {idx}. {badge} {hit.title}  (score={hit.score:.3f})")
            print(f"     {path}")
            if hit.excerpt:
                print(f"     {hit.excerpt}")

    if result.answer:
        print()
        print(f"Answer (model={result.model}, used_llm={result.used_llm}):")
        print(result.answer)
    elif result.fallback_reason:
        print()
        print(f"(no LLM answer: {result.fallback_reason})")


def _run_query_repl(run_one, *, json_output: bool, use_llm: bool) -> int:
    """A tiny readline-backed REPL.

    Blank line or EOF exits cleanly. The chat history is kept short (last 6
    turns) so the prompt stays bounded; the system block carries the wiki
    overview and ontology and is cached across turns.
    """

    try:
        import readline  # noqa: F401 — importing enables arrow-key history
    except ImportError:
        pass  # Windows or stripped builds: REPL still works, no history.

    history: List[dict] = []
    print("LLM-Wiki query REPL — blank line or EOF exits.")
    while True:
        try:
            question = input("wiki> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question.strip():
            return 0
        result = run_one(question, history=history if use_llm else None)
        if json_output:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_query_result(result)
        if use_llm and result.answer:
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": result.answer})
            # Keep the last 6 turns (12 messages).
            if len(history) > 12:
                history = history[-12:]


def project_main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a per-project .llm-wiki workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .llm-wiki in a project directory")
    init_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    init_parser.add_argument("--name", help="MCP server/config name; defaults to sanitized project directory name")
    init_parser.add_argument("--source-kind", default="SourceDocument", help="Default source kind for project ingest")
    init_parser.add_argument("--source", action="append", default=[], help="Default project-relative source path for project compile; repeat for multiple paths")

    setup_parser = subparsers.add_parser("setup", help="Open the colored setup wizard for sources and companion tools")
    setup_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    setup_parser.add_argument("--name", help="MCP server/config name; defaults to sanitized project directory name")
    setup_parser.add_argument("--source-kind", default="Repository", help="Default source kind for project compile")
    setup_parser.add_argument("--source", action="append", default=[], help="Project-relative source path; repeat for multiple paths")
    setup_parser.add_argument("--with-understand-anything", action="store_true", help="Include .understand-anything/knowledge-graph.json as a companion source")
    setup_parser.add_argument("--run-understand-anything", action="store_true", help="Run the configured Understand Anything refresh command now and mark it for compile-time auto-refresh")
    setup_parser.add_argument("--understand-anything-command", help="Shell command that refreshes .understand-anything/knowledge-graph.json")
    setup_parser.add_argument("--install-understand-anything", action="store_true", help="Install/update Understand Anything during setup when selected")
    setup_parser.add_argument("--skip-install-understand-anything", action="store_true", help="Do not auto-install Understand Anything even when selected")
    setup_parser.add_argument("--understand-anything-platform", default="codex", help="Understand Anything installer platform id (default: codex)")
    setup_parser.add_argument("--with-raganything", action="store_true", help="Enable RAG-Anything multimodal ingestion + memory backend")
    setup_parser.add_argument("--skip-raganything", action="store_true", help="Disable RAG-Anything even if previously configured")
    setup_parser.add_argument("--install-raganything", action="store_true", help="Auto-install raganything during setup")
    setup_parser.add_argument("--skip-install-raganything", action="store_true", help="Do not auto-install raganything")
    setup_parser.add_argument("--raganything-parser", choices=["mineru", "docling", "paddleocr"], default="mineru", help="Parser backend for RAG-Anything (default: mineru)")
    setup_parser.add_argument("--raganything-extras", default="all", help="pip extras to use when installing raganything (default: all)")
    setup_parser.add_argument("--run-raganything", action="store_true", help="Auto-refresh RAG-Anything on every compile")
    setup_parser.add_argument("--no-cognee", action="store_true", help="Do not enable Cognee as the default project memory backend")
    setup_parser.add_argument("--install-cognee", action="store_true", help="Install Cognee during setup and allow compile to auto-install if missing")
    setup_parser.add_argument("--skip-install-cognee", action="store_true", help="Do not auto-install Cognee even when --run-cognee is selected")
    setup_parser.add_argument("--cognee-mode", choices=["add", "cognify", "codex_cognify"], default="codex_cognify", help="Cognee mode saved in project config (default: codex_cognify)")
    setup_parser.add_argument("--run-cognee", action="store_true", help="Auto-add/cognify Cognee on every project compile using the saved safe config")
    setup_parser.add_argument("--yes", action="store_true", help="Accept detected defaults without interactive prompts")
    setup_parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors in setup output")

    ingest_parser = subparsers.add_parser("ingest", help="Ingest markdown files into the project graph artifacts")
    ingest_parser.add_argument("inputs", nargs="+", help="Project-relative or absolute markdown files/directories")
    ingest_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    ingest_parser.add_argument("--source-kind", help="Override configured source kind")
    ingest_parser.add_argument("--changed-only", action="store_true", help="Skip unchanged files using .llm-wiki/manifest.json")
    ingest_parser.add_argument("--limit", type=int, help="Maximum number of changed files to process")
    ingest_parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes")
    ingest_parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum sources needed for Trend nodes")

    compile_parser = subparsers.add_parser("compile", help="Compile configured project sources into all .llm-wiki artifacts")
    compile_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    compile_parser.add_argument("--source-kind", help="Override configured source kind")
    compile_parser.add_argument("--changed-only", action="store_true", help="Skip unchanged files using .llm-wiki/manifest.json")
    compile_parser.add_argument("--limit", type=int, help="Maximum number of changed files to process")
    compile_parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes")
    compile_parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum sources needed for Trend nodes")
    compile_parser.add_argument("--include-data", action="store_true", help="Documentation flag: project_root/data is auto-included by default; this flag is a no-op kept for clarity")
    compile_parser.add_argument("--exclude-data", action="store_true", help="Skip the implicit project_root/data auto-include even if data/ exists")
    compile_parser.add_argument("--refresh-external-tools", action="store_true", help="Run configured external tool refresh commands before compile, even if they are not marked auto_refresh")
    # --- Cognee cognify pass (opt-in, runs after the bundle is written) ----
    compile_parser.add_argument("--cognee-add", action="store_true", help="After compile, add the Cognee bundle to the Cognee dataset (no cognify)")
    compile_parser.add_argument("--cognee-cognify", action="store_true", help="After compile, add the bundle and run Cognee cognify (uses configured LLM/embedding providers)")
    compile_parser.add_argument("--cognee-codex-cognify", action="store_true", help="After compile, run Cognee cognify with Cognee's LLM client patched to Codex CLI/OAuth")
    compile_parser.add_argument("--cognee-codex-model", default="gpt-5.4", help="Codex CLI model for --cognee-codex-cognify")
    compile_parser.add_argument("--cognee-codex-timeout", type=int, default=300, help="Timeout per Codex CLI structured call")
    compile_parser.add_argument("--cognee-local-embedding-dimensions", type=int, default=128, help="Embedding dimensions for --cognee-codex-cognify; qwen3-embedding:0.6b uses 1024")
    compile_parser.add_argument("--cognee-embedding-provider", choices=["deterministic", "ollama"], default="deterministic", help="Embedding provider for cognify")
    compile_parser.add_argument("--cognee-ollama-embedding-model", default="qwen3-embedding:0.6b", help="Ollama embedding model when --cognee-embedding-provider=ollama")
    compile_parser.add_argument("--cognee-ollama-embedding-endpoint", default="http://127.0.0.1:11434/api/embed", help="Ollama /api/embed endpoint for Cognee embeddings")
    compile_parser.add_argument("--cognee-ollama-embedding-timeout", type=int, default=120, help="Ollama embedding request timeout in seconds")
    compile_parser.add_argument("--cognee-dataset", default="llm_wiki_research_graph", help="Cognee dataset name")
    compile_parser.add_argument("--cognee-system-root", help="Optional isolated Cognee system root directory")
    compile_parser.add_argument("--cognee-data-root", help="Optional isolated Cognee data root directory")

    ua_refresh_parser = subparsers.add_parser("refresh-understand-anything", help="Run LLM-Wiki's managed Understand Anything refresh")
    ua_refresh_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    ua_refresh_parser.add_argument("--platform", default="codex", help="Agent platform to use: codex, opencode, or claude")
    ua_refresh_parser.add_argument("--full", action="store_true", help="Force /understand --full")
    ua_refresh_parser.add_argument("--force", action="store_true", help="Run even if the existing graph appears current")
    ua_refresh_parser.add_argument("--timeout", type=int, help="Optional timeout in seconds")

    refresh_raga_parser = subparsers.add_parser(
        "refresh-raganything",
        help="Run the managed RAG-Anything refresh wrapper",
    )
    refresh_raga_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    refresh_raga_parser.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"])
    refresh_raga_parser.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"])
    refresh_raga_parser.add_argument("--root", action="append", dest="roots", help="Restrict to this root (repeatable)")
    refresh_raga_parser.add_argument("--force", action="store_true")
    refresh_raga_parser.add_argument("--full", action="store_true")

    lint_parser = subparsers.add_parser(
        "lint",
        help="Lint the compiled wiki: orphan papers, stale citations, drift, ghost synthesis inputs, and more.",
    )
    lint_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    lint_parser.add_argument(
        "--fix-trivial",
        action="store_true",
        help="Apply safe auto-fixes (add missing implemented_in edges; prune ghost synthesis inputs)",
    )
    lint_parser.add_argument(
        "--severity",
        choices=["info", "warning", "error"],
        default="warning",
        help="Severity floor for the exit code (default: warning). Findings below the floor are still reported.",
    )
    lint_parser.add_argument(
        "--json",
        dest="lint_json",
        action="store_true",
        help="Print the JSON report to stdout instead of the markdown summary.",
    )

    query_parser = subparsers.add_parser(
        "query",
        help="Search the compiled wiki and (optionally) ask the LLM for a synthesized answer with citations.",
    )
    query_parser.add_argument("question", nargs="?", default=None, help="Question text; omit to use --interactive")
    query_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    query_parser.add_argument("--top-k", type=int, default=8, help="Maximum number of search hits to return / feed to the LLM (default: 8)")
    query_parser.add_argument("--kind", help="Restrict hits to a single wiki kind (e.g. papers, concepts, repos)")
    query_parser.add_argument("--llm", action="store_true", help="Force the LLM path on, even if LLM_WIKI_QUERY_LLM is unset")
    query_parser.add_argument("--no-llm", action="store_true", help="Force the LLM path off, even if LLM_WIKI_QUERY_LLM=1")
    query_parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model id for --llm (default: claude-sonnet-4-6)")
    query_parser.add_argument("--json", dest="json_output", action="store_true", help="Print the structured QueryResult as JSON")
    query_parser.add_argument("--interactive", action="store_true", help="Drop into a REPL with readline history; blank line or EOF exits")

    ask_parser = subparsers.add_parser("ask", help="Ask the configured project memory backend; uses Cognee when enabled")
    ask_parser.add_argument("question", help="Question text")
    ask_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    ask_parser.add_argument("--backend", choices=["auto", "cognee", "wiki"], default="auto", help="Question backend (default: auto; Cognee if enabled, otherwise wiki query)")
    ask_parser.add_argument("--top-k", type=int, default=8, help="Maximum results/context items")
    ask_parser.add_argument("--cognee-search-type", default="INSIGHTS", help="Cognee SearchType name, e.g. INSIGHTS, CHUNKS, SUMMARIES, GRAPH_COMPLETION")
    ask_parser.add_argument("--cognee-dataset", help="Override configured Cognee dataset")
    ask_parser.add_argument("--json", dest="json_output", action="store_true", help="Print backend/result JSON")

    mcp_parser = subparsers.add_parser("mcp-config", help="Print a Hermes mcp_servers config snippet for this project")
    mcp_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    mcp_parser.add_argument("--server-name", help="MCP server name in Hermes config")
    mcp_parser.add_argument("--pythonpath", help="PYTHONPATH pointing at the LLM-Wiki checkout")

    export_graphiti_parser = subparsers.add_parser("export-graphiti", help="Export project graph as dependency-free Graphiti episode JSONL")
    export_graphiti_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    export_graphiti_parser.add_argument("--group-id", help="Graphiti group_id; defaults to project wiki name")
    export_graphiti_parser.add_argument("--output", help="Episode JSONL output path; defaults to .llm-wiki/graphiti_episodes.jsonl")

    sync_graphiti_parser = subparsers.add_parser("sync-graphiti", help="Sync project graph episodes into Graphiti/Neo4j")
    sync_graphiti_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    sync_graphiti_parser.add_argument("--group-id", help="Graphiti group_id; defaults to project wiki name")
    sync_graphiti_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI for Graphiti")
    sync_graphiti_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j username")
    sync_graphiti_parser.add_argument("--neo4j-password", default="password", help="Neo4j password")
    sync_graphiti_parser.add_argument("--dry-run", action="store_true", help="Count episodes without requiring Graphiti or Neo4j")

    harness_parser = subparsers.add_parser("export-agent-harness", help="Export context/config harnesses for coding agents")
    harness_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    harness_parser.add_argument("--target", action="append", default=[], help="Agent target to export; repeat for multiple targets. Defaults to all supported targets")
    harness_parser.add_argument("--output", help="Harness output directory; defaults to .llm-wiki/agent_harness")

    obsidian_parser = subparsers.add_parser("export-obsidian", help="Export the compiled graph as an Obsidian vault")
    obsidian_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    obsidian_parser.add_argument("--vault", help="Vault output directory; defaults to .llm-wiki/obsidian_vault")

    sessions_parser = subparsers.add_parser("sessions", help="Manage inbound agent harness session history")
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_import = sessions_sub.add_parser("import", help="Import normalized HarnessSession JSON files")
    sessions_import.add_argument("paths", nargs="+", help="JSON files containing one session object or a list of sessions")
    sessions_import.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    sessions_discover = sessions_sub.add_parser("discover", help="Discover local Claude Code/Codex sessions scoped to this project")
    sessions_discover.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    sessions_discover.add_argument("--root", action="append", default=[], help="Harness config root to scan; repeat for multiple roots. Defaults to auto-detected Claude/Codex config roots under HOME")
    sessions_discover.add_argument("--harness", action="append", default=[], choices=["claude-code", "codex"], help="Harness to scan; repeat for multiple harnesses. Defaults to both")
    sessions_discover.add_argument("--import", dest="import_sessions", action="store_true", help="Import discovered normalized sessions into .llm-wiki/harness_sessions")
    sessions_list = sessions_sub.add_parser("list", help="List normalized harness sessions for this project")
    sessions_list.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")

    site_parser = subparsers.add_parser("build-site", help="Build the static frontend site for this project wiki")
    site_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    site_parser.add_argument("--output", help="Site output directory; defaults to .llm-wiki/site")

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Deploy the compiled site to the GitHub Pages branch of the project's git origin. Optionally rebuilds first and turns Pages on via the gh CLI.",
    )
    deploy_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    deploy_parser.add_argument("--branch", default="gh-pages", help="Branch to push the site to (default: gh-pages)")
    deploy_parser.add_argument("--remote", default="origin", help="Git remote to push to (default: origin)")
    deploy_parser.add_argument("--message", help="Commit message for the deploy commit")
    deploy_parser.add_argument("--dry-run", action="store_true", help="Stage and commit but skip the final git push")
    deploy_parser.add_argument("--build", action="store_true", help="Run project compile before deploying so the site is fresh")
    deploy_parser.add_argument("--enable-pages", action="store_true", help="Enable GitHub Pages on the repo via the gh CLI (idempotent)")
    deploy_parser.add_argument("--force", action="store_true", help="Allow deploying even when the project working tree is dirty")
    deploy_parser.add_argument("--force-push", action="store_true", help="Use git push --force; refused for protected branches")

    serve_parser = subparsers.add_parser("serve", help="Serve the static frontend site")
    serve_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    serve_parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    serve_parser.add_argument("--dry-run", action="store_true", help="Print the site URL without starting a server")

    watch_parser = subparsers.add_parser(
        "watch",
        help="Auto-recompile when files change. Pairs with python3 -m http.server in another terminal.",
    )
    watch_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    watch_parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (default: 2)")
    watch_parser.add_argument("--debounce", type=float, default=1.0, help="Quiet window after a burst of edits before rebuilding (default: 1.0)")
    watch_parser.add_argument("--once", action="store_true", help="Snapshot once, rebuild only if anything changed since the last run, exit")
    watch_parser.add_argument("--paths", action="append", default=[], help="Additional directory to watch; repeat for multiple paths")
    watch_parser.add_argument("--quiet", action="store_true", help="Suppress the banner and per-cycle progress output")

    args = parser.parse_args(argv)
    if args.command == "init":
        wiki = ProjectWiki.init(args.project, name=args.name, source_kind=args.source_kind, sources=args.source)
        print(f"Initialized project wiki: {wiki.root}")
        print(f"Graph: {wiki.paths.graph}")
        print("Next: python3 -m llm_wiki.cli project ingest <paths>")
        return 0
    if args.command == "setup":
        try:
            if args.yes:
                plan = build_setup_plan(
                    args.project,
                    name=args.name,
                    source_kind=args.source_kind,
                    sources=args.source or None,
                    include_understand_anything=args.with_understand_anything,
                    run_understand_anything=args.run_understand_anything,
                    understand_anything_command=args.understand_anything_command,
                    install_understand_anything=(False if args.skip_install_understand_anything else True if args.install_understand_anything else None),
                    understand_anything_platform=args.understand_anything_platform,
                    enable_cognee=not args.no_cognee,
                    cognee_mode=args.cognee_mode,
                    cognee_auto_cognify=args.run_cognee,
                    install_cognee=(False if args.skip_install_cognee else True if args.install_cognee else None),
                    include_raganything=(False if args.skip_raganything else args.with_raganything),
                    install_raganything=(False if args.skip_install_raganything else args.install_raganything),
                    raganything_parser=args.raganything_parser,
                    raganything_extras=args.raganything_extras,
                    run_raganything=args.run_raganything,
                )
                print(render_setup_summary(plan, color=not args.no_color), end="")
            else:
                plan = interactive_setup_plan(args.project, color=not args.no_color)
            result = apply_setup_plan(plan)
        except KeyboardInterrupt:
            print("Setup cancelled.")
            return 130
        except Exception as exc:
            print(f"Setup failed: {exc}", file=sys.stderr)
            return 2
        print(f"Initialized project wiki: {result.wiki.root}")
        print(f"Config: {result.config_path}")
        if result.ran_tools:
            failures = [row for row in result.ran_tools if row.get("status") in {"failed", "install_failed"}]
            installed = [row for row in result.ran_tools if row.get("status") == "installed"]
            installed_ids = {row.get("id") for row in installed}
            if "understand-anything" in installed_ids:
                print("Understand Anything installed/updated.")
            if "cognee" in installed_ids:
                print("Cognee installed/updated.")
            if failures:
                print("External tool install/refresh had warnings; setup was saved anyway.")
                for failure in failures:
                    detail = (failure.get("stderr") or failure.get("stdout") or "").strip().splitlines()
                    tail = f": {detail[-1]}" if detail else ""
                    print(f"  - {failure.get('id')}: {failure.get('command')} exited {failure.get('returncode')}{tail}")
            else:
                print(f"External tools refreshed: {len(result.ran_tools)}")
        print("Next: llm_wiki project compile && llm_wiki project build-site")
        return 0
    if args.command == "ingest":
        wiki = ProjectWiki.load(args.project)
        result = wiki.ingest(
            args.inputs,
            source_kind=args.source_kind,
            changed_only=args.changed_only,
            limit=args.limit,
            trends=args.trends,
            min_trend_sources=args.min_trend_sources,
        )
        print(
            "Ingested project wiki: "
            f"processed={result['processed_files']} skipped={result['skipped_files']} "
            f"nodes={result['node_count']} edges={result['edge_count']}"
        )
        print(f"Graph: {result['graph_path']}")
        return 0
    if args.command == "compile":
        wiki = ProjectWiki.load(args.project)
        try:
            refreshed = refresh_configured_external_tools(args.project, only_auto=not args.refresh_external_tools, fail_fast=False)
        except Exception as exc:
            print(f"External tool refresh failed: {exc}", file=sys.stderr)
            return 2
        if refreshed:
            failures = [row for row in refreshed if row.get("status") == "failed"]
            if failures:
                print("External tool refresh had warnings; compile will continue.")
                for failure in failures:
                    detail = (failure.get("stderr") or failure.get("stdout") or "").strip().splitlines()
                    tail = f": {detail[-1]}" if detail else ""
                    print(f"  - {failure.get('id')}: {failure.get('command')} exited {failure.get('returncode')}{tail}")
            else:
                print(f"Refreshed external tools: {len(refreshed)}")
        explicit_cognee = args.cognee_codex_cognify or args.cognee_cognify or args.cognee_add
        cognify_mode = (
            "codex_cognify" if args.cognee_codex_cognify
            else "cognify" if args.cognee_cognify
            else "add" if args.cognee_add
            else "off"
        )
        cognify_options = CognifyOptions(
            mode=cognify_mode,
            dataset=args.cognee_dataset,
            codex_model=args.cognee_codex_model,
            codex_timeout=args.cognee_codex_timeout,
            embedding_provider=args.cognee_embedding_provider,
            ollama_embedding_model=args.cognee_ollama_embedding_model,
            ollama_embedding_endpoint=args.cognee_ollama_embedding_endpoint,
            ollama_embedding_timeout=args.cognee_ollama_embedding_timeout,
            local_embedding_dimensions=args.cognee_local_embedding_dimensions,
            system_root=args.cognee_system_root,
            data_root=args.cognee_data_root,
        ) if explicit_cognee else cognify_options_from_config(wiki.config())
        result = wiki.compile(
            source_kind=args.source_kind,
            changed_only=args.changed_only,
            limit=args.limit,
            trends=args.trends,
            min_trend_sources=args.min_trend_sources,
            exclude_data=args.exclude_data,
            cognify=cognify_options if (cognify_options and cognify_options.is_active) else None,
        )
        print(
            "Compiled project wiki: "
            f"processed={result['processed_files']} skipped={result['skipped_files']} "
            f"nodes={result['node_count']} edges={result['edge_count']}"
        )
        print(f"Graph: {result['graph_path']}")
        return 0
    if args.command == "refresh-understand-anything":
        return refresh_understand_anything(
            args.project,
            platform=args.platform,
            full=args.full,
            force=args.force,
            timeout=args.timeout,
        )
    if args.command == "refresh-raganything":
        forwarded = ["--project", args.project, "--parser", args.parser, "--parse-method", args.parse_method]
        for r in (args.roots or []):
            forwarded += ["--root", r]
        if args.force:
            forwarded.append("--force")
        if args.full:
            forwarded.append("--full")
        return _raganything_refresh_main(forwarded)
    if args.command == "lint":
        wiki = ProjectWiki.load(args.project)
        report = wiki.lint(fix_trivial=args.fix_trivial, severity_floor=args.severity)
        if args.lint_json:
            sys.stdout.write(report.to_json())
        else:
            sys.stdout.write(report.to_markdown())
        # Exit code maps to severity floor: ``--severity warning`` (default)
        # treats warnings as failure; ``--severity error`` only fails on
        # errors; ``--severity info`` makes any finding fail.
        floor = args.severity
        if report.has_errors():
            return 2
        if floor in ("info", "warning") and report.has_warnings():
            return 1
        if floor == "info" and report.findings:
            return 1
        return 0
    if args.command == "query":
        return _project_query_handler(args)
    if args.command == "ask":
        return _project_ask_handler(args)
    if args.command == "mcp-config":
        wiki = ProjectWiki.load(args.project)
        print(wiki.render_mcp_config(server_name=args.server_name, pythonpath=args.pythonpath), end="")
        return 0
    if args.command == "export-graphiti":
        wiki = ProjectWiki.load(args.project)
        result = wiki.export_graphiti(group_id=args.group_id, output=args.output)
        print(f"Exported Graphiti episodes: episodes={result['episodes']} path={result['path']} group_id={result['group_id']}")
        return 0
    if args.command == "sync-graphiti":
        wiki = ProjectWiki.load(args.project)
        try:
            result = wiki.sync_graphiti(
                neo4j_uri=args.neo4j_uri,
                neo4j_user=args.neo4j_user,
                neo4j_password=args.neo4j_password,
                group_id=args.group_id,
                dry_run=args.dry_run,
            )
        except GraphitiSyncUnavailableError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        prefix = "Graphiti dry-run" if result.get("dry_run") else "Synced Graphiti"
        print(f"{prefix}: episodes={result['episodes']} group_id={result['group_id']}")
        return 0
    if args.command == "export-agent-harness":
        wiki = ProjectWiki.load(args.project)
        result = wiki.export_agent_harness(targets=args.target or None, output=args.output)
        print(f"Exported agent harness: files={result['files']} path={result['path']} targets={','.join(result['targets'])}")
        return 0
    if args.command == "export-obsidian":
        wiki = ProjectWiki.load(args.project)
        result = wiki.export_obsidian(vault=args.vault)
        print(f"Exported Obsidian vault: notes={result['notes']} path={result['vault_path']}")
        return 0
    if args.command == "sessions":
        wiki = ProjectWiki.load(args.project)
        store = HarnessSessionStore(wiki.paths.harness_sessions)
        if args.sessions_command == "import":
            sessions = []
            skipped = 0
            for raw_path in args.paths:
                payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError(f"Session import item must be an object: {raw_path}")
                    session = HarnessSession.from_dict(item)
                    if session_matches_project(session, wiki.project_root):
                        sessions.append(session)
                    else:
                        skipped += 1
            result = store.write_sessions(sessions)
            print(f"Imported harness sessions: {result['sessions']} path={result['path']}")
            if skipped:
                print(f"Skipped non-project harness sessions: {skipped}")
            return 0
        if args.sessions_command == "discover":
            sessions = discover_harness_sessions(
                wiki.project_root,
                roots=args.root or None,
                harnesses=args.harness or None,
            )
            print(f"Project working directory: {wiki.project_root.resolve()}")
            print(f"Project-attached harness sessions: {len(sessions)}")
            for harness, count in sorted(Counter(session.harness for session in sessions).items()):
                print(f"  {harness}: {count}")
            for session in sessions[:100]:
                print(
                    f"  {session.date}  {session.harness}  {session.project_name}  "
                    f"{session.title or session.slug}"
                )
            if len(sessions) > 100:
                print(f"  ... {len(sessions) - 100} more")
            if args.import_sessions:
                result = store.write_sessions(sessions)
                print(f"Imported harness sessions: {result['sessions']} path={result['path']}")
            return 0
        if args.sessions_command == "list":
            sessions = store.list_sessions()
            print(f"Harness sessions: {len(sessions)}")
            for session in sessions:
                print(
                    f"  {session.date}  {session.harness}  {session.project_name}  "
                    f"{session.title or session.slug}"
                )
            return 0
    if args.command == "build-site":
        wiki = ProjectWiki.load(args.project)
        result = wiki.build_site(output=args.output)
        print(f"Built frontend site: nodes={result['nodes']} edges={result['edges']} path={result['site_path']}")
        return 0
    if args.command == "deploy":
        wiki = ProjectWiki.load(args.project)
        if args.build:
            wiki.compile()
        try:
            result = wiki.deploy_github_pages(
                branch=args.branch,
                remote=args.remote,
                commit_message=args.message,
                dry_run=args.dry_run,
                force=args.force,
                force_push=args.force_push,
                enable_pages=args.enable_pages,
            )
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Deployed to {result['site_url']}")
        print(f"  branch: {result['branch']}")
        print(f"  files: {result['files_uploaded']}")
        print(f"  sha: {result['commit_sha']}")
        return 0
    if args.command == "serve":
        wiki = ProjectWiki.load(args.project)
        url = f"http://{args.host}:{args.port}/"
        if args.dry_run:
            print(f"Frontend site ready: {wiki.paths.site} at {url}")
            return 0
        from functools import partial
        import http.server
        import socketserver
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(wiki.paths.site))
        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            with ReusableTCPServer((args.host, args.port), handler) as httpd:
                print(f"Serving frontend site: {wiki.paths.site} at {url}")
                httpd.serve_forever()
        except OSError as exc:
            print(f"Could not serve frontend site at {url}: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "watch":
        from .watch import WatchLoop

        watch_paths = args.paths or None
        loop = WatchLoop(
            Path(args.project).resolve(),
            interval=args.interval,
            debounce=args.debounce,
            watch_paths=watch_paths,
            quiet=args.quiet,
        )
        loop.run(once=args.once)
        return 0
    raise ValueError(f"Unknown project command: {args.command}")


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "project":
        return project_main(argv[1:])
    parser = argparse.ArgumentParser(description="Extract a typed research intelligence graph from LLM-Wiki notes.")
    parser.add_argument("paths", nargs="+", help="Markdown file or directory paths to extract")
    parser.add_argument("--source-kind", default="SourceDocument", help="Default source kind: Paper, Repository, ResearchDigest, SourceDocument")
    parser.add_argument("--output", "-o", help="Write JSON graph to this path instead of stdout")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes for concepts repeated across sources")
    parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum distinct sources required to create a Trend node")
    parser.add_argument("--extractor", choices=["deterministic", "claude-cli", "selective-claude"], default="deterministic", help="Extractor backend to use")
    parser.add_argument("--claude-config-dir", action="append", default=[], help="CLAUDE_CONFIG_DIR to try for Claude-backed extractors; repeat for fallbacks")
    parser.add_argument("--claude-model", default="sonnet", help="Claude CLI model alias for Claude-backed extractors")
    parser.add_argument("--claude-timeout", type=int, default=180, help="Claude CLI timeout in seconds")
    parser.add_argument("--claude-include", action="append", default=[], help="Glob pattern selecting files for --extractor selective-claude; repeat for multiple subsets")
    parser.add_argument("--claude-limit", type=int, help="Maximum number of files to send to Claude in --extractor selective-claude")
    parser.add_argument("--canonicalize", action="store_true", help="Merge high-confidence aliases and produce review candidates for ambiguous duplicates")
    parser.add_argument("--review-output", help="Write canonicalization review queue JSON to this path")
    parser.add_argument("--review-markdown-output", help="Write a human-readable markdown review queue")
    parser.add_argument("--review-jsonl-output", help="Write review queue items as JSONL")
    parser.add_argument("--review-decisions-template", help="Write a starter review decisions JSON template")
    parser.add_argument("--apply-review-decisions", help="Apply review decisions JSON after canonicalization; implies --canonicalize")
    parser.add_argument("--project-markdown", help="Write a human-readable markdown projection of the final graph to this directory")
    parser.add_argument("--sqlite-output", help="Persist the final graph to a local SQLite database")
    parser.add_argument("--kuzu-output", help="Persist the final graph to a local Kuzu database")
    parser.add_argument("--cognee-output", help="Write a Cognee-friendly JSONL export bundle to this directory")
    parser.add_argument("--cognee-add", action="store_true", help="Add the generated --cognee-output bundle to Cognee without running cognify")
    parser.add_argument("--cognee-cognify", action="store_true", help="After --cognee-add, run Cognee cognify for the dataset; may invoke configured LLM/embedding providers")
    parser.add_argument("--cognee-codex-cognify", action="store_true", help="Run Cognee cognify with Cognee's LLM client patched to Codex CLI/OAuth")
    parser.add_argument("--cognee-codex-model", default="gpt-5.4", help="Codex CLI model for --cognee-codex-cognify")
    parser.add_argument("--cognee-codex-timeout", type=int, default=300, help="Timeout per Codex CLI structured call")
    parser.add_argument("--cognee-local-embedding-dimensions", type=int, default=128, help="Embedding dimensions for --cognee-codex-cognify; qwen3-embedding:0.6b uses 1024")
    parser.add_argument("--cognee-embedding-provider", choices=["deterministic", "ollama"], default="deterministic", help="Embedding provider for --cognee-codex-cognify")
    parser.add_argument("--cognee-ollama-embedding-model", default="qwen3-embedding:0.6b", help="Ollama embedding model for --cognee-embedding-provider ollama")
    parser.add_argument("--cognee-ollama-embedding-endpoint", default="http://127.0.0.1:11434/api/embed", help="Ollama /api/embed endpoint for Cognee embeddings")
    parser.add_argument("--cognee-ollama-embedding-timeout", type=int, default=120, help="Ollama embedding request timeout in seconds")
    parser.add_argument("--cognee-dataset", default="llm_wiki_research_graph", help="Cognee dataset name for --cognee-add")
    parser.add_argument("--cognee-system-root", help="Optional isolated Cognee system root directory, useful when changing vector dimensions")
    parser.add_argument("--cognee-data-root", help="Optional isolated Cognee data root directory")
    parser.add_argument("--batch-manifest", help="Track file hashes for incremental changed-only batch ingestion")
    parser.add_argument("--changed-only", action="store_true", help="When used with --batch-manifest, skip files whose content hash is unchanged")
    parser.add_argument("--limit", type=int, help="Maximum number of files to process in this run")
    parser.add_argument("--report-output", help="Write a markdown summary report for the final graph")
    args = parser.parse_args(argv)

    if args.extractor == "claude-cli":
        extractor = ClaudeCLIResearchExtractor(
            config_dirs=args.claude_config_dir or ["/Users/neo/.claude-personal1", "/Users/neo/.claude-personal2"],
            model=args.claude_model,
            timeout=args.claude_timeout,
        )
    elif args.extractor == "selective-claude":
        deterministic = ResearchGraphExtractor()
        claude = ClaudeCLIResearchExtractor(
            config_dirs=args.claude_config_dir or ["/Users/neo/.claude-personal1", "/Users/neo/.claude-personal2"],
            model=args.claude_model,
            timeout=args.claude_timeout,
        )
        extractor = SelectiveClaudeResearchExtractor(
            deterministic=deterministic,
            claude=claude,
            include_patterns=args.claude_include,
            claude_limit=args.claude_limit,
        )
    else:
        extractor = ResearchGraphExtractor()
    graphs = []
    markdown_files = []
    for raw_path in args.paths:
        markdown_files.extend(iter_markdown_files(Path(raw_path)))
    if args.batch_manifest:
        batch = BatchIngestRunner(extractor=extractor, manifest_path=Path(args.batch_manifest)).run(
            markdown_files,
            source_kind=args.source_kind,
            changed_only=args.changed_only,
            limit=args.limit,
        )
        graphs = batch.graphs or [batch.graph]
    else:
        if args.limit is not None:
            markdown_files = markdown_files[: args.limit]
        for md in markdown_files:
            graphs.append(extractor.extract_file(md, source_kind=args.source_kind))

    graph = merge_graphs(graphs)
    if args.trends:
        graph = ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=args.min_trend_sources)
    if args.canonicalize or args.review_output or args.apply_review_decisions or args.review_markdown_output or args.review_jsonl_output or args.review_decisions_template:
        canonicalization = GraphCanonicalizer().canonicalize(graph)
        graph = canonicalization.graph
        if args.apply_review_decisions:
            decisions = load_review_decisions(Path(args.apply_review_decisions))
            graph = canonicalization.review_queue().apply_decisions(graph, decisions)
        review_queue = canonicalization.review_queue()
        if args.review_output:
            review_payload = review_queue.model_dump()
            Path(args.review_output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.review_output).write_text(json.dumps(review_payload, ensure_ascii=False, indent=2 if args.pretty else None) + "\n", encoding="utf-8")
        if args.review_markdown_output or args.review_jsonl_output or args.review_decisions_template:
            ReviewQueueExporter().write_files(
                review_queue,
                markdown_path=args.review_markdown_output,
                jsonl_path=args.review_jsonl_output,
                decision_template_path=args.review_decisions_template,
            )
    if args.project_markdown:
        GraphMarkdownProjector().write_projection(graph, Path(args.project_markdown))
    if args.sqlite_output:
        SQLiteResearchGraphStore(Path(args.sqlite_output)).write_graph(graph, replace=True)
    if args.kuzu_output:
        KuzuResearchGraphStore(Path(args.kuzu_output)).write_graph(graph, replace=True)
    if args.cognee_output:
        CogneeResearchGraphAdapter().write_bundle(graph, Path(args.cognee_output))
        if args.cognee_codex_cognify:
            with CogneeCodexPatch(
                model=args.cognee_codex_model,
                timeout=args.cognee_codex_timeout,
                deterministic_embeddings=args.cognee_embedding_provider == "deterministic",
                ollama_embeddings=args.cognee_embedding_provider == "ollama",
                ollama_model=args.cognee_ollama_embedding_model,
                ollama_endpoint=args.cognee_ollama_embedding_endpoint,
                ollama_timeout=args.cognee_ollama_embedding_timeout,
                embedding_dimensions=args.cognee_local_embedding_dimensions,
            ):
                asyncio.run(CogneeDirectImporter().add_bundle(
                    Path(args.cognee_output),
                    dataset_name=args.cognee_dataset,
                    cognify=True,
                    system_root=args.cognee_system_root,
                    data_root=args.cognee_data_root,
                ))
        elif args.cognee_add or args.cognee_cognify:
            asyncio.run(CogneeDirectImporter().add_bundle(
                Path(args.cognee_output),
                dataset_name=args.cognee_dataset,
                cognify=args.cognee_cognify,
                system_root=args.cognee_system_root,
                data_root=args.cognee_data_root,
            ))
    if args.report_output:
        report = GraphReporter().render_markdown(GraphReporter().summarize(graph))
        Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_output).write_text(report, encoding="utf-8")
    payload = graph.to_json(indent=2 if args.pretty else None)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
