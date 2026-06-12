"""CLI for Tesserae research graph extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .batch import BatchIngestRunner
from .canonicalization import GraphCanonicalizer, ReviewDecision
from .cognee_adapter import CogneeResearchGraphAdapter
from .cognee_codex import CogneeCodexPatch
from .cognee_direct import CogneeDirectImporter
from .harness_sessions import HarnessSession, HarnessSessionStore, discover_harness_sessions, session_matches_project
from .ingest.orchestrator import ingest_sources
from .llm_extractor import ClaudeCLIResearchExtractor
from .locking import CompileLockHeldError
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
    """Handle the ``query`` command (one-shot or interactive REPL).

    A standalone handler so the parser-vs-handler block ordering can
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
            use_llm=bool(getattr(args, "llm", False)),
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
        notes = envelope.get("auto_notes") or []
        if notes:
            # auto fell through to wiki because a richer backend errored —
            # say so, so the user isn't left thinking wiki was the only try.
            print(f"(auto: {'; '.join(notes)} — fell back to wiki search)", file=sys.stderr)
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
                f"Run `tesserae projects list` to see available names, or "
                f"`tesserae projects register <path> --name {args.wiki}` to register one.",
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
                "or `tesserae projects activate <name>`.",
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
            f"Did you run `tesserae init` there?",
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
            use_llm=bool(getattr(args, "llm", False)),
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
            "No projects registered. Use `tesserae projects register <path> --name <alias>` first.",
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
                f"Use `tesserae projects list` to see registered projects.",
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
            print("No projects registered. Use `tesserae projects register <path> --name <alias>`.")
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
        "Usage: tesserae projects {list|register|activate|unregister|obsidian-set-root|obsidian-sync-all}",
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
        print("No registered projects. Use `tesserae projects register <path>` first.", file=sys.stderr)
        return 2

    vault_root = registry.get_vault_root()
    if vault_root is None:
        print("error: no registry vault root. Run `tesserae vault set-root <PATH>` first.", file=sys.stderr)
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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae ask \"what did we decide about the compiler?\"\n"
            "  tesserae ask \"summarize the graph schema\" --scope all-registered\n"
        ),
    )
    parser.add_argument("question", help="Natural-language question text.")
    parser.add_argument("--wiki", help="Registered project name (see `tesserae projects list`).")
    parser.add_argument("--project", help="Project root path (overrides --wiki).")
    parser.add_argument(
        "--backend",
        choices=["auto", "raganything", "cognee", "wiki"],
        default="auto",
        help="Backend to use (default: auto = raganything -> cognee -> wiki).",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Maximum results/context items (default: 5).")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Synthesize an answer with the LLM (also honored via TESSERAE_QUERY_LLM=1; "
        "requires ANTHROPIC_API_KEY). Without it, only ranked hits are returned.",
    )
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


def _add_llm_client_args(parser: argparse.ArgumentParser, persisted: bool = False) -> None:
    """Attach the synthesis-LLM backend flags (claude | codex).

    On ``init`` (``persisted=True``) the values are written into the project
    ``config.json`` (``llm_provider`` / ``llm_claude_config_dirs`` /
    ``llm_codex_home``). On ``compile`` they are per-run overrides surfaced
    as env vars (``TESSERAE_LLM_PROVIDER`` / ``CLAUDE_CONFIG_DIR`` /
    ``CODEX_HOME``) so every internal client build and extractor sees them.
    """
    suffix = " (persisted into config.json)" if persisted else " (this run only; overrides config.json)"
    parser.add_argument(
        "--llm-provider",
        choices=["claude", "codex"],
        default=None,
        help="CLI backend for the synthesis/insights LLM client" + suffix,
    )
    parser.add_argument(
        "--claude-config-dir",
        action="append",
        default=[],
        help="Claude CLI config directory (e.g. ~/.claude-personal2); repeat for fallback accounts" + suffix,
    )
    parser.add_argument(
        "--codex-home",
        default=None,
        help="Codex CLI home directory (CODEX_HOME, e.g. ~/.codex-personal1)" + suffix,
    )


def _apply_llm_cli_env(args: argparse.Namespace) -> None:
    """Surface per-run LLM backend flags as env vars.

    Env is the one channel every consumer already honors — the
    ``build_default_json_client`` factory, both CLI clients, and the
    extractor subprocesses — so flags set here win over project config
    (see ``resolve_llm_client_settings`` precedence).
    """
    import os

    if getattr(args, "llm_provider", None):
        os.environ["TESSERAE_LLM_PROVIDER"] = args.llm_provider
    claude_dirs = getattr(args, "claude_config_dir", None) or []
    if claude_dirs:
        os.environ["CLAUDE_CONFIG_DIR"] = claude_dirs[0]
    if getattr(args, "codex_home", None):
        os.environ["CODEX_HOME"] = args.codex_home


def _handle_llm_defaults(args: argparse.Namespace) -> int:
    import json as _json

    import tesserae.llm_json as _lj

    path = _lj.GLOBAL_CONFIG_PATH
    existing = _lj._load_global_llm_config()
    if args.show:
        effective = {
            "llm_provider": existing.get("llm_provider"),
            "llm_claude_config_dirs": existing.get("llm_claude_config_dirs"),
            "llm_codex_home": existing.get("llm_codex_home"),
        }
        print(f"Machine-wide LLM defaults ({path}):")
        print(_json.dumps(effective, ensure_ascii=False, indent=2))
        return 0
    if not (args.llm_provider or args.claude_config_dir or args.codex_home):
        print(
            "Nothing to set — pass --llm-provider/--claude-config-dir/--codex-home, or --show.",
            file=sys.stderr,
        )
        return 2
    # Merge-preserving write: only the passed keys change, unrelated keys
    # (and unset llm keys) survive.
    merged = dict(existing)
    if args.llm_provider:
        merged["llm_provider"] = args.llm_provider
    if args.claude_config_dir:
        merged["llm_claude_config_dirs"] = list(args.claude_config_dir)
    if args.codex_home:
        merged["llm_codex_home"] = args.codex_home
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"Saved machine-wide LLM defaults to {path}:")
    for key in ("llm_provider", "llm_claude_config_dirs", "llm_codex_home"):
        if key in merged:
            print(f"  {key}: {merged[key]}")
    return 0


