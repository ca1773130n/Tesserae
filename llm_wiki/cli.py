"""CLI for LLM-Wiki research graph extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Iterable, List

from .batch import BatchIngestRunner
from .canonicalization import GraphCanonicalizer, ReviewDecision
from .cognee_adapter import CogneeResearchGraphAdapter
from .cognee_codex import CogneeCodexPatch
from .cognee_direct import CogneeDirectImporter
from .llm_extractor import ClaudeCLIResearchExtractor
from .markdown_projection import GraphMarkdownProjector
from .persistence import KuzuResearchGraphStore, SQLiteResearchGraphStore
from .graphiti_adapter import GraphitiSyncUnavailableError
from .project import ProjectWiki
from .report import GraphReporter
from .research_graph import ResearchCorpusAnalyzer, ResearchGraph, ResearchGraphExtractor
from .review_workflow import ReviewQueueExporter
from .selective_extractor import SelectiveClaudeResearchExtractor


def iter_markdown_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() == ".md":
            yield path
        return
    for child in sorted(path.rglob("*.md")):
        if any(part.startswith(".") for part in child.relative_to(path).parts):
            continue
        yield child


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


def project_main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a per-project .llm-wiki workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .llm-wiki in a project directory")
    init_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    init_parser.add_argument("--name", help="MCP server/config name; defaults to sanitized project directory name")
    init_parser.add_argument("--source-kind", default="SourceDocument", help="Default source kind for project ingest")
    init_parser.add_argument("--source", action="append", default=[], help="Default project-relative source path for project compile; repeat for multiple paths")

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

    args = parser.parse_args(argv)
    if args.command == "init":
        wiki = ProjectWiki.init(args.project, name=args.name, source_kind=args.source_kind, sources=args.source)
        print(f"Initialized project wiki: {wiki.root}")
        print(f"Graph: {wiki.paths.graph}")
        print("Next: python3 -m llm_wiki.cli project ingest <paths>")
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
        result = wiki.compile(
            source_kind=args.source_kind,
            changed_only=args.changed_only,
            limit=args.limit,
            trends=args.trends,
            min_trend_sources=args.min_trend_sources,
        )
        print(
            "Compiled project wiki: "
            f"processed={result['processed_files']} skipped={result['skipped_files']} "
            f"nodes={result['node_count']} edges={result['edge_count']}"
        )
        print(f"Graph: {result['graph_path']}")
        return 0
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
