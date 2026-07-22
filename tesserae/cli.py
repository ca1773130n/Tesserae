"""CLI for Tesserae research graph extraction."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .activity_summary import SummaryResult, build_summary, resolve_windows
from .batch import BatchIngestRunner
from .canonicalization import GraphCanonicalizer, ReviewDecision
from .harness_sessions import HarnessSession, HarnessSessionStore, discover_harness_sessions, session_matches_project
from .ingest.orchestrator import ingest_sources
from .llm_extractor import ClaudeCLIResearchExtractor, LLMResearchExtractor
from .locking import CompileLockHeldError
from .markdown_projection import GraphMarkdownProjector
from .persistence import KuzuResearchGraphStore, SQLiteResearchGraphStore
from .graphiti_adapter import GraphitiSyncUnavailableError
from .project import ProjectWiki, SessionExtractionOptions, iter_markdown_files, load_graph_file as _load_graph_file, resolve_project_input
from .project_setup import refresh_configured_external_tools
from .report import GraphReporter
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

    ``query`` is the raw-retrieval surface: BM25/semantic search over the
    compiled wiki by default, or an explicit backend
    (``--backend raganything``) for the optional memory backend.
    LLM synthesis lives on ``tesserae ask``; the wiki path here still honors
    the ``TESSERAE_QUERY_LLM`` env gate for scripted setups.
    """

    from .query import QueryResult, WikiQuery, env_enabled

    project_root = args.project
    top_k = args.top_k
    kind_filter = args.kind
    json_output = bool(args.json_output)
    interactive = bool(args.interactive)
    backend = getattr(args, "backend", "wiki") or "wiki"
    agent = getattr(args, "agent", None)

    if backend == "cognee":
        # Clean-break stub (no-silent-aliases convention): one line, exit 2.
        # "cognee" stays parseable so this stub can answer instead of a
        # confusing argparse choices error.
        print(
            "removed in 0.19 — cognee was demoted in 0.18 and never fed the "
            "graph; use plain query or ask",
            file=sys.stderr,
        )
        return 2

    if backend == "raganything":
        from .query import ask_project

        if agent:
            print(
                "query: --agent is not supported with --backend raganything "
                "(no typed graph to scope). Use the default wiki backend.",
                file=sys.stderr,
            )
            return 2
        if interactive:
            print("query: --interactive is wiki-search only (not usable with --backend)", file=sys.stderr)
            return 2
        question = (args.question or "").strip()
        if not question:
            print("query: question is required", file=sys.stderr)
            return 2
        try:
            wiki = ProjectWiki.load(args.project)
        except FileNotFoundError:
            print(f"No Tesserae project at {args.project}. Did you run `tesserae init`?", file=sys.stderr)
            return 2
        try:
            envelope = ask_project(
                wiki,
                question,
                backend=backend,
                top_k=top_k,
                use_llm=False,
                no_llm=True,
            )
        except RuntimeError as exc:
            # Backend-specific failures with explicit --backend surface here.
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"query failed: {exc}", file=sys.stderr)
            return 2
        return _emit_ask_envelope(envelope, json_output=json_output)

    wq = WikiQuery(project_root, top_k=top_k, kind_filter=kind_filter)

    # `query` runs BM25 over the projected search index (not the typed graph),
    # so --agent post-filters hits to the resolved view's node set: a hit
    # survives only when its node_id is a member of the agent's view. Hits with
    # no node_id can't be proven in-scope, so they drop.
    agent_node_ids = None
    if agent:
        try:
            wiki = ProjectWiki.load(project_root)
        except FileNotFoundError:
            print(f"No Tesserae project at {project_root}. Did you run `tesserae init`?", file=sys.stderr)
            return 2
        if not wiki.paths.graph.exists():
            print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
            return 2
        resolved = _resolve_agent_view_or_none(wiki.project_root, wiki.paths.graph, agent)
        if resolved is None:
            return 1
        view, _info = resolved
        agent_node_ids = {node.id for node in view.nodes}

    def run_one(question: str, history: List[dict] | None = None) -> "QueryResult":
        result = wq.answer(question, history=history)
        if agent_node_ids is not None:
            result.hits = [hit for hit in result.hits if hit.node_id in agent_node_ids]
        return result

    use_llm = env_enabled()  # REPL history tracking only; flags moved to `ask`

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