def _handle_init(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.init(
        args.project,
        name=args.name,
        source_kind=args.source_kind,
        sources=args.source,
        llm_provider=args.llm_provider,
        llm_claude_config_dirs=args.claude_config_dir or None,
        llm_codex_home=args.codex_home,
    )
    print(f"Initialized project wiki: {wiki.root}")
    print(f"Graph: {wiki.paths.graph}")
    print("Next: python3 -m tesserae compile <paths>")
    return 0


def _handle_setup(args: argparse.Namespace) -> int:
    if True:
        from .setup import (
            WizardNotInteractive,
            apply_plan,
            build_plan,
            detect,
            render_review,
            run_wizard,
        )

        try:
            report = detect(args.project)
            if args.yes:
                yes_overrides: dict = {
                    "name": args.name,
                    "source_kind": args.source_kind,
                    "sources": args.source or None,
                    "include_understand_anything": args.with_understand_anything,
                    "understand_anything_platform": args.understand_anything_platform,
                    "understand_anything_command": args.understand_anything_command,
                    "run_understand_anything": args.run_understand_anything,
                    "install_understand_anything": (
                        False if args.skip_install_understand_anything
                        else True if args.install_understand_anything
                        else None
                    ),
                    "include_raganything": (
                        False if args.skip_raganything else args.with_raganything
                    ),
                    "install_raganything": (
                        False if args.skip_install_raganything
                        else True if args.install_raganything
                        else None
                    ),
                    "raganything_extras": args.raganything_extras,
                    "raganything_parser": args.raganything_parser,
                    "enable_cognee": not args.no_cognee,
                    "cognee_mode": args.cognee_mode,
                    "cognee_auto_cognify": args.run_cognee,
                    "install_cognee": (
                        False if args.skip_install_cognee
                        else True if args.install_cognee
                        else None
                    ),
                }
                yes_overrides = {
                    k: v for k, v in yes_overrides.items() if v is not None
                }
                plan = build_plan(report, overrides=yes_overrides)
                print(render_review(plan), end="")
            else:
                try:
                    plan = run_wizard(report)
                except WizardNotInteractive:
                    print(
                        "tesserae setup: stdin is not a TTY. Re-run from a real "
                        "terminal, or pass --yes to use detected defaults.",
                        file=sys.stderr,
                    )
                    return 2
            result = apply_plan(
                plan,
                confirm_install_actions=True,
                confirm_run_actions=True,
            )
        except KeyboardInterrupt:
            print("Setup cancelled.")
            return 130
        except Exception as exc:
            print(f"Setup failed: {exc}", file=sys.stderr)
            return 2
        print(f"Initialized project wiki: {result.wiki_root}")
        print(f"Config: {result.config_path}")
        if result.actions_taken:
            for row in result.actions_taken:
                status = row.get("status") or "?"
                detail = row.get("command") or row.get("description") or ""
                print(f"  [{status}] {row.get('id')}: {detail}")
        if result.warnings:
            for w in result.warnings:
                print(f"warning: {w}", file=sys.stderr)
        print("Next: tesserae compile && tesserae export site")
        return 0


def _handle_ingest(args: argparse.Namespace) -> int:
    if True:
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


def _handle_ingest_code(args: argparse.Namespace) -> int:
    if True:
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


def _handle_sync_code(args: argparse.Namespace) -> int:
    if True:
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
                "Then re-run `tesserae code sync` (optionally with --auto-sync).",
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


def _handle_compile_legacy(args: argparse.Namespace) -> int:
    if True:
        _apply_llm_cli_env(args)
        wiki = ProjectWiki.load(args.project)
        # Dieted (non-everyday) compile knobs live under config.json's
        # `compile_options` block now; each removed flag's old argparse
        # default is the fallback (see docs spec "Flag diet").
        opts = wiki._compile_options()
        try:
            refreshed = refresh_configured_external_tools(args.project, only_auto=not args.refresh_integrations, fail_fast=False)
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
        cognee_codex_cognify = bool(opts.get("cognee_codex_cognify", False))
        cognee_cognify = bool(opts.get("cognee_cognify", False))
        cognee_add = bool(opts.get("cognee_add", False))
        explicit_cognee = cognee_codex_cognify or cognee_cognify or cognee_add
        cognify_mode = (
            "codex_cognify" if cognee_codex_cognify
            else "cognify" if cognee_cognify
            else "add" if cognee_add
            else "off"
        )
        cognify_options = CognifyOptions(
            mode=cognify_mode,
            dataset=opts.get("cognee_dataset", "tesserae_research_graph"),
            codex_model=opts.get("cognee_codex_model", "gpt-5.4"),
            codex_timeout=int(opts.get("cognee_codex_timeout", 300)),
            embedding_provider=opts.get("cognee_embedding_provider", "deterministic"),
            ollama_embedding_model=opts.get("cognee_ollama_embedding_model", "qwen3-embedding:0.6b"),
            ollama_embedding_endpoint=opts.get("cognee_ollama_embedding_endpoint", "http://127.0.0.1:11434/api/embed"),
            ollama_embedding_timeout=int(opts.get("cognee_ollama_embedding_timeout", 120)),
            local_embedding_dimensions=int(opts.get("cognee_local_embedding_dimensions", 128)),
            system_root=opts.get("cognee_system_root", None),
            data_root=opts.get("cognee_data_root", None),
        ) if explicit_cognee else cognify_options_from_config(wiki.config())
        # Build a SessionExtractionOptions override when a --sessions/--no-sessions
        # CLI flag was passed OR a sessions_* compile_option is configured. None
        # means "no override — read from config", which is what
        # _merge_session_graph does by default.
        sessions_llm = opts.get("sessions_llm", None)
        sessions_model = opts.get("sessions_model", None)
        session_override = None
        if (
            args.sessions_enabled is not None
            or sessions_llm is not None
            or sessions_model is not None
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
                    sessions_llm
                    if sessions_llm is not None
                    else str(base.get("llm_enabled", "auto")).lower()
                ),
                max_turns_per_chunk=int(base.get("max_turns_per_chunk", 30)),
                max_tokens_per_call=int(base.get("max_tokens_per_call", 30000)),
                model=(
                    sessions_model
                    if sessions_model is not None
                    else (base.get("model") or None)
                ),
                include_doc_id_context=int(base.get("include_doc_id_context", 200)),
            )
        from .compile_progress import NullCompileProgress, make_compile_progress

        # Live codegraph-style progress on an interactive terminal; a no-op
        # (and the plain summary line below) when piped/CI/MCP/daemon.
        progress = make_compile_progress()
        with progress:
            result = wiki.compile(
                source_kind=opts.get("source_kind", None),
                changed_only=args.changed_only,
                limit=args.limit,
                trends=bool(opts.get("trends", False)),
                min_trend_sources=int(opts.get("min_trend_sources", 2)),
                exclude_data=bool(opts.get("exclude_data", False)),
                cognify=cognify_options if (cognify_options and cognify_options.is_active) else None,
                vault_pull=not bool(opts.get("no_vault_pull", False)),
                session_options=session_override,
                use_extraction_feedback=bool(opts.get("use_extraction_feedback", False)),
                progress=progress,
            )
            progress.done(nodes=result["node_count"], edges=result["edge_count"])
        if isinstance(progress, NullCompileProgress):
            # Non-TTY: keep the stable, script-parseable summary line.
            print(
                "Compiled project wiki: "
                f"processed={result['processed_files']} skipped={result['skipped_files']} "
                f"nodes={result['node_count']} edges={result['edge_count']}"
            )
        print(f"Graph: {result['graph_path']}")
        return 0


def _handle_schema_drift(args: argparse.Namespace) -> int:
    if True:
        wiki = ProjectWiki.load(args.project)
        if not wiki.paths.graph.exists():
            print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
            return 2
        from .research_graph import ResearchNodeType as _ResearchNodeType
        from .schema_drift import analyze_schema_drift
        host_args = args.host_type or ["SourceDocument"]
        try:
            host_types = [_ResearchNodeType(value) for value in host_args]
        except ValueError as exc:
            print(f"error: unknown --host-type: {exc}", file=sys.stderr)
            return 2
        llm = wiki._build_json_client()
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


def _handle_evolve(args: argparse.Namespace) -> int:
    if True:
        wiki = ProjectWiki.load(args.project)
        # An LLM phrases each cluster; when none is reachable evolve degrades
        # gracefully to deterministic-templated bullets rather than erroring.
        llm = wiki._build_json_client()
        summary = wiki.evolve(json_client=llm)
        print(
            f"events={summary['events']} bullets={summary['bullets']} "
            f"guidance at {summary['guidance_path']}"
        )
        return 0


def _handle_research(args: argparse.Namespace) -> int:
    if True:
        from .mcp_server import LLMWikiMCPServer
        from .research_mode import GraphSearchBackend, ResearchSession

        wiki = ProjectWiki.load(args.project)
        if not wiki.paths.graph.exists():
            print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
            return 2
        llm = wiki._build_json_client()
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


def _handle_refresh_understand_anything(args: argparse.Namespace) -> int:
    return refresh_understand_anything(
        args.project,
        platform=args.platform,
        full=args.full,
        force=args.force,
        timeout=args.timeout,
    )


def _handle_obsidian_sync(args: argparse.Namespace) -> int:
    if True:
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
                print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
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


def _handle_refresh(args: argparse.Namespace) -> int:
    """Run the refresh chain (sessions-import -> compile -> obsidian-sync) in-process.

    This is success criterion #1 of ENG-01: the refresh sequence is CODE routed
    through ``Pipeline`` rather than the prose chain in the using-tesserae skill.

    Ordering (Pitfall #2): sessions-import MUST precede compile — ``compile``
    reads ``.tesserae/harness_sessions/`` and silently skips session extraction
    if the import has not run. Vault guard (Pitfall #3): the ``.is_dir()`` check
    turns "no vault configured" into a graceful ok-skip, not a crash. Default
    (Pitfall #6): ``changed_only`` defaults to False (a full compile).
    """
    from .engine.pipeline import Pipeline
    from .harness_sessions import discover_harness_sessions, HarnessSessionStore

    wiki = ProjectWiki.load(args.project)

    def step_sessions_import():
        sessions = discover_harness_sessions(wiki.project_root)
        store = HarnessSessionStore(wiki.paths.harness_sessions)
        return store.write_sessions(sessions)  # {"sessions": n, "path": ...}

    def step_compile():
        return wiki.compile(changed_only=args.changed_only)

    def step_obsidian_sync():
        vault = wiki.effective_obsidian_vault()
        if not vault.is_dir():
            return {"skipped": "no vault configured"}  # ok, not a failure
        r = wiki.reproject_after_vault_change()
        return {
            "overrides_applied": r.overrides_applied,
            "user_link_changes_applied": r.user_link_changes_applied,
            "stubs_minted": r.stubs_minted,
        }

    steps = []
    if not args.skip_sessions:
        steps.append(("sessions-import", step_sessions_import))
    steps += [("compile", step_compile), ("obsidian-sync", step_obsidian_sync)]

    results = Pipeline(steps).run()

    for r in results:
        status = "ok" if r.ok else f"FAILED: {r.error}"
        print(f"  {r.name}: {status}")
    return 0 if all(r.ok for r in results) else 2


