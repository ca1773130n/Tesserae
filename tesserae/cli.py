"""CLI for Tesserae research graph extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional

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
from .project import CognifyOptions, ProjectWiki, SessionExtractionOptions, cognify_options_from_config, cognee_backend_config, iter_markdown_files, load_graph_file as _load_graph_file
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
    from .query import ask_project

    wiki = ProjectWiki.load(args.project)
    try:
        envelope = ask_project(
            wiki,
            args.question,
            backend=args.backend,
            top_k=args.top_k,
            cognee_search_type=args.cognee_search_type,
            cognee_dataset=args.cognee_dataset,
        )
    except RuntimeError as exc:
        # Backend-specific failures with explicit --backend surface here.
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ask failed: {exc}", file=sys.stderr)
        return 2

    return _emit_ask_envelope(envelope, json_output=bool(args.json_output))


def _emit_ask_envelope(envelope: dict, *, json_output: bool) -> int:
    """Print an ``ask_project`` envelope in human or JSON form.

    Shared by ``project ask`` and the new top-level ``ask`` command so output
    formatting stays in lockstep with the dispatcher's contract.
    """

    if json_output:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    backend = envelope.get("backend")
    if backend == "raganything":
        answer = envelope.get("answer")
        if answer is None:
            note = envelope.get("note") or "no answer"
            print(f"RAG-Anything backend returned no answer ({note}).", file=sys.stderr)
            return 2
        print("RAG-Anything answer:")
        print(answer)
        return 0
    if backend == "cognee":
        dataset = envelope.get("dataset")
        results = envelope.get("results") or []
        print(f"Cognee answer (dataset={dataset or 'default'}):")
        if results:
            for idx, result in enumerate(results, start=1):
                print(f"\n[{idx}] {result}")
        else:
            print("No Cognee results returned.")
        return 0
    if backend == "wiki":
        print("Compiled wiki answer:")
        from .query import QueryHit, QueryResult

        hits = [
            QueryHit(
                title=hit.get("title", ""),
                kind=hit.get("kind", ""),
                href=hit.get("href", ""),
                score=float(hit.get("score") or 0.0),
                excerpt=hit.get("excerpt", ""),
                page_path=Path(hit["page_path"]) if hit.get("page_path") else None,
                node_id=hit.get("node_id"),
                arxiv_id=hit.get("arxiv_id"),
            )
            for hit in envelope.get("hits") or []
        ]
        synthetic = QueryResult(
            question=envelope.get("question", ""),
            hits=hits,
            answer=envelope.get("answer"),
            model=envelope.get("model"),
            used_llm=bool(envelope.get("used_llm")),
            fallback_reason=envelope.get("fallback_reason"),
        )
        _print_query_result(synthetic)
        return 0

    print(envelope)
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
    print("Tesserae query REPL — blank line or EOF exits.")
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


def _top_level_ask_handler(args) -> int:
    """Resolve a project via --project/--wiki/active and call the shared ask dispatcher.

    Project resolution order (highest priority first):
      1. ``--project <path>`` — direct path (no registry lookup).
      2. ``--wiki <name>`` — look up the registered alias.
      3. The registry's currently active project.

    Bet B2 — ``--scope all-registered`` fans out across every registered
    project instead of just the one resolved above. The single-project
    path is kept as the default so existing call sites are unchanged.
    """

    from .mcp_server import ProjectRegistry
    from .query import ask_project

    # B2 — multi-project scope. We dispatch through the same ask_project
    # helper for each registered project, then aggregate the envelopes
    # under one top-level wrapper so JSON consumers can iterate the
    # ``by_project`` map. ``current`` (default) keeps the legacy
    # single-project behaviour byte-for-byte.
    scope = getattr(args, "scope", "current") or "current"
    if scope == "all-registered":
        return _top_level_ask_scope_all_registered(args)

    project_root: Optional[Path] = None
    source: str = ""

    if args.project:
        project_root = Path(args.project).expanduser().resolve()
        source = f"--project {project_root}"
    elif args.wiki:
        registry = ProjectRegistry()
        data = registry.load()
        entry = (data.get("projects") or {}).get(args.wiki)
        if not entry:
            print(
                f"No registered project named '{args.wiki}'. "
                f"Run `tesserae wiki list` to see available names, or "
                f"`tesserae wiki register <path> --name {args.wiki}` to register one.",
                file=sys.stderr,
            )
            return 2
        if entry.get("root"):
            project_root = Path(entry["root"]).resolve()
        else:
            gp = Path(entry["graph_path"]).resolve()
            project_root = gp.parent.parent if gp.parent.name == ".tesserae" else gp.parent
        source = f"--wiki {args.wiki}"
    else:
        registry = ProjectRegistry()
        data = registry.load()
        active = data.get("active")
        if not active:
            print(
                "No project specified and no active project in the registry. "
                "Use `tesserae ask --wiki <name>`, `tesserae ask --project <path>`, "
                "or `tesserae wiki activate <name>`.",
                file=sys.stderr,
            )
            return 2
        entry = (data.get("projects") or {}).get(active) or {}
        if entry.get("root"):
            project_root = Path(entry["root"]).resolve()
        elif entry.get("graph_path"):
            gp = Path(entry["graph_path"]).resolve()
            project_root = gp.parent.parent if gp.parent.name == ".tesserae" else gp.parent
        if project_root is None:
            print(
                f"Active project '{active}' has no recorded root; re-register it.",
                file=sys.stderr,
            )
            return 2
        source = f"active project '{active}'"

    try:
        wiki = ProjectWiki.load(project_root)
    except FileNotFoundError:
        print(
            f"No Tesserae project at {project_root} (resolved from {source}). "
            f"Did you run `tesserae project setup` there?",
            file=sys.stderr,
        )
        return 2

    try:
        envelope = ask_project(
            wiki,
            args.question,
            backend=args.backend,
            top_k=args.top_k,
            cognee_search_type=args.cognee_search_type,
            cognee_dataset=args.cognee_dataset,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ask failed: {exc}", file=sys.stderr)
        return 2

    return _emit_ask_envelope(envelope, json_output=bool(args.json_output))


def _top_level_ask_scope_all_registered(args) -> int:
    """B2 — fan out the question across every registered project.

    Aggregates each project's :func:`ask_project` envelope into a single
    ``{"scope": "all-registered", "question": ..., "by_project": {...}}``
    payload. Failures in one project never abort the run — they're
    captured as ``{"error": "..."}`` entries so the aggregate view stays
    legible. Supports an optional ``--scope-aliases`` filter to restrict
    to a hand-picked subset of the registry.
    """

    from .mcp_server import ProjectRegistry
    from .query import ask_project

    registry = ProjectRegistry()
    data = registry.list_projects()
    all_projects: List[dict] = list(data.get("projects") or [])
    if not all_projects:
        print(
            "No projects registered. Use `tesserae wiki register <path> --name <alias>` first.",
            file=sys.stderr,
        )
        return 2

    requested = list(getattr(args, "scope_aliases", None) or [])
    if requested:
        wanted = {str(a) for a in requested}
        all_projects = [p for p in all_projects if p.get("name") in wanted]
        missing = wanted - {p.get("name") for p in all_projects}
        if missing:
            print(
                f"Unknown scope alias(es): {sorted(missing)}. "
                f"Use `tesserae wiki list` to see registered projects.",
                file=sys.stderr,
            )
            return 2

    by_project: Dict[str, dict] = {}
    for entry in all_projects:
        name = entry.get("name") or ""
        root_str = entry.get("root")
        if not root_str:
            gp = Path(entry.get("graph_path") or "").resolve()
            project_root = gp.parent.parent if gp.parent.name == ".tesserae" else gp.parent
        else:
            project_root = Path(root_str).resolve()
        try:
            wiki = ProjectWiki.load(project_root)
        except Exception as exc:
            by_project[name] = {"error": f"could not load project: {exc}"}
            continue
        try:
            envelope = ask_project(
                wiki,
                args.question,
                backend=args.backend,
                top_k=args.top_k,
                cognee_search_type=args.cognee_search_type,
                cognee_dataset=args.cognee_dataset,
            )
            by_project[name] = envelope
        except RuntimeError as exc:
            by_project[name] = {"error": str(exc)}
        except Exception as exc:
            by_project[name] = {"error": f"ask failed: {exc}"}

    aggregate = {
        "scope": "all-registered",
        "question": args.question,
        "by_project": by_project,
    }

    if bool(args.json_output):
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
        return 0

    # Human-readable rendering: one section per project, using the same
    # ``_emit_ask_envelope`` helper for individual envelopes so the
    # backend-specific formatting stays consistent with single-project
    # ``ask``. Each section is preceded by a banner so the user can
    # tell whose answer came from where.
    print(f"All-registered scope · question: {args.question!r}")
    for name in sorted(by_project.keys()):
        envelope = by_project[name]
        print()
        print(f"=== {name} ===")
        if isinstance(envelope, dict) and "error" in envelope:
            print(f"(error: {envelope['error']})")
            continue
        # _emit_ask_envelope prints to stdout; ignore its return code
        # since aggregation success doesn't depend on any single
        # project's envelope rendering.
        _emit_ask_envelope(envelope, json_output=False)
    return 0


def _wiki_command_handler(args) -> int:
    """Manage the persistent multi-project registry from the top-level CLI."""

    from .mcp_server import ProjectRegistry

    registry = ProjectRegistry()
    sub = args.wiki_command

    if sub == "list":
        data = registry.list_projects()
        if getattr(args, "wiki_list_json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        active = data.get("active")
        projects = data.get("projects") or []
        if not projects:
            print("No projects registered. Use `tesserae wiki register <path> --name <alias>`.")
            return 0
        print(f"Active: {active or '(none)'}")
        for entry in projects:
            marker = "*" if entry.get("name") == active else " "
            print(f" {marker} {entry.get('name', ''):<24} {entry.get('root', '')}")
        return 0

    if sub == "register":
        try:
            entry = registry.register(args.path, name=args.name)
        except Exception as exc:
            print(f"register failed: {exc}", file=sys.stderr)
            return 2
        print(f"Registered '{entry['name']}' -> {entry['root']}")
        if getattr(args, "activate", False):
            try:
                registry.activate(entry["name"])
            except Exception as exc:
                print(f"activate failed: {exc}", file=sys.stderr)
                return 2
            print(f"Active: {entry['name']}")
        return 0

    if sub == "activate":
        try:
            entry = registry.activate(args.name)
        except Exception as exc:
            print(f"activate failed: {exc}", file=sys.stderr)
            return 2
        print(f"Active: {entry['name']} -> {entry['root']}")
        return 0

    if sub == "unregister":
        try:
            registry.unregister(args.name)
        except Exception as exc:
            print(f"unregister failed: {exc}", file=sys.stderr)
            return 2
        print(f"Unregistered: {args.name}")
        return 0

    if sub == "obsidian-set-root":
        if args.clear:
            registry.set_vault_root(None)
            print("Cleared registry obsidian.vault_root.")
            return 0
        if not args.path:
            current = registry.get_vault_root()
            print(f"Current obsidian.vault_root: {current or '(unset)'}")
            print("Pass a path to set, or --clear to unset.")
            return 0
        from pathlib import Path as _Path
        resolved = _Path(args.path).expanduser()
        if not resolved.is_absolute():
            resolved = resolved.resolve()
        if not resolved.parent.is_dir():
            print(f"error: parent dir does not exist: {resolved.parent}", file=sys.stderr)
            return 2
        resolved.mkdir(parents=True, exist_ok=True)
        registry.set_vault_root(str(resolved))
        print(f"Set registry obsidian.vault_root = {resolved}")
        print("Each registered project now projects into:")
        for alias, root in registry.iter_registered_projects():
            print(f"  {alias:<24} -> {resolved / alias}")
        return 0

    if sub == "obsidian-sync-all":
        return _wiki_obsidian_sync_all(
            registry,
            poll_interval=args.poll_interval,
            prune_orphans=args.prune_orphans,
            force_prune_with_notes=args.force_prune_with_notes,
            no_watch=args.no_watch,
        )

    print(
        "Usage: tesserae wiki {list|register|activate|unregister|obsidian-set-root|obsidian-sync-all}",
        file=sys.stderr,
    )
    return 2


def _wiki_obsidian_sync_all(
    registry,
    *,
    poll_interval: float,
    prune_orphans: bool = False,
    force_prune_with_notes: bool = False,
    no_watch: bool = False,
) -> int:
    """Spawn one VaultWatcher thread per registered project.

    Each thread owns its own ProjectWiki + VaultWatcher and polls only its
    own vault subdir. Ctrl-C cleanly signals all threads to stop.

    When ``prune_orphans`` is set, every project's vault is swept for
    stale projected pages (node_id no longer in that project's graph)
    BEFORE the watchers start. This handles the case where a previous
    compile shrank the source set and left orphan pages behind in the
    vault — the projector overwrites but never deletes.
    """
    import threading
    from .project import ProjectWiki, load_graph_file
    from .vault_pull import prune_orphan_pages
    from .vault_snapshot import write_snapshot
    from .vault_watch import VaultWatcher

    projects = list(registry.iter_registered_projects())
    if not projects:
        print("No registered projects. Use `tesserae wiki register <path>` first.", file=sys.stderr)
        return 2

    vault_root = registry.get_vault_root()
    if vault_root is None:
        print("error: no registry vault root. Run `tesserae wiki obsidian-set-root <PATH>` first.", file=sys.stderr)
        return 2

    if prune_orphans:
        total_deleted = 0
        total_skipped = 0
        for alias, root in projects:
            try:
                wiki = ProjectWiki.load(str(root))
            except Exception as exc:
                print(f"[{alias}] skip prune: {exc}", file=sys.stderr)
                continue
            vault = wiki.effective_obsidian_vault()
            if not wiki.paths.graph.is_file():
                print(f"[{alias}] no graph yet; skip prune")
                continue
            graph = load_graph_file(wiki.paths.graph)
            result = prune_orphan_pages(vault, graph, force=force_prune_with_notes)
            total_deleted += len(result.deleted)
            total_skipped += len(result.skipped_with_user_notes)
            note = ""
            if result.skipped_with_user_notes:
                note = f", {len(result.skipped_with_user_notes)} kept-with-notes"
            print(f"[{alias}] pruned {len(result.deleted)} orphan(s){note}")
            # Refresh snapshot so subsequent watcher doesn't replay the deletes
            write_snapshot(graph.nodes, wiki.paths.vault_snapshot)
        print(f"total: {total_deleted} deleted, {total_skipped} kept-with-notes across {len(projects)} project(s)")
        if no_watch:
            return 0
    elif no_watch:
        print("error: --no-watch is only meaningful with --prune-orphans", file=sys.stderr)
        return 2

    print(f"watching {len(projects)} registered project(s) under {vault_root}")
    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    def _watch(alias: str, root):
        try:
            wiki = ProjectWiki.load(str(root))
        except Exception as exc:
            print(f"[{alias}] could not load project: {exc}", flush=True)
            return
        watcher = VaultWatcher(wiki, poll_interval=poll_interval)
        # Run a tick at a time so we can react to the stop_event between iterations.
        try:
            while not stop_event.is_set():
                # _tick is a single poll+react cycle; sleeping inside it
                # honors the same poll_interval the watcher uses normally.
                changed = False
                try:
                    changed = watcher._tick()  # noqa: SLF001 — using internal for graceful stop
                except Exception as exc:
                    print(f"[{alias}] watcher error: {exc}", flush=True)
                if not changed:
                    # When tick returns False it already slept once (the
                    # poll); no extra wait needed.
                    pass
        except KeyboardInterrupt:
            return

    for alias, root in projects:
        t = threading.Thread(target=_watch, args=(alias, root), name=f"vault-watch:{alias}", daemon=True)
        t.start()
        threads.append(t)
        print(f"  + watching {alias}")
    print("Ctrl-C to stop all.")

    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nstopping watchers...", flush=True)
        for t in threads:
            t.join(timeout=2.0)
    return 0


def _build_top_level_ask_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae ask",
        description=(
            "Ask a question about a registered Tesserae project. Resolves the project via "
            "--project, --wiki, or the registry's active project. Dispatches through the same "
            "backend selector as `project ask` (raganything -> cognee -> wiki)."
        ),
    )
    parser.add_argument("question", help="Natural-language question text.")
    parser.add_argument("--wiki", help="Registered project name (see `tesserae wiki list`).")
    parser.add_argument("--project", help="Project root path (overrides --wiki).")
    parser.add_argument(
        "--backend",
        choices=["auto", "raganything", "cognee", "wiki"],
        default="auto",
        help="Backend to use (default: auto = raganything -> cognee -> wiki).",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Maximum results/context items (default: 5).")
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the raw JSON envelope instead of the pretty-printed answer.",
    )
    parser.add_argument(
        "--cognee-search-type",
        default=None,
        help="Cognee SearchType name when --backend cognee (e.g. INSIGHTS, CHUNKS).",
    )
    parser.add_argument(
        "--cognee-dataset",
        default=None,
        help="Override the configured Cognee dataset.",
    )
    # Bet B2 — registry-scoped fan-out.
    parser.add_argument(
        "--scope",
        choices=["current", "all-registered"],
        default="current",
        help=(
            "Query scope: 'current' (default) hits the active/named project; "
            "'all-registered' fans out across every project in the registry."
        ),
    )
    parser.add_argument(
        "--scope-aliases",
        nargs="*",
        default=None,
        help=(
            "When --scope=all-registered, optionally restrict to this list "
            "of registered alias names (e.g. --scope-aliases research work)."
        ),
    )
    return parser


def _build_top_level_wiki_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae wiki",
        description="Manage the persistent Tesserae project registry used by `ask` and the MCP server.",
    )
    subparsers = parser.add_subparsers(dest="wiki_command", required=True)

    wiki_list = subparsers.add_parser("list", help="List registered projects and show the active one.")
    wiki_list.add_argument(
        "--json",
        dest="wiki_list_json",
        action="store_true",
        help="Emit the registry payload as JSON.",
    )

    wiki_register = subparsers.add_parser(
        "register",
        help="Register a project root in the persistent registry.",
    )
    wiki_register.add_argument("path", help="Path to the project root containing .tesserae/.")
    wiki_register.add_argument("--name", help="Friendly name (defaults to the sanitized directory name).")
    wiki_register.add_argument(
        "--activate",
        action="store_true",
        help="Also set the new entry as the active project.",
    )

    wiki_activate = subparsers.add_parser("activate", help="Set a registered project as the active one.")
    wiki_activate.add_argument("name")

    wiki_unregister = subparsers.add_parser("unregister", help="Remove a project from the registry.")
    wiki_unregister.add_argument("name")

    wiki_set_root = subparsers.add_parser(
        "obsidian-set-root",
        help="Set the registry-wide Obsidian vault root. Each registered project then projects into <root>/<alias>/.",
    )
    wiki_set_root.add_argument("path", nargs="?", help="Absolute path; omit and pass --clear to unset.")
    wiki_set_root.add_argument("--clear", action="store_true", help="Remove the configured vault root.")

    wiki_watch_all = subparsers.add_parser(
        "obsidian-sync-all",
        help="Run an obsidian-sync --watch loop for every registered project (one thread per project).",
    )
    wiki_watch_all.add_argument(
        "--poll-interval", type=float, default=1.5,
        help="Per-watcher poll interval in seconds (default: 1.5).",
    )
    wiki_watch_all.add_argument(
        "--prune-orphans",
        action="store_true",
        help="Prune stale projected pages in every project's vault before starting watchers.",
    )
    wiki_watch_all.add_argument(
        "--force-prune-with-notes",
        action="store_true",
        help="With --prune-orphans, also delete orphans with user-notes content.",
    )
    wiki_watch_all.add_argument(
        "--no-watch",
        action="store_true",
        help="Run prune-only (requires --prune-orphans); skip the watch phase.",
    )
    return parser


def project_main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a per-project .tesserae workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .tesserae in a project directory")
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
    setup_parser.add_argument(
        "--raganything-llm-provider",
        choices=["codex", "claude"],
        default="codex",
        help="LLM provider for raganything runtime queries (default: codex; uses OAuth CLI, no API key)",
    )
    setup_parser.add_argument(
        "--raganything-llm-model",
        default=None,
        help="LLM model name (default: gpt-5.4 for codex; leave unset for claude default)",
    )
    setup_parser.add_argument(
        "--raganything-claude-config-dir",
        default=None,
        help="CLAUDE_CONFIG_DIR for raganything when provider=claude (supports multi-account setups like ~/.claude-personal2)",
    )
    setup_parser.add_argument(
        "--raganything-embedding",
        choices=["deterministic", "ollama"],
        default="deterministic",
        help="Embedding provider for raganything (default: deterministic, no external deps)",
    )
    setup_parser.add_argument(
        "--raganything-embedding-dim",
        type=int,
        default=768,
        help="Embedding dimensionality (default: 768)",
    )
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
    ingest_parser.add_argument("--changed-only", action="store_true", help="Skip unchanged files using .tesserae/manifest.json")
    ingest_parser.add_argument("--limit", type=int, help="Maximum number of changed files to process")
    ingest_parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes")
    ingest_parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum sources needed for Trend nodes")

    ingest_code_parser = subparsers.add_parser(
        "ingest-code",
        help="Mint a typed code graph from Python source via the stdlib ast module",
    )
    ingest_code_parser.add_argument(
        "paths",
        nargs="*",
        help="Project-relative or absolute paths to walk; defaults to the project root",
    )
    ingest_code_parser.add_argument(
        "--project",
        default=".",
        help="Project root directory; defaults to current working directory",
    )
    ingest_code_parser.add_argument(
        "--output",
        help="Override output path; defaults to <project>/.tesserae/code-graph.json",
    )
    ingest_code_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional directory basename to skip (repeatable). Adds to the built-in exclude set",
    )

    # Option-C / CodeGraph adapter. Separate subcommand (not a flag on
    # ingest-code) because the producer is fundamentally different —
    # we delegate extraction to colbymchenry/codegraph's 21-language
    # tree-sitter pipeline and only translate its SQLite store here.
    sync_code_parser = subparsers.add_parser(
        "sync-code",
        help="Translate a colbymchenry/codegraph SQLite store into .tesserae/code-graph.json",
    )
    sync_code_parser.add_argument(
        "--project",
        default=".",
        help="Project root directory; defaults to current working directory",
    )
    sync_code_parser.add_argument(
        "--db",
        help="Path to the CodeGraph SQLite database; defaults to <project>/.codegraph/codegraph.db",
    )
    sync_code_parser.add_argument(
        "--output",
        help="Override output path; defaults to <project>/.tesserae/code-graph.json",
    )
    sync_code_parser.add_argument(
        "--auto-sync",
        action="store_true",
        help="Run `codegraph sync <project>` first if the binary is on PATH; skip silently otherwise",
    )

    compile_parser = subparsers.add_parser("compile", help="Compile configured project sources into all .tesserae artifacts")
    compile_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    compile_parser.add_argument("--source-kind", help="Override configured source kind")
    compile_parser.add_argument("--changed-only", action="store_true", help="Skip unchanged files using .tesserae/manifest.json")
    compile_parser.add_argument("--limit", type=int, help="Maximum number of changed files to process")
    compile_parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes")
    compile_parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum sources needed for Trend nodes")
    compile_parser.add_argument("--include-data", action="store_true", help="Documentation flag: project_root/data is auto-included by default; this flag is a no-op kept for clarity")
    compile_parser.add_argument("--exclude-data", action="store_true", help="Skip the implicit project_root/data auto-include even if data/ exists")
    compile_parser.add_argument(
        "--no-vault-pull",
        action="store_true",
        help=(
            "Skip the Obsidian vault overlay step. By default, when a vault and a prior "
            "vault_snapshot.json both exist, compile reads user edits out of the vault "
            "and applies them on top of the typed graph. Pass this to bypass — useful "
            "for recovery, or when you intentionally want the source markdown to win."
        ),
    )
    compile_parser.add_argument("--refresh-external-tools", action="store_true", help="Run configured external tool refresh commands before compile, even if they are not marked auto_refresh")
    # --- Session graph extractor (Phase 3 wires only the structural pass; LLM in Phase 5) ----
    session_group = compile_parser.add_mutually_exclusive_group()
    session_group.add_argument("--sessions", dest="sessions_enabled", action="store_true", default=None, help="Force session graph extraction on (default if .tesserae/harness_sessions/ exists)")
    session_group.add_argument("--no-sessions", dest="sessions_enabled", action="store_false", default=None, help="Skip session graph extraction entirely")
    compile_parser.add_argument("--sessions-llm", choices=["auto", "true", "false"], default=None, help="LLM extraction mode (default 'auto' — runs when an LLM backend is configured). Honored once Phase 5 lands.")
    compile_parser.add_argument("--sessions-model", default=None, help="Override the LLM model used for session extraction (Phase 5)")
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
    compile_parser.add_argument("--cognee-dataset", default="tesserae_research_graph", help="Cognee dataset name")
    compile_parser.add_argument("--cognee-system-root", help="Optional isolated Cognee system root directory")
    compile_parser.add_argument("--cognee-data-root", help="Optional isolated Cognee data root directory")

    schema_drift_parser = subparsers.add_parser(
        "schema-drift",
        help="EDC-style pass that proposes ResearchNodeType sub-types from clustered host-type nodes.",
    )
    schema_drift_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    schema_drift_parser.add_argument(
        "--host-type",
        action="append",
        default=[],
        help=(
            "ResearchNodeType to analyze (enum value, e.g. 'SourceDocument'). "
            "Repeat to analyze multiple. Default: SourceDocument."
        ),
    )
    schema_drift_parser.add_argument("--min-volume", type=int, default=10, help="Skip host types with fewer than this many members (default: 10)")
    schema_drift_parser.add_argument("--top-k", type=int, default=5, help="Take only the top-K clusters per host type (default: 5)")
    schema_drift_parser.add_argument("--min-cluster-size", type=int, default=5, help="Drop clusters smaller than this size (default: 5)")
    schema_drift_parser.add_argument("--jaccard-threshold", type=float, default=0.34, help="Jaccard similarity threshold for clustering (default: 0.34)")

    research_parser = subparsers.add_parser(
        "research",
        help="Agentic research loop (dzhng-style): plan → search → reflect → synthesize. Mints OpenQuestion / SessionHypothesis nodes against the compiled graph.",
    )
    research_parser.add_argument("query", help="Research query to investigate")
    research_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    research_parser.add_argument("--breadth", type=int, default=3, help="Sub-questions per level (default: 3)")
    research_parser.add_argument("--depth", type=int, default=2, help="Maximum follow-up depth beyond the root (default: 2)")
    research_parser.add_argument("--max-iters", type=int, default=6, help="Hard cap on (search + reflect) iterations (default: 6)")
    research_parser.add_argument("--top-k", type=int, default=5, help="Top-K graph evidence nodes per sub-question (default: 5)")
    research_parser.add_argument("--output", help="Report output path; defaults to .tesserae/research/<slug>.md")
    research_parser.add_argument("--no-web", action="store_true", help="Disable web search even if a backend is configured (v1 default — web stays off)")

    ua_refresh_parser = subparsers.add_parser("refresh-understand-anything", help="Run Tesserae's managed Understand Anything refresh")
    ua_refresh_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    ua_refresh_parser.add_argument("--platform", default="codex", help="Agent platform to use: codex, opencode, or claude")
    ua_refresh_parser.add_argument("--full", action="store_true", help="Force /understand --full")
    ua_refresh_parser.add_argument("--force", action="store_true", help="Run even if the existing graph appears current")
    ua_refresh_parser.add_argument("--timeout", type=int, help="Optional timeout in seconds")

    obsidian_sync_parser = subparsers.add_parser(
        "obsidian-sync",
        help="Apply vault edits onto the typed graph and re-project. Pass --watch for live mode.",
    )
    obsidian_sync_parser.add_argument("--project", default=".", help="Project root; defaults to cwd")
    obsidian_sync_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Run a long-lived poll loop that re-applies the overlay every time "
            "the vault changes. Press Ctrl-C to stop."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compute the overlay diff and write .tesserae/diverged-fields.md, "
            "but DON'T apply changes to the graph or re-project. Useful for "
            "previewing what a compile would do."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.5,
        help="Watch-mode poll interval in seconds (default: 1.5).",
    )
    obsidian_sync_parser.add_argument(
        "--vault",
        type=str,
        default=None,
        help=(
            "Override the configured Obsidian vault directory for this call. "
            "Resolution order at runtime is --vault > config.obsidian.vault_path > "
            ".tesserae/obsidian_vault/. Use `project setup --obsidian-vault PATH` "
            "to make the override persistent."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--persist-vault",
        action="store_true",
        help=(
            "When passed with --vault, writes the path to .tesserae/config.json under "
            "obsidian.vault_path so future commands (`project compile`, "
            "`project obsidian-sync` without --vault) use it automatically."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help=(
            "Delete projected pages in the vault whose node_id no longer exists "
            "in the current graph. Useful after the source set shrinks "
            "(exclusions, deleted directories) — the projector only overwrites, "
            "never deletes. Files with user-notes content are kept by default; "
            "pass --force-prune-with-notes to delete those too."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--force-prune-with-notes",
        action="store_true",
        help="With --prune-orphans, also delete orphan pages that have user-notes content.",
    )

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
    query_parser.add_argument("--llm", action="store_true", help="Force the LLM path on, even if TESSERAE_QUERY_LLM is unset")
    query_parser.add_argument("--no-llm", action="store_true", help="Force the LLM path off, even if TESSERAE_QUERY_LLM=1")
    query_parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model id for --llm (default: claude-sonnet-4-6)")
    query_parser.add_argument("--json", dest="json_output", action="store_true", help="Print the structured QueryResult as JSON")
    query_parser.add_argument("--interactive", action="store_true", help="Drop into a REPL with readline history; blank line or EOF exits")

    ask_parser = subparsers.add_parser("ask", help="Ask the configured project memory backend; uses RAG-Anything or Cognee when enabled")
    ask_parser.add_argument("question", help="Question text")
    ask_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    ask_parser.add_argument("--backend", choices=["auto", "raganything", "cognee", "wiki"], default="auto", help="Question backend (default: auto; tries RAG-Anything, then Cognee, then wiki query)")
    ask_parser.add_argument("--top-k", type=int, default=8, help="Maximum results/context items")
    ask_parser.add_argument("--cognee-search-type", default="INSIGHTS", help="Cognee SearchType name, e.g. INSIGHTS, CHUNKS, SUMMARIES, GRAPH_COMPLETION")
    ask_parser.add_argument("--cognee-dataset", help="Override configured Cognee dataset")
    ask_parser.add_argument("--json", dest="json_output", action="store_true", help="Print backend/result JSON")

    mcp_parser = subparsers.add_parser("mcp-config", help="Print a Hermes mcp_servers config snippet for this project")
    mcp_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    mcp_parser.add_argument("--server-name", help="MCP server name in Hermes config")
    mcp_parser.add_argument("--pythonpath", help="PYTHONPATH pointing at the Tesserae checkout")

    export_graphiti_parser = subparsers.add_parser("export-graphiti", help="Export project graph as dependency-free Graphiti episode JSONL")
    export_graphiti_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    export_graphiti_parser.add_argument("--group-id", help="Graphiti group_id; defaults to project wiki name")
    export_graphiti_parser.add_argument("--output", help="Episode JSONL output path; defaults to .tesserae/graphiti_episodes.jsonl")

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
    harness_parser.add_argument("--output", help="Harness output directory; defaults to .tesserae/agent_harness")

    obsidian_parser = subparsers.add_parser("export-obsidian", help="Export the compiled graph as an Obsidian vault")
    obsidian_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    obsidian_parser.add_argument("--vault", help="Vault output directory; defaults to .tesserae/obsidian_vault")

    sessions_parser = subparsers.add_parser("sessions", help="Manage inbound agent harness session history")
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_import = sessions_sub.add_parser("import", help="Import normalized HarnessSession JSON files")
    sessions_import.add_argument("paths", nargs="+", help="JSON files containing one session object or a list of sessions")
    sessions_import.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    sessions_discover = sessions_sub.add_parser("discover", help="Discover local Claude Code/Codex sessions scoped to this project")
    sessions_discover.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    sessions_discover.add_argument("--root", action="append", default=[], help="Harness config root to scan; repeat for multiple roots. Defaults to auto-detected Claude/Codex config roots under HOME")
    sessions_discover.add_argument("--harness", action="append", default=[], choices=["claude-code", "codex"], help="Harness to scan; repeat for multiple harnesses. Defaults to both")
    sessions_discover.add_argument("--import", dest="import_sessions", action="store_true", help="Import discovered normalized sessions into .tesserae/harness_sessions")
    sessions_list = sessions_sub.add_parser("list", help="List normalized harness sessions for this project")
    sessions_list.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")

    site_parser = subparsers.add_parser("build-site", help="Build the static frontend site for this project wiki")
    site_parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    site_parser.add_argument("--output", help="Site output directory; defaults to .tesserae/site")

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
        print("Next: python3 -m tesserae.cli project ingest <paths>")
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
                    install_raganything=(False if args.skip_install_raganything else True if args.install_raganything else None),
                    raganything_parser=args.raganything_parser,
                    raganything_extras=args.raganything_extras,
                    run_raganything=args.run_raganything,
                    raganything_llm_provider=args.raganything_llm_provider,
                    raganything_llm_model=args.raganything_llm_model,
                    raganything_claude_config_dir=args.raganything_claude_config_dir,
                    raganything_embedding_provider=args.raganything_embedding,
                    raganything_embedding_dim=args.raganything_embedding_dim,
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
            if "raganything" in installed_ids:
                print("RAG-Anything (raganything + docling) installed/updated.")
            if failures:
                print("External tool install/refresh had warnings; setup was saved anyway.")
                for failure in failures:
                    detail = (failure.get("stderr") or failure.get("stdout") or "").strip().splitlines()
                    tail = f": {detail[-1]}" if detail else ""
                    print(f"  - {failure.get('id')}: {failure.get('command')} exited {failure.get('returncode')}{tail}")
            else:
                print(f"External tools refreshed: {len(result.ran_tools)}")
        print("Next: tesserae project compile && tesserae project build-site")
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
    if args.command == "ingest-code":
        # Defer the import so the rest of the CLI does not pay the cost
        # of pulling in ast / pathlib walkers when they're not needed.
        from .code_graph_extractor import CodeGraphExtractor, DEFAULT_EXCLUDES, write_code_graph

        project_root = Path(args.project).resolve()
        excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
        extractor = CodeGraphExtractor(project_root, excludes=excludes)
        result = extractor.extract(args.paths or None)
        output = Path(args.output) if args.output else (project_root / ".tesserae" / "code-graph.json")
        write_code_graph(result.graph, output)
        print(
            "Ingested code graph: "
            f"processed={result.processed_files} skipped_dirs={result.skipped_dirs} "
            f"nodes={result.nodes} edges={result.edges}"
        )
        print(f"Graph: {output}")
        return 0
    if args.command == "sync-code":
        from .code_graph_adapter import (
            CodeGraphAdapter,
            _default_codegraph_db,
            _run_codegraph_sync,
            write_code_graph_from_codegraph,
        )

        project_root = Path(args.project).resolve()
        db_path = Path(args.db).resolve() if args.db else _default_codegraph_db(project_root)
        if args.auto_sync:
            _run_codegraph_sync(project_root)
        adapter = CodeGraphAdapter(db_path, project_root=project_root)
        if not adapter.available():
            print(
                f"CodeGraph database not found at {db_path}.\n"
                "Install CodeGraph and initialize it in this project:\n"
                f"  npx @colbymchenry/codegraph init -i {project_root}\n"
                "Then re-run `tesserae project sync-code` (optionally with --auto-sync).",
                file=sys.stderr,
            )
            return 2
        output = Path(args.output) if args.output else (project_root / ".tesserae" / "code-graph.json")
        result = write_code_graph_from_codegraph(db_path, output, project_root=project_root)
        print(
            "Synced code graph from CodeGraph: "
            f"nodes={result.nodes} edges={result.edges} "
            f"files={result.processed_files} languages={result.languages}"
        )
        print(f"Graph: {output}")
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
        # Build a SessionExtractionOptions override when any --sessions* CLI
        # flag was passed. None means "no override — read from config", which
        # is what _merge_session_graph does by default.
        session_override = None
        if (
            args.sessions_enabled is not None
            or args.sessions_llm is not None
            or args.sessions_model is not None
        ):
            cfg_sessions = wiki.config().get("sessions") if wiki.paths.config.exists() else {}
            base = cfg_sessions if isinstance(cfg_sessions, dict) else {}
            session_override = SessionExtractionOptions(
                enabled=(
                    args.sessions_enabled
                    if args.sessions_enabled is not None
                    else bool(base.get("enabled", True))
                ),
                llm_enabled=(
                    args.sessions_llm
                    if args.sessions_llm is not None
                    else str(base.get("llm_enabled", "auto")).lower()
                ),
                max_turns_per_chunk=int(base.get("max_turns_per_chunk", 30)),
                max_tokens_per_call=int(base.get("max_tokens_per_call", 30000)),
                model=(
                    args.sessions_model
                    if args.sessions_model is not None
                    else (base.get("model") or None)
                ),
                include_doc_id_context=int(base.get("include_doc_id_context", 200)),
            )
        result = wiki.compile(
            source_kind=args.source_kind,
            changed_only=args.changed_only,
            limit=args.limit,
            trends=args.trends,
            min_trend_sources=args.min_trend_sources,
            exclude_data=args.exclude_data,
            cognify=cognify_options if (cognify_options and cognify_options.is_active) else None,
            vault_pull=not args.no_vault_pull,
            session_options=session_override,
        )
        print(
            "Compiled project wiki: "
            f"processed={result['processed_files']} skipped={result['skipped_files']} "
            f"nodes={result['node_count']} edges={result['edge_count']}"
        )
        print(f"Graph: {result['graph_path']}")
        return 0
    if args.command == "schema-drift":
        wiki = ProjectWiki.load(args.project)
        if not wiki.paths.graph.exists():
            print("error: no compiled graph yet — run `project compile` first.", file=sys.stderr)
            return 2
        from .research_graph import ResearchNodeType as _ResearchNodeType
        from .schema_drift import analyze_schema_drift
        from .llm_json import build_default_json_client
        host_args = args.host_type or ["SourceDocument"]
        try:
            host_types = [_ResearchNodeType(value) for value in host_args]
        except ValueError as exc:
            print(f"error: unknown --host-type: {exc}", file=sys.stderr)
            return 2
        llm = build_default_json_client()
        if llm is None:
            print(
                "error: no LLM backend configured (claude CLI or ANTHROPIC_API_KEY required).",
                file=sys.stderr,
            )
            return 2
        graph = _load_graph_file(wiki.paths.graph)
        report_path, reports = analyze_schema_drift(
            graph,
            tesserae_dir=wiki.root,
            llm=llm,
            host_types=host_types,
            min_volume=args.min_volume,
            top_k_clusters=args.top_k,
            min_cluster_size=args.min_cluster_size,
            jaccard_threshold=args.jaccard_threshold,
        )
        candidate_count = sum(
            len(proposals) for r in reports for _cluster, proposals in r.clusters
        )
        print(
            f"{len(reports)} type families analyzed; "
            f"{candidate_count} candidate subtypes proposed; "
            f"report at {report_path}"
        )
        return 0
    if args.command == "research":
        from .llm_json import build_default_json_client
        from .mcp_server import LLMWikiMCPServer
        from .research_mode import GraphSearchBackend, ResearchSession

        wiki = ProjectWiki.load(args.project)
        if not wiki.paths.graph.exists():
            print("error: no compiled graph yet — run `project compile` first.", file=sys.stderr)
            return 2
        llm = build_default_json_client()
        if llm is None:
            print(
                "error: no LLM backend configured (claude CLI or ANTHROPIC_API_KEY required).",
                file=sys.stderr,
            )
            return 2
        graph = _load_graph_file(wiki.paths.graph)
        server = LLMWikiMCPServer(default_graph_path=wiki.paths.graph)
        backend = GraphSearchBackend(server=server, graph=graph)
        output_path = Path(args.output) if args.output else None
        output_dir = output_path.parent if output_path else (wiki.root / "research")
        # punt: web disabled by default in v1 — wiring a stdlib DuckDuckGo
        # scraper is finicky to test deterministically and adds zero value
        # without a real BeautifulSoup-style HTML parser. --no-web is a
        # forward-compat knob for the day a WebFetcher backend ships.
        session = ResearchSession(
            query=args.query,
            llm=llm,
            search=backend,
            output_dir=output_dir,
            breadth=args.breadth,
            depth=args.depth,
            max_iters=args.max_iters,
            top_k_evidence=args.top_k,
            web=None,
            # codex PR #16 P2 fix — merge the minted research slice
            # (Question/Hypothesis/SourceDoc nodes + derived_from/
            # references edges) into the project's live graph.json so
            # subsequent compiles / MCP ``ask`` calls can recover the
            # research thread.
            graph_path=wiki.paths.graph,
        )
        # codex PR #16 P3 fix — when --output is a custom path, write
        # ONLY there. Previously session.run() wrote the slug-named
        # report into output_dir AND the CLI wrote the custom path,
        # leaving a stale extra file (especially visible for relative
        # outputs like --output report.md which spilled the slug copy
        # into the current working directory).
        if output_path is not None:
            session.output_dir = output_path.parent
            # Replace the in-session slug-based filename with the
            # caller-chosen one by routing the slug through a temp
            # rename after run() — simpler: run() writes to its own
            # path, then we move it to output_path and ensure no
            # duplicate at the slug path remains.
        report = session.run()
        final_path = report.report_path
        if output_path is not None and output_path != report.report_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report.report_text.rstrip() + "\n", encoding="utf-8")
            # Remove the slug-named duplicate the session just wrote.
            try:
                report.report_path.unlink()
            except OSError:
                pass
            final_path = output_path
        merged_note = f" merged_into={report.merged_into}" if report.merged_into else ""
        print(
            f"report={final_path} questions={report.questions} "
            f"hypotheses={report.hypotheses} sources={report.sources} edges={report.edges}"
            f"{merged_note}"
        )
        return 0
    if args.command == "refresh-understand-anything":
        return refresh_understand_anything(
            args.project,
            platform=args.platform,
            full=args.full,
            force=args.force,
            timeout=args.timeout,
        )
    if args.command == "obsidian-sync":
        wiki = ProjectWiki.load(args.project)
        if args.dry_run and args.watch:
            print("error: --dry-run and --watch are mutually exclusive", file=sys.stderr)
            return 2
        if args.vault:
            from pathlib import Path as _Path
            vault_path = _Path(args.vault).expanduser()
            if not vault_path.is_absolute():
                vault_path = (wiki.project_root / vault_path).resolve()
            if not vault_path.is_dir():
                print(f"error: --vault path is not a directory: {vault_path}", file=sys.stderr)
                return 2
            wiki.set_vault_override(vault_path)
            if args.persist_vault:
                import json as _json
                cfg = wiki.config() if wiki.paths.config.is_file() else {}
                cfg.setdefault("obsidian", {})["vault_path"] = str(vault_path)
                wiki.paths.config.write_text(
                    _json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                print(f"saved obsidian.vault_path = {vault_path} to {wiki.paths.config}")
        elif args.persist_vault:
            print("error: --persist-vault requires --vault", file=sys.stderr)
            return 2
        if args.dry_run:
            # Compute overlay + write the diverged-fields report but skip the
            # apply step. Loads the existing graph; never re-projects.
            from .markdown_projection import unique_slugs
            from .vault_pull import (
                compute_overrides,
                compute_user_link_changes,
                write_diverged_fields_report,
            )
            from .vault_snapshot import read_snapshot
            if not wiki.paths.graph.is_file():
                print("error: no compiled graph yet — run `project compile` first.", file=sys.stderr)
                return 2
            graph = _load_graph_file(wiki.paths.graph)
            snap = read_snapshot(wiki.paths.vault_snapshot)
            overrides = (
                compute_overrides(wiki.effective_obsidian_vault(), snap, {n.id: n for n in graph.nodes})
                if snap is not None else []
            )
            link_changes = compute_user_link_changes(
                wiki.effective_obsidian_vault(), graph, unique_slugs(graph.nodes),
            )
            write_diverged_fields_report(overrides, wiki.paths.diverged_fields, link_changes)
            print(
                f"dry-run: {len(overrides)} field override(s), "
                f"{len(link_changes)} user-link change(s). "
                f"See {wiki.paths.diverged_fields.relative_to(wiki.project_root)}."
            )
            return 0
        if args.prune_orphans:
            from .vault_pull import prune_orphan_pages
            graph = _load_graph_file(wiki.paths.graph)
            vault = wiki.effective_obsidian_vault()
            result = prune_orphan_pages(vault, graph, force=args.force_prune_with_notes)
            print(
                f"pruned {len(result.deleted)} orphan page(s), "
                f"removed {len(result.removed_empty_dirs)} empty dir(s)"
            )
            if result.skipped_with_user_notes:
                print(
                    f"  ⚠ kept {len(result.skipped_with_user_notes)} orphan(s) with "
                    f"user-notes content (re-run with --force-prune-with-notes to delete)"
                )
                for p in result.skipped_with_user_notes[:5]:
                    print(f"    - {p.relative_to(vault)}")
                if len(result.skipped_with_user_notes) > 5:
                    print(f"    ... and {len(result.skipped_with_user_notes) - 5} more")
            # Refresh snapshot so subsequent watcher/sync doesn't flag the
            # deletions as "user removed file" overrides.
            from .vault_snapshot import write_snapshot
            write_snapshot(graph.nodes, wiki.paths.vault_snapshot)
            if not args.watch:
                return 0
        if args.watch:
            from .vault_watch import VaultWatcher
            VaultWatcher(wiki, poll_interval=args.poll_interval).run()
            return 0
        # No flag: one-shot apply (same as a compile, but skipping extraction).
        result = wiki.reproject_after_vault_change()
        print(
            f"applied: {result.overrides_applied} override(s), "
            f"{result.user_link_changes_applied} user-link change(s), "
            f"{result.stubs_minted} Stub node(s) minted."
        )
        return 0

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
        import socketserver
        from .serve import build_ask_aware_handler

        handler_cls = build_ask_aware_handler(project_root=Path(args.project).resolve())
        handler = partial(handler_cls, directory=str(wiki.paths.site))

        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            with ReusableTCPServer((args.host, args.port), handler) as httpd:
                print(f"Serving frontend site: {wiki.paths.site} at {url}")
                print(f"  ask endpoint: {url}api/ask (POST)")
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
    if argv and argv[0] == "ask":
        ask_parser = _build_top_level_ask_parser()
        ask_args = ask_parser.parse_args(argv[1:])
        return _top_level_ask_handler(ask_args)
    if argv and argv[0] == "wiki":
        wiki_parser = _build_top_level_wiki_parser()
        wiki_args = wiki_parser.parse_args(argv[1:])
        return _wiki_command_handler(wiki_args)
    parser = argparse.ArgumentParser(description="Extract a typed research intelligence graph from Tesserae notes.")
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
    parser.add_argument("--cognee-dataset", default="tesserae_research_graph", help="Cognee dataset name for --cognee-add")
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