def _emit_ask_envelope(envelope: dict, *, json_output: bool) -> int:
    """Print an ``ask_project`` envelope in human or JSON form.

    Shared by the top-level ``ask`` command and ``query --backend ...`` so
    output formatting stays in lockstep with the dispatcher's contract.
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
    if backend == "wiki":
        notes = envelope.get("auto_notes") or []
        if notes:
            # auto fell through to wiki because a richer backend errored —
            # say so, so the user isn't left thinking wiki was the only try.
            print(f"(auto: {'; '.join(notes)} — fell back to wiki search)", file=sys.stderr)
        plan = envelope.get("plan") or {}
        plan_steps = plan.get("steps") or []
        if plan_steps:
            rendered = "; ".join(
                f"{s.get('action')}({json.dumps(s.get('args') or {}, ensure_ascii=False)})"
                for s in plan_steps
            )
            reason = str(plan.get("reasoning") or "")
            print(f"(retrieval plan: {rendered}{' — ' + reason if reason else ''})", file=sys.stderr)
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
        # A planned KG answer can be fully grounded in graph evidence with no
        # wiki-page hits — "No matches" would be misleading next to it.
        if not result.answer:
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


# --------------------------------------------------------------------------- #
# CLI-1 — agent-scoped reads (`--agent` on query / ask / context).
#
# `--agent KEY` runs the existing retrieval/synthesis over one agent's resolved
# view instead of the raw L0 graph. KEY may be a worker key (its L0 ∪ own
# distillate), a manager key (its team's distillates) or 'org' (every agent's
# distillate). Unknown keys / missing artifacts fail loud with the resolver's
# remedy command. The default (no --agent) path stays byte-identical.
# --------------------------------------------------------------------------- #


def _resolve_agent_view_or_none(project_root, graph_path, agent, *, l0=None):
    """Resolve ``agent``'s read view over the L0 graph at ``graph_path``.

    Returns ``(view_graph, info)`` on success, or ``None`` after printing the
    :class:`AgentViewError` remedy message (unknown key / missing artifact) so
    the caller can map ``None`` to a fail-loud exit 1. ``l0`` lets a caller that
    already materialized the base graph avoid re-reading it.
    """

    from .agent_view import AgentViewError, resolve_agent_view

    if l0 is None:
        l0 = _load_graph_file(graph_path)
    try:
        return resolve_agent_view(project_root, agent, l0, l0_path=graph_path)
    except AgentViewError as exc:
        print(str(exc), file=sys.stderr)
        return None


class _GraphPathProxy:
    """A ``wiki.paths`` stand-in that swaps only ``.graph`` for a scoped copy."""

    def __init__(self, real_paths, graph_path):
        self._real = real_paths
        self.graph = graph_path

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AgentScopedWiki:
    """A ``ProjectWiki`` proxy whose ``paths.graph`` points at a materialized
    agent view.

    Everything else delegates to the real wiki, so ``ask_project`` and the
    planner read the SCOPED graph while sessions, config and ``project_root``
    stay real. The scoped graph is a throwaway temp file — nothing is written to
    a committed artifact, so artifact byte-idempotence is untouched.
    """

    def __init__(self, real_wiki, graph_path):
        self._wiki = real_wiki
        self.paths = _GraphPathProxy(real_wiki.paths, graph_path)

    def __getattr__(self, name):
        return getattr(self._wiki, name)


def _run_scoped_ask(wiki, view, question, *, top_k, no_llm):
    """Run ``ask_project`` over an agent-scoped ``view`` by materializing it to a
    throwaway ``graph.json`` and pointing an :class:`_AgentScopedWiki` at it.

    ask reaches the graph only through ``wiki.paths.graph`` (the planner loads it
    lazily), so re-pointing that one path scopes the whole planner without
    touching ``query.py`` / ``ask_planner.py``.
    """

    import tempfile

    from .query import ask_project

    with tempfile.TemporaryDirectory(prefix="tesserae-agentview-") as tmp:
        scoped_path = Path(tmp) / "graph.json"
        scoped_path.write_text(view.to_json(indent=2) + "\n", encoding="utf-8")
        scoped_wiki = _AgentScopedWiki(wiki, scoped_path)
        return ask_project(
            scoped_wiki, question, top_k=top_k, use_llm=not no_llm, no_llm=no_llm
        )


def _top_level_ask_handler(args) -> int:
    """Route a question and call the shared ask dispatcher.

    ``ask`` is the LLM-answer surface: the model plans retrieval over the
    knowledge graph and synthesizes a cited answer by default; ``--no-llm``
    forces search-only (and beats ``TESSERAE_QUERY_LLM=1``). With no
    ``--scope`` and no explicit project, a smart router (ask_router) decides
    where the question goes — one project, all registered, or federated (the
    fallback). An explicit ``--scope`` (current/all-registered/federated) or
    ``--project``/``--name`` overrides the router. ``current`` resolves one
    project via ``--project`` → ``--name`` → the project you're standing in
    (cwd ancestor). There is no active project.
    """

    from .mcp_server import ProjectRegistry
    from .query import ask_project

    # B2 — multi-project scope. We dispatch through the same ask_project
    # helper for each registered project, then aggregate the envelopes
    # under one top-level wrapper so JSON consumers can iterate the
    # ``by_project`` map. ``current`` (default) keeps the legacy
    # single-project behaviour byte-for-byte.
    registry = ProjectRegistry()
    scope = getattr(args, "scope", None)
    explicit_project = bool(args.project or args.name)

    # No --scope and no explicit project => SMART ROUTER picks where the question
    # goes (single project / all-registered / federated), federated as the
    # fallback so "unsure" never means the wrong project. Replaces active-project.
    if scope is None and not explicit_project:
        from .ask_router import SCOPE_ALL, SCOPE_CURRENT, SCOPE_FEDERATED, make_llm_classifier, route_ask
        from .llm_json import build_rotating_client

        cwd_root = registry.resolve_project_by_cwd()
        cwd_alias = registry.alias_for_root(cwd_root) if cwd_root else None
        # LLM classifier (lazy, no API key) fires ONLY for the ambiguous middle;
        # heuristic-resolved questions never build or call it.
        route = route_ask(
            args.question, registry.all_project_names(), cwd_alias=cwd_alias,
            llm_classify=make_llm_classifier(build_rotating_client),
        )
        tail = (" " + ", ".join(route.aliases)) if route.aliases else ""
        print(f"(scope: {route.scope}{tail} — {route.reason})", file=sys.stderr)
        scope = route.scope
        if route.scope in (SCOPE_FEDERATED, SCOPE_ALL):
            args.scope_aliases = route.aliases  # an LLM-narrowed subset must be honored
        elif route.scope == SCOPE_CURRENT and route.aliases:
            args.name = route.aliases[0]
    elif scope is None:
        scope = "current"  # explicit --project/--name => single-project

    # --agent scopes ONE project's agent view — it only makes sense on the
    # single-project (current) path, and needs the LLM planner to read the
    # scoped graph (search-only --no-llm goes through the unscoped index).
    agent = getattr(args, "agent", None)
    if agent:
        if scope in ("all-registered", "federated"):
            print(
                "ask: --agent scopes ONE project's agent view — it is incompatible "
                "with --scope all-registered/federated. Use --scope current "
                "(or --project/--name).",
                file=sys.stderr,
            )
            return 2
        if bool(getattr(args, "no_llm", False)):
            print(
                "ask: --agent needs the LLM planner to read the scoped graph; "
                "search-only --no-llm can't scope to an agent view. Drop --no-llm, "
                "or use `tesserae query --agent`.",
                file=sys.stderr,
            )
            return 2

    if scope == "all-registered":
        return _top_level_ask_scope_all_registered(args)
    if scope == "federated":
        return _top_level_ask_scope_federated(args)

    # scope == "current": one project via --project / --wiki / cwd-ancestor.
    project_root: Optional[Path] = None
    source: str = ""

    if args.project:
        project_root = Path(args.project).expanduser().resolve()
        source = f"--project {project_root}"
    elif args.name:
        data = registry.load()
        entry = (data.get("projects") or {}).get(args.name)
        if not entry:
            print(
                f"No registered project named '{args.name}'. "
                f"Run `tesserae projects list` to see available names, or "
                f"`tesserae projects register <path> --name {args.name}` to register one.",
                file=sys.stderr,
            )
            return 2
        if entry.get("root"):
            project_root = Path(entry["root"]).resolve()
        else:
            gp = Path(entry["graph_path"]).resolve()
            project_root = gp.parent.parent if gp.parent.name == ".tesserae" else gp.parent
        source = f"--name {args.name}"
    else:
        cwd_root = registry.resolve_project_by_cwd()
        if cwd_root is None:
            known = ", ".join(registry.all_project_names()) or "(none registered)"
            print(
                "No project specified and not inside a registered project. Pass "
                "--project <path> / --name <name>, use --scope all-registered|federated, "
                f"or cd into a registered project. Registered: {known}.",
                file=sys.stderr,
            )
            return 2
        project_root = cwd_root
        source = f"cwd project {cwd_root}"

    try:
        wiki = ProjectWiki.load(project_root)
    except FileNotFoundError:
        print(
            f"No Tesserae project at {project_root} (resolved from {source}). "
            f"Did you run `tesserae init` there?",
            file=sys.stderr,
        )
        return 2

    no_llm = bool(getattr(args, "no_llm", False))
    try:
        if agent:
            if not wiki.paths.graph.exists():
                print("ask: no compiled graph yet — run `compile` first.", file=sys.stderr)
                return 2
            resolved = _resolve_agent_view_or_none(wiki.project_root, wiki.paths.graph, agent)
            if resolved is None:
                return 1
            view, _info = resolved
            envelope = _run_scoped_ask(
                wiki, view, args.question, top_k=args.top_k, no_llm=no_llm
            )
        else:
            envelope = ask_project(
                wiki,
                args.question,
                top_k=args.top_k,
                use_llm=not no_llm,
                no_llm=no_llm,
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ask failed: {exc}", file=sys.stderr)
        return 2

    return _emit_ask_envelope(envelope, json_output=bool(args.json_output))


def _top_level_ask_scope_federated(args) -> int:
    """Federated scope — assemble ONE identity-merged graph from the named
    projects and compile a single cross-referenced, cited answer (not the
    per-project fan-out). Defaults to ALL registered projects; --scope-aliases narrows it."""
    from .federation import federated_recall
    from .mcp_server import ProjectRegistry

    registry = ProjectRegistry()
    aliases = list(getattr(args, "scope_aliases", None) or [])
    if not aliases:
        aliases = registry.all_project_names()  # federate everything by default
    if not aliases:
        print(
            "No registered projects to federate. Register some with "
            "`tesserae projects register <path>`.",
            file=sys.stderr,
        )
        return 2
    try:
        envelope = federated_recall(
            aliases,
            args.question,
            synthesize=not bool(getattr(args, "no_llm", False)),
            semantic=bool(getattr(args, "semantic", False)),
            recency_weight=getattr(args, "recency_weight", None),
            registry=registry,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if bool(getattr(args, "json_output", False)):
        print(json.dumps(envelope, ensure_ascii=False, indent=2, default=str))
        return 0
    stats = envelope["stats"]
    print(
        f"Federated scope · projects: {', '.join(envelope['projects'])} · "
        f"nodes={stats['nodes']} merged_groups={stats['merged_groups']}"
    )
    print(f"question: {args.question!r}\n")
    print(envelope["body"])
    return 0


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
    no_llm = bool(getattr(args, "no_llm", False))
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
            # Bug fix: thread the LLM knobs into the fan-out (previously the
            # all-registered path silently dropped them vs the current scope).
            envelope = ask_project(
                wiki,
                args.question,
                top_k=args.top_k,
                use_llm=not no_llm,
                no_llm=no_llm,
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
        projects = data.get("projects") or []
        if not projects:
            print("No projects registered. Use `tesserae projects register <path> --name <alias>`.")
            return 0
        print(f"{len(projects)} registered project(s) — all active (no privileged project):")
        for entry in projects:
            print(f"   {entry.get('name', ''):<24} {entry.get('root', '')}")
        return 0

    if sub == "register":
        try:
            entry = registry.register(args.path, name=args.name)
        except Exception as exc:
            print(f"register failed: {exc}", file=sys.stderr)
            return 2
        print(f"Registered '{entry['name']}' -> {entry['root']}")
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
        "Usage: tesserae projects {list|register|unregister|obsidian-set-root|obsidian-sync-all}",
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


class _RemovedFlagAction(argparse.Action):
    """Clean-break stub for a removed/renamed FLAG: one-line stderr + exit 2.

    Mirrors the MOVED_COMMANDS convention (cli_tree.py) at flag granularity —
    never a silent alias. ``nargs="?"`` swallows an optional attached value so
    both ``--flag value`` and ``--flag=value`` hit the stub instead of a
    confusing argparse error. Reusable: pass ``message=`` via
    ``parser.add_argument(..., action=_RemovedFlagAction, message="...")``.
    """

    def __init__(self, option_strings, dest, message: str = "flag removed", **kwargs):
        self.message = message
        kwargs.setdefault("nargs", "?")
        kwargs.setdefault("help", argparse.SUPPRESS)
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(self.message, file=sys.stderr)
        raise SystemExit(2)


def _build_top_level_ask_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae ask",
        description=(
            "LLM answer over the knowledge graph (planned retrieval). The model plans "
            "retrieval over the compiled graph, then synthesizes a cited answer — this "
            "is the default; pass --no-llm for ranked search hits only. Works with a "
            "logged-in claude/codex CLI (OAuth) or ANTHROPIC_API_KEY. With no --scope a "
            "smart router picks the target (one project / all / federated); --project, "
            "--name or --scope override it. Raw retrieval and the explicit "
            "raganything backend live on `tesserae query`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae ask \"what did we decide about the compiler?\"\n"
            "  tesserae ask \"summarize the graph schema\" --scope all-registered\n"
        ),
    )
    parser.add_argument("question", help="Natural-language question text.")
    parser.add_argument("--name", help="Registered project name (see `tesserae projects list`).")
    parser.add_argument("--project", help="Project root path (overrides --name).")
    parser.add_argument(
        "--wiki",
        action=_RemovedFlagAction,
        message="ask: --wiki has moved → --name",
    )
    parser.add_argument("--top-k", type=int, default=8, help="Maximum results/context items (default: 8).")
    parser.add_argument(
        "--agent",
        default=None,
        metavar="KEY",
        help="Scope the answer to one agent's distilled view: a worker key (its L0 ∪ own distillate), a manager key (its team's distillates), or 'org' (every agent's distillate). Needs the LLM planner and --scope current. Unknown/undistilled keys fail loud.",
    )
    parser.add_argument(
        "--llm",
        action=_RemovedFlagAction,
        message="ask: --llm is now the default; use --no-llm to disable",
    )
    parser.add_argument(
        "--no-llm",
        dest="no_llm",
        action="store_true",
        help="Skip the LLM planner/synthesis and return ranked search hits only "
        "(force-off: beats TESSERAE_QUERY_LLM=1).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the raw JSON envelope instead of the pretty-printed answer.",
    )
    parser.add_argument(
        "--backend",
        action=_RemovedFlagAction,
        message="ask: backend flags have moved → tesserae query",
    )
    # Removed backend (0.19): the old moved-flag stubs for --cognee-* now
    # report the removal instead of pointing at `tesserae query`.
    for _removed in ("--cognee-search-type", "--cognee-dataset"):
        parser.add_argument(
            _removed,
            action=_RemovedFlagAction,
            message=f"ask: {_removed} was removed in 0.19 — cognee was demoted in 0.18 and never fed the graph",
        )
    # Bet B2 — registry-scoped fan-out.
    parser.add_argument(
        "--scope",
        choices=["current", "all-registered", "federated"],
        default=None,
        help=(
            "Query scope. Omit it and a smart router picks per question (federated "
            "fallback). 'current' hits one project (--project/--wiki or the project "
            "you're in); 'all-registered' fans out, one answer per project; "
            "'federated' merges projects into ONE graph and returns a single "
            "cross-referenced answer (defaults to ALL registered; narrow with --scope-aliases)."
        ),
    )
    parser.add_argument(
        "--scope-aliases",
        # ONE comma-separated value (not nargs="*"): a greedy nargs list here
        # used to swallow the positional question when the flag came first.
        type=lambda raw: [a.strip() for a in raw.split(",") if a.strip()],
        default=None,
        metavar="A,B,...",
        help=(
            "Comma-separated registered alias names. Optionally narrows the "
            "fan-out (--scope=all-registered) or the set of projects to "
            "federate (--scope=federated; defaults to ALL registered), e.g. "
            "--scope-aliases research,work."
        ),
    )
    parser.add_argument(
        "--semantic",
        dest="semantic",
        action="store_true",
        default=True,
        help=(
            "With --scope=federated, add embedding-backed cross-project links so "
            "the answer bridges RELATED (not just identical) concepts. ON by default; "
            "degrades cleanly without a real embedding backend (tesserae[semantic])."
        ),
    )
    parser.add_argument(
        "--no-semantic",
        dest="semantic",
        action="store_false",
        help="With --scope=federated, identity merges only (no embedding-backed links).",
    )
    parser.add_argument(
        "--recency-weight",
        type=float,
        default=None,
        help=(
            "Blend node recency into federated ranking [0..1] so a 'what's recent' "
            "query doesn't magnet onto old 'review of all work' syntheses (default "
            "0.25; pass 0 to rank by pure relevance)."
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
        choices=["claude", "codex", "anthropic", "custom"],
        default=None,
        help="Backend for the LLM client (claude/codex CLI over OAuth, anthropic API key, or a custom claude-compatible endpoint)" + suffix,
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
            "llm_codex_reasoning_effort": existing.get("llm_codex_reasoning_effort"),
            "llm_model": existing.get("llm_model"),
            "llm_base_url": existing.get("llm_base_url"),
            # NEVER echo the key itself — show only whether one is stored.
            "llm_api_key": "set" if existing.get("llm_api_key") else "unset",
        }
        print(f"Machine-wide LLM defaults ({path}):")
        print(_json.dumps(effective, ensure_ascii=False, indent=2))
        return 0
    reasoning = getattr(args, "reasoning_effort", None)
    llm_model = getattr(args, "llm_model", None)
    llm_base_url = getattr(args, "llm_base_url", None)
    llm_api_key = getattr(args, "llm_api_key", None)
    if not (args.llm_provider or args.claude_config_dir or args.codex_home or reasoning
            or llm_model or llm_base_url or llm_api_key):
        print(
            "Nothing to set — pass --llm-provider/--claude-config-dir/--codex-home/"
            "--reasoning-effort/--llm-model/--llm-base-url/--llm-api-key, "
            "or run `tesserae config show` to view the current defaults.",
            file=sys.stderr,
        )
        return 2
    merged = _merge_global_llm_config(
        existing,
        llm_provider=args.llm_provider,
        claude_config_dirs=args.claude_config_dir,
        codex_home=args.codex_home,
        reasoning_effort=reasoning,
        model=llm_model,
        base_url=llm_base_url,
        api_key=llm_api_key,
    )
    _write_global_config(path, merged)
    if llm_api_key:
        print(
            f"warning: llm_api_key is stored in plaintext in {path}; "
            "prefer the ANTHROPIC_API_KEY environment variable",
            file=sys.stderr,
        )
    print(f"Saved machine-wide LLM defaults to {path}:")
    for key in ("llm_provider", "llm_claude_config_dirs", "llm_codex_home",
                "llm_codex_reasoning_effort", "llm_model", "llm_base_url", "llm_api_key"):
        if key in merged:
            value = "(set)" if key == "llm_api_key" else merged[key]
            print(f"  {key}: {value}")
    return 0


def _merge_global_llm_config(existing: dict, *, llm_provider=None, claude_config_dirs=None,
                             codex_home=None, reasoning_effort=None, model=None,
                             base_url=None, api_key=None) -> dict:
    """Merge-preserving update of the machine-wide config: only passed keys change."""
    merged = dict(existing)
    if llm_provider:
        merged["llm_provider"] = llm_provider
    if claude_config_dirs:
        merged["llm_claude_config_dirs"] = list(claude_config_dirs)
    if codex_home:
        merged["llm_codex_home"] = codex_home
    if reasoning_effort:
        merged["llm_codex_reasoning_effort"] = reasoning_effort
    if model:
        merged["llm_model"] = model
    if base_url:
        merged["llm_base_url"] = base_url
    if api_key:
        merged["llm_api_key"] = api_key
    return merged


def _write_global_config(path, merged: dict) -> None:
    import json as _json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    # The custom-endpoint knobs are persisted the same only-when-set way as
    # llm_provider above (ProjectWiki.init predates them; patch config here so
    # `--bare` never silently drops a flag).
    endpoint_keys = {
        "llm_model": getattr(args, "llm_model", None),
        "llm_base_url": getattr(args, "llm_base_url", None),
        "llm_api_key": getattr(args, "llm_api_key", None),
    }
    endpoint_keys = {k: v for k, v in endpoint_keys.items() if v}
    if endpoint_keys:
        cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
        cfg.update(endpoint_keys)
        wiki.paths.config.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if "llm_api_key" in endpoint_keys:
            print(
                f"warning: llm_api_key is stored in plaintext in {wiki.paths.config}; "
                "prefer the ANTHROPIC_API_KEY environment variable",
                file=sys.stderr,
            )
    print(f"Initialized project wiki: {wiki.root}")
    print(f"Graph: {wiki.paths.graph}")
    print("Next: tesserae compile")
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
            # LLM client flags (provider / dirs / model / base_url / api_key)
            # must reach the plan on BOTH paths — the old code silently
            # dropped them (only `init --bare` persisted them).
            llm_overrides: dict = {
                "llm_provider": getattr(args, "llm_provider", None),
                "llm_model": getattr(args, "llm_model", None),
                "llm_base_url": getattr(args, "llm_base_url", None),
                "llm_api_key": getattr(args, "llm_api_key", None),
                "codex_home": getattr(args, "codex_home", None),
                "claude_config_dir": (
                    args.claude_config_dir[0]
                    if getattr(args, "claude_config_dir", None)
                    else None
                ),
            }
            llm_overrides = {k: v for k, v in llm_overrides.items() if v is not None}
            if args.yes:
                yes_overrides: dict = {
                    "name": args.name,
                    "source_kind": args.source_kind,
                    "sources": args.source or None,
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
                }
                yes_overrides = {
                    k: v for k, v in yes_overrides.items() if v is not None
                }
                yes_overrides.update(llm_overrides)
                plan = build_plan(report, overrides=yes_overrides)
                print(render_review(plan), end="")
            else:
                try:
                    # CLI flags seed the wizard's recommended defaults.
                    wizard_defaults = (
                        build_plan(report, overrides=llm_overrides)
                        if llm_overrides
                        else None
                    )
                    plan = run_wizard(report, wizard_defaults)
                except WizardNotInteractive:
                    print(
                        "tesserae init: stdin is not a TTY. Re-run from a real "
                        "terminal, or run `tesserae init --yes` to use detected "
                        "defaults.",
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
                if "llm_api_key" in w and "plaintext" in w:
                    # apply_plan already printed this one at write time —
                    # exactly ONE plaintext-key warning per run.
                    continue
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
            # `compile <paths> --extractor` routes through here; honor the LLM
            # extractor (deterministic / unset -> None, unchanged).
            doc_extractor=(
                _build_doc_extractor(args, cfg=wiki.config())
                if getattr(args, "extractor", "deterministic") != "deterministic"
                else None
            ),
        )
        print(
            "Ingested project wiki: "
            f"processed={result['processed_files']} skipped={result['skipped_files']} "
            f"nodes={result['node_count']} edges={result['edge_count']}"
        )
        print(f"Graph: {result['graph_path']}")
        _warn_if_concept_poor(result)
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


_CONCEPT_LAYER_TYPES = frozenset({
    "Concept", "TechnicalTerm", "MethodologicalConcept", "MathematicalConcept",
    "Algorithm", "ArchitecturePattern", "TrainingParadigm", "InferenceStrategy",
    "ObjectiveFunction", "Task", "Capability", "ResearchTopic", "ProblemArea",
    "ApproachFamily", "Claim", "ContributionClaim", "PerformanceClaim",
    "ComparisonClaim", "LimitationClaim", "CausalClaim", "OpenQuestion",
})


def _warn_if_concept_poor(result: dict) -> None:
    """A non-trivial graph with NO concept/claim layer means retrieval is just
    full-text search over document blobs. The default deterministic extractor
    only mints concepts for registry-matching headings, so a docs-heavy compile
    can land here silently. Point the way to the LLM extractor. Best-effort."""
    try:
        if int(result.get("node_count", 0)) < 20:
            return
        from .project import load_graph_file

        graph = load_graph_file(result["graph_path"])
        conceptual = sum(
            1 for n in graph.nodes
            if (n.type.value if hasattr(n.type, "value") else str(n.type)) in _CONCEPT_LAYER_TYPES
        )
        if conceptual == 0:
            print(
                f"note: compiled {result['node_count']} nodes but no concept/claim layer — the "
                "deterministic extractor only mints concepts for known headings, so "
                "`ask` falls back to full-text search. For a real typed graph, recompile with "
                "`--extractor llm` (or `--extractor selective-llm --llm-include <globs>`).",
                file=sys.stderr,
            )
    except Exception:
        pass  # the hint must never break a successful compile


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
        # ``cognee_*`` compile_options keys are ignored: the cognee backend was
        # removed in 0.19 (a legacy config carrying the memory_backends.cognee
        # section gets a one-line note from the config loader).
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
        # AgentRunbook distillation toggle. The distillation + Event passes
        # read ``distillation_enabled(cfg)`` (env first, then config). A CLI
        # --distill / --no-distill sets the env var for this process so it
        # overrides config for this run; unset leaves config in control.
        if getattr(args, "distill_enabled", None) is not None:
            import os as _os

            _os.environ["TESSERAE_RUNBOOK_DISTILLATION"] = (
                "true" if args.distill_enabled else "false"
            )

        from .compile_progress import NullCompileProgress, make_compile_progress

        # Live codegraph-style progress on an interactive terminal; a no-op
        # (and the plain summary line below) when piped/CI/MCP/daemon.
        progress = make_compile_progress()
        with progress:
            # --extractor != deterministic -> use the LLM extractor (concept/claim
            # layer). Default (deterministic) passes None so the pipeline keeps its
            # existing behaviour byte-for-byte.
            doc_extractor = (
                _build_doc_extractor(args, cfg=wiki.config())
                if getattr(args, "extractor", "deterministic") != "deterministic"
                else None
            )
            result = wiki.compile(
                source_kind=opts.get("source_kind", None),
                changed_only=args.changed_only,
                limit=args.limit,
                trends=bool(opts.get("trends", False)),
                min_trend_sources=int(opts.get("min_trend_sources", 2)),
                exclude_data=bool(opts.get("exclude_data", False)),
                vault_pull=not bool(opts.get("no_vault_pull", False)),
                session_options=session_override,
                use_extraction_feedback=bool(opts.get("use_extraction_feedback", False)),
                doc_extractor=doc_extractor,
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
        # Output-snapshot no-op signal (see tesserae/output_snapshot.py): a
        # stable, script-parseable line. Omitted when the key is absent
        # (defensive: injected compile doubles in older tests).
        if result.get("output_changed") is not None:
            print(
                f"Output: {'changed' if result['output_changed'] else 'unchanged'} "
                f"(sha256 {str(result.get('output_sha256', ''))[:12]})"
            )
        # Code-graph reuse signal (delta-scoped regeneration, see
        # tesserae/code_graph.py). Omitted when the code branch didn't run
        # (non-code projects, injected compile doubles). Info-only — never a
        # failure condition, so --strict is deliberately not extended.
        cg = result.get("code_graph_cache")
        if cg is not None:
            if cg["reused"]:
                print(f"Code graph: reused (tree unchanged, {cg['files']} files)")
            else:
                d = cg.get("delta") or {}
                print(
                    f"Code graph: re-extracted ({cg['files']} files; delta "
                    f"+{d.get('added', 0)} ~{d.get('changed', 0)} -{d.get('removed', 0)})"
                )
        _warn_if_concept_poor(result)
        # --strict: gate the exit code on the byte-idempotence tripwire first
        # (a suspected determinism regression outranks lint warnings), then on
        # the post-compile lint, reusing the `tesserae lint` exit-code mapping
        # at its default floor (warning): errors → 2, warnings → 1. Default
        # stays report-only.
        if getattr(args, "strict", False):
            if result.get("idempotence_suspect"):
                print(
                    "compile --strict: projections changed while graph/config were "
                    "byte-identical — byte-idempotence regression suspected",
                    file=sys.stderr,
                )
                return 2
            lint_counts = result.get("lint")
            if lint_counts is None:
                # compile() swallows lint crashes and omits the key entirely;
                # strict must fail CLOSED on a missing signal, not exit 0.
                print(
                    "compile --strict: lint did not run (crashed); failing --strict",
                    file=sys.stderr,
                )
                return 2
            if int(lint_counts.get("errors", 0)) > 0:
                print(
                    "compile --strict: lint reported errors — see .tesserae/lint-report.md",
                    file=sys.stderr,
                )
                return 2
            if int(lint_counts.get("warnings", 0)) > 0:
                print(
                    "compile --strict: lint reported warnings — see .tesserae/lint-report.md",
                    file=sys.stderr,
                )
                return 1
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
        # punt: web search is not implemented (web=None always) — wiring a
        # stdlib DuckDuckGo scraper is finicky to test deterministically and
        # adds zero value without a real BeautifulSoup-style HTML parser.
        # (The dead --no-web flag was removed; it is a stub now.)
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
        # A non-empty discovery is authoritative (replace: prunes stale
        # records); an EMPTY discovery merges (a no-op) so a scan that finds
        # nothing — wrong HOME, detached harness roots — never wipes the store.
        return store.write_sessions(sessions, replace=bool(sessions))  # {"sessions": n, "path": ...}

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

    def step_chunk_backfill():
        # Skip-if-held flock inside backfill(); chunking is an optimization —
        # any failure degrades to the raw scan and must not fail the refresh.
        from .session_chunks import backfill

        try:
            result = backfill(wiki.project_root)
        except Exception as exc:  # noqa: BLE001
            return {"skipped": f"backfill failed: {exc}"}
        if result.skipped:
            return {"skipped": result.reason}
        return {"turns_inserted": result.turns_inserted, "days_covered": result.days_covered}

    def step_agent_distill():
        # §8.2 freshness path: consolidation fires when an agent's raw recall
        # stops fitting one read. Triple-gated inside (TESSERAE_AGENT_DISTILL
        # opt-in, watermark, memory pressure) — a no-op for everyone else, and
        # never a refresh failure.
        from .agent_distill import maybe_distill_on_refresh
        from .project import load_graph_file

        graph_path = wiki.project_root / ".tesserae" / "graph.json"
        if not graph_path.is_file():
            return {"skipped": "no compiled graph"}
        try:
            return maybe_distill_on_refresh(
                wiki.project_root, load_graph_file(graph_path)
            )
        except Exception as exc:  # noqa: BLE001 — optimization, not correctness
            return {"skipped": f"agent distill failed: {exc}"}

    steps = []
    if not args.no_sessions:
        steps.append(("sessions-import", step_sessions_import))
        steps.append(("chunk-backfill", step_chunk_backfill))
    steps += [
        ("compile", step_compile),
        ("agent-distill", step_agent_distill),
        ("obsidian-sync", step_obsidian_sync),
    ]

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
        report = wiki.lint(
            fix_trivial=args.fix_trivial,
            severity_floor=args.severity,
            verify_claims=args.verify_claims,
            claim_cap=args.claim_cap,
        )
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


def _handle_context(args: argparse.Namespace) -> int:
    from .context_compiler import compile_context

    wiki = ProjectWiki.load(args.project)
    if not wiki.paths.graph.exists():
        print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
        return 2
    graph = _load_graph_file(wiki.paths.graph)
    agent = getattr(args, "agent", None)
    if agent:
        resolved = _resolve_agent_view_or_none(
            wiki.project_root, wiki.paths.graph, agent, l0=graph
        )
        if resolved is None:
            return 1
        graph, _info = resolved
    bundle = compile_context(
        graph,
        str(wiki.project_root),
        query=args.query,
        seeds=args.seeds,
        depth=args.depth,
        budget=args.budget,
        synthesize=args.synthesize,
        multi_pool=getattr(args, "multi_pool", False),
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
    result = wiki.export_agent_harness(
        targets=args.target or None,
        output=args.output,
        install_pointer=getattr(args, "install_pointer", False),
    )
    print(f"Exported agent harness: files={result['files']} path={result['path']} targets={','.join(result['targets'])}")
    for name, status in result.get("pointer", {}).items():
        print(f"Pointer: {name} {status}")
    return 0


def _handle_export_obsidian(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    # `vault export --output` (the old `--vault` spelling is a removed-flag stub).
    result = wiki.export_obsidian(vault=args.output)
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
                # Non-empty discovery = authoritative replace; empty = merge
                # no-op so it never wipes previously imported sessions.
                result = store.write_sessions(sessions, replace=bool(sessions))
                print(f"Imported harness sessions: {result['sessions']} path={result['path']}")
            return 0
        if args.sessions_command == "list":
            sessions = store.list_sessions()
            if getattr(args, "as_json", False):
                print(json.dumps(
                    [
                        {
                            "date": session.date,
                            "harness": session.harness,
                            "project": session.project_name,
                            "title": session.title or session.slug,
                            "slug": session.slug,
                        }
                        for session in sessions
                    ],
                    ensure_ascii=False, indent=2,
                ))
                return 0
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
    from .engine.daemon import raise_fd_limit

    # In-process compiles + per-project tailers blow straight through macOS's
    # default 256-fd soft limit; exhaustion surfaces as sqlite "unable to open
    # database file" storms (observed with `engine --all`, 5 projects).
    raise_fd_limit()
    if not getattr(args, "all", False) and getattr(args, "compile_slots", None) is not None:
        print("tesserae engine: --compile-slots requires --all (fleet mode)", file=sys.stderr)
        return 2
    if getattr(args, "all", False):
        if args.project is not None:
            print("tesserae engine: --all and --project are mutually exclusive", file=sys.stderr)
            raise SystemExit(2)
        from .engine.fleet import FleetDaemon

        registry_env = os.environ.get("TESSERAE_REGISTRY")
        pidfile_env = os.environ.get("TESSERAE_FLEET_PIDFILE")
        fleet = FleetDaemon(
            registry_path=Path(registry_env) if registry_env else None,
            compile_slots=args.compile_slots if args.compile_slots is not None else 1,
            debounce=args.debounce,
            watch_interval=args.interval,
            pidfile=Path(pidfile_env) if pidfile_env else None,
            consolidate=getattr(args, "consolidate", True),
            consolidate_idle_seconds=getattr(args, "consolidate_idle", 300.0),
            consolidate_max_interval_seconds=getattr(args, "consolidate_every", 21600.0),
            consolidate_check_interval=getattr(args, "consolidate_check", 30.0),
        )
        try:
            return fleet.run(once=args.once)
        except RuntimeError as exc:
            print(f"tesserae engine: {exc}", file=sys.stderr)
            return 2

    from .engine.daemon import Daemon

    daemon = Daemon(
        Path(args.project or ".").resolve(),
        debounce=args.debounce,
        watch_interval=args.interval,
        consolidate=getattr(args, "consolidate", True),
        consolidate_idle_seconds=getattr(args, "consolidate_idle", 300.0),
        consolidate_max_interval_seconds=getattr(args, "consolidate_every", 21600.0),
        consolidate_check_interval=getattr(args, "consolidate_check", 30.0),
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
        if hint.startswith("removed"):
            # Terminal removal — no replacement spelling exists.
            print(f"tesserae {old_prefix} was {hint}", file=sys.stderr)
        else:
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
    except FileNotFoundError as exc:
        # Central catch for "project not initialized" / missing-input errors so
        # every command gets a one-line message instead of a traceback.
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
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the byte-idempotence tripwire fires or the post-compile lint reports problems (lint errors → exit 2, warnings → exit 1; default: report-only)")
    # Document extractor. Tesserae is an LLM wiki: 'llm' is the DEFAULT — it
    # builds the concept/claim layer via the configured provider (codex/claude/api
    # per llm_provider). 'deterministic' is the structural, key-free, byte-stable
    # opt-out (CI). No per-doc timeout: a slow doc runs to completion.
    parser.add_argument("--extractor", choices=["llm", "selective-llm", "deterministic", "claude-cli", "selective-claude"], default="llm",
                        help="Extraction backend. 'llm' (default) builds the concept/claim layer via the configured provider; 'selective-llm' routes only --llm-include globs through the LLM; 'deterministic' is structural-only / byte-stable / key-free.")
    # NB: --llm-provider is already provided by the compile parser's LLM-client args.
    parser.add_argument("--llm-model", default=None, help="Model for the LLM extractor (default: the provider's default).")
    parser.add_argument("--llm-include", action="append", default=None, help="Glob selecting files for --extractor selective-llm; repeat for several.")
    parser.add_argument("--llm-limit", type=int, default=None, help="Max files sent to the LLM under --extractor selective-llm.")
    # Deprecated Claude-specific aliases (kept hidden so 0.12.x invocations still
    # parse). --claude-timeout is a removed-flag stub — it was parsed but never
    # read (extraction is no longer truncated).
    parser.add_argument("--claude-include", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--claude-limit", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--claude-timeout",
        action=_RemovedFlagAction,
        message="compile: --claude-timeout was removed (extraction is no longer truncated)",
    )
    parser.add_argument("--claude-model", default=None, help=argparse.SUPPRESS)
    # NB: --claude-config-dir is already provided by the compile parser's LLM-client args.
    parser.add_argument(
        "--refresh-integrations",
        dest="refresh_integrations",
        action="store_true",
        help="Run configured integration refresh commands before compile, even if they are not marked auto_refresh",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--sessions", dest="sessions_enabled", action="store_true", default=None, help="Force session graph extraction on (default if .tesserae/harness_sessions/ exists)")
    session_group.add_argument("--no-sessions", dest="sessions_enabled", action="store_false", default=None, help="Skip session graph extraction entirely")
    distill_group = parser.add_mutually_exclusive_group()
    distill_group.add_argument("--distill", dest="distill_enabled", action="store_true", default=None, help="Run AgentRunbook distillation (Event/Runbook/Gotcha memory layers); also via config distillation.enabled or TESSERAE_RUNBOOK_DISTILLATION")
    distill_group.add_argument("--no-distill", dest="distill_enabled", action="store_false", default=None, help="Skip AgentRunbook distillation even if enabled in config")
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
            "  tesserae context \"how does compile work?\" --budget 4000 --llm\n"
        ),
    )
    parser.add_argument("query", nargs="?", default="", help="Query text to seed the context doc")
    parser.add_argument("--seeds", nargs="*", help="Explicit seed node IDs")
    parser.add_argument("--depth", type=int, default=2, help="PPR expansion depth (default: 2)")
    parser.add_argument("--budget", type=int, default=32_000, help="Character budget for the doc body (default: 32000; <=0 = uncapped)")
    parser.add_argument("--llm", dest="synthesize", action="store_true", help="Add an LLM-synthesized summary (requires an LLM backend)")
    parser.add_argument(
        "--synthesize",
        action=_RemovedFlagAction,
        message="context: --synthesize has moved → --llm",
    )
    parser.add_argument("--multi-pool", dest="multi_pool", action="store_true", help="AgentRunbook multi-pool retrieval: decompose the query and reserve slots for Runbook/Gotcha/Event memory")
    parser.add_argument("--output", "-o", help="Write the doc to a file instead of stdout")
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument(
        "--agent",
        default=None,
        metavar="KEY",
        help="Scope the context to one agent's distilled view: a worker key (its L0 ∪ own distillate), a manager key (its team's distillates), or 'org' (every agent's distillate). Unknown/undistilled keys fail loud.",
    )
    return parser


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae serve",
        description="Browse the compiled site. Bare `serve` runs ALL registered projects "
                    "under one server (Projects switcher in the header); --project serves one. "
                    "The in-page /api/ask widget defaults to search-only for latency (unlike "
                    "`tesserae ask`); a request opts into synthesis with payload {\"llm\": true}.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae serve                 # every registered project at /<alias>/\n"
            "  tesserae serve --project .     # just this project (with live ask)\n"
            "  tesserae serve --port 8765 --no-build\n"
        ),
    )
    parser.add_argument("--project", default=None, help="Serve ONE project root (with live ask). Omit to serve every registered project.")
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
    parser.add_argument("--project", default=None, help="Project root directory; defaults to current working directory")
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
        default=None,
        help="Fleet mode: max concurrent compiles across all projects (requires --all; default 1).",
    )
    parser.add_argument(
        "--consolidate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Idle/periodic memory-consolidation ('sleep') cycle: while the project "
            "rests, distill agent memory in the background (no-op unless "
            "TESSERAE_AGENT_DISTILL is set). Use --no-consolidate to disable (default: on)."
        ),
    )
    parser.add_argument(
        "--consolidate-idle",
        type=float,
        default=300.0,
        help="Sleep cycle: idle window in seconds before consolidating (default: 300 = 5 min).",
    )
    parser.add_argument(
        "--consolidate-every",
        type=float,
        default=21600.0,
        help="Sleep cycle: max seconds between consolidations regardless of activity; 0 disables the ceiling (default: 21600 = 6h).",
    )
    parser.add_argument(
        "--consolidate-check",
        type=float,
        default=30.0,
        help="Sleep cycle: how often in seconds to check the consolidation trigger (default: 30).",
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
            "  tesserae refresh --changed-only --no-sessions\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--changed-only", action="store_true", default=False, help="Opt-in incremental compile (skip unchanged files); default is a full compile")
    parser.add_argument("--no-sessions", dest="no_sessions", action="store_true", default=False, help="Opt-in skip of the slow harness-session discovery scan (matches `compile --no-sessions`)")
    parser.add_argument(
        "--skip-sessions",
        action=_RemovedFlagAction,
        message="refresh: --skip-sessions has moved → --no-sessions",
    )
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
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit the status as JSON instead of the aligned text block.")
    return parser


def _count_imported_sessions(wiki) -> int:
    """Cheap imported-session count from the harness_sessions manifest."""
    manifest = Path(wiki.paths.harness_sessions) / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return len(payload.get("sessions") or [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return 0


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
    sessions_count = _count_imported_sessions(wiki)
    if getattr(args, "as_json", False):
        payload = {
            "project": str(wiki.project_root),
            "nodes": len(graph.nodes) if graph is not None else None,
            "edges": len(graph.edges) if graph is not None else None,
            "graph_corrupt": graph is None,
            "sessions": sessions_count,
            "last_compile": None if compiled == "never" else compiled,
            "vault": str(wiki.effective_obsidian_vault()),
            "site": str(wiki.paths.site),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"project:       {wiki.project_root}")
    if graph is None:
        print("nodes:         corrupt graph.json")
        print("edges:         corrupt graph.json")
    else:
        print(f"nodes:         {len(graph.nodes)}")
        print(f"edges:         {len(graph.edges)}")
    print(f"sessions:      {sessions_count}")
    print(f"last compile:  {compiled}")
    print(f"vault:         {wiki.effective_obsidian_vault()}")
    print(f"site:          {wiki.paths.site}")
    return 0


def _build_summary_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae summary",
        description="Daily/weekly activity digest — sessions, findings, commits, PRs, ingested docs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae summary                       # today, every registered project\n"
            "  tesserae summary --day 2026-07-04\n"
            "  tesserae summary --week                # the last 7 days\n"
            "  tesserae summary --week 2026-07-04     # 7 days ending on that date\n"
            "  tesserae summary --since 2026-07-01 --until 2026-07-04\n"
            "  tesserae summary --name my-repo --no-llm\n"
        ),
    )
    parser.add_argument("--day", default=None, help="Single day YYYY-MM-DD (default: today).")
    parser.add_argument(
        "--week",
        nargs="?",
        const="",
        default=None,
        help="Seven daily windows ending on YYYY-MM-DD; bare --week = the last 7 days.",
    )
    parser.add_argument("--since", default=None, help="Window start (ISO datetime/date).")
    parser.add_argument("--until", default=None, help="Window end (ISO datetime/date).")
    parser.add_argument(
        "--name",
        action="append",
        default=None,
        help="Limit to a registered project by name; repeat for several. "
        "Unknown names error (exit 2). Omit = all registered.",
    )
    parser.add_argument(
        "--project",
        action=_RemovedFlagAction,
        message="summary: --project has moved → --name <registered name>",
    )
    parser.add_argument(
        "--no-llm",
        dest="no_llm",
        action="store_true",
        help="Skip the LLM narrative; print the deterministic digest only.",
    )
    parser.add_argument(
        "--max-turns",
        dest="max_turns",
        type=int,
        default=None,
        help=(
            "Scan memory guard: cap the turns gathered per session while "
            "scanning transcripts (default: unbounded). LLM reading is "
            "chunked and always reads everything gathered."
        ),
    )
    return parser


def _handle_summary(args: argparse.Namespace) -> int:
    try:
        windows = resolve_windows(
            day=args.day, week=args.week, since=args.since, until=args.until
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    synthesize = not args.no_llm
    try:
        result = build_summary(
            windows, args.name, synthesize=synthesize, write=True,
            # `is None` (not `or`): an explicit --max-turns 0 must mean 0,
            # not silently fall back to the unbounded default.
            turn_limit=args.max_turns if args.max_turns is not None else 100_000,
        )
    except ValueError as exc:
        # Strict --name: a typo'd registered name errors instead of silently
        # meaning "no projects" (see activity_summary._resolve_projects).
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result.markdown)
    for path in result.paths:
        print(f"wrote {path}", file=sys.stderr)
    return 0


def _handle_compile_paths_ingest(args: argparse.Namespace) -> int:
    """Ad-hoc ingest of explicit paths (INGEST-ONLY).

    Reuses the legacy ``ingest`` handler logic for the given paths and RETURNS —
    it does NOT run a full ``wiki.compile()`` of configured sources afterward
    (that would overwrite the ad-hoc graph). Backfills the attrs the legacy
    ingest handler reads with the old ingest parser's defaults.
    """
    # Flags that only mean something on a FULL compile are rejected instead of
    # silently ignored on the ad-hoc ingest path.
    inapplicable = [
        flag
        for flag, is_set in (
            ("--strict", getattr(args, "strict", False)),
            ("--sessions/--no-sessions", getattr(args, "sessions_enabled", None) is not None),
            ("--distill/--no-distill", getattr(args, "distill_enabled", None) is not None),
            ("--refresh-integrations", getattr(args, "refresh_integrations", False)),
        )
        if is_set
    ]
    if inapplicable:
        print(
            f"compile <paths>: {'/'.join(inapplicable)} only apply to a full compile, "
            "not ad-hoc path ingest",
            file=sys.stderr,
        )
        return 2
    # Per-run LLM backend flags (--llm-provider/--claude-config-dir/--codex-home)
    # must reach the extractor here too, exactly like the full-compile path.
    _apply_llm_cli_env(args)
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


def _fleet_landing_html(projects: List[dict]) -> str:
    """A minimal projects-landing page served at `/` in fleet mode."""
    import html as _html

    cards = "\n".join(
        f'<a class="card" href="/{_html.escape(p["alias"])}/">'
        f'<span class="name">{_html.escape(p.get("title") or p["alias"])}</span>'
        f'<span class="alias">{_html.escape(p["alias"])}</span></a>'
        for p in projects
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Tesserae — projects</title><style>"
        "body{margin:0;background:#0f131c;color:#dde;font:16px system-ui,-apple-system,sans-serif}"
        ".wrap{max-width:880px;margin:0 auto;padding:48px 24px}"
        "h1{font-size:22px;font-weight:700;margin:0 0 6px}.sub{color:#8995a8;margin:0 0 28px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}"
        ".card{display:flex;flex-direction:column;gap:4px;padding:18px;background:#161b27;"
        "border:1px solid #2a3140;border-radius:10px;text-decoration:none;color:inherit}"
        ".card:hover{border-color:#4a93ff;background:#1a2030}"
        ".name{font-weight:600;font-size:17px}.alias{color:#74e0c0;font-size:13px}"
        "</style></head><body><div class='wrap'>"
        f"<h1>Tesserae</h1><p class='sub'>{len(projects)} registered project(s) — all active.</p>"
        f"<div class='grid'>{cards}</div></div></body></html>"
    )


def _serve_fleet(args: argparse.Namespace) -> int:
    """Serve EVERY registered project under one server: a landing page at `/`,
    each project's site at `/<alias>/`, with a Projects switcher in the header."""
    import shutil
    import socketserver

    from .mcp_server import ProjectRegistry
    from .serve import build_fleet_handler

    projects = ProjectRegistry().list_projects().get("projects") or []
    if not projects:
        print(
            "No projects registered. Register some with `tesserae projects register <path>`, "
            "or serve one directly with `tesserae serve --project <path>`.",
            file=sys.stderr,
        )
        return 2

    nav_projects: List[dict] = []
    links: List[tuple] = []
    # --dry-run is hoisted ABOVE the per-project builds: report what would be
    # served without triggering (potentially heavy) site builds.
    dry_run = getattr(args, "dry_run", False)
    for entry in projects:
        root = Path(entry.get("root") or "").expanduser().resolve()
        try:
            wiki = ProjectWiki.load(root)
        except Exception as exc:
            print(f"  skip {entry.get('name')}: {exc}", file=sys.stderr)
            continue
        index = wiki.paths.site / "index.html"
        if not getattr(args, "no_build", False) and not dry_run:
            stale = (not index.exists()) or (
                wiki.paths.graph.exists() and wiki.paths.graph.stat().st_mtime > index.stat().st_mtime
            )
            if stale:
                print(f"building {entry['name']} site …")
                try:
                    wiki.build_site()
                except Exception as exc:
                    print(f"  build failed for {entry['name']}: {exc}", file=sys.stderr)
        if index.exists():
            title = (wiki.config().get("site_title") or entry["name"])
            nav_projects.append({"alias": entry["name"], "title": title})
            links.append((entry["name"], wiki.paths.site.resolve(), root))
    if not links:
        print("No buildable project sites found (run `tesserae compile` for a project first).", file=sys.stderr)
        return 2

    url = f"http://{args.host}:{args.port}/"
    if dry_run:
        print(f"Fleet site ready ({len(links)} project(s)) at {url}")
        return 0

    # Per-process temp root holding ONLY the landing + projects.json. Project
    # sites are mapped by alias (no symlink tree), so the handler can enforce
    # containment and a stray `serve` can never clobber a fixed shared dir.
    import tempfile

    served_root = Path(tempfile.mkdtemp(prefix="tesserae-fleet-"))
    project_sites = {alias: site_dir for alias, site_dir, _root in links}
    project_roots = {alias: root for alias, _site_dir, root in links}
    (served_root / "projects.json").write_text(json.dumps(nav_projects), encoding="utf-8")
    (served_root / "index.html").write_text(_fleet_landing_html(nav_projects), encoding="utf-8")

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    handler = build_fleet_handler(
        served_root=served_root, project_sites=project_sites, project_roots=project_roots
    )
    try:
        with ReusableTCPServer((args.host, args.port), handler) as httpd:
            print(f"Serving {len(links)} project(s) at {url}")
            for p in nav_projects:
                print(f"  {url}{p['alias']}/  — {p['title']}")
            httpd.serve_forever()
    except OSError as exc:
        print(f"Could not serve fleet site at {url}: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(served_root, ignore_errors=True)  # clean the per-process temp root
    return 0


def _handle_serve(args: argparse.Namespace) -> int:
    """Serve all registered projects (bare `serve`) or one (`--project`).

    Fleet mode (no --project) auto-builds each project's site when stale, then
    serves them under one server with a Projects switcher. An EMPTY registry
    falls back to serving the current directory as a single project.
    """
    if getattr(args, "project", None) is None:
        from .mcp_server import ProjectRegistry

        if ProjectRegistry().list_projects().get("projects"):
            return _serve_fleet(args)
        # Empty registry: bare `serve` degrades to cwd single-project mode.
        print("No projects registered — serving the current directory.", file=sys.stderr)
        args.project = "."
    try:
        wiki = ProjectWiki.load(args.project)
    except FileNotFoundError:
        print(
            "tesserae serve: project not initialized — run `tesserae init` first.",
            file=sys.stderr,
        )
        return 2
    # --dry-run is hoisted ABOVE the auto-build: it reports the URL without
    # triggering a (potentially heavy) site build.
    if not getattr(args, "no_build", False) and not getattr(args, "dry_run", False):
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


def _route_summary(rest: List[str]) -> int:
    args = _build_summary_parser().parse_args(rest)
    return _handle_summary(args)


def _build_decisions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae decisions",
        description="Decisions across projects + time — explicit human choices (AskUserQuestion) + agent decisions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae decisions --since 2026-06-30          # all projects since a date\n"
            "  tesserae decisions --day 2026-07-04\n"
            "  tesserae decisions --week                      # the last 7 days\n"
            "  tesserae decisions --name my-repo --no-llm     # human decisions only\n"
            "  tesserae decisions --since 2026-06-30 --json   # structured output\n"
        ),
    )
    parser.add_argument("--day", default=None, help="Single day YYYY-MM-DD (default: today).")
    parser.add_argument(
        "--week",
        nargs="?",
        const="",
        default=None,
        help="Seven daily windows ending on YYYY-MM-DD; bare --week = the last 7 days.",
    )
    parser.add_argument("--since", default=None, help="Window start (ISO datetime/date).")
    parser.add_argument("--until", default=None, help="Window end (ISO datetime/date).")
    parser.add_argument(
        "--name",
        action="append",
        default=None,
        help="Limit to a registered project by name; repeat for several. "
        "Unknown names error (exit 2). Omit = all registered.",
    )
    parser.add_argument(
        "--project",
        action=_RemovedFlagAction,
        message="decisions: --project has moved → --name <registered name>",
    )
    parser.add_argument(
        "--no-llm",
        dest="no_llm",
        action="store_true",
        help="Only the deterministic human (AskUserQuestion) decisions; skip agent-decision mining.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the structured decision list as JSON instead of markdown.",
    )
    parser.add_argument(
        "--max-turns",
        dest="max_turns",
        type=int,
        default=None,
        help=(
            "Scan memory guard: cap the turns gathered per session while "
            "scanning transcripts (default: unbounded). LLM reading is "
            "chunked and always reads everything gathered."
        ),
    )
    return parser