def _handle_refresh_raganything(args: argparse.Namespace) -> int:
    if True:
        forwarded = ["--project", args.project, "--parser", args.parser, "--parse-method", args.parse_method]
        for r in (args.roots or []):
            forwarded += ["--root", r]
        if args.force:
            forwarded.append("--force")
        if args.full:
            forwarded.append("--full")
        return _raganything_refresh_main(forwarded)


def _handle_lint(args: argparse.Namespace) -> int:
    if True:
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


def _handle_query(args: argparse.Namespace) -> int:
    return _project_query_handler(args)


def _handle_ask(args: argparse.Namespace) -> int:
    return _project_ask_handler(args)


def _handle_context(args: argparse.Namespace) -> int:
    from .context_compiler import compile_context

    wiki = ProjectWiki.load(args.project)
    if not wiki.paths.graph.exists():
        print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
        return 2
    graph = _load_graph_file(wiki.paths.graph)
    bundle = compile_context(
        graph,
        str(wiki.project_root),
        query=args.query,
        seeds=args.seeds,
        depth=args.depth,
        budget=args.budget,
        synthesize=args.synthesize,
    )
    if args.output:
        Path(args.output).write_text(bundle.body, encoding="utf-8")
        print(
            f"Written to {args.output} "
            f"({bundle.char_budget_used} chars, {len(bundle.citations)} citations)"
        )
    else:
        print(bundle.body)
    return 0


def _handle_mcp_config(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    print(wiki.render_mcp_config(server_name=args.server_name, pythonpath=args.pythonpath), end="")
    return 0


def _handle_export_graphiti(args: argparse.Namespace) -> int:
    if True:
        wiki = ProjectWiki.load(args.project)
        result = wiki.export_graphiti(group_id=args.group_id, output=args.output)
        print(f"Exported Graphiti episodes: episodes={result['episodes']} path={result['path']} group_id={result['group_id']}")
        return 0


def _handle_sync_graphiti(args: argparse.Namespace) -> int:
    if True:
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


def _handle_export_agent_harness(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    result = wiki.export_agent_harness(targets=args.target or None, output=args.output)
    print(f"Exported agent harness: files={result['files']} path={result['path']} targets={','.join(result['targets'])}")
    return 0


def _handle_export_obsidian(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    result = wiki.export_obsidian(vault=args.vault)
    print(f"Exported Obsidian vault: notes={result['notes']} path={result['vault_path']}")
    return 0


def _handle_sessions(args: argparse.Namespace) -> int:
    if True:
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
    raise ValueError(f"Unknown project command: {args.command}")


def _handle_build_site(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    result = wiki.build_site(output=args.output)
    print(f"Built frontend site: nodes={result['nodes']} edges={result['edges']} path={result['site_path']}")
    return 0


def _handle_deploy(args: argparse.Namespace) -> int:
    if True:
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


def _handle_serve_legacy(args: argparse.Namespace) -> int:
    if True:
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


def _handle_watch(args: argparse.Namespace) -> int:
    if True:
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


def _handle_engine(args: argparse.Namespace) -> int:
    """Start the supervised refresh daemon (alias: ``daemon``).

    ``--all`` switches to fleet mode: one process supervising every project in
    the registry (see docs/superpowers/specs/2026-06-12-global-engine-design.md).
    """
    import logging
    import os

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    if getattr(args, "all", False):
        if args.project != ".":  # user passed an explicit --project alongside --all
            print("tesserae engine: --all and --project are mutually exclusive", file=sys.stderr)
            raise SystemExit(2)
        from .engine.fleet import FleetDaemon

        registry_env = os.environ.get("TESSERAE_REGISTRY")
        pidfile_env = os.environ.get("TESSERAE_FLEET_PIDFILE")
        fleet = FleetDaemon(
            registry_path=Path(registry_env) if registry_env else None,
            compile_slots=args.compile_slots,
            pidfile=Path(pidfile_env) if pidfile_env else None,
        )
        try:
            return fleet.run(once=args.once)
        except RuntimeError as exc:
            print(f"tesserae engine: {exc}", file=sys.stderr)
            return 2

    from .engine.daemon import Daemon

    daemon = Daemon(
        Path(args.project).resolve(),
        debounce=args.debounce,
        watch_interval=args.interval,
    )
    try:
        return daemon.run(once=args.once)
    except RuntimeError as exc:
        print(f"tesserae engine: {exc}", file=sys.stderr)
        return 2


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    from .cli_tree import KNOWN_COMMANDS, moved_replacement, package_version, render_root_help

    if not argv or argv[0] in ("--help", "-h", "help"):
        print(render_root_help(), end="")
        if argv and argv[0] in ("--help", "-h"):
            raise SystemExit(0)
        return 0
    if argv[0] in ("--version", "-V", "version"):
        print(f"tesserae {package_version()}")
        return 0
    moved = moved_replacement(argv)
    if moved is not None:
        old_prefix, hint = moved
        print(f"tesserae {old_prefix} has moved → {hint}", file=sys.stderr)
        return 2
    if argv[0] not in KNOWN_COMMANDS:
        from .cli_tree import looks_like_extraction_path

        if looks_like_extraction_path(argv[0]):
            print(
                "bare extraction has moved → tesserae extract <paths>",
                file=sys.stderr,
            )
            return 2
        print(
            f"tesserae: unknown command {argv[0]!r} — see `tesserae --help`",
            file=sys.stderr,
        )
        return 2
    try:
        return _dispatch_command(argv[0], argv[1:])
    except NotImplementedError:
        print(
            f"tesserae {argv[0]}: not wired up yet on this branch — coming in a later redesign task.",
            file=sys.stderr,
        )
        return 2
    except CompileLockHeldError as exc:
        print(f"tesserae {argv[0]}: {exc}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# Flat-verb tree (redesign task 3): standalone parser builders + routers.
#
# Each builder returns a fresh argparse.ArgumentParser carrying the flags the
# now-removed legacy `project <cmd>` subparser used to expose (copied here when
# the old surface was retired in task 7).
# ---------------------------------------------------------------------------


def _build_compile_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae compile",
        description="Rebuild the knowledge graph (compile [paths] = ad-hoc ingest).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae compile\n"
            "  tesserae compile --changed-only\n"
            "  tesserae compile notes/idea.md\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Ad-hoc markdown paths to ingest into the graph (replaces `project ingest`)",
    )
    _add_llm_client_args(parser)
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--changed-only", action="store_true", help="Skip unchanged files using .tesserae/manifest.json")
    parser.add_argument("--limit", type=int, help="Maximum number of changed files to process")
    parser.add_argument(
        "--refresh-integrations",
        dest="refresh_integrations",
        action="store_true",
        help="Run configured integration refresh commands before compile, even if they are not marked auto_refresh",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--sessions", dest="sessions_enabled", action="store_true", default=None, help="Force session graph extraction on (default if .tesserae/harness_sessions/ exists)")
    session_group.add_argument("--no-sessions", dest="sessions_enabled", action="store_false", default=None, help="Skip session graph extraction entirely")
    # Every other former compile flag is now a `compile_options.<dest>` key
    # in config.json (read in _handle_compile_legacy via wiki._compile_options()).
    # See docs spec "Flag diet" — the old --help text is the key's doc.
    return parser


def _build_context_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae context",
        description="Compile a cited context doc for a query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae context \"how does compile work?\"\n"
            "  tesserae context \"how does compile work?\" --budget 4000 --synthesize\n"
        ),
    )
    parser.add_argument("query", nargs="?", default="", help="Query text to seed the context doc")
    parser.add_argument("--seeds", nargs="*", help="Explicit seed node IDs")
    parser.add_argument("--depth", type=int, default=2, help="PPR expansion depth (default: 2)")
    parser.add_argument("--budget", type=int, default=32_000, help="Character budget for the doc body (default: 32000; <=0 = uncapped)")
    parser.add_argument("--synthesize", action="store_true", help="Add an LLM-synthesized summary (requires an LLM backend)")
    parser.add_argument("--output", "-o", help="Write the doc to a file instead of stdout")
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    return parser


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae serve",
        description="Browse the compiled site (auto-builds if missing/stale).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae serve\n"
            "  tesserae serve --port 8765 --no-build\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    parser.add_argument("--dry-run", action="store_true", help="Print the site URL without starting a server")
    parser.add_argument("--no-build", action="store_true", help="Skip the auto-build step even if the site is missing or stale")
    return parser


def _build_engine_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae engine",
        description="Run the supervised refresh daemon: watch sources, coalesce bursts, auto-recompile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae engine --once\n"
            "  tesserae engine --interval 30\n"
            "  tesserae engine --all --once\n"
            "  TESSERAE_REGISTRY=~/.tesserae/registry.json tesserae engine --all\n"
            "  TESSERAE_FLEET_PIDFILE=/run/tesserae-fleet.pid tesserae engine --all\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (default: 2)")
    parser.add_argument("--debounce", type=float, default=1.0, help="Quiet window after a burst of edits before rebuilding (default: 1.0)")
    parser.add_argument("--once", action="store_true", help="Run a single drain cycle then exit (deterministic; no long-running loop)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fleet mode: run every project in ~/.tesserae/registry.json from one process.",
    )
    parser.add_argument(
        "--compile-slots",
        type=int,
        default=1,
        help="Fleet mode: max concurrent compiles across all projects (default 1).",
    )
    return parser


def _build_refresh_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae refresh",
        description="Import new sessions, compile, sync vault (in-process pipeline).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae refresh\n"
            "  tesserae refresh --changed-only --skip-sessions\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--changed-only", action="store_true", default=False, help="Opt-in incremental compile (skip unchanged files); default is a full compile")
    parser.add_argument("--skip-sessions", action="store_true", default=False, help="Opt-in skip of the slow harness-session discovery scan")
    return parser


def _build_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae status",
        description="Node/edge counts, last compile, vault state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae status\n"
            "  tesserae status --project ../other-repo\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    return parser


def _handle_status(args: argparse.Namespace) -> int:
    try:
        wiki = ProjectWiki.load(args.project)
    except FileNotFoundError:
        print(
            "tesserae status: project not initialized — run `tesserae init` first.",
            file=sys.stderr,
        )
        return 2
    graph = None
    if wiki.paths.graph.exists():
        try:
            graph = _load_graph_file(wiki.paths.graph)
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            graph = None  # corrupt/unreadable graph.json — report below, don't crash
    else:
        graph = ResearchGraph()
    import datetime as _dt
    compiled = (
        _dt.datetime.fromtimestamp(wiki.paths.graph.stat().st_mtime).isoformat(timespec="seconds")
        if wiki.paths.graph.exists() else "never"
    )
    print(f"project:       {wiki.project_root}")
    if graph is None:
        print("nodes:         corrupt graph.json")
        print("edges:         corrupt graph.json")
    else:
        print(f"nodes:         {len(graph.nodes)}")
        print(f"edges:         {len(graph.edges)}")
    print(f"last compile:  {compiled}")
    print(f"vault:         {wiki.effective_obsidian_vault()}")
    print(f"site:          {wiki.paths.site}")
    return 0


def _handle_compile_paths_ingest(args: argparse.Namespace) -> int:
    """Ad-hoc ingest of explicit paths (INGEST-ONLY).

    Reuses the legacy ``ingest`` handler logic for the given paths and RETURNS —
    it does NOT run a full ``wiki.compile()`` of configured sources afterward
    (that would overwrite the ad-hoc graph). Backfills the attrs the legacy
    ingest handler reads with the old ingest parser's defaults.
    """
    args.inputs = list(args.paths)
    # After the Task 8 flag diet the compile parser no longer defines these
    # (they moved to compile_options); the ad-hoc ingest path keeps the old
    # `project ingest` argparse defaults rather than reading compile_options.
    if not hasattr(args, "source_kind"):
        args.source_kind = None
    if not hasattr(args, "changed_only"):
        args.changed_only = False
    if not hasattr(args, "limit"):
        args.limit = None
    if not hasattr(args, "trends"):
        args.trends = False
    if not hasattr(args, "min_trend_sources"):
        args.min_trend_sources = 2
    return _handle_ingest(args)


def _handle_compile(args: argparse.Namespace) -> int:
    """New-tree compile wrapper. With explicit paths → ad-hoc ingest-only;
    otherwise → the full legacy compile via ``_handle_compile_legacy``."""
    if getattr(args, "paths", None):
        return _handle_compile_paths_ingest(args)
    return _handle_compile_legacy(args)


def _serve_build_site(args: argparse.Namespace) -> int:
    """Invoke the legacy build-site handler with a backfilled namespace."""
    build_args = argparse.Namespace(project=args.project, output=None)
    return _handle_build_site(build_args)


def _handle_serve(args: argparse.Namespace) -> int:
    """New-tree serve wrapper: auto-build the site when missing/stale, then
    delegate to the legacy serve handler (``_handle_serve_legacy``).
    """
    try:
        wiki = ProjectWiki.load(args.project)
    except FileNotFoundError:
        print(
            "tesserae serve: project not initialized — run `tesserae init` first.",
            file=sys.stderr,
        )
        return 2
    if not getattr(args, "no_build", False):
        index = wiki.paths.site / "index.html"
        reason = None
        if not index.exists():
            reason = "missing"
        # graph.json missing -> no graph -> nothing to rebuild from; serve whatever site exists.
        elif wiki.paths.graph.exists() and wiki.paths.graph.stat().st_mtime > index.stat().st_mtime:
            reason = "stale"
        if reason is not None:
            print(f"building site first ({reason}) …")
            rc = _serve_build_site(args)
            if rc != 0:
                return rc
    return _handle_serve_legacy(args)


def _route_compile(rest: List[str]) -> int:
    args = _build_compile_parser().parse_args(rest)
    return _handle_compile(args)


def _route_context(rest: List[str]) -> int:
    args = _build_context_parser().parse_args(rest)
    return _handle_context(args)


def _route_serve(rest: List[str]) -> int:
    args = _build_serve_parser().parse_args(rest)
    return _handle_serve(args)


def _route_status(rest: List[str]) -> int:
    args = _build_status_parser().parse_args(rest)
    return _handle_status(args)


def _route_engine(rest: List[str]) -> int:
    args = _build_engine_parser().parse_args(rest)
    return _handle_engine(args)


def _route_refresh(rest: List[str]) -> int:
    args = _build_refresh_parser().parse_args(rest)
    return _handle_refresh(args)


def _route_ask(rest: List[str]) -> int:
    ask_parser = _build_top_level_ask_parser()
    return _top_level_ask_handler(ask_parser.parse_args(rest))


def _build_init_parser() -> argparse.ArgumentParser:
    """The dieted `tesserae init` parser: EXACTLY 8 flags.

    `tesserae init` runs the setup wizard by default; `--yes` accepts detected
    defaults non-interactively; `--bare` skips the wizard entirely and writes a
    minimal workspace (the old `project init`). The ~21 other legacy `setup`
    flags (``--source-kind`` and the integration toggles) become wizard prompts
    and/or documented config.json keys — they are NOT surfaced here. The legacy
    `project init` / `init` parsers keep their own full flag sets.
    """
    parser = argparse.ArgumentParser(
        prog="tesserae init",
        description="Set up .tesserae (wizard by default; --yes non-interactive; --bare skips the wizard).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae init --yes --source .\n"
            "  tesserae init --bare --name myproj\n"
            "  tesserae init --llm-provider codex\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--name", help="MCP server/config name; defaults to sanitized project directory name")
    parser.add_argument("--source", action="append", default=[], help="Project-relative source path; repeat for multiple paths")
    parser.add_argument("--yes", action="store_true", help="Accept detected defaults non-interactively (all optional integrations OFF)")
    parser.add_argument("--bare", action="store_true", help="Skip the wizard; write a minimal workspace (the old `project init`)")
    _add_llm_client_args(parser, persisted=True)
    return parser


# keep in sync with _handle_setup's args.* reads
def _backfill_setup_defaults(args: argparse.Namespace) -> None:
    """Fill the namespace with every attr the legacy `_handle_setup` reads.

    `_handle_init_v2` delegates to the unchanged `_handle_setup`, whose ``--yes``
    branch reads ~21 attrs that the dieted init parser no longer defines. We
    ``setdefault`` each one with the legacy `setup_parser` default, EXCEPT the
    integration toggles, which take the NEW ``--yes`` defaults: every optional
    integration (cognee, raganything, understand-anything) lands OFF. Color is
    auto-disabled when stdout is not a TTY.
    """
    d = args.__dict__
    # Legacy setup defaults (verbatim from the `setup_parser.add_argument` calls).
    d.setdefault("source_kind", "Repository")
    d.setdefault("understand_anything_command", None)
    d.setdefault("understand_anything_platform", "codex")
    d.setdefault("raganything_parser", "mineru")
    d.setdefault("raganything_extras", "all")
    d.setdefault("cognee_mode", "codex_cognify")
    # --yes default: cognee OFF (this is exactly what CI's `--no-cognee` encoded).
    d.setdefault("no_cognee", True)
    # --yes default: raganything OFF (CI's `--skip-raganything`; never `--with-raganything`).
    d.setdefault("with_raganything", False)
    d.setdefault("skip_raganything", True)
    # --yes default: understand-anything OFF (never `--with-understand-anything`).
    d.setdefault("with_understand_anything", False)
    # --yes default: no companion-tool installs/runs (CI's `--skip-install-*`,
    # never `--install-*`/`--run-*`). These keep the wizard from shelling out.
    d.setdefault("install_understand_anything", False)
    d.setdefault("skip_install_understand_anything", True)
    d.setdefault("run_understand_anything", False)
    d.setdefault("install_raganything", False)
    d.setdefault("skip_install_raganything", True)
    d.setdefault("install_cognee", False)
    d.setdefault("skip_install_cognee", True)
    d.setdefault("run_cognee", False)
    # --yes default: color auto-disabled when stdout is not an interactive TTY.
    d.setdefault("no_color", not sys.stdout.isatty())


def _backfill_bare_init_defaults(args: argparse.Namespace) -> None:
    """Fill the namespace with attrs the legacy `_handle_init` reads.

    `_handle_init` reads ``args.source_kind`` (and ``args.source`` /
    ``args.name`` / the LLM attrs, which the dieted init parser already
    supplies). Only ``source_kind`` is missing from the diet — backfill it with
    the legacy `project init` default.
    """
    args.__dict__.setdefault("source_kind", "SourceDocument")


def _handle_init_v2(args: argparse.Namespace) -> int:
    """Dispatch wrapper for the dieted `tesserae init`.

    `--bare` → the legacy `project init` workspace writer. Otherwise the legacy
    `init` wizard (which honors ``--yes``). Both legacy handlers are
    unchanged; we only backfill the namespace they expect. The module-level
    names ``_handle_init`` / ``_handle_setup`` are resolved at call time so
    ``monkeypatch.setattr(cli, "_handle_setup", …)`` is honored.
    """
    if args.bare:
        _backfill_bare_init_defaults(args)
        return _handle_init(args)
    _backfill_setup_defaults(args)
    return _handle_setup(args)


def _route_init(rest: List[str]) -> int:
    args = _build_init_parser().parse_args(rest)
    return _handle_init_v2(args)


# ---------------------------------------------------------------------------
# Redesign task 5: group builders + remaining standalone verbs.
#
# Each group builder owns a REQUIRED sub-subparser; `_route_<group>` parses
# then dispatches to module-level handler names (resolved at CALL time so
# `monkeypatch.setattr(cli, "_handle_…", …)` is honored). Wrappers that route
# a NEW command to an OLD handler backfill EVERY attribute the old handler
# reads, using the defaults the now-removed legacy parser used to supply.
# ---------------------------------------------------------------------------


# ----- sessions -------------------------------------------------------------
def _handle_sessions_import(args: argparse.Namespace) -> int:
    args.sessions_command = "import"
    return _handle_sessions(args)


def _handle_sessions_discover(args: argparse.Namespace) -> int:
    args.sessions_command = "discover"
    return _handle_sessions(args)


def _handle_sessions_list(args: argparse.Namespace) -> int:
    args.sessions_command = "list"
    return _handle_sessions(args)


def _build_sessions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae sessions",
        description="Manage inbound agent harness session history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae sessions import path/to/session.json\n"
            "  tesserae sessions discover --harness codex --import\n"
            "  tesserae sessions list\n"
        ),
    )
    sub = parser.add_subparsers(dest="sessions_command", required=True)
    p_import = sub.add_parser(
        "import",
        help="Import normalized HarnessSession JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae sessions import path/to/session.json\n"
            "  tesserae sessions import a.json b.json\n"
        ),
    )
    # DEVIATION from the legacy `nargs="+"`: the new tree uses `nargs="*"` so
    # `tesserae sessions import` (no paths) parses to an empty-import no-op
    # instead of an argparse usage error. `_handle_sessions`' import branch
    # iterates an empty list cleanly (writes 0 sessions, exits 0). This is
    # what the Task-5 `test_group_dispatch` contract requires.
    p_import.add_argument("paths", nargs="*", default=[], help="JSON files containing one session object or a list of sessions")
    p_import.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_import.set_defaults(_handler="_handle_sessions_import")
    p_discover = sub.add_parser("discover", help="Discover local Claude Code/Codex sessions scoped to this project")
    p_discover.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_discover.add_argument("--root", action="append", default=[], help="Harness config root to scan; repeat for multiple roots. Defaults to auto-detected Claude/Codex config roots under HOME")
    p_discover.add_argument("--harness", action="append", default=[], choices=["claude-code", "codex"], help="Harness to scan; repeat for multiple harnesses. Defaults to both")
    p_discover.add_argument("--import", dest="import_sessions", action="store_true", help="Import discovered normalized sessions into .tesserae/harness_sessions")
    p_discover.set_defaults(_handler="_handle_sessions_discover")
    p_list = sub.add_parser("list", help="List normalized harness sessions for this project")
    p_list.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_list.set_defaults(_handler="_handle_sessions_list")
    return parser