def _handle_decisions(args: argparse.Namespace) -> int:
    try:
        windows = resolve_windows(
            day=args.day, week=args.week, since=args.since, until=args.until
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from .decisions import gather_decisions, render_decisions

    try:
        decisions = gather_decisions(
            windows, args.name, include_agent=not args.no_llm,
            # `is None` (not `or`): an explicit --max-turns 0 must mean 0.
            turn_limit=args.max_turns if args.max_turns is not None else 100_000,
        )
    except ValueError as exc:
        # Strict --name: a typo'd registered name errors instead of silently
        # meaning "no projects" (see activity_summary._resolve_projects).
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "ts": d.ts.isoformat(),
                        "source": d.source,
                        "project": d.project,
                        "session_id": d.session_id,
                        "question": d.question,
                        "answer": d.answer,
                        "options": d.options,
                        "header": d.header,
                    }
                    for d in decisions
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_decisions(decisions))
    return 0


def _route_decisions(rest: List[str]) -> int:
    args = _build_decisions_parser().parse_args(rest)
    return _handle_decisions(args)


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
    """The dieted `tesserae init` parser: EXACTLY 11 flags.

    `tesserae init` runs the setup wizard by default; `--yes` accepts detected
    defaults non-interactively; `--bare` skips the wizard entirely and writes a
    minimal workspace (the old `project init`). The ~21 other legacy `setup`
    flags (``--source-kind`` and the integration toggles) become wizard prompts
    and/or documented config.json keys — they are NOT surfaced here. The legacy
    `project init` / `init` parsers keep their own full flag sets. The LLM
    client surface (provider / claude dir / codex home / model / base_url /
    api_key) IS here — init is the single onboarding step, so every runtime
    ``llm_*`` config key must be settable from it.
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
    parser.add_argument("--llm-model", default=None, help="Model for the synthesis/insights LLM client (persisted into config.json as llm_model)")
    parser.add_argument("--llm-base-url", default=None, help="Claude-compatible endpoint base URL for --llm-provider anthropic/custom (persisted as llm_base_url)")
    parser.add_argument("--llm-api-key", default=None, help="API key for --llm-provider anthropic/custom (persisted in PLAINTEXT config.json as llm_api_key; prefer ANTHROPIC_API_KEY)")
    return parser


# keep in sync with _handle_setup's args.* reads
def _backfill_setup_defaults(args: argparse.Namespace) -> None:
    """Fill the namespace with every attr the legacy `_handle_setup` reads.

    `_handle_init_v2` delegates to the unchanged `_handle_setup`, whose ``--yes``
    branch reads ~21 attrs that the dieted init parser no longer defines. We
    ``setdefault`` each one with the legacy `setup_parser` default, EXCEPT the
    integration toggles, which take the NEW ``--yes`` defaults: every optional
    integration (raganything) lands OFF. Color is
    auto-disabled when stdout is not a TTY.
    """
    d = args.__dict__
    # Legacy setup defaults (verbatim from the `setup_parser.add_argument` calls).
    d.setdefault("source_kind", "Repository")
    d.setdefault("raganything_parser", "mineru")
    d.setdefault("raganything_extras", "all")
    # --yes default: raganything OFF (CI's `--skip-raganything`; never `--with-raganything`).
    d.setdefault("with_raganything", False)
    d.setdefault("skip_raganything", True)
    # --yes default: no companion-tool installs/runs (CI's `--skip-install-*`,
    # never `--install-*`/`--run-*`). These keep the wizard from shelling out.
    d.setdefault("install_raganything", False)
    d.setdefault("skip_install_raganything", True)
    # --yes default: color auto-disabled when stdout is not an interactive TTY.
    d.setdefault("no_color", not sys.stdout.isatty())


def _backfill_bare_init_defaults(args: argparse.Namespace) -> None:
    """Fill the namespace with attrs the legacy `_handle_init` reads.

    `_handle_init` reads ``args.source_kind`` (and ``args.source`` /
    ``args.name`` / the LLM attrs, which the dieted init parser already
    supplies). Only ``source_kind`` is missing from the diet — backfill it with
    ``Repository``, the SAME default the ``--yes`` path uses (the legacy
    ``project init`` default of ``SourceDocument`` made `--bare` and `--yes`
    disagree about what kind of project they were initializing).
    """
    args.__dict__.setdefault("source_kind", "Repository")


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
        rc = _handle_init(args)
    else:
        _backfill_setup_defaults(args)
        rc = _handle_setup(args)
    if rc == 0:
        _maybe_offer_memex_install(args)
    return rc


def _maybe_offer_memex_install(args: argparse.Namespace) -> None:
    """Offer to install memex (fast transcript search) after an interactive init.

    Only prompts in a real interactive session — never under ``--yes``/``--bare``
    or a non-TTY (those keep optional integrations OFF). No new init flag (the
    init parser is deliberately dieted); install later via ``config deps``.
    """
    if getattr(args, "yes", False) or getattr(args, "bare", False) or not sys.stdin.isatty():
        return
    from . import deps

    if deps.DEPS_BY_NAME["memex"].detect():
        return
    try:
        ans = input(
            "\nInstall memex for fast transcript search on the sessions dashboard? "
            "(needs the Rust toolchain) [y/N] "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if ans in ("y", "yes"):
        _install_deps(["memex"])
    else:
        print("Skipped. Install later with: tesserae config deps --install memex")


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


def _handle_sessions_prune_internal(args: argparse.Namespace) -> int:
    """Retroactively delete Tesserae's OWN captured LLM calls from the live DB.

    The harness session monitor records every codex/claude CLI invocation —
    including the ones Tesserae itself runs during compile (extraction,
    synthesis, …). Before the discovery/tailer filter existed those landed in
    ``.tesserae/harness_sessions.db`` as if they were user sessions, drowning
    real work. This prunes them in one pass; the live filter prevents new ones.
    """
    wiki = ProjectWiki.load(args.project)
    live_db_path = wiki.project_root / ".tesserae" / "harness_sessions.db"
    if not live_db_path.exists():
        print(f"No live sessions DB at {live_db_path}; nothing to prune.")
        return 0
    from .harness_sessions_db import HarnessSessionsDB

    db = HarnessSessionsDB(live_db_path)
    before = db.count_sessions()
    removed = db.prune_internal_sessions()
    print(
        f"Pruned Tesserae self-captured sessions: removed={removed} "
        f"remaining={before - removed} db={live_db_path}"
    )
    return 0


def _handle_sessions_chunk_backfill(args: argparse.Namespace) -> int:
    """Backfill the daily session-chunk store from existing transcripts."""
    from .session_chunks import backfill

    wiki = ProjectWiki.load(args.project)
    result = backfill(wiki.project_root, since=args.since)
    if result.skipped:
        print(f"chunk-backfill skipped: {result.reason}")
        return 0
    print(
        f"chunk-backfill: inserted={result.turns_inserted} turn(s), "
        f"covered={result.days_covered} day(s) db={wiki.paths.session_chunks}"
    )
    return 0


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
    p_list.add_argument("--json", dest="as_json", action="store_true", help="Emit the session list as JSON.")
    p_list.set_defaults(_handler="_handle_sessions_list")
    p_prune = sub.add_parser(
        "prune-internal",
        help="Delete Tesserae's OWN captured compile-time LLM calls from the live sessions DB (self-capture cleanup)",
    )
    p_prune.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_prune.set_defaults(_handler="_handle_sessions_prune_internal")
    p_chunk = sub.add_parser(
        "chunk-backfill",
        help="Backfill the daily session-chunk store (.tesserae/session_chunks.db) from existing transcripts",
    )
    p_chunk.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_chunk.add_argument("--since", default=None, help="Earliest day to backfill (YYYY-MM-DD); defaults to full history")
    p_chunk.set_defaults(_handler="_handle_sessions_chunk_backfill")
    return parser


def _route_sessions(rest: List[str]) -> int:
    args = _build_sessions_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- vault ----------------------------------------------------------------
def _handle_vault_sync(args: argparse.Namespace) -> int:
    """`vault sync` = old `obsidian-sync`."""
    return _handle_obsidian_sync(args)


def _handle_vault_prune(args: argparse.Namespace) -> int:
    """`vault prune` = `obsidian-sync --prune-orphans` preset.

    ``--dry-run`` takes a dedicated preview path (NOT the sync overlay
    dry-run): list the orphan pages that would be deleted, touch nothing —
    no deletions, no snapshot refresh.
    """
    args.prune_orphans = True
    if getattr(args, "dry_run", False):
        from .vault_pull import prune_orphan_pages

        wiki = ProjectWiki.load(args.project)
        if not wiki.paths.graph.is_file():
            print("error: no compiled graph yet — run `compile` first.", file=sys.stderr)
            return 2
        graph = _load_graph_file(wiki.paths.graph)
        vault = wiki.effective_obsidian_vault()
        result = prune_orphan_pages(
            vault, graph, force=args.force_prune_with_notes, dry_run=True
        )
        print(f"dry-run: would prune {len(result.deleted)} orphan page(s)")
        for p in result.deleted[:20]:
            print(f"  - {p.relative_to(vault)}")
        if len(result.deleted) > 20:
            print(f"  ... and {len(result.deleted) - 20} more")
        if result.skipped_with_user_notes:
            print(
                f"  would keep {len(result.skipped_with_user_notes)} orphan(s) with "
                "user-notes content (pass --force-prune-with-notes to include them)"
            )
        return 0
    # dry_run stays False here so the sync overlay dry-run branch is not taken.
    args.dry_run = False
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
        p.add_argument("--interval", dest="poll_interval", type=float, default=1.5, help="Watch-mode poll interval in seconds (default: 1.5; matches `engine --interval`).")
        p.add_argument(
            "--poll-interval",
            action=_RemovedFlagAction,
            message="vault: --poll-interval has moved → --interval",
        )
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
    p_prune.add_argument("--dry-run", action="store_true", help="List the orphan pages that would be deleted; delete nothing.")
    p_prune.set_defaults(
        _handler="_handle_vault_prune",
        watch=False, poll_interval=1.5, vault=None,
        persist_vault=False, prune_orphans=True,
    )

    p_export = sub.add_parser("export", help="Export the compiled graph as an Obsidian vault")
    p_export.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_export.add_argument("--output", help="Vault output directory; defaults to .tesserae/obsidian_vault")
    p_export.add_argument(
        "--vault",
        action=_RemovedFlagAction,
        message="vault export: --vault has moved → --output",
    )
    p_export.set_defaults(_handler="_handle_vault_export")

    p_set_root = sub.add_parser("set-root", help="Set the registry-wide Obsidian vault root.")
    p_set_root.add_argument("path", nargs="?", help="Absolute path; omit and pass --clear to unset.")
    p_set_root.add_argument("--clear", action="store_true", help="Remove the configured vault root.")
    p_set_root.set_defaults(_handler="_handle_vault_set_root")

    p_sync_all = sub.add_parser("sync-all", help="Run an obsidian-sync --watch loop for every registered project (one thread per project).")
    p_sync_all.add_argument("--interval", dest="poll_interval", type=float, default=1.5, help="Per-watcher poll interval in seconds (default: 1.5; matches `engine --interval`).")
    p_sync_all.add_argument(
        "--poll-interval",
        action=_RemovedFlagAction,
        message="vault: --poll-interval has moved → --interval",
    )
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
    """`export graphiti` = old `export-graphiti`; `--sync` → old `sync-graphiti`.

    Mode-mismatch flags error instead of being silently ignored: the Neo4j
    connection flags and --dry-run only mean something under --sync, while
    --output only applies to the JSONL export.
    """
    import os as _os

    if getattr(args, "sync", False):
        if getattr(args, "output", None):
            print(
                "export graphiti: --output only applies to the JSONL export; "
                "--sync writes to Neo4j",
                file=sys.stderr,
            )
            return 2
        # Fill connection defaults; NEO4J_PASSWORD env beats the built-in default.
        args.neo4j_uri = args.neo4j_uri or "bolt://localhost:7687"
        args.neo4j_user = args.neo4j_user or "neo4j"
        args.neo4j_password = (
            args.neo4j_password or _os.environ.get("NEO4J_PASSWORD") or "password"
        )
        return _handle_sync_graphiti(args)
    set_sync_flags = [
        flag
        for flag, value in (
            ("--neo4j-uri", args.neo4j_uri),
            ("--neo4j-user", args.neo4j_user),
            ("--neo4j-password", args.neo4j_password),
            ("--dry-run", getattr(args, "dry_run", False) or None),
        )
        if value
    ]
    if set_sync_flags:
        print(
            f"export graphiti: {'/'.join(set_sync_flags)} "
            f"{'requires' if len(set_sync_flags) == 1 else 'require'} --sync",
            file=sys.stderr,
        )
        return 2
    return _handle_export_graphiti(args)


def _handle_export_site(args: argparse.Namespace) -> int:
    """`export site` = old `build-site`; `--deploy` → old `deploy`, `--watch` → old `watch`."""
    deploy = getattr(args, "deploy", False)
    watch = getattr(args, "watch", False)
    if deploy and watch:
        print("export site: --deploy and --watch are mutually exclusive", file=sys.stderr)
        return 2
    if (deploy or watch) and getattr(args, "output", None):
        # Deploy/watch operate on the project's configured site path; a custom
        # --output would be silently ignored — reject instead.
        print(
            "export site: --output only applies to a plain build; "
            "--deploy/--watch use the project's .tesserae/site",
            file=sys.stderr,
        )
        return 2
    if deploy:
        if not getattr(args, "build", False):
            # Auto-build when the site is missing or older than the graph, so
            # `export site --deploy` never publishes a stale site by accident.
            wiki = ProjectWiki.load(args.project)
            index = wiki.paths.site / "index.html"
            stale = (not index.exists()) or (
                wiki.paths.graph.exists() and wiki.paths.graph.stat().st_mtime > index.stat().st_mtime
            )
            if stale:
                print("building site first (stale) …")
                wiki.build_site()
        return _handle_deploy(args)
    if watch:
        return _handle_watch(args)
    return _handle_build_site(args)


def _handle_export_okf(args: argparse.Namespace) -> int:
    """`export okf` — write/read a Google OKF v0.1 bundle. `--import DIR` reads."""
    from .okf import read_okf_bundle, write_okf_bundle

    wiki = ProjectWiki.load(args.project)
    graph_dir = Path(wiki.paths.graph).parent
    if getattr(args, "import_dir", None):
        graph = read_okf_bundle(args.import_dir)
        out = args.output or str(graph_dir / "okf-imported.graph.json")
        # Import writes a SEPARATE graph file by default; even with an explicit
        # --output, refuse to clobber the compiled graph.json (codex review).
        if Path(out).resolve() == Path(wiki.paths.graph).resolve():
            print("export okf --import: refusing to overwrite the compiled graph.json; "
                  "choose a different --output", file=sys.stderr)
            return 2
        Path(out).write_text(graph.to_json(), encoding="utf-8")
        print(f"Imported OKF bundle: nodes={len(graph.nodes)} edges={len(graph.edges)} path={out}")
        return 0
    graph = _load_graph_file(wiki.paths.graph)
    out = args.output or str(graph_dir / "okf")
    written = write_okf_bundle(graph, out)
    print(f"Exported OKF v0.1 bundle: files={len(written)} path={out}")
    return 0


def _build_export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae export",
        description="Artifact exports: harness | graphiti | site | okf.",
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
    p_harness.add_argument("--install-pointer", action="store_true", help="Also install/refresh the Tesserae pointer block in the project's AGENTS.md/CLAUDE.md")
    p_harness.set_defaults(_handler="_handle_export_harness")

    # graphiti = export-graphiti flags UNION sync-graphiti flags + --sync.
    p_graphiti = sub.add_parser("graphiti", help="Export project graph as Graphiti episode JSONL; --sync pushes into Neo4j.")
    p_graphiti.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_graphiti.add_argument("--group-id", help="Graphiti group_id; defaults to project wiki name")
    p_graphiti.add_argument("--output", help="Episode JSONL output path; defaults to .tesserae/graphiti_episodes.jsonl")
    p_graphiti.add_argument("--sync", action="store_true", help="Sync episodes into Graphiti/Neo4j instead of writing JSONL")
    p_graphiti.add_argument("--neo4j-uri", default=None, help="Neo4j URI for Graphiti (--sync; default: bolt://localhost:7687)")
    p_graphiti.add_argument("--neo4j-user", default=None, help="Neo4j username (--sync; default: neo4j)")
    p_graphiti.add_argument("--neo4j-password", default=None, help="Neo4j password (--sync; default: $NEO4J_PASSWORD, then 'password')")
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

    p_okf = sub.add_parser(
        "okf",
        help="Export the graph as a Google OKF v0.1 bundle; --import reads one back.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae export okf\n"
            "  tesserae export okf --output ./my-okf\n"
            "  tesserae export okf --import ./my-okf\n"
        ),
    )
    p_okf.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_okf.add_argument("--output", help="Export: bundle dir (default .tesserae/okf). Import: graph.json path (default .tesserae/okf-imported.graph.json)")
    p_okf.add_argument("--import", dest="import_dir", metavar="DIR", help="Read an OKF bundle DIR into a graph.json instead of exporting")
    p_okf.set_defaults(_handler="_handle_export_okf")
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
    parser.add_argument(
        "--source-kind",
        choices=["Paper", "Repository", "ResearchDigest", "SourceDocument"],
        default=None,
        help="Override source classification (exact: 'Paper'/'Repository'/'ResearchDigest' map "
        "directly to that node type; 'SourceDocument' keeps path-based detection)",
    )
    parser.add_argument("--full", action="store_true", help="Force a full recompile (skip the fast path); matches `integrations refresh --full`")
    parser.add_argument(
        "--exact",
        action=_RemovedFlagAction,
        message="ingest: --exact was renamed --full",
    )
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
        exact=args.full,
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


def _handle_config_status(args: argparse.Namespace) -> int:
    """`config status` — show the RESOLVED LLM backend (with the SOURCE of each
    setting) and ping it for liveness.

    Answers the two otherwise-invisible questions: *which* backend is actually
    in effect (env > project config > ~/.tesserae/config.json > default), and
    *is it alive* — a rate-limited / mis-authed codex account silently makes
    session extraction produce zero findings."""
    import os as _os

    from .llm_json import (
        _load_global_llm_config,
        build_default_json_client,
        resolve_llm_client_settings,
    )

    project_cfg: dict = {}
    proj = getattr(args, "project", None)
    if proj:
        cfgp = Path(proj) / ".tesserae" / "config.json"
        if cfgp.is_file():
            try:
                project_cfg = json.loads(cfgp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                project_cfg = {}
    global_cfg = _load_global_llm_config()
    settings = resolve_llm_client_settings(project_cfg)

    def _source(key: str, env_name: str) -> str:
        if _os.environ.get(env_name):
            return f"env {env_name}"
        if project_cfg.get(key) is not None:
            return "project .tesserae/config.json"
        if global_cfg.get(key) is not None:
            return "~/.tesserae/config.json"
        return "default"

    provider = settings["provider"]
    print("Tesserae LLM backend (resolved" + (f" for {proj}" if proj else "") + "):")
    print(f"  provider   : {provider}   [{_source('llm_provider', 'TESSERAE_LLM_PROVIDER')}]")
    if provider == "codex":
        home = settings["codex_home"] or "~/.codex (OS default)"
        print(f"  codex_home : {home}   [{_source('llm_codex_home', 'CODEX_HOME')}]")
        effort = settings.get("codex_reasoning_effort") or "medium"
        print(f"  effort     : {effort}   [{_source('llm_codex_reasoning_effort', 'TESSERAE_CODEX_REASONING_EFFORT')}]")
    elif provider in ("anthropic", "custom"):
        # API-key providers: no CLI dirs — show the endpoint knobs instead.
        model = settings.get("model") or "<provider default>"
        print(f"  model      : {model}   [{_source('llm_model', 'TESSERAE_LLM_MODEL')}]")
        base_url = settings.get("base_url") or "<api default>"
        print(f"  base_url   : {base_url}   [{_source('llm_base_url', 'ANTHROPIC_BASE_URL')}]")
        # NEVER print the key itself — only whether one is resolved.
        key_state = "set" if settings.get("api_key") else "unset"
        print(f"  api_key    : {key_state}   [{_source('llm_api_key', 'ANTHROPIC_API_KEY')}]")
    else:
        dirs = settings["claude_config_dirs"] or ["<CLI default>"]
        print(f"  claude_dirs: {dirs}   [{_source('llm_claude_config_dirs', 'CLAUDE_CONFIG_DIR')}]")
    if provider not in ("anthropic", "custom") and settings.get("model"):
        print(f"  model      : {settings['model']}   [{_source('llm_model', 'TESSERAE_LLM_MODEL')}]")

    # Optional dependency status — the rest of what `tesserae setup` manages,
    # so `status` is a full picture, not LLM-only.
    from .deps import status as _dep_status

    print("\nOptional dependencies:")
    for dep in _dep_status():
        mark = "✓ installed" if dep["installed"] else "· not installed"
        print(f"  {mark:>15}  {dep['name']}")

    if not getattr(args, "ping", True):
        return 0

    client = build_default_json_client(
        provider=provider,
        codex_home=settings["codex_home"],
        claude_config_dirs=settings["claude_config_dirs"],
        model=settings.get("model"),
        base_url=settings.get("base_url"),
        api_key=settings.get("api_key"),
    )
    if client is None:
        print("  liveness   : ✗ no client could be built (CLI missing / not configured)")
        return 1
    try:
        resp = client.complete_json(
            system="Return JSON only.",
            user='Return {"ok": true} exactly.',
            schema_name="probe",
            cache_key="config-status-probe",
        )
    except Exception as exc:  # noqa: BLE001 — surface the backend's own error
        print(f"  liveness   : ✗ FAILED — {type(exc).__name__}: {str(exc)[:200]}")
        return 1
    if resp:
        print("  liveness   : ✓ OK (backend responded)")
        return 0
    print(
        "  liveness   : ✗ FAILED — no response (rate-limited / auth / unsupported "
        "model). Session extraction will produce zero findings until this is fixed."
    )
    return 1


def _build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae config",
        description=(
            "Machine-wide config: llm (set LLM defaults) | deps (optional dependencies) | "
            "show (effective defaults) | status (resolved view + liveness) | clip-token "
            "(/api/clip auth). (`config setup` moved → `tesserae setup`.)"
        ),
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
    p_llm.add_argument("--llm-provider", choices=["claude", "codex", "anthropic", "custom"], default=None, help="Default backend for the synthesis/insights LLM client on this machine")
    p_llm.add_argument("--claude-config-dir", action="append", default=[], help="Default Claude CLI config directory; repeat for fallback accounts")
    p_llm.add_argument("--codex-home", default=None, help="Default Codex CLI home (e.g. ~/.codex-personal1)")
    p_llm.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"], default=None, help="Default codex reasoning effort for Tesserae's own LLM calls")
    p_llm.add_argument("--llm-model", default=None, help="Default model for the synthesis LLM client (llm_model)")
    p_llm.add_argument("--llm-base-url", default=None, help="Claude-compatible endpoint base URL for anthropic/custom (llm_base_url)")
    p_llm.add_argument("--llm-api-key", default=None, help="API key for anthropic/custom (stored in PLAINTEXT ~/.tesserae/config.json; prefer ANTHROPIC_API_KEY)")
    p_llm.set_defaults(_handler="_handle_config_llm")

    p_deps = sub.add_parser(
        "deps",
        help="List optional dependency status (memex, raganything, …) or install them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae config deps                 # show what's installed\n"
            "  tesserae config deps --install memex\n"
            "  tesserae config deps --all           # install everything\n"
        ),
    )
    p_deps.add_argument("--install", action="append", default=[], metavar="NAME", help="Dependency to install (or 'all'); repeat for several")
    p_deps.add_argument("--all", action="store_true", help="Install every known optional dependency")
    p_deps.set_defaults(_handler="_handle_config_deps")

    # `config setup` was removed — the MOVED_COMMANDS stub in cli_tree.py
    # prints "tesserae config setup has moved → tesserae setup" (exit 2).

    p_show = sub.add_parser("show", help="Print the effective machine-wide LLM defaults and exit.")
    p_show.set_defaults(_handler="_handle_config_show")

    p_status = sub.add_parser(
        "status",
        help="Show the RESOLVED LLM backend (provider + home + source) and ping it for liveness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae config status                 # resolved backend + live ping\n"
            "  tesserae config status --project .     # as this project sees it\n"
            "  tesserae config status --no-ping       # skip the live call\n"
        ),
    )
    p_status.add_argument("--project", default=".", help="Resolve as a specific project sees it (reads its .tesserae/config.json); defaults to the current directory.")
    p_status.add_argument("--no-ping", dest="ping", action="store_false", default=True, help="Skip the live backend ping (don't spend an LLM call).")
    p_status.set_defaults(_handler="_handle_config_status")

    p_clip_token = sub.add_parser(
        "clip-token",
        help="Get/set the /api/clip access token (the extension sends it as X-Tesserae-Token).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae config clip-token                 # show the current token\n"
            "  tesserae config clip-token --generate      # create + set a random token\n"
            "  tesserae config clip-token --set my-secret\n"
            "  tesserae config clip-token --clear         # disable token auth\n"
        ),
    )
    _ct = p_clip_token.add_mutually_exclusive_group()
    _ct.add_argument("--set", metavar="TOKEN", default=None, help="Set the token to this exact value")
    _ct.add_argument("--generate", action="store_true", help="Generate a random token and set it")
    _ct.add_argument("--clear", action="store_true", help="Remove the token (disable /api/clip auth)")
    p_clip_token.set_defaults(_handler="_handle_config_clip_token")
    return parser


def _handle_config_clip_token(args: argparse.Namespace) -> int:
    """`config clip-token` — get/set/generate/clear the /api/clip auth token.

    Stored machine-wide in ``~/.tesserae/config.json`` (``clip_token``). The
    running ``tesserae serve`` reads it FRESH per request, so a change takes
    effect immediately — no restart needed.
    """
    import json as _json
    import secrets
    from . import llm_json as _lj

    path = _lj.GLOBAL_CONFIG_PATH
    try:
        cfg = _json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, _json.JSONDecodeError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    if args.clear:
        cfg.pop("clip_token", None)
        _write_global_config(path, cfg)
        print("Clip token cleared — /api/clip is open again (no token required).")
        return 0

    value = None
    if args.generate:
        value = secrets.token_urlsafe(24)
    elif args.set is not None:
        value = args.set.strip()
        if not value:
            print("config clip-token --set: empty value; use --clear to disable auth.", file=sys.stderr)
            return 2

    if value is not None:
        cfg["clip_token"] = value
        _write_global_config(path, cfg)
        print("Clip token set (takes effect immediately — no serve restart needed).")
        print("Put this exact value in the extension → Options → Access token:\n")
        print(f"    {value}\n")
        return 0

    current = str(cfg.get("clip_token") or "")
    if current:
        print(f"Clip token (send as X-Tesserae-Token): {current}")
    else:
        print("No clip token set — /api/clip is open. Create one with:\n"
              "    tesserae config clip-token --generate")
    return 0


def _print_dep_status() -> None:
    from . import deps

    print("Optional dependencies:")
    for d in deps.status():
        mark = "✓ installed" if d["installed"] else "· not installed"
        note = f"  ({d['note']})" if d["note"] and not d["installed"] else ""
        print(f"  {mark:>15}  {d['name']:<20} {d['summary']}{note}")


def _resolve_dep_targets(install: List[str], all_flag: bool) -> Tuple[List[str], List[str]]:
    """Return ``(targets, unknown)``. ``all`` (flag or token) expands to every
    dep; targets are de-duplicated, order-preserved."""
    from . import deps

    if all_flag or "all" in (install or []):
        return list(deps.DEP_NAMES), []
    targets: List[str] = []
    for name in install or []:
        if name not in targets:
            targets.append(name)
    unknown = [n for n in targets if n not in deps.DEPS_BY_NAME]
    return targets, unknown


def _install_deps(names: List[str]) -> int:
    """Install each named dependency once; return 0 only if all succeeded."""
    from . import deps

    rc = 0
    seen: set = set()
    for name in names:
        if name in seen:  # never run the same installer twice
            continue
        seen.add(name)
        dep = deps.DEPS_BY_NAME.get(name)
        # Surface the EXACT command that will run (pip deps resolve to uv-pip in a
        # pip-less env).
        if dep is not None:
            shown = deps._pip_install_argv(dep.pip_specs) if dep.pip_specs else dep.install_cmd
            print(f"Installing {name} … ({' '.join(shown)})", flush=True)
        res = deps.install(name)
        if res.get("already"):
            print(f"  {name}: already installed.")
        elif res["ok"]:
            print(f"  {name}: installed.")
        else:
            print(f"  {name}: FAILED — {res.get('error')}", file=sys.stderr)
            rc = 1
    return rc


def _handle_config_deps(args: argparse.Namespace) -> int:
    """`config deps` — list optional dependency status, or install some."""
    from . import deps

    targets, unknown = _resolve_dep_targets(args.install, getattr(args, "all", False))
    if not targets:
        _print_dep_status()
        return 0
    if unknown:
        print(f"Unknown dependency: {', '.join(unknown)} (known: {', '.join(deps.DEP_NAMES)})", file=sys.stderr)
        return 2
    return _install_deps(targets)


def _setup_wants_interactive(args: argparse.Namespace) -> bool:
    """Interactive when on a TTY with no actionable flags and not --yes — so a
    bare `tesserae setup` prompts (like the init wizard) instead of dumping status.

    Only the top-level `tesserae setup` opts in (``_interactive_default``);
    the old `config setup` alias is a moved-command stub now."""
    if not getattr(args, "_interactive_default", False):
        return False
    explicit = bool(
        args.llm_provider or args.claude_config_dir or args.codex_home
        or args.reasoning_effort or args.install or getattr(args, "install_all", False)
        or getattr(args, "llm_model", None) or getattr(args, "llm_base_url", None)
        or getattr(args, "llm_api_key", None)
    )
    return (
        sys.stdin.isatty() and sys.stdout.isatty()
        and not explicit and not getattr(args, "yes", False)
    )


def _setup_interactive_fill(args: argparse.Namespace) -> bool:
    """Prompt for LLM defaults + which optional deps to install, writing the
    answers back onto ``args``. Returns False if the user declines to apply."""
    from rich.prompt import Confirm, Prompt

    import tesserae.llm_json as _lj
    from . import deps

    current = _lj._load_global_llm_config()
    print("Tesserae setup — machine-wide LLM defaults + optional dependencies.\n")
    args.llm_provider = Prompt.ask(
        "LLM provider", choices=["codex", "claude", "anthropic", "custom"],
        default=current.get("llm_provider") or "codex",
    )
    if args.llm_provider == "codex":
        args.reasoning_effort = Prompt.ask(
            "Codex reasoning effort", choices=["low", "medium", "high", "xhigh"],
            default=current.get("llm_codex_reasoning_effort") or "medium",
        )
    if args.llm_provider == "custom":
        args.llm_base_url = Prompt.ask(
            "Base URL (claude-compatible endpoint)",
            default=current.get("llm_base_url") or "",
        ) or None
        args.llm_api_key = Prompt.ask(
            "API key (stored in plaintext config; blank = use ANTHROPIC_API_KEY env)",
            default="",
            password=True,
        ) or None
        args.llm_model = Prompt.ask(
            "Model name", default=current.get("llm_model") or "",
        ) or None
    elif args.llm_provider == "anthropic":
        args.llm_model = Prompt.ask(
            "Model name (blank = provider default)",
            default=current.get("llm_model") or "",
        ) or None

    installed = {d["name"]: d["installed"] for d in deps.status()}
    recommended = {"memex": True, "raganything": False}
    chosen: List[str] = []
    print("\nOptional dependencies:")
    for name in deps.DEP_NAMES:
        if installed.get(name):
            print(f"  · {name}: already installed")
            continue
        if Confirm.ask(f"  install {name}?", default=recommended.get(name, False)):
            chosen.append(name)
    args.install = chosen
    args.install_all = False
    print()
    return Confirm.ask("Apply this setup?", default=True)


def _handle_setup_machine(args: argparse.Namespace) -> int:
    """`tesserae setup` — machine-wide LLM defaults + optional deps.
    Interactive by default on a TTY; flags or --yes skip the prompts."""
    if _setup_wants_interactive(args):
        try:
            if not _setup_interactive_fill(args):
                print("Setup cancelled — nothing changed.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled — nothing changed.")
            return 130
    return _handle_config_setup(args)


def _handle_config_setup(args: argparse.Namespace) -> int:
    """One-shot machine-wide setup: LLM defaults + dep installs (the body of
    `tesserae setup`; the old `config setup` spelling is a moved stub)."""
    import tesserae.llm_json as _lj
    from . import deps

    # Resolve + VALIDATE install targets BEFORE writing any config, so a bad
    # --install never leaves a half-applied setup (config mutated, install
    # rejected).
    targets, unknown = _resolve_dep_targets(args.install, args.install_all)
    if unknown:
        print(f"Unknown dependency: {', '.join(unknown)} (known: {', '.join(deps.DEP_NAMES)})", file=sys.stderr)
        return 2

    llm_model = getattr(args, "llm_model", None)
    llm_base_url = getattr(args, "llm_base_url", None)
    llm_api_key = getattr(args, "llm_api_key", None)
    wrote_llm = bool(args.llm_provider or args.claude_config_dir or args.codex_home
                     or args.reasoning_effort or llm_model or llm_base_url or llm_api_key)
    if wrote_llm:
        merged = _merge_global_llm_config(
            _lj._load_global_llm_config(),
            llm_provider=args.llm_provider,
            claude_config_dirs=args.claude_config_dir,
            codex_home=args.codex_home,
            reasoning_effort=args.reasoning_effort,
            model=llm_model,
            base_url=llm_base_url,
            api_key=llm_api_key,
        )
        wrote = ["LLM defaults"]
        _write_global_config(_lj.GLOBAL_CONFIG_PATH, merged)
        if llm_api_key:
            print(
                f"warning: llm_api_key is stored in plaintext in {_lj.GLOBAL_CONFIG_PATH}; "
                "prefer the ANTHROPIC_API_KEY environment variable",
                file=sys.stderr,
            )
        print(f"Saved machine-wide config to {_lj.GLOBAL_CONFIG_PATH}: {', '.join(wrote)}.")

    rc = _install_deps(targets) if targets else 0

    if not wrote_llm and not targets:
        # No-op invocation → show what's configured + available so the user
        # knows what to pass.
        _handle_config_status(argparse.Namespace(project=None, ping=False))
        print()
        _print_dep_status()
        print("\nRun `tesserae setup` for the interactive wizard, or pass flags, e.g.:\n"
              "  tesserae setup --llm-provider codex --reasoning-effort medium --install all")
    return rc


def _route_config(rest: List[str]) -> int:
    args = _build_config_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- setup (machine-wide, interactive by default) --------------------------
def _build_setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae setup",
        description="Machine-wide setup: LLM defaults + optional dependencies. "
                    "Interactive by default — run it bare to be prompted; pass flags to skip.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae setup                       # interactive wizard\n"
            "  tesserae setup --install all         # install every optional dep\n"
            "  tesserae setup --llm-provider codex --reasoning-effort medium --install all\n"
        ),
    )
    parser.add_argument("--llm-provider", choices=["claude", "codex", "anthropic", "custom"], default=None, help="Machine-wide default LLM backend")
    parser.add_argument("--claude-config-dir", action="append", default=[], help="Default Claude CLI config dir; repeat for fallbacks")
    parser.add_argument("--codex-home", default=None, help="Default Codex CLI home")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"], default=None, help="Default codex reasoning effort")
    parser.add_argument("--llm-model", default=None, help="Machine-wide default model for the synthesis LLM client (llm_model)")
    parser.add_argument("--llm-base-url", default=None, help="Claude-compatible endpoint base URL for anthropic/custom (llm_base_url)")
    parser.add_argument("--llm-api-key", default=None, help="API key for anthropic/custom (stored in PLAINTEXT ~/.tesserae/config.json; prefer ANTHROPIC_API_KEY)")
    parser.add_argument("--install", action="append", default=[], metavar="NAME", help="Dependency to install (memex, raganything, or 'all'); repeat")
    parser.add_argument("--install-all", action="store_true", help="Install every known optional dependency")
    # Removed backend (0.19): the cognee cognify pass no longer exists.
    parser.add_argument(
        "--enable-cognee",
        action=_RemovedFlagAction,
        message="setup: --enable-cognee was removed in 0.19 — the cognee backend no longer exists",
    )
    parser.add_argument(
        "--cognee-mode",
        action=_RemovedFlagAction,
        message="setup: --cognee-mode was removed in 0.19 — the cognee backend no longer exists",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive: apply the flags as given without prompting")
    parser.set_defaults(_handler="_handle_setup_machine", _interactive_default=True)
    return parser


def _route_setup(rest: List[str]) -> int:
    args = _build_setup_parser().parse_args(rest)
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
        print(
            f"{candidate} was not a Tesserae project — initialized .tesserae/ "
            f"(run `tesserae compile --project {candidate}` to populate the graph)."
        )
    args.wiki_command = "register"
    return _wiki_command_handler(args)


def _handle_projects_list(args: argparse.Namespace) -> int:
    args.wiki_command = "list"
    return _wiki_command_handler(args)


def _handle_projects_unregister(args: argparse.Namespace) -> int:
    # Convenience: accept a project PATH as well as the registered alias —
    # resolve it against the registry before delegating.
    from .mcp_server import ProjectRegistry

    registry = ProjectRegistry()
    entries = list(registry.iter_registered_projects())
    if args.name not in {alias for alias, _root in entries}:
        candidate = Path(args.name).expanduser()
        if candidate.exists() or "/" in args.name:
            resolved = candidate.resolve()
            for alias, root in entries:
                if Path(root).expanduser().resolve() == resolved:
                    args.name = alias
                    break
    args.wiki_command = "unregister"
    return _wiki_command_handler(args)


def _handle_projects_mcp_config(args: argparse.Namespace) -> int:
    """`projects mcp-config` = old `mcp-config`."""
    return _handle_mcp_config(args)


def _build_projects_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae projects",
        description="Project registry: register | list | unregister | mcp-config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae projects register /path/to/project\n"
            "  tesserae projects list\n"
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
            "  tesserae projects register /path/to/project --name myproj\n"
        ),
    )
    p_register.add_argument("path", help="Path to the project root containing .tesserae/.")
    p_register.add_argument("--name", help="Friendly name (defaults to the sanitized directory name).")
    p_register.set_defaults(_handler="_handle_projects_register")

    p_list = sub.add_parser("list", help="List registered projects (all active — no privileged project).")
    p_list.add_argument("--json", dest="wiki_list_json", action="store_true", help="Emit the registry payload as JSON.")
    p_list.set_defaults(_handler="_handle_projects_list")

    p_unregister = sub.add_parser("unregister", help="Remove a project from the registry (the project itself is untouched).")
    p_unregister.add_argument("name", help="Registered project name, or the project's path (resolved against the registry).")
    p_unregister.set_defaults(_handler="_handle_projects_unregister")

    p_mcp = sub.add_parser("mcp-config", help="Print a Hermes mcp_servers config snippet for this project")
    p_mcp.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_mcp.add_argument("--server-name", help="MCP server name in Hermes config")
    p_mcp.add_argument("--pythonpath", help="PYTHONPATH pointing at the Tesserae checkout")
    p_mcp.set_defaults(_handler="_handle_projects_mcp_config")
    return parser


def _route_projects(rest: List[str]) -> int:
    if rest[:1] == ["activate"]:
        # Terminal removal stub (mirrors the `wiki activate` MOVED_COMMANDS row).
        print(
            "tesserae projects activate was removed — all registered projects "
            "are active; see `tesserae projects list`",
            file=sys.stderr,
        )
        return 2
    args = _build_projects_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- agents (role-grade org registry) -------------------------------------
def _agents_observed_stats(sessions, registry) -> Dict[str, Dict[str, set]]:
    """Per-agent-key observation stats over the imported session corpus.

    Mirrors the label sourcing in the structural pass
    (session_graph_structural._agent_metadata): parent sessions contribute
    their ``agent_label``, subagents their descriptor ``type``. Returns
    ``{agent_key: {"labels": set[str], "session_ids": set[str]}}``.
    """
    from .agent_identity import resolve_agent_key

    stats: Dict[str, Dict[str, set]] = {}

    def note(key: str, label: str, session_id: str) -> None:
        row = stats.setdefault(key, {"labels": set(), "session_ids": set()})
        row["session_ids"].add(session_id)
        if label:
            row["labels"].add(label)

    for session in sessions:
        note(resolve_agent_key(session, registry), (session.agent_label or "").strip(), session.id)
        subagents = (session.metadata or {}).get("subagents")
        if isinstance(subagents, list):
            for descriptor in subagents:
                if isinstance(descriptor, dict):
                    note(
                        resolve_agent_key(session, registry, subagent=descriptor),
                        str(descriptor.get("type") or "").strip(),
                        session.id,
                    )
    return stats


def _org_tree_lines(parent_of, keys, annotate) -> List[str]:
    """Render an indented org tree from ``org:root`` down.

    ``parent_of`` maps each key to its parent (``org:root`` or another key);
    ``annotate(key)`` returns the trailing per-line detail. Deterministic:
    siblings are emitted in sorted key order. Any key whose parent chain never
    reaches the root (orphan / broken reference) is emitted directly under the
    root so no observed agent is ever silently dropped from the tree.
    """
    from .agent_identity import ORG_ROOT

    all_keys = sorted(set(keys))
    children: Dict[str, List[str]] = {}
    for key in all_keys:
        parent = parent_of.get(key, ORG_ROOT)
        if parent == key:
            parent = ORG_ROOT
        children.setdefault(parent, []).append(key)
    lines = [ORG_ROOT]
    emitted: set = set()

    def walk(node: str, depth: int) -> None:
        for child in sorted(children.get(node, [])):
            if child in emitted:
                continue
            emitted.add(child)
            lines.append(("  " * depth + f"{child}  {annotate(child)}").rstrip())
            walk(child, depth + 1)

    walk(ORG_ROOT, 1)
    for key in all_keys:
        if key not in emitted:
            emitted.add(key)
            lines.append(("  " + f"{key}  {annotate(key)}").rstrip())
    return lines


def _handle_agents_init(args: argparse.Namespace) -> int:
    from .agent_identity import ORG_ROOT, AgentRegistry, infer_org_parents

    wiki = ProjectWiki.load(args.project)
    registry = AgentRegistry.for_project(wiki.project_root)
    if registry.path.exists() and not args.force:
        print(
            f"Agent registry already exists at {registry.path} — pass --force to "
            "re-infer it from the current sessions (do this after a "
            "`tesserae sessions discover --import` to pick up newly captured "
            "subagent roles).",
            file=sys.stderr,
        )
        return 1
    sessions = HarnessSessionStore(wiki.paths.harness_sessions).list_sessions()
    # Raw envelope keys (registry=None): init PROPOSES a registry from what
    # the sessions themselves say, so a pre-existing registry being --force
    # overwritten never bakes its aliases or match rules into the scan.
    stats = _agents_observed_stats(sessions, registry=None)
    # Structural org: a subagent role parents to its same-account main agent
    # (CORE A infer_org_parents), unless --flat forces the legacy everyone-under-
    # org:root chart. Pure function of the observed key set, so it stays
    # deterministic and byte-stable.
    parents = {} if args.flat else infer_org_parents(stats.keys())
    agents = {
        key: {
            "label": sorted(row["labels"])[0] if row["labels"] else key,
            "parent": parents.get(key, ORG_ROOT),
            "aliases": [],
            "match": [],
        }
        for key, row in sorted(stats.items())
    }
    registry.save({"version": 1, "agents": agents})
    print(f"Proposed agent registry: {len(agents)} agent(s) path={registry.path}")

    def _annotate(key: str) -> str:
        row = stats.get(key, {"labels": set(), "session_ids": set()})
        label = agents[key]["label"]
        return f"{label}  ({len(row['session_ids'])} session(s))"

    for line in _org_tree_lines(
        {k: v["parent"] for k, v in agents.items()}, agents.keys(), _annotate
    ):
        print(f"  {line}")
    if not agents:
        print("No sessions imported yet — run `tesserae sessions discover --import` first.")
    elif not args.flat and not any(v["parent"] != ORG_ROOT for v in agents.values()):
        # Every agent landed flat under org:root: no subagent roles were
        # observed, so there is no hierarchy to infer. The usual cause is a
        # session store imported before subagent-descriptor capture existed —
        # tell the user how to surface the roles instead of leaving a silent
        # flat org that looks like the feature did nothing.
        print(
            "\nNote: only main (:default) agents were observed, so the org is "
            "flat — no subagent roles to build a hierarchy from.\n"
            "If you use subagents, re-import to capture them, then re-run init:\n"
            "  tesserae sessions discover --import && tesserae agents init --force",
            file=sys.stderr,
        )
    return 0


def _handle_agents_list(args: argparse.Namespace) -> int:
    from .agent_identity import ORG_ROOT, AgentRegistry

    wiki = ProjectWiki.load(args.project)
    registry = AgentRegistry.for_project(wiki.project_root)
    try:
        declared = registry.load()["agents"]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sessions = HarnessSessionStore(wiki.paths.harness_sessions).list_sessions()
    stats = _agents_observed_stats(sessions, registry)
    rows = []
    for key in sorted(set(stats) | set(declared)):
        entry = declared.get(key)
        observed = stats.get(key, {"labels": set(), "session_ids": set()})
        # Label preference matches the structural pass: registry declaration,
        # then sorted-first observed label, then the key itself.
        label = str(entry.get("label") or "").strip() if isinstance(entry, dict) else ""
        if not label:
            label = sorted(observed["labels"])[0] if observed["labels"] else key
        parent = ORG_ROOT
        if isinstance(entry, dict) and entry.get("parent"):
            parent = str(entry["parent"])
        rows.append(
            {
                "key": key,
                "label": label,
                "parent": parent,
                "sessions": len(observed["session_ids"]),
                "registered": entry is not None,
            }
        )
    if getattr(args, "as_json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(
            "No agents observed — run `tesserae sessions discover --import` "
            "then `tesserae agents init`."
        )
        return 0
    registry_state = registry.path if registry.path.exists() else "none"
    print(f"Agents: {len(rows)} (registry: {registry_state})")
    for row in rows:
        marker = "registered" if row["registered"] else "observed"
        print(
            f"  {row['key']}  parent={row['parent']}  sessions={row['sessions']}  "
            f"[{marker}]  {row['label']}"
        )
    return 0


def _handle_agents_set_parent(args: argparse.Namespace) -> int:
    from .agent_identity import ORG_ROOT, AgentRegistry, sanitize_agent_key

    wiki = ProjectWiki.load(args.project)
    registry = AgentRegistry.for_project(wiki.project_root)
    try:
        declared = registry.load()["agents"]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sessions = HarnessSessionStore(wiki.paths.harness_sessions).list_sessions()
    stats = _agents_observed_stats(sessions, registry)
    # Fail loud (spec §3.2): both ends must be a declared agent, an observed
    # envelope key, or the implicit org:root — never a silent typo'd org chart.
    known = set(declared) | set(stats) | {ORG_ROOT}
    child = sanitize_agent_key(args.child)
    parent = sanitize_agent_key(args.parent)
    for what, key in (("agent", child), ("parent agent", parent)):
        if key not in known:
            print(
                f"Unknown {what}: {key}. Known agents: {', '.join(sorted(known))}",
                file=sys.stderr,
            )
            return 1
    # Post-sanitize self-parent check ("A b" and "a_b" sanitize to the same
    # key) BEFORE the auto-register writes below — set_parent would reject it
    # anyway, but only after new registry entries had already been saved, and
    # a failed command must not leave partial writes behind.
    if child == parent:
        print(f"Agent {child!r} cannot be its own parent", file=sys.stderr)
        return 1
    # Observed-but-undeclared endpoints are registered on the fly (parented to
    # org:root) so reparenting an agent that only exists in session history
    # needs no manual registry ceremony first.
    for key in (child, parent):
        if key != ORG_ROOT and key not in declared:
            labels = stats.get(key, {}).get("labels") or set()
            registry.register(key, label=sorted(labels)[0] if labels else "")
    try:
        row = registry.set_parent(child, parent)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{child} now reports to {row['parent']}")
    return 0


def _handle_agents_rename(args: argparse.Namespace) -> int:
    from .agent_identity import AgentRegistry, sanitize_agent_key

    wiki = ProjectWiki.load(args.project)
    registry = AgentRegistry.for_project(wiki.project_root)
    try:
        data = registry.load()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    agents = data["agents"]
    old = sanitize_agent_key(args.old)
    new = sanitize_agent_key(args.new)
    if old not in agents:
        print(
            f"Unknown agent: {old}. Declared agents: {', '.join(sorted(agents)) or '(none)'}",
            file=sys.stderr,
        )
        return 1
    if new == old:
        print(f"Agent is already named {new}", file=sys.stderr)
        return 1
    if new in agents:
        print(f"Agent already declared: {new}", file=sys.stderr)
        return 1
    entry = agents.pop(old)
    # The old key stays behind as an alias so envelope keys from already
    # imported sessions keep resolving to the renamed agent.
    aliases = {str(a) for a in (entry.get("aliases") or [])}
    aliases.add(old)
    entry["aliases"] = sorted(aliases)
    agents[new] = entry
    for other in agents.values():
        if isinstance(other, dict) and other.get("parent") == old:
            other["parent"] = new
    # Migrate the per-agent artifact dir (distill outputs, Phase 2+) together
    # with the registry entry: rename the dir first and roll it back if the
    # registry save fails, so dir and registry never disagree.
    old_dir = registry.path.parent / old
    new_dir = registry.path.parent / new
    moved = False
    if old_dir.is_dir():
        if new_dir.exists():
            print(f"Cannot rename: {new_dir} already exists", file=sys.stderr)
            return 1
        old_dir.rename(new_dir)
        moved = True
    try:
        registry.save(data)
    except (ValueError, OSError) as exc:
        # OSError covers a failed tmp write/rename (disk full, permissions)
        # — the dir rename must be undone on ANY save failure, or dir and
        # registry disagree exactly as this block promises they never will.
        if moved:
            new_dir.rename(old_dir)
        print(str(exc), file=sys.stderr)
        return 1
    migrated = f" (migrated {old_dir.name}/ -> {new_dir.name}/)" if moved else ""
    print(f"Renamed agent: {old} -> {new}{migrated} (old key kept as alias)")
    return 0


def _handle_agents_tree(args: argparse.Namespace) -> int:
    from .agent_distill import agent_artifact_path
    from .agent_identity import ORG_ROOT, AgentRegistry
    from .project import load_graph_file

    wiki = ProjectWiki.load(args.project)
    registry = AgentRegistry.for_project(wiki.project_root)
    try:
        declared = registry.load()["agents"]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sessions = HarnessSessionStore(wiki.paths.harness_sessions).list_sessions()
    stats = _agents_observed_stats(sessions, registry)
    keys = sorted(set(stats) | set(declared))
    if not keys:
        print(
            "No agents observed — run `tesserae sessions discover --import` "
            "then `tesserae agents init`."
        )
        return 0

    # Parent edges come from the CURRENT registry (declared parent, else the
    # implicit org:root for purely-observed keys) — tree reflects the live org
    # chart, exactly like `agents list`, not a re-inferred one.
    parent_of = {
        key: str(declared[key].get("parent") or ORG_ROOT)
        if isinstance(declared.get(key), dict)
        else ORG_ROOT
        for key in keys
    }

    def _staleness(key: str) -> str:
        path = agent_artifact_path(wiki.project_root, key)
        if not path.is_file():
            return "(not distilled)"
        graph = load_graph_file(path)
        stamps = [
            str(node.metadata.get("distilled_through") or "")
            for node in graph.nodes
            if node.metadata.get("distilled_through")
        ]
        return f"distilled_through={max(stamps)}" if stamps else "(distilled)"

    def _annotate(key: str) -> str:
        entry = declared.get(key)
        observed = stats.get(key, {"labels": set(), "session_ids": set()})
        label = str(entry.get("label") or "").strip() if isinstance(entry, dict) else ""
        if not label:
            label = sorted(observed["labels"])[0] if observed["labels"] else key
        return f"{label}  sessions={len(observed['session_ids'])}  {_staleness(key)}"

    registry_state = registry.path if registry.path.exists() else "none"
    print(f"Agent org tree: {len(keys)} agent(s) (registry: {registry_state})")
    for line in _org_tree_lines(parent_of, keys, _annotate):
        print(f"  {line}")
    return 0


def _handle_agents_show(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    if not wiki.paths.graph.exists():
        print(
            f"No compiled graph at {wiki.paths.graph} — run: tesserae compile",
            file=sys.stderr,
        )
        return 1
    resolved = _resolve_agent_view_or_none(wiki.project_root, wiki.paths.graph, args.key)
    if resolved is None:
        return 1
    _view, info = resolved
    members = info.get("members") or []
    print(f"agent: {info.get('agent')}")
    print(f"mode: {info.get('mode')}")
    print(f"members: {len(members)}")
    for member in members:
        through = str(member.get("distilled_through") or "") or "(unknown)"
        print(
            f"  {member.get('agent_key')}  nodes={member.get('nodes')}  "
            f"distilled_through={through}  {member.get('artifact_path')}"
        )
    return 0


def _handle_agents_drill(args: argparse.Namespace) -> int:
    from .agent_view import drill_down
    from .project import load_graph_file

    wiki = ProjectWiki.load(args.project)
    if not wiki.paths.graph.exists():
        print(
            f"No compiled graph at {wiki.paths.graph} — run: tesserae compile",
            file=sys.stderr,
        )
        return 1
    l0 = load_graph_file(wiki.paths.graph)
    try:
        result = drill_down(
            wiki.project_root,
            l0,
            args.node_id,
            content_hash=getattr(args, "content_hash", "") or "",
            agent=args.agent or "",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"node: {result['node_id']}")
    print(f"status: {result['status']}")
    if result.get("agent"):
        print(f"agent: {result['agent']}")
    if result.get("absorbed_by"):
        print(f"absorbed_by: {result['absorbed_by']}")
    node = result.get("node")
    if isinstance(node, dict):
        print(f"  {node.get('name')} ({node.get('type')})")
        if node.get("description"):
            print(f"  {node['description']}")
        print(f"  content_hash={node.get('content_hash')}")
    print(
        "audit: "
        + ("recorded" if result.get("audited") else "write failed")
        + " in the drill_down_audit ledger"
    )
    return 0


def _build_agents_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae agents",
        description="Role-grade agent org registry: init | list | tree | show | drill | set-parent | rename.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents init\n"
            "  tesserae agents tree\n"
            "  tesserae agents show claude-code:me:reviewer\n"
            "  tesserae agents set-parent claude-code:me:reviewer claude-code:me:default\n"
        ),
    )
    sub = parser.add_subparsers(dest="agents_command", required=True)

    p_init = sub.add_parser(
        "init",
        help="Scan imported sessions and write a proposed registry (subagent roles under their main agent; --flat for a flat org).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents init\n"
            "  tesserae agents init --flat\n"
            "  tesserae agents init --force\n"
        ),
    )
    p_init.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing registry (reversible — it is one JSON file)")
    p_init.add_argument("--flat", action="store_true", help="Parent every agent to org:root (legacy flat org) instead of inferring role hierarchy.")
    p_init.set_defaults(_handler="_handle_agents_init")

    p_tree = sub.add_parser(
        "tree",
        help="Render the current org chart as an indented tree (label, session count, distill staleness).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents tree\n"
        ),
    )
    p_tree.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_tree.set_defaults(_handler="_handle_agents_tree")

    p_show = sub.add_parser(
        "show",
        help="Resolve an agent's read view and print its mode + members (artifact path, node count, staleness).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents show claude-code:me:reviewer\n"
            "  tesserae agents show org\n"
        ),
    )
    p_show.add_argument("key", help="Agent key to resolve (a worker/manager key, or 'org').")
    p_show.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_show.set_defaults(_handler="_handle_agents_show")

    p_drill = sub.add_parser(
        "drill",
        help="Drill a distillate member_ref back to the raw L0 node (alive/absorbed/changed/gone); audited.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents drill SessionInsight:f1\n"
            "  tesserae agents drill SessionInsight:f1 --agent claude-code:me:reviewer\n"
        ),
    )
    p_drill.add_argument("node_id", help="The member_refs[].node_id to resolve against L0.")
    p_drill.add_argument("--agent", default=None, help="Owning agent key — checks its L1 artifact for an absorbing distillate.")
    p_drill.add_argument("--content-hash", dest="content_hash", default="", help="Expected content hash; a mismatch reports status=changed.")
    p_drill.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_drill.set_defaults(_handler="_handle_agents_drill")

    p_list = sub.add_parser(
        "list",
        help="List observed agent keys + registry state (label, parent, session counts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents list\n"
            "  tesserae agents list --json\n"
        ),
    )
    p_list.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_list.add_argument("--json", dest="as_json", action="store_true", help="Emit the agent rows as JSON.")
    p_list.set_defaults(_handler="_handle_agents_list")

    p_set = sub.add_parser(
        "set-parent",
        help="Reparent an agent in the org chart (both keys validated against observed/registry keys).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents set-parent claude-code:me:reviewer claude-code:me:default\n"
            "  tesserae agents set-parent codex:me:default org:root\n"
        ),
    )
    p_set.add_argument("child", help="Agent key to reparent.")
    p_set.add_argument("parent", help="New parent agent key, or org:root.")
    p_set.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_set.set_defaults(_handler="_handle_agents_set_parent")

    p_rename = sub.add_parser(
        "rename",
        help="Rename a declared agent — migrates the agents/<key>/ dir + registry entry atomically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae agents rename claude-code:old-account:reviewer claude-code:me:reviewer\n"
        ),
    )
    p_rename.add_argument("old", help="Currently declared agent key.")
    p_rename.add_argument("new", help="New agent key (the old key is kept as an alias).")
    p_rename.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    p_rename.set_defaults(_handler="_handle_agents_rename")
    return parser


def _route_agents(rest: List[str]) -> int:
    args = _build_agents_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- sources (compile scope: local + global) ------------------------------
def _normalize_source(project_root: Path, raw: str):
    """Map a user-given path to its stored form + resolved location + kind.

    A path that lands INSIDE the project root is stored project-relative (a
    *local* source — keeps config.json portable); anything outside (an absolute
    path, or a relative one like ``../shared`` that escapes the root) is stored
    absolute (a *global* source). ``resolve_project_input`` resolves both at
    compile time, so either kind just works."""
    abs_resolved = resolve_project_input(project_root, raw).resolve()
    try:
        return (str(abs_resolved.relative_to(project_root.resolve())), abs_resolved, False)
    except ValueError:
        return (str(abs_resolved), abs_resolved, True)


def _write_sources(wiki, sources: List[str]) -> None:
    cfg = wiki.config()
    cfg["sources"] = sources
    wiki.paths.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _handle_sources_add(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    root = wiki.project_root
    sources = list(wiki.config().get("sources") or [])
    stored, abs_resolved, is_global = _normalize_source(root, args.path)
    if not abs_resolved.exists():
        print(f"warning: {abs_resolved} does not exist (adding anyway)", file=sys.stderr)
    existing = {resolve_project_input(root, s).resolve() for s in sources}
    if abs_resolved in existing:
        print(f"already a source: {stored}")
        return 0
    sources.append(stored)
    _write_sources(wiki, sources)
    print(f"Added {'global' if is_global else 'local'} source: {stored}  ({len(sources)} total)")
    return 0


def _handle_sources_list(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    root = wiki.project_root
    sources = list(wiki.config().get("sources") or [])
    if not sources:
        print("No sources configured. Add one: tesserae sources add <path>")
        return 0
    for s in sources:
        kind = "global" if Path(s).is_absolute() else "local"
        exists = "" if resolve_project_input(root, s).resolve().exists() else "  (MISSING)"
        print(f"  [{kind:6}] {s}{exists}")
    return 0


def _handle_sources_remove(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    root = wiki.project_root
    sources = list(wiki.config().get("sources") or [])
    target = resolve_project_input(root, args.path).resolve()
    kept = [s for s in sources if resolve_project_input(root, s).resolve() != target]
    if len(kept) == len(sources):
        print(f"not a source: {args.path}", file=sys.stderr)
        return 1
    _write_sources(wiki, kept)
    print(f"Removed source: {args.path}  ({len(kept)} total)")
    return 0


def _build_sources_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae sources",
        description="Manage the project's compile source directories (local & global).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae sources add docs                 # local (project-relative)\n"
            "  tesserae sources add /data/shared-notes   # global (absolute)\n"
            "  tesserae sources add ../sibling-project   # global (escapes the root)\n"
            "  tesserae sources list\n"
            "  tesserae sources remove docs\n"
        ),
    )
    sub = parser.add_subparsers(dest="sources_command", required=True)

    p_add = sub.add_parser("add", help="Add a directory/file to the compile scope (inside the project = local, outside = global).")
    p_add.add_argument("path", help="Directory or file to compile. Inside the project → stored project-relative (local); outside → stored absolute (global).")
    p_add.add_argument("--project", default=".", help="Project root; defaults to the current directory.")
    p_add.set_defaults(_handler="_handle_sources_add")

    p_list = sub.add_parser("list", help="List the configured compile sources (marks each local/global, flags missing).")
    p_list.add_argument("--project", default=".", help="Project root; defaults to the current directory.")
    p_list.set_defaults(_handler="_handle_sources_list")

    p_remove = sub.add_parser("remove", help="Remove a source from the compile scope (matched by resolved location).")
    p_remove.add_argument("path", help="The source path to remove.")
    p_remove.add_argument("--project", default=".", help="Project root; defaults to the current directory.")
    p_remove.set_defaults(_handler="_handle_sources_remove")
    return parser


def _route_sources(rest: List[str]) -> int:
    args = _build_sources_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


# ----- federation (v3 inspectability) ---------------------------------------
def _build_federation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae federation",
        description="Inspect cross-project federation: status | explain.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae federation status research work\n"
            "  tesserae federation status research work --semantic\n"
            "  tesserae federation explain 'research::Concept:42' research work --semantic\n"
        ),
    )
    sub = parser.add_subparsers(dest="federation_command", required=True)

    p_status = sub.add_parser("status", help="Counts: per-project nodes, identity merges, semantic links.")
    p_status.add_argument("projects", nargs="*", help="Aliases to federate (default: all registered).")
    p_status.add_argument("--semantic", dest="semantic", action="store_true", help="Include embedding-backed cross-project links (the default; matches `explain` and federated ask).")
    p_status.add_argument("--no-semantic", dest="semantic", action="store_false", help="Identity merges only (default includes semantic links).")
    p_status.add_argument("--json", dest="federation_json", action="store_true", help="Emit JSON.")
    p_status.set_defaults(_handler="_handle_federation_status", semantic=True)

    p_explain = sub.add_parser("explain", help="Show one node's cross-project connections.")
    p_explain.add_argument("node", help="Node id (alias::id, a merged-away id, or a unique suffix).")
    p_explain.add_argument("projects", nargs="*", help="Aliases to federate (default: all registered).")
    p_explain.add_argument("--semantic", dest="semantic", action="store_true", help="Include semantic links (the default).")
    p_explain.add_argument("--no-semantic", dest="semantic", action="store_false", help="Identity merges only (default includes semantic links).")
    p_explain.add_argument("--json", dest="federation_json", action="store_true", help="Emit JSON.")
    p_explain.set_defaults(_handler="_handle_federation_explain", semantic=True)
    return parser


def _route_federation(rest: List[str]) -> int:
    args = _build_federation_parser().parse_args(rest)
    return _resolve_handler(args._handler)(args)


def _resolve_federation_aliases(args):
    from .mcp_server import ProjectRegistry

    registry = ProjectRegistry()
    aliases = list(getattr(args, "projects", None) or [])
    if not aliases:
        data = registry.list_projects()
        aliases = sorted(p.get("name") for p in (data.get("projects") or []) if p.get("name"))
    return registry, aliases


def _handle_federation_status(args: argparse.Namespace) -> int:
    from .federation import federation_status

    registry, aliases = _resolve_federation_aliases(args)
    if not aliases:
        print("No projects registered. Use `tesserae projects register <path>`.", file=sys.stderr)
        return 2
    try:
        result = federation_status(aliases, registry, semantic=bool(getattr(args, "semantic", True)))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if getattr(args, "federation_json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"Federation · {', '.join(result['projects'])}")
    print(f"  nodes={result['nodes']}  edges={result['edges']}  "
          f"identity_merges={result['identity_merges']}  dropped_edges={result['dropped_edges']}")
    for alias, count in result["per_project_nodes"].items():
        print(f"    {alias}: {count} nodes")
    sem = result["semantic"]
    if sem.get("semantic_skipped"):
        print(f"  semantic: skipped — {sem['semantic_skipped']}")
    elif "semantic_added" in sem:
        cached = " [cached]" if sem.get("semantic_cached") else ""
        print(f"  semantic: {sem['semantic_added']} cross-project links via {sem.get('semantic_backend', '?')}{cached}")
    return 0


def _handle_federation_explain(args: argparse.Namespace) -> int:
    from .federation import federation_explain

    registry, aliases = _resolve_federation_aliases(args)
    if not aliases:
        print("No projects registered. Use `tesserae projects register <path>`.", file=sys.stderr)
        return 2
    try:
        result = federation_explain(args.node, aliases, registry, semantic=bool(getattr(args, "semantic", True)))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if getattr(args, "federation_json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"{result['node']}  ({result['type']}: {result['name']})")
    if len(result["merged_from_projects"]) > 1:
        print(f"  identity-merged across: {', '.join(result['merged_from_projects'])}")
    print(f"  links: {len(result['links'])}")
    for link in result["links"]:
        kind = f"~{link['cosine']}" if link["semantic"] else link["type"]
        proj = ",".join(link["other_projects"]) or "?"
        print(f"    [{kind}] {link['other']} ({proj}) — {link['other_name']}")
    return 0


# ----- integrations ---------------------------------------------------------
def _handle_integrations_refresh(args: argparse.Namespace) -> int:
    """`integrations refresh <name>` routes to the managed refresh handlers."""
    if args.name == "understand-anything":
        # Clean-break stub (no-silent-aliases convention): one line, exit 2.
        print(
            "removed — code-structure nodes are extracted natively; see tesserae code ingest",
            file=sys.stderr,
        )
        return 2
    return _handle_refresh_raganything(args)


def _build_integrations_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae integrations",
        description="Managed integration refreshes: refresh <name>.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae integrations refresh raganything\n"
        ),
    )
    sub = parser.add_subparsers(dest="integrations_command", required=True)
    p_refresh = sub.add_parser(
        "refresh",
        help="Run the managed refresh for raganything",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae integrations refresh raganything --full\n"
        ),
    )
    # "understand-anything" stays parseable so the removal stub (exit 2) can
    # answer instead of a confusing argparse choices error.
    p_refresh.add_argument("name", choices=["raganything", "understand-anything"], help="Integration to refresh", metavar="raganything")
    p_refresh.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    # raganything flags (refresh-raganything parser)
    p_refresh.add_argument("--parser", default="mineru", choices=["mineru", "docling", "paddleocr"], help="raganything parser backend")
    p_refresh.add_argument("--parse-method", default="auto", choices=["auto", "ocr", "txt"], help="raganything parse method")
    p_refresh.add_argument("--root", action="append", dest="roots", help="Restrict to this root (repeatable; raganything)")
    p_refresh.add_argument("--full", action="store_true", help="Force a full refresh")
    p_refresh.add_argument("--force", action="store_true", help="Run even if the existing graph appears current")
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
    kuzu/canonicalize flags live HERE, not on `compile`).
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
    parser.add_argument(
        "--source-kind",
        choices=["Paper", "Repository", "ResearchDigest", "SourceDocument"],
        default="SourceDocument",
        help="Default source kind (exact: 'Paper'/'Repository'/'ResearchDigest' map directly "
        "to that node type; 'SourceDocument' keeps path-based detection)",
    )
    parser.add_argument("--output", "-o", help="Write JSON graph to this path instead of stdout")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--trends", action="store_true", help="Add corpus-level Trend nodes for concepts repeated across sources")
    parser.add_argument("--min-trend-sources", type=int, default=2, help="Minimum distinct sources required to create a Trend node")
    parser.add_argument("--extractor", choices=["llm", "selective-llm", "deterministic", "claude-cli", "selective-claude"], default="llm",
                        help="Extraction backend. 'llm' (default) builds the concept/claim layer via the configured provider (codex/claude/api); 'selective-llm' routes only --llm-include globs through the LLM; 'deterministic' is structural-only.")
    parser.add_argument("--llm-provider", choices=["claude", "codex", "anthropic", "custom"], default=None, help="Override the LLM provider (default: llm_provider in config).")
    parser.add_argument("--llm-model", default=None, help="Model for the LLM extractor (default: the provider's default).")
    parser.add_argument("--llm-include", action="append", default=None, help="Glob selecting files for --extractor selective-llm; repeat for several.")
    parser.add_argument("--llm-limit", type=int, default=None, help="Max files sent to the LLM under --extractor selective-llm.")
    # Deprecated Claude-specific aliases (kept hidden so 0.12.x invocations parse).
    parser.add_argument("--claude-config-dir", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--claude-model", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--claude-timeout", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--claude-include", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--claude-limit", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--canonicalize", action="store_true", help="Merge high-confidence aliases and produce review candidates for ambiguous duplicates")
    parser.add_argument("--review-output", help="Write canonicalization review queue JSON to this path")
    parser.add_argument("--review-markdown-output", help="Write a human-readable markdown review queue")
    parser.add_argument("--review-jsonl-output", help="Write review queue items as JSONL")
    parser.add_argument("--review-decisions-template", help="Write a starter review decisions JSON template")
    parser.add_argument("--apply-review-decisions", help="Apply review decisions JSON after canonicalization; implies --canonicalize")
    parser.add_argument("--project-markdown", help="Write a human-readable markdown projection of the final graph to this directory")
    parser.add_argument("--sqlite-output", help="Persist the final graph to a local SQLite database")
    parser.add_argument("--kuzu-output", help="Persist the final graph to a local Kuzu database")
    # Removed backend (0.19): the cognee bundle export / cognify pass no longer
    # exist. Every --cognee-* flag is a clean-break stub (exit 2, one line).
    for _removed in (
        "--cognee-output",
        "--cognee-add",
        "--cognee-cognify",
        "--cognee-codex-cognify",
        "--cognee-codex-model",
        "--cognee-codex-timeout",
        "--cognee-local-embedding-dimensions",
        "--cognee-embedding-provider",
        "--cognee-ollama-embedding-model",
        "--cognee-ollama-embedding-endpoint",
        "--cognee-ollama-embedding-timeout",
        "--cognee-dataset",
        "--cognee-system-root",
        "--cognee-data-root",
    ):
        parser.add_argument(
            _removed,
            action=_RemovedFlagAction,
            message=f"extract: {_removed} was removed in 0.19 — cognee was demoted in 0.18 and never fed the graph",
        )
    parser.add_argument("--batch-manifest", help="Track file hashes for incremental changed-only batch ingestion")
    parser.add_argument("--changed-only", action="store_true", help="When used with --batch-manifest, skip files whose content hash is unchanged")
    parser.add_argument("--limit", type=int, help="Maximum number of files to process in this run")
    parser.add_argument("--report-output", help="Write a markdown summary report for the final graph")
    return parser


def _build_doc_extractor(args: argparse.Namespace, cfg: Optional[dict] = None):
    """Build the document extractor for `compile` / `extract`.

    Tesserae is an LLM wiki, so the concept/claim layer is the DEFAULT: 'llm'
    (and 'selective-llm') drive the configured provider (codex / claude / api per
    ``llm_provider``) through the shared LLMJsonClient. 'deterministic' is the
    explicit opt-out — the structural, key-free, byte-idempotent mode (CI). If no
    LLM backend is configured/authed we degrade to deterministic with a warning
    rather than hard-fail. ('claude-cli'/'selective-claude' are deprecated aliases
    for 'llm'/'selective-llm'.) ``cfg`` is the PROJECT config so a per-project
    ``llm_provider`` is honoured, not just the machine-global default."""
    kind = getattr(args, "extractor", None) or "llm"
    aliases = {"claude-cli": "llm", "selective-claude": "selective-llm"}
    if kind in aliases:
        print(f"note: --extractor {kind} is deprecated; use --extractor {aliases[kind]} "
              "(provider comes from llm_provider in config).", file=sys.stderr)
        kind = aliases[kind]

    if kind == "deterministic":
        return ResearchGraphExtractor()

    # llm / selective-llm: provider-agnostic client (codex/claude/api per config).
    from .llm_json import build_default_json_client, resolve_llm_client_settings

    settings = resolve_llm_client_settings(cfg)  # honour the PROJECT's llm_provider
    client = build_default_json_client(
        model=getattr(args, "llm_model", None) or getattr(args, "claude_model", None),
        provider=getattr(args, "llm_provider", None) or settings.get("provider"),
        claude_config_dirs=(getattr(args, "claude_config_dir", None) or settings.get("claude_config_dirs")),
        codex_home=settings.get("codex_home"),
        codex_reasoning_effort=settings.get("codex_reasoning_effort") or "medium",
        timeout=None,  # no cutoff — a slow doc runs to completion (timeout is opt-in only)
    )
    if client is None:
        print("warning: no LLM backend available (codex/claude not authed, no "
              "ANTHROPIC_API_KEY) — building the STRUCTURAL graph only. Run "
              "`tesserae setup` to configure a provider, or pass "
              "`--extractor deterministic` to silence this.", file=sys.stderr)
        return ResearchGraphExtractor()

    # Wrap in the selective router so a backend failure on ONE doc (auth expiry,
    # timeout, None/invalid generation -> GraphJSONValidationError) falls back to
    # deterministic for THAT doc instead of aborting the whole compile. Plain
    # 'llm' routes every doc to the LLM (include=["*"]); 'selective-llm' only the
    # user's globs.
    det = ResearchGraphExtractor()
    llm = LLMResearchExtractor(client)
    if kind == "selective-llm":
        return SelectiveClaudeResearchExtractor(
            deterministic=det, claude=llm,
            include_patterns=getattr(args, "llm_include", None) or getattr(args, "claude_include", []),
            claude_limit=getattr(args, "llm_limit", None) or getattr(args, "claude_limit", None),
        )
    return SelectiveClaudeResearchExtractor(deterministic=det, claude=llm, include_patterns=["*"])


def _handle_extract(args: argparse.Namespace) -> int:
    """Body lifted verbatim from the legacy bare-extraction main (now removed;
    sans its own parse_args)."""
    extractor = _build_doc_extractor(args)
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
    parser.add_argument(
        "--no-web",
        action=_RemovedFlagAction,
        message="research: web search is not implemented; --no-web was removed",
    )
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
    parser.add_argument("--verify-claims", dest="verify_claims", action="store_true", help="Opt-in: sample cited claims from synthesis pages and LLM-verify the cited node supports each (supported/partial/unsupported). Needs an LLM backend; costs one batched call.")
    parser.add_argument("--claim-cap", dest="claim_cap", type=int, default=20, help="Max claims to sample for --verify-claims (default: 20).")
    return parser


def _route_lint(rest: List[str]) -> int:
    args = _build_lint_parser().parse_args(rest)
    return _resolve_handler("_handle_lint")(args)


def _build_doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae doctor",
        description="Project health checks: init/graph/config, registry, staleness, locks, hygiene. Read-only by default; --fix applies only the safe repairs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae doctor\n"
            "  tesserae doctor --fix\n"
            "  tesserae doctor --all --json\n"
        ),
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--all", dest="all_projects", action="store_true", help="Doctor every registered project (ignores --project)")
    parser.add_argument("--fix", action="store_true", help="Apply the safe fixes only: registry prune, site rebuild, lint trivial fixes, stale daemon.pid removal, build-history trim, hook-log rotation, vault mkdir, git worktree prune. Never kills or removes a live compile lock.")
    parser.add_argument("--json", dest="doctor_json", action="store_true", help="Print the JSON report to stdout instead of the markdown checklist")
    return parser


def _handle_doctor(args: argparse.Namespace) -> int:
    from .doctor import (
        overall_exit_code,
        render_markdown,
        run_doctor,
        run_doctor_all,
        to_json,
        write_report,
    )

    if args.all_projects:
        from .mcp_server import ProjectRegistry

        reports = run_doctor_all(ProjectRegistry(), fix=args.fix)
        for _alias, report in sorted(reports.items()):
            if (Path(report.project_root) / ".tesserae").is_dir():
                write_report(report.project_root, report)
            sys.stdout.write(to_json(report) if args.doctor_json else render_markdown(report))
        return overall_exit_code(reports)
    report = run_doctor(args.project, fix=args.fix)
    if (Path(args.project) / ".tesserae").is_dir():
        write_report(args.project, report)  # .tesserae/doctor-report.{md,json}
    sys.stdout.write(to_json(report) if args.doctor_json else render_markdown(report))
    return report.exit_code  # 0 healthy / 1 warnings / 2 errors (mirrors lint)


def _route_doctor(rest: List[str]) -> int:
    args = _build_doctor_parser().parse_args(rest)
    return _resolve_handler("_handle_doctor")(args)


def _build_query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae query",
        description=(
            "Raw retrieval: BM25/semantic search over the compiled wiki, or an "
            "explicit memory backend (--backend raganything). No LLM "
            "synthesis — that lives on `tesserae ask`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae query \"what cites the compile pipeline?\"\n"
            "  tesserae query \"open questions\" --top-k 12\n"
            "  tesserae query \"vector store choice\" --backend raganything\n"
        ),
    )
    parser.add_argument("question", nargs="?", default=None, help="Question text; omit to use --interactive")
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--top-k", type=int, default=8, help="Maximum number of search hits to return (default: 8)")
    parser.add_argument("--kind", help="Restrict hits to a single wiki kind (e.g. papers, concepts, repos)")
    # "cognee" stays parseable so the removal stub in `_project_query_handler`
    # (exit 2) can answer instead of a confusing argparse choices error.
    parser.add_argument(
        "--backend",
        choices=["wiki", "raganything", "cognee"],
        default="wiki",
        metavar="{wiki,raganything}",
        help="Retrieval backend (default: wiki = compiled-wiki search). Explicit "
        "raganything short-circuits to that backend and surfaces its errors.",
    )
    # Removed backend (0.19): every --cognee-* flag is a clean-break stub.
    for _removed in ("--cognee-search-type", "--cognee-dataset"):
        parser.add_argument(
            _removed,
            action=_RemovedFlagAction,
            message=f"query: {_removed} was removed in 0.19 — cognee was demoted in 0.18 and never fed the graph",
        )
    for _moved in ("--llm", "--no-llm", "--model"):
        parser.add_argument(
            _moved,
            action=_RemovedFlagAction,
            message="query: LLM synthesis has moved → tesserae ask",
        )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print the structured QueryResult as JSON")
    parser.add_argument("--interactive", action="store_true", help="Drop into a REPL with readline history; blank line or EOF exits")
    parser.add_argument(
        "--agent",
        default=None,
        metavar="KEY",
        help="Scope hits to one agent's distilled view: a worker key (its L0 ∪ own distillate), a manager key (its team's distillates), or 'org' (every agent's distillate). Post-filters hits to the view's nodes. Unknown/undistilled keys fail loud.",
    )
    return parser


def _route_query(rest: List[str]) -> int:
    args = _build_query_parser().parse_args(rest)
    return _resolve_handler("_handle_query")(args)


def _build_distill_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesserae distill",
        description=(
            "Distill per-agent L1 expertise artifacts "
            "(.tesserae/agents/<key>/distilled.graph.json) from the compiled "
            "graph. Runs outside compile; opt-in via TESSERAE_AGENT_DISTILL=1 "
            "or config.json {\"agent_distill\": {\"enabled\": true}}. "
            "Exit codes: 0 ok, 1 failure (gate off, unknown agent, distill "
            "error), 2 usage / missing inputs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tesserae distill --dry-run\n"
            "  tesserae distill --agent claude-code:me:reviewer\n"
            "  tesserae distill --all --max-llm-calls 20\n"
            "  tesserae distill --full --retry-fallbacks\n"
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--agent",
        dest="agents",
        action="append",
        default=[],
        metavar="KEY",
        help="Distill only this agent key (repeatable; registry aliases resolve). Unknown keys fail loud.",
    )
    scope.add_argument(
        "--all",
        dest="all_agents",
        action="store_true",
        help="Distill every agent observed in the compiled graph (the default when no --agent is given).",
    )
    parser.add_argument("--project", default=".", help="Project root directory; defaults to current working directory")
    parser.add_argument("--dry-run", action="store_true", help="Print clusters + estimated LLM calls per agent; write nothing, call nothing.")
    parser.add_argument("--max-llm-calls", type=int, default=None, metavar="N", help="Cap provider calls for this run (the shared cache makes capped runs converge over several invocations).")
    parser.add_argument("--jobs", type=int, default=1, metavar="N", help="Accepted for CLI parity with the spec; execution is sequential in this release (validated >= 1).")
    parser.add_argument("--full", action="store_true", help="Ignore per-agent watermarks (still uses the shared distill cache) — converges a fresh clone to byte-identical artifacts.")
    parser.add_argument("--retry-fallbacks", action="store_true", help="Re-attempt clusters whose cached verdict is a deterministic fallback (provider recovered).")
    parser.add_argument("--recheck", action="store_true", help="Re-audit cached outputs against the current validation contract (pair with a schema_version bump).")
    parser.add_argument("--as-of", default=None, metavar="TS", help="ISO-8601 corpus-clock override — required only when scope sessions carry no timestamps.")
    parser.set_defaults(_handler="_handle_distill")
    return parser


def _handle_distill(args: argparse.Namespace) -> int:
    from .agent_distill import (
        DistillError,
        DistillOptions,
        agent_distill_enabled,
        build_llm_summarizer,
        distill_agent,
        distill_all,
    )
    from .agent_identity import ORG_ROOT, AgentRegistry, sanitize_agent_key
    from .research_graph import ResearchNodeType

    if args.jobs < 1:
        print("distill: --jobs must be >= 1", file=sys.stderr)
        return 2

    wiki = ProjectWiki.load(args.project)
    if not agent_distill_enabled(wiki.config()):
        print(
            "Agent distillation is opt-in — set TESSERAE_AGENT_DISTILL=1 or "
            'config.json {"agent_distill": {"enabled": true}} to enable it.',
            file=sys.stderr,
        )
        return 1
    if not wiki.paths.graph.is_file():
        print("error: no compiled graph yet — run `tesserae compile` first.", file=sys.stderr)
        return 2

    graph = _load_graph_file(wiki.paths.graph)
    registry = AgentRegistry.for_project(wiki.project_root)
    try:
        declared = set(registry.load()["agents"])
    except ValueError as exc:  # corrupt registry — same fail-loud as `agents list`
        print(str(exc), file=sys.stderr)
        return 1
    observed = {
        str((node.metadata or {}).get("agent_key") or "")
        for node in graph.nodes
        if node.type is ResearchNodeType.AGENT
    }
    observed -= {"", ORG_ROOT}

    options = DistillOptions(
        max_llm_calls=args.max_llm_calls,
        dry_run=args.dry_run,
        full=args.full,
        retry_fallbacks=args.retry_fallbacks,
        recheck=args.recheck,
        as_of=args.as_of,
        jobs=args.jobs,
    )
    # Resolved through build_llm_summarizer so the set_agent_distill_test_client
    # seam intercepts; None means no LLM backend — the pass still runs, every
    # cluster takes the deterministic fallback path and is visibly counted.
    summarizer = build_llm_summarizer()

    try:
        if args.agents:
            # Fail loud on unknown keys (spec §3.2 culture): a key must be
            # declared in the registry or observed as an Agent node in the
            # compiled graph — never a silently empty distill run.
            known = declared | observed
            keys: List[str] = []
            for raw in args.agents:
                key = registry.resolve_alias(sanitize_agent_key(raw))
                if key not in known:
                    print(
                        f"Unknown agent: {key}. Known agents: "
                        f"{', '.join(sorted(known)) or '(none)'}",
                        file=sys.stderr,
                    )
                    return 1
                keys.append(key)
            results = [
                distill_agent(
                    graph,
                    key,
                    project_root=wiki.project_root,
                    registry=registry,
                    summarizer=summarizer,
                    options=options,
                )
                for key in dict.fromkeys(keys)  # dedupe, keep CLI order
            ]
        else:
            results = distill_all(
                graph,
                project_root=wiki.project_root,
                registry=registry,
                summarizer=summarizer,
                options=options,
            )
    except DistillError as exc:
        print(f"distill: {exc}", file=sys.stderr)
        return 1

    if not results:
        print(
            "No agents observed in the compiled graph — import sessions and "
            "run `tesserae compile` first (then `tesserae agents init`)."
        )
        return 0

    totals: Dict[str, int] = {}
    for result in results:
        totals[result.status] = totals.get(result.status, 0) + 1
        if result.status == "skipped-watermark":
            print(f"{result.agent_key}  skipped-watermark (inputs unchanged)")
            continue
        if result.status == "no-sessions":
            print(f"{result.agent_key}  no-sessions (nothing attributed to this agent)")
            continue
        if result.status == "dry-run":
            print(
                f"{result.agent_key}  dry-run  clusters={result.cluster_count} "
                f"estimated_llm_calls={result.estimated_llm_calls} "
                f"scope={result.scope_count}"
            )
            continue
        print(
            f"{result.agent_key}  {result.status}  clusters={result.cluster_count} "
            f"llm_calls={result.llm_calls} cache_hits={result.llm_cache_hits} "
            f"folds={result.llm_folds} fallbacks={result.llm_fallbacks} "
            f"rejected={result.llm_rejected} failed={result.llm_failed}"
        )
        print(
            f"  -> {result.artifact_path} "
            f"({result.artifact_chars} chars, {result.size_level})"
        )
        if result.llm_aborted:
            print(
                f"  !! LLM stage aborted (circuit breaker) — "
                f"{result.llm_failed} transport failure(s); affected clusters "
                "took the cached deterministic fallback. Re-run when the "
                "provider recovers (--retry-fallbacks upgrades them)."
            )
        if result.size_level == "warning":
            print(
                f"  !! artifact at {result.artifact_chars} chars is within 10% "
                "of the one-read 48k bound (spec §2) — expect index truncation soon."
            )
    summary = "  ".join(f"{status}={count}" for status, count in sorted(totals.items()))
    print(f"Distill pass over {len(results)} agent(s): {summary}")
    return 0


def _route_distill(rest: List[str]) -> int:
    args = _build_distill_parser().parse_args(rest)
    return _resolve_handler("_handle_distill")(args)


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
    "summary": _route_summary,
    "decisions": _route_decisions,
    "engine": _route_engine,
    "refresh": _route_refresh,
    # task 5: groups
    "sessions": _route_sessions,
    "vault": _route_vault,
    "export": _route_export,
    "code": _route_code,
    "ingest": _route_ingest,
    "config": _route_config,
    "setup": _route_setup,
    "projects": _route_projects,
    "agents": _route_agents,
    "sources": _route_sources,
    "federation": _route_federation,
    "integrations": _route_integrations,
    "lab": _route_lab,
    "extract": _route_extract,
    # task 5: standalone verbs
    "research": _route_research,
    "lint": _route_lint,
    "doctor": _route_doctor,
    "query": _route_query,
    # layered agent KG (Phase 2): per-agent L1 distillation
    "distill": _route_distill,
}


def _dispatch_command(command: str, rest: List[str]) -> int:
    router = _NEW_DISPATCH.get(command)
    if router is not None:
        return router(rest)
    raise NotImplementedError(f"tesserae {command}: wired in a later task")


if __name__ == "__main__":
    raise SystemExit(main())