def _route_sessions(rest: List[str]) -> int:
    args = _build_sessions_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- vault ----------------------------------------------------------------
def _handle_vault_sync(args: argparse.Namespace) -> int:
    """`vault sync` = old `obsidian-sync`."""
    return _handle_obsidian_sync(args)


def _handle_vault_prune(args: argparse.Namespace) -> int:
    """`vault prune` = `obsidian-sync --prune-orphans` preset."""
    args.prune_orphans = True
    return _handle_obsidian_sync(args)


def _handle_vault_export(args: argparse.Namespace) -> int:
    """`vault export` = old `export-obsidian`."""
    return _handle_export_obsidian(args)


def _handle_vault_set_root(args: argparse.Namespace) -> int:
    """`vault set-root` = old `wiki obsidian-set-root` (delegates via namespace)."""
    args.wiki_command = "obsidian-set-root"
    return _wiki_command_handler(args)


def _handle_vault_sync_all(args: argparse.Namespace) -> int:
    """`vault sync-all` = old `wiki obsidian-sync-all` (delegates via namespace)."""
    args.wiki_command = "obsidian-sync-all"
    return _wiki_command_handler(args)


def _build_vault_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae vault",
        description="Obsidian vault projection: sync | sync-all | set-root | export | prune.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae vault sync\n"
            "  tesserae vault sync --dry-run\n"
            "  tesserae vault sync --watch\n"
        ),
    )
    sub = parser.add_subparsers(dest="vault_command", required=True)

    def _add_obsidian_sync_args(p: argparse.ArgumentParser) -> None:
        # verbatim from the legacy obsidian-sync parser (now removed)
        p.add_argument("--project", default=".", help="Project root; defaults to cwd")
        p.add_argument("--watch", action="store_true", help="Run a long-lived poll loop that re-applies the overlay every time the vault changes. Press Ctrl-C to stop.")
        p.add_argument("--dry-run", action="store_true", help="Compute the overlay diff and write .tesserae/diverged-fields.md, but DON'T apply changes to the graph or re-project. Useful for previewing what a compile would do.")
        p.add_argument("--poll-interval", type=float, default=1.5, help="Watch-mode poll interval in seconds (default: 1.5).")
        p.add_argument("--vault", type=str, default=None, help="Override the configured Obsidian vault directory for this call.")
        p.add_argument("--persist-vault", action="store_true", help="When passed with --vault, writes the path to .tesserae/config.json under obsidian.vault_path.")
        p.add_argument("--prune-orphans", action="store_true", help="Delete projected pages in the vault whose node_id no longer exists in the current graph.")
        p.add_argument("--force-prune-with-notes", action="store_true", help="With --prune-orphans, also delete orphan pages that have user-notes content.")

    p_sync = sub.add_parser(
        "sync",
        help="Apply vault edits onto the typed graph and re-project. Pass --watch for live mode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae vault sync\n"
            "  tesserae vault sync --dry-run\n"
            "  tesserae vault sync --watch\n"
        ),
    )
    _add_obsidian_sync_args(p_sync)
    p_sync.set_defaults(_handler="_handle_vault_sync")

    # prune = obsidian-sync --prune-orphans preset; rest of sync's attrs backfilled.
    p_prune = sub.add_parser("prune", help="Delete projected pages whose node_id no longer exists in the graph (preset of `sync --prune-orphans`).")
    p_prune.add_argument("--project", default=".", help="Project root; defaults to cwd")
    p_prune.add_argument("--force-prune-with-notes", action="store_true", help="Also delete orphan pages that have user-notes content.")
    p_prune.set_defaults(
        _handler="_handle_vault_prune",
        watch=False, dry_run=False, poll_interval=1.5, vault=None,
        persist_vault=False, prune_orphans=True,
    )

    p_export = sub.add_parser("export", help="Export the compiled graph as an Obsidian vault")
    p_export.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_export.add_argument("--vault", help="Vault output directory; defaults to .tesserae/obsidian_vault")
    p_export.set_defaults(_handler="_handle_vault_export")

    p_set_root = sub.add_parser("set-root", help="Set the registry-wide Obsidian vault root.")
    p_set_root.add_argument("path", nargs="?", help="Absolute path; omit and pass --clear to unset.")
    p_set_root.add_argument("--clear", action="store_true", help="Remove the configured vault root.")
    p_set_root.set_defaults(_handler="_handle_vault_set_root")

    p_sync_all = sub.add_parser("sync-all", help="Run an obsidian-sync --watch loop for every registered project (one thread per project).")
    p_sync_all.add_argument("--poll-interval", type=float, default=1.5, help="Per-watcher poll interval in seconds (default: 1.5).")
    p_sync_all.add_argument("--prune-orphans", action="store_true", help="Prune stale projected pages in every project's vault before starting watchers.")
    p_sync_all.add_argument("--force-prune-with-notes", action="store_true", help="With --prune-orphans, also delete orphans with user-notes content.")
    p_sync_all.add_argument("--no-watch", action="store_true", help="Run prune-only (requires --prune-orphans); skip the watch phase.")
    p_sync_all.set_defaults(_handler="_handle_vault_sync_all")
    return parser


def _route_vault(rest: List[str]) -> int:
    args = _build_vault_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- export ---------------------------------------------------------------
def _handle_export_harness(args: argparse.Namespace) -> int:
    """`export harness` = old `export-agent-harness`."""
    return _handle_export_agent_harness(args)


def _handle_export_graphiti_cmd(args: argparse.Namespace) -> int:
    """`export graphiti` = old `export-graphiti`; `--sync` → old `sync-graphiti`."""
    if getattr(args, "sync", False):
        return _handle_sync_graphiti(args)
    return _handle_export_graphiti(args)


def _handle_export_site(args: argparse.Namespace) -> int:
    """`export site` = old `build-site`; `--deploy` → old `deploy`, `--watch` → old `watch`."""
    if getattr(args, "deploy", False):
        return _handle_deploy(args)
    if getattr(args, "watch", False):
        return _handle_watch(args)
    return _handle_build_site(args)


def _build_export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae export",
        description="Artifact exports: harness | graphiti | site.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae export site\n"
            "  tesserae export site --deploy\n"
            "  tesserae export site --watch\n"
        ),
    )
    sub = parser.add_subparsers(dest="export_command", required=True)

    p_harness = sub.add_parser("harness", help="Export context/config harnesses for coding agents")
    p_harness.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_harness.add_argument("--target", action="append", default=[], help="Agent target to export; repeat for multiple targets. Defaults to all supported targets")
    p_harness.add_argument("--output", help="Harness output directory; defaults to .tesserae/agent_harness")
    p_harness.set_defaults(_handler="_handle_export_harness")

    # graphiti = export-graphiti flags UNION sync-graphiti flags + --sync.
    p_graphiti = sub.add_parser("graphiti", help="Export project graph as Graphiti episode JSONL; --sync pushes into Neo4j.")
    p_graphiti.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_graphiti.add_argument("--group-id", help="Graphiti group_id; defaults to project wiki name")
    p_graphiti.add_argument("--output", help="Episode JSONL output path; defaults to .tesserae/graphiti_episodes.jsonl")
    p_graphiti.add_argument("--sync", action="store_true", help="Sync episodes into Graphiti/Neo4j instead of writing JSONL")
    p_graphiti.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI for Graphiti (--sync)")
    p_graphiti.add_argument("--neo4j-user", default="neo4j", help="Neo4j username (--sync)")
    p_graphiti.add_argument("--neo4j-password", default="password", help="Neo4j password (--sync)")
    p_graphiti.add_argument("--dry-run", action="store_true", help="Count episodes without requiring Graphiti or Neo4j (--sync)")
    p_graphiti.set_defaults(_handler="_handle_export_graphiti_cmd")

    # site = build-site UNION deploy UNION watch flags + --deploy / --watch routing.
    p_site = sub.add_parser(
        "site",
        help="Build the static site; --deploy publishes, --watch rebuilds on change.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae export site\n"
            "  tesserae export site --deploy\n"
            "  tesserae export site --watch\n"
        ),
    )
    p_site.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_site.add_argument("--output", help="Site output directory; defaults to .tesserae/site")
    p_site.add_argument("--deploy", action="store_true", help="Deploy the compiled site to GitHub Pages (old `project deploy`)")
    p_site.add_argument("--watch", action="store_true", help="Auto-recompile when files change (old `project watch`)")
    # deploy flags
    p_site.add_argument("--branch", default="gh-pages", help="Branch to push the site to (--deploy; default: gh-pages)")
    p_site.add_argument("--remote", default="origin", help="Git remote to push to (--deploy; default: origin)")
    p_site.add_argument("--message", help="Commit message for the deploy commit (--deploy)")
    p_site.add_argument("--dry-run", action="store_true", help="Stage and commit but skip the final git push (--deploy)")
    p_site.add_argument("--build", action="store_true", help="Run compile before deploying (--deploy)")
    p_site.add_argument("--enable-pages", action="store_true", help="Enable GitHub Pages on the repo via the gh CLI (--deploy; idempotent)")
    p_site.add_argument("--force", action="store_true", help="Allow deploying even when the working tree is dirty (--deploy)")
    p_site.add_argument("--force-push", action="store_true", help="Use git push --force; refused for protected branches (--deploy)")
    # watch flags
    p_site.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (--watch; default: 2)")
    p_site.add_argument("--debounce", type=float, default=1.0, help="Quiet window after a burst of edits before rebuilding (--watch; default: 1.0)")
    p_site.add_argument("--once", action="store_true", help="Snapshot once, rebuild only if anything changed since the last run, exit (--watch)")
    p_site.add_argument("--paths", action="append", default=[], help="Additional directory to watch; repeat for multiple paths (--watch)")
    p_site.add_argument("--quiet", action="store_true", help="Suppress the banner and per-cycle progress output (--watch)")
    p_site.set_defaults(_handler="_handle_export_site")
    return parser


def _route_export(rest: List[str]) -> int:
    args = _build_export_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- code -----------------------------------------------------------------
def _handle_code_ingest(args: argparse.Namespace) -> int:
    """`code ingest` = old `ingest-code`."""
    return _handle_ingest_code(args)


def _handle_code_sync(args: argparse.Namespace) -> int:
    """`code sync` = old `sync-code`."""
    return _handle_sync_code(args)


def _build_code_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae code",
        description="CodeGraph ⇄ project graph: ingest | sync.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae code sync\n"
            "  tesserae code sync --auto-sync\n"
            "  tesserae code ingest\n"
        ),
    )
    sub = parser.add_subparsers(dest="code_command", required=True)

    p_ingest = sub.add_parser("ingest", help="Mint a typed code graph from Python source via the stdlib ast module")
    p_ingest.add_argument("paths", nargs="*", help="Project-relative or absolute paths to walk; defaults to the project root")
    p_ingest.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_ingest.add_argument("--output", help="Override output path; defaults to <project>/.tesserae/code-graph.json")
    p_ingest.add_argument("--exclude", action="append", default=[], help="Additional directory basename to skip (repeatable). Adds to the built-in exclude set")
    p_ingest.set_defaults(_handler="_handle_code_ingest")

    p_sync = sub.add_parser(
        "sync",
        help="Translate a colbymchenry/codegraph SQLite store into .tesserae/code-graph.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae code sync\n"
            "  tesserae code sync --auto-sync\n"
        ),
    )
    p_sync.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_sync.add_argument("--db", help="Path to the CodeGraph SQLite database; defaults to <project>/.codegraph/codegraph.db")
    p_sync.add_argument("--output", help="Override output path; defaults to <project>/.tesserae/code-graph.json")
    p_sync.add_argument("--auto-sync", action="store_true", help="Run `codegraph sync <project>` first if the binary is on PATH; skip silently otherwise")
    p_sync.set_defaults(_handler="_handle_code_sync")
    return parser


def _route_code(rest: List[str]) -> int:
    args = _build_code_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- ingest ---------------------------------------------------------------
def _build_ingest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae ingest",
        description="Ingest a single document file or URL into the knowledge base.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="File path(s) or http(s) URL(s) to ingest")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--title", default=None, help="Title override (useful for URLs)")
    parser.add_argument("--source-kind", default=None, help="Override source classification")
    parser.add_argument("--exact", action="store_true", help="Force a full recompile (skip the fast path)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + report, write no graph")
    parser.set_defaults(_handler="_handle_ingest_docs")
    return parser


def _handle_ingest_docs(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    result = ingest_sources(
        wiki,
        args.inputs,
        source_kind=args.source_kind,
        title=args.title,
        exact=args.exact,
        dry_run=getattr(args, "dry_run", False),
    )
    print(
        f"Ingested ({result['path_taken']}): "
        f"processed={result['processed_files']} skipped={result['skipped_files']} "
        f"nodes={result['node_count']} edges={result['edge_count']}"
    )
    print(f"Sources: {', '.join(result['sources'])}")
    print(f"Graph: {result['graph_path']}")
    return 0


def _route_ingest(rest: List[str]) -> int:
    args = _build_ingest_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- config ---------------------------------------------------------------
def _handle_config_llm(args: argparse.Namespace) -> int:
    """`config llm` = old `llm-defaults` minus --show."""
    args.show = False
    return _handle_llm_defaults(args)


def _handle_config_show(args: argparse.Namespace) -> int:
    """`config show` = old `llm-defaults --show`."""
    args.show = True
    args.llm_provider = None
    args.claude_config_dir = []
    args.codex_home = None
    return _handle_llm_defaults(args)


def _build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae config",
        description="Machine-wide LLM defaults (~/.tesserae/config.json): llm | show.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae config llm --llm-provider codex --codex-home ~/.codex-personal1\n"
            "  tesserae config show\n"
        ),
    )
    sub = parser.add_subparsers(dest="config_command", required=True)

    p_llm = sub.add_parser(
        "llm",
        help="Set machine-wide LLM backend defaults for ALL projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae config llm --llm-provider codex --codex-home ~/.codex-personal1\n"
            "  tesserae config llm --llm-provider claude --claude-config-dir ~/.claude-personal2\n"
        ),
    )
    p_llm.add_argument("--llm-provider", choices=["claude", "codex"], default=None, help="Default CLI backend for the synthesis/insights LLM client on this machine")
    p_llm.add_argument("--claude-config-dir", action="append", default=[], help="Default Claude CLI config directory; repeat for fallback accounts")
    p_llm.add_argument("--codex-home", default=None, help="Default Codex CLI home (e.g. ~/.codex-personal1)")
    p_llm.set_defaults(_handler="_handle_config_llm")

    p_show = sub.add_parser("show", help="Print the effective machine-wide LLM defaults and exit.")
    p_show.set_defaults(_handler="_handle_config_show")
    return parser


def _route_config(rest: List[str]) -> int:
    args = _build_config_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- projects -------------------------------------------------------------
def _handle_projects_register(args: argparse.Namespace) -> int:
    # Convenience: if the target is an existing directory that isn't a
    # Tesserae project yet, initialize a bare workspace first so register
    # "just works" instead of failing with "No .tesserae/graph.json found".
    # A missing/typo'd path is NOT created (it stays a register error), and
    # an already-initialized project is left untouched (no config overwrite).
    candidate = Path(args.path).expanduser()
    if (
        candidate.is_dir()
        and candidate.name != ".tesserae"
        and not (candidate / ".tesserae" / "graph.json").is_file()
    ):
        from .project import ProjectWiki

        ProjectWiki.init(candidate, name=args.name)
        alias = args.name or candidate.name
        print(
            f"{candidate} was not a Tesserae project — initialized .tesserae/ "
            f"(run `tesserae compile --project {alias}` to populate the graph)."
        )
    args.wiki_command = "register"
    return _wiki_command_handler(args)


def _handle_projects_list(args: argparse.Namespace) -> int:
    args.wiki_command = "list"
    return _wiki_command_handler(args)


def _handle_projects_activate(args: argparse.Namespace) -> int:
    args.wiki_command = "activate"
    return _wiki_command_handler(args)


def _handle_projects_unregister(args: argparse.Namespace) -> int:
    args.wiki_command = "unregister"
    return _wiki_command_handler(args)


def _handle_projects_mcp_config(args: argparse.Namespace) -> int:
    """`projects mcp-config` = old `mcp-config`."""
    return _handle_mcp_config(args)


def _build_projects_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae projects",
        description="Project registry: register | list | activate | unregister | mcp-config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae projects register /path/to/project\n"
            "  tesserae projects list\n"
            "  tesserae projects activate myproj\n"
        ),
    )
    sub = parser.add_subparsers(dest="projects_command", required=True)

    p_register = sub.add_parser(
        "register",
        help="Register a project root in the persistent registry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae projects register /path/to/project\n"
            "  tesserae projects register /path/to/project --name myproj --activate\n"
        ),
    )
    p_register.add_argument("path", help="Path to the project root containing .tesserae/.")
    p_register.add_argument("--name", help="Friendly name (defaults to the sanitized directory name).")
    p_register.add_argument("--activate", action="store_true", help="Also set the new entry as the active project.")
    p_register.set_defaults(_handler="_handle_projects_register")

    p_list = sub.add_parser("list", help="List registered projects and show the active one.")
    p_list.add_argument("--json", dest="wiki_list_json", action="store_true", help="Emit the registry payload as JSON.")
    p_list.set_defaults(_handler="_handle_projects_list")

    p_activate = sub.add_parser("activate", help="Set a registered project as the active one.")
    p_activate.add_argument("name")
    p_activate.set_defaults(_handler="_handle_projects_activate")

    p_unregister = sub.add_parser("unregister", help="Remove a project from the registry.")
    p_unregister.add_argument("name")
    p_unregister.set_defaults(_handler="_handle_projects_unregister")

    p_mcp = sub.add_parser("mcp-config", help="Print a Hermes mcp_servers config snippet for this project")
    p_mcp.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_mcp.add_argument("--server-name", help="MCP server name in Hermes config")
    p_mcp.add_argument("--pythonpath", help="PYTHONPATH pointing at the Tesserae checkout")
    p_mcp.set_defaults(_handler="_handle_projects_mcp_config")
    return parser


def _route_projects(rest: List[str]) -> int:
    args = _build_projects_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- integrations ---------------------------------------------------------
def _handle_integrations_refresh(args: argparse.Namespace) -> int:
    """`integrations refresh <name>` routes to the two old refresh handlers."""
    if args.name == "raganything":
        return _handle_refresh_raganything(args)
    return _handle_refresh_understand_anything(args)


def _build_integrations_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae integrations",
        description="Managed integration refreshes: refresh <name>.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae integrations refresh raganything\n"
            "  tesserae integrations refresh understand-anything\n"
        ),
    )
    sub = parser.add_subparsers(dest="integrations_command", required=True)
    p_refresh = sub.add_parser(
        "refresh",
        help="Run the managed refresh for raganything | understand-anything",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae integrations refresh raganything\n"
            "  tesserae integrations refresh understand-anything --full\n"
        ),
    )
    p_refresh.add_argument("name", choices=["raganything", "understand-anything"], help="Integration to refresh")
    p_refresh.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    # raganything flags (refresh-raganything parser)
    p_refresh.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"], help="raganything parser backend")
    p_refresh.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"], help="raganything parse method")
    p_refresh.add_argument("--root", action="append", dest="roots", help="Restrict to this root (repeatable; raganything)")
    p_refresh.add_argument("--full", action="store_true", help="Force a full refresh")
    p_refresh.add_argument("--force", action="store_true", help="Run even if the existing graph appears current")
    # understand-anything flags (refresh-understand-anything parser)
    p_refresh.add_argument("--platform", default="codex", help="Agent platform to use: codex, opencode, or claude (understand-anything)")
    p_refresh.add_argument("--timeout", type=int, help="Optional timeout in seconds (understand-anything)")
    p_refresh.set_defaults(_handler="_handle_integrations_refresh")
    return parser


def _route_integrations(rest: List[str]) -> int:
    args = _build_integrations_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- lab ------------------------------------------------------------------
def _handle_lab_evolve(args: argparse.Namespace) -> int:
    """`lab evolve` = old `evolve`."""
    return _handle_evolve(args)


def _handle_lab_schema_drift(args: argparse.Namespace) -> int:
    """`lab schema-drift` = old `schema-drift`."""
    return _handle_schema_drift(args)


def _build_lab_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae lab",
        description="Experimental LLM ops: evolve | schema-drift.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae lab evolve\n"
            "  tesserae lab schema-drift\n"
        ),
    )
    sub = parser.add_subparsers(dest="lab_command", required=True)

    p_evolve = sub.add_parser(
        "evolve",
        help="Distill collected human-correction feedback into .tesserae/extraction-guidance.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae lab evolve\n"
            "  tesserae lab evolve --project ../other-repo\n"
        ),
    )
    p_evolve.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_evolve.set_defaults(_handler="_handle_lab_evolve")

    p_drift = sub.add_parser("schema-drift", help="EDC-style pass that proposes ResearchNodeType sub-types from clustered host-type nodes.")
    p_drift.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_drift.add_argument("--host-type", action="append", default=[], help="ResearchNodeType to analyze (enum value, e.g. 'SourceDocument'). Repeat to analyze multiple. Default: SourceDocument.")
    p_drift.add_argument("--min-volume", type=int, default=10, help="Skip host types with fewer than this many members (default: 10)")
    p_drift.add_argument("--top-k", type=int, default=5, help="Take only the top-K clusters per host type (default: 5)")
    p_drift.add_argument("--min-cluster-size", type=int, default=5, help="Drop clusters smaller than this size (default: 5)")
    p_drift.add_argument("--jaccard-threshold", type=float, default=0.34, help="Jaccard similarity threshold for clustering (default: 0.34)")
    p_drift.set_defaults(_handler="_handle_lab_schema_drift")
    return parser


def _route_lab(rest: List[str]) -> int:
    args = _build_lab_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- extract --------------------------------------------------------------
def _build_extract_parser() -> argparse.ArgumentParser:
    """The preserved bare-extraction parser, surfaced as `tesserae extract`.

    Flags copied verbatim from the legacy bare-extraction main (now removed; the
    kuzu/cognee/canonicalize flags live HERE, not on `compile`).
    """
    parser = argparse.ArgumentParser(
        prog="tesserae extract",
        description="Extract a typed research intelligence graph from Tesserae notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae extract notes/ --pretty\n"
            "  tesserae extract notes/idea.md -o graph.json --trends\n"
        ),
    )
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
    return parser


def _handle_extract(args: argparse.Namespace) -> int:
    """Body lifted verbatim from the legacy bare-extraction main (now removed;
    sans its own parse_args)."""
    if args.extractor == "claude-cli":
        extractor = ClaudeCLIResearchExtractor(
            config_dirs=args.claude_config_dir or None,
            model=args.claude_model,
            timeout=args.claude_timeout,
        )
    elif args.extractor == "selective-claude":
        deterministic = ResearchGraphExtractor()
        claude = ClaudeCLIResearchExtractor(
            config_dirs=args.claude_config_dir or None,
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


def _route_extract(rest: List[str]) -> int:
    args = _build_extract_parser().parse_args(rest)
    return _handle_extract(args)


# ----- standalone verbs: research / lint / query ----------------------------
def _build_research_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae research",
        description="Agentic research loop: plan → search → reflect → synthesize.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae research \"how do agents use the typed graph?\"\n"
            "  tesserae research \"compiler internals\" --breadth 4 --depth 3\n"
        ),
    )
    parser.add_argument("query", help="Research query to investigate")
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--breadth", type=int, default=3, help="Sub-questions per level (default: 3)")
    parser.add_argument("--depth", type=int, default=2, help="Maximum follow-up depth beyond the root (default: 2)")
    parser.add_argument("--max-iters", type=int, default=6, help="Hard cap on (search + reflect) iterations (default: 6)")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K graph evidence nodes per sub-question (default: 5)")
    parser.add_argument("--output", help="Report output path; defaults to .tesserae/research/<slug>.md")
    parser.add_argument("--no-web", action="store_true", help="Disable web search even if a backend is configured (v1 default — web stays off)")
    return parser


def _route_research(rest: List[str]) -> int:
    args = _build_research_parser().parse_args(rest)
    return _resolve_handler("_handle_research")(args)


def _build_lint_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae lint",
        description="Lint the compiled wiki: orphan papers, stale citations, drift, ghost synthesis inputs, and more.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae lint\n"
            "  tesserae lint --fix-trivial --severity error\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--fix-trivial", action="store_true", help="Apply safe auto-fixes (add missing implemented_in edges; prune ghost synthesis inputs)")
    parser.add_argument("--severity", choices=["info", "warning", "error"], default="warning", help="Severity floor for the exit code (default: warning). Findings below the floor are still reported.")
    parser.add_argument("--json", dest="lint_json", action="store_true", help="Print the JSON report to stdout instead of the markdown summary.")
    return parser


def _route_lint(rest: List[str]) -> int:
    args = _build_lint_parser().parse_args(rest)
    return _resolve_handler("_handle_lint")(args)


def _build_query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae query",
        description="Search the compiled wiki and (optionally) ask the LLM for a synthesized answer with citations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae query \"what cites the compile pipeline?\"\n"
            "  tesserae query \"open questions\" --llm --top-k 12\n"
        ),
    )
    parser.add_argument("question", nargs="?", default=None, help="Question text; omit to use --interactive")
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--top-k", type=int, default=8, help="Maximum number of search hits to return / feed to the LLM (default: 8)")
    parser.add_argument("--kind", help="Restrict hits to a single wiki kind (e.g. papers, concepts, repos)")
    parser.add_argument("--llm", action="store_true", help="Force the LLM path on, even if TESSERAE_QUERY_LLM is unset")
    parser.add_argument("--no-llm", action="store_true", help="Force the LLM path off, even if TESSERAE_QUERY_LLM=1")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model id for --llm (default: claude-sonnet-4-6)")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print the structured QueryResult as JSON")
    parser.add_argument("--interactive", action="store_true", help="Drop into a REPL with readline history; blank line or EOF exits")
    return parser


def _route_query(rest: List[str]) -> int:
    args = _build_query_parser().parse_args(rest)
    return _resolve_handler("_handle_query")(args)


def _resolve_handler(name: str) -> Callable[[argparse.Namespace], int]:
    """Resolve a handler by its module-level name at CALL time.

    Routing through this indirection (instead of binding the function object
    when the dispatch table is built) is what lets tests
    ``monkeypatch.setattr(cli, "_handle_…", stub)`` intercept the dispatch.
    """
    import sys as _sys

    return getattr(_sys.modules[__name__], name)


_NEW_DISPATCH: Dict[str, Callable[[List[str]], int]] = {
    "ask": _route_ask,
    "init": _route_init,
    "compile": _route_compile,
    "context": _route_context,
    "serve": _route_serve,
    "status": _route_status,
    "engine": _route_engine,
    "refresh": _route_refresh,
    # task 5: groups
    "sessions": _route_sessions,
    "vault": _route_vault,
    "export": _route_export,
    "code": _route_code,
    "ingest": _route_ingest,
    "config": _route_config,
    "projects": _route_projects,
    "integrations": _route_integrations,
    "lab": _route_lab,
    "extract": _route_extract,
    # task 5: standalone verbs
    "research": _route_research,
    "lint": _route_lint,
    "query": _route_query,
}


def _dispatch_command(command: str, rest: List[str]) -> int:
    router = _NEW_DISPATCH.get(command)
    if router is not None:
        return router(rest)
    raise NotImplementedError(f"tesserae {command}: wired in a later task")


if __name__ == "__main__":
    raise SystemExit(main())
