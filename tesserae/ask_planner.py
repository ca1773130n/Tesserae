"""LLM-planned KG retrieval for ``ask --llm`` (plan → execute → synthesize).

When the LLM is in the loop it reasons about the question FIRST and emits a
retrieval plan over the knowledge graph; the plan is executed against the
graph/wiki primitives and the gathered evidence is synthesized into a cited
answer. There are deliberately NO keyword heuristics here — the model decides
which primitives fit the question ("what happened recently?" → timeline +
recent_sessions + activity_summary; "what is the hybrid retriever?" →
wiki_search). When no LLM backend is usable, planning or synthesis fails, the
caller falls back to the classic BM25 path unchanged.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .citation_names import NODE_CITATION_RE, rewrite_citations
from .research_graph import (ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES,
                             SESSION_FINDING_KIND_TO_TYPE,
                             SESSION_FINDING_KINDS)
from .retrieval.views import VIEWS

MAX_STEPS = 5
_EVIDENCE_CLIP = 2500  # chars per evidence block fed to synthesis
#: The bundle is BUILT to fit the evidence slot, not built large and then
#: truncated. ``compile_context`` defaults to 32,000 chars while every branch
#: returns through ``_clip`` — without this ~92% of each bundle would be
#: computed, paid for and thrown away mid-sentence, and the compiler's own
#: budget-aware selection would be meaningless because the real cut happens
#: outside it. The headroom carries the one-line views header inside the clip.
_CONTEXT_BUDGET = 2_200
#: A proposal is a suggestion, not a queue: three of each is enough to act on
#: and small enough that a bad plan cannot flood the envelope.
_MAX_PROPOSED = 3

# One entry per retrieval primitive: (action, args signature, when to use).
# This catalog IS the planner prompt — keep descriptions honest about what
# each primitive can and cannot answer.
_CATALOG: List[Tuple[str, str, str]] = [
    (
        "wiki_search",
        '{"query": str, "top_k": int<=8}',
        "BM25 over compiled wiki pages (concepts, repos, papers, sources). "
        "Best for 'what is X', capabilities, architecture, design docs. "
        "Static descriptions only — it has NO dates and cannot answer "
        "'what happened / changed'.",
    ),
    (
        "timeline",
        '{"query": str, "since": "YYYY-MM-DD", "limit": int<=50}',
        "Dated events projected from the graph, ordered by valid_from. Best "
        "for 'what happened', changes over time, when something started. "
        "query is optional keywords; empty query returns everything in range.",
    ),
    (
        "search_facts",
        '{"query": str, "limit": int<=20}',
        "Subject-predicate-object temporal facts with evidence/provenance. "
        "Best for verifying a specific claim or relation between two things.",
    ),
    (
        "recent_sessions",
        '{"since": "YYYY-MM-DD", "limit": int<=20}',
        "Work sessions (agent + human) newest-first: title, start time, "
        "summary. Best for recent activity and 'what was worked on'.",
    ),
    (
        "session_findings",
        # The kind union is INTERPOLATED from the taxonomy, not retyped: this
        # string is the planner's system prompt, so a kind missing here is a
        # kind the planner is instructed never to emit — and the executor
        # branch that maps it then never runs, however correct that map is.
        '{"kind": "' + "|".join(SESSION_FINDING_KINDS) + '", "limit": int<=20}',
        "Findings extracted from sessions, newest first. Omit kind for all "
        "kinds. Best for 'what did we learn/decide/try' style questions; "
        "kind='failure' answers 'what broke' / 'what did we try that did not "
        "work'.",
    ),
    (
        "activity_summary",
        '{"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}',
        "Deterministic digest of sessions, findings, commits, PRs and "
        "ingested docs per day in the window. The single best source for "
        "'what happened recently'. Keep the window <= 14 days.",
    ),
    (
        "decisions",
        '{"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}',
        "Explicit human choices + agent decisions in the window, with the "
        "question asked and the answer picked.",
    ),
    (
        "compile_context",
        # The view union is INTERPOLATED from the registry for the same reason
        # the kind union above is: a view missing from this signature is a view
        # the planner is instructed never to select.
        '{"query": str, "views": [' + "|".join(VIEWS) + '], "depth": int<=3}',
        "Compiles a cited context bundle by WALKING the graph from seeds "
        "matched to query — relationships between things, not page text. "
        "views restricts which edge classes the walk may traverse; several "
        "views run one walk each over the same seeds and fuse the rankings. "
        "Best for 'how does X relate to Y', 'why did X change', 'what depends "
        "on X'. It has NO dates and no as-of pivot — pair it with timeline / "
        "activity_summary for anything time-bounded. It ranks and selects; it "
        "does not verify a specific claim (use search_facts). A view NARROWS "
        "what is reachable: omit views when the question does not clearly "
        "name one shape.",
    ),
]

_PLANNER_SYSTEM = (
    "You are the retrieval planner for a project knowledge graph (Tesserae). "
    "Given a user question, reason about what KIND of information would answer "
    "it, then emit a small retrieval plan using ONLY the primitives below.\n\n"
    + "\n".join(f"- {name} args={sig}\n  {desc}" for name, sig, desc in _CATALOG)
    + "\n\nRules:\n"
    f"- At most {MAX_STEPS} steps; prefer 2-3 complementary primitives.\n"
    "- Temporal questions ('recently', 'lately', 'what changed', 'last week') "
    "need dated primitives (timeline / recent_sessions / activity_summary), "
    "never wiki_search alone.\n"
    "- Conceptual questions ('what is', 'how does') want wiki_search, "
    "optionally search_facts for verification.\n"
    "- Relational questions ('how does X relate to Y', 'why did X change', "
    "'what depends on X', 'what led to X', 'what broke and what fixed it') "
    "want compile_context — it walks the graph where wiki_search only reads "
    "page text. It ADDS to the rules above, never replaces them: a 'recently' "
    "question still needs a dated primitive, and compile_context has no date "
    "filter of its own.\n"
    "- Choose views by the SHAPE of the question, most specific first: causal "
    "for breakage and repair ('why did it break', 'what caused', 'what fixed', "
    "'regression', 'recovered'); temporal for order and replacement "
    "('before/after', 'what superseded', 'what replaced'); entity for named "
    "things and code structure ('who', 'which file/repo/author', 'what calls/"
    "contains/implements'); semantic for ideas ('how does X relate to Y', "
    "'improves on', 'similar to'). Two views only when the question genuinely "
    "has two halves ('why did retrieval regress and who fixed it' -> causal + "
    "entity). No clear match: omit views and walk the whole graph — never "
    "guess a view.\n"
    "- Compute concrete ISO dates from TODAY when a primitive takes since/until.\n"
    "- You may PROPOSE a graph write, never perform one. When the QUESTION "
    "itself asserts a durable fact worth recording, add an optional "
    '"proposed_write" object: {"nodes": [{"name", "type", "description"}], '
    '"edges": [{"source", "target", "type", "evidence"}], "rationale": str}. '
    "Ground it ONLY in what the question states — never in what a retrieval "
    "step might return. It is returned to the agent as a suggestion to submit "
    "explicitly; omit the key entirely when nothing is worth recording.\n"
    'Respond with JSON only: {"reasoning": "<one sentence>", '
    '"steps": [{"action": "<name>", "args": {...}}]}'
)


class _ExecContext:
    """Lazy per-question handles: graph, temporal facts, registry alias."""

    def __init__(self, wiki: Any) -> None:
        self.wiki = wiki
        self._graph: Any = None
        self._facts: Any = None
        self._alias: Any = False  # False = unresolved, None = not registered
        #: node_id -> display name for nodes a graph walk cited. Carried here
        #: rather than minted as synthetic ``QueryHit``s: ``hits`` is a
        #: documented envelope field consumed as wiki results (href, score,
        #: page_text) and feeding the caller-side LRU bump, so fabricating
        #: rows there would put scoreless, hrefless entries in a public shape
        #: AND turn a read into a disk side effect.
        self.citation_names: Dict[str, str] = {}
        #: What each executed step actually did — the honesty half of the
        #: split. Never merged into the validated plan ``steps``: those are
        #: the REQUEST, this is the outcome.
        self.executed: List[Dict[str, Any]] = []

    def graph(self) -> Any:
        if self._graph is None:
            from .project import load_graph_file

            self._graph = load_graph_file(self.wiki.paths.graph)
        return self._graph

    def facts(self) -> Any:
        if self._facts is None:
            from .temporal import TemporalFactProjector

            self._facts = TemporalFactProjector().project(self.graph())
        return self._facts

    def alias(self) -> Optional[str]:
        if self._alias is False:
            from .mcp_server import ProjectRegistry

            self._alias = ProjectRegistry().alias_for_root(self.wiki.project_root)
        return self._alias


def _clip(text: str) -> str:
    return text[:_EVIDENCE_CLIP]


def _as_int(value: Any, default: int, cap: int) -> int:
    try:
        return max(1, min(int(value), cap))
    except (TypeError, ValueError):
        return default


_FINDING_TYPES = {kind: t.value for kind, t in SESSION_FINDING_KIND_TO_TYPE.items()}


def _node_ts(node: Any) -> str:
    md = node.metadata or {}
    return str(md.get("started_at") or md.get("created_at") or md.get("ts") or "")


def _execute_step(action: str, args: Dict[str, Any], ctx: _ExecContext, top_k: int) -> Tuple[str, List[Any]]:
    """Run one plan step. Returns (evidence text, wiki hits if any)."""
    if action == "wiki_search":
        from .query import WikiQuery

        wq = WikiQuery(ctx.wiki.project_root, top_k=_as_int(args.get("top_k"), top_k, 8))
        hits = wq.search(str(args.get("query") or ""))
        lines = [f"- [{h.kind}] {h.title}: {h.excerpt}" for h in hits]
        return _clip("\n".join(lines) or "(no wiki matches)"), hits

    if action == "compile_context":
        from .context_compiler import compile_context

        query = str(args.get("query") or "").strip()
        if not query:
            return "(compile_context needs a query — no seeds to walk from)", []
        # Unknown view names DEGRADE to the full graph, never raise: an
        # invented name would otherwise reach ``weights_for``'s fail-fast
        # ValueError, land in the per-step catch, and cost the whole step its
        # evidence. The superset can only be too broad; a wrong view silently
        # zeroes out entire edge classes.
        raw_views = args.get("views")
        if isinstance(raw_views, str):
            raw_views = [raw_views]
        requested = [str(v) for v in raw_views] if isinstance(raw_views, list) else []
        views: List[str] = []
        dropped: List[str] = []
        for name in requested:
            if name in VIEWS:
                if name not in views:
                    views.append(name)
            elif name not in dropped:
                dropped.append(name)
        # Deliberately NOT passed: recency_now/recency_weight (wall clock in a
        # pure function), synthesize (a second LLM call inside a retrieval
        # step), scope/strategy/tame_hubs (sidecar-backed, and the --agent
        # path hands us a temp graph beside the real project root). Same graph
        # + same effective args => byte-identical bundle body.
        bundle = compile_context(
            ctx.graph(),
            str(ctx.wiki.project_root),
            query=query,
            depth=_as_int(args.get("depth"), 2, 3),
            budget=_CONTEXT_BUDGET,
            view=views or None,
        )
        ctx.citation_names.update(
            {c.node_id: c.node_name for c in bundle.citations if c.node_id}
        )
        reached = sorted({v for c in bundle.citations for v in (c.via_views or ())})
        ctx.executed.append(
            {
                "views": list(views),
                "views_reached": reached,
                "views_dropped": list(dropped),
                "depth": _as_int(args.get("depth"), 2, 3),
                "citations": len(bundle.citations),
            }
        )
        header = (
            f"(views applied: {', '.join(views) or 'none — full graph'}; "
            f"reached: {', '.join(reached) or 'none'})\n"
        )
        return _clip(header + bundle.body), []

    if action == "timeline":
        from .temporal import timeline

        result = timeline(ctx.facts(), query=str(args.get("query") or ""), limit=_as_int(args.get("limit"), 50, 50))
        since = str(args.get("since") or "")
        events = [
            e for e in result["events"]
            if not since or str(e.get("valid_from") or "") >= since
        ]
        lines = [
            f"- {e.get('valid_from') or '(undated)'} {e.get('subject_name')} "
            f"--{e.get('predicate')}--> {e.get('object_name')}"
            + (f" ({e.get('evidence')})" if e.get("evidence") else "")
            for e in events
        ]
        return _clip("\n".join(lines) or "(no timeline events in range)"), []

    if action == "search_facts":
        from .temporal import search_facts

        result = search_facts(ctx.facts(), query=str(args.get("query") or ""), limit=_as_int(args.get("limit"), 10, 20))
        return _clip(json.dumps(result["facts"], ensure_ascii=False, default=str)), []

    if action == "recent_sessions":
        since = str(args.get("since") or "")
        sessions = [n for n in ctx.graph().nodes if n.type.value == "Session"]
        if since:
            sessions = [s for s in sessions if _node_ts(s) >= since]
        sessions.sort(key=_node_ts, reverse=True)
        lines = [
            f"- {_node_ts(s) or '(undated)'} {s.name}: {s.description or (s.metadata or {}).get('summary') or ''}"
            for s in sessions[: _as_int(args.get("limit"), 10, 20)]
        ]
        return _clip("\n".join(lines) or "(no sessions in range)"), []

    if action == "session_findings":
        kind = str(args.get("kind") or "").lower()
        wanted = {_FINDING_TYPES[kind]} if kind in _FINDING_TYPES else set(_FINDING_TYPES.values())
        nodes = [n for n in ctx.graph().nodes if n.type.value in wanted]
        nodes.sort(key=_node_ts, reverse=True)
        lines = [
            f"- [{n.type.value}] {_node_ts(n) or '(undated)'} {n.name}: {n.description}"
            for n in nodes[: _as_int(args.get("limit"), 10, 20)]
        ]
        return _clip("\n".join(lines) or "(no findings)"), []

    if action == "activity_summary":
        alias = ctx.alias()
        if alias is None:
            return "(project not registered — activity_summary unavailable; rely on the other steps)", []
        from .activity_summary import build_summary, resolve_windows

        windows = resolve_windows(since=str(args.get("since") or "") or None, until=str(args.get("until") or "") or None)
        result = build_summary(windows, [alias], synthesize=False, write=False)
        return _clip(result.markdown), []

    if action == "decisions":
        alias = ctx.alias()
        from .activity_summary import resolve_windows
        from .decisions import gather_decisions

        windows = resolve_windows(since=str(args.get("since") or "") or None, until=str(args.get("until") or "") or None)
        found = gather_decisions(windows, [alias] if alias else None, include_agent=False)
        lines = [f"- {d.ts.isoformat()} [{d.source}] {d.question} -> {d.answer}" for d in found]
        return _clip("\n".join(lines) or "(no decisions in window)"), []

    raise ValueError(f"unknown action {action!r}")


def _validated_steps(raw: Any) -> List[Dict[str, Any]]:
    known = {name for name, _sig, _desc in _CATALOG}
    steps: List[Dict[str, Any]] = []
    raw_steps = raw.get("steps") if isinstance(raw, dict) else None
    for step in raw_steps if isinstance(raw_steps, list) else []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "")
        if action not in known:
            continue
        args = step.get("args")
        steps.append({"action": action, "args": args if isinstance(args, dict) else {}})
        if len(steps) >= MAX_STEPS:
            break
    return steps


def _validated_proposal(raw: Any) -> Optional[Dict[str, Any]]:
    """The planner's optional ``proposed_write``, validated into a payload the
    agent may submit — or ``None``.

    PROPOSE, NEVER EXECUTE. Nothing here writes: the returned object is data
    the caller hands to ``graph_write`` itself, which re-validates it in full.
    Three properties make that a guarantee rather than a promise:

    * ``provenance`` is ALWAYS ``None``. ``agent_write`` refuses a write whose
      provenance lacks ``agent`` or every external anchor, so a proposal is
      structurally unsubmittable until a caller that HAS an agent key and an
      outside anchor supplies one. The planner has neither — it answered a
      question; it touched no url, file, commit or session.
    * Total, like :func:`_validated_steps`: it never raises. ``plan_and_answer``
      swallows every exception and falls back to BM25, so a raise here would
      silently downgrade EVERY ask — catastrophic and green.
    * It DROPS what it cannot verify and repairs nothing, mirroring the module
      it feeds ("refuses instead of coercing"). Producer-owned node types are
      deliberately not re-checked here: ``graph_write`` owns that deny set, and
      a second copy would drift.
    """
    if not isinstance(raw, dict):
        return None
    proposal = raw.get("proposed_write")
    if not isinstance(proposal, dict):
        return None

    nodes: List[Dict[str, Any]] = []
    names: set = set()
    for item in proposal.get("nodes") or []:
        if not isinstance(item, dict) or len(nodes) >= _MAX_PROPOSED:
            continue
        name = str(item.get("name") or "").strip()
        type_name = str(item.get("type") or "").strip()
        if not name or type_name not in ALLOWED_NODE_TYPES:
            continue
        nodes.append(
            {
                "name": name,
                "type": type_name,
                "description": str(item.get("description") or ""),
            }
        )
        names.add(name)

    edges: List[Dict[str, Any]] = []
    for item in proposal.get("edges") or []:
        if not isinstance(item, dict) or len(edges) >= _MAX_PROPOSED:
            continue
        edge_type = str(item.get("type") or "").strip()
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if edge_type not in ALLOWED_EDGE_TYPES or not evidence:
            continue
        # An endpoint must name a proposed node or look like a node id
        # ("Type:..."). Deliberately a shape check, not an existence check:
        # this function has no graph, and ``graph_write`` re-resolves every
        # endpoint against the real one on submission (refusing what it cannot
        # find). A stricter guess here would only drop legitimate ids —
        # hand-minted ones carry a single colon, ``stable_id`` ones two.
        if not all(e in names or ":" in e for e in (source, target)):
            continue
        edges.append(
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "evidence": evidence,
            }
        )

    if not nodes and not edges:
        return None
    return {
        "tool": "graph_write",
        "nodes": nodes,
        "edges": edges,
        # Never filled in by the planner — see the docstring.
        "provenance": None,
        "provenance_required": ["agent", "one of: url | file | commit | session_id"],
        "rationale": str(proposal.get("rationale") or ""),
        "status": "unsubmitted",
    }


def _build_synthesis_message(question: str, evidence: List[Dict[str, Any]], hits: List[Any]) -> str:
    from .query import _strip_frontmatter  # noqa: PLC0415 — avoid import cycle at module load

    parts = [
        "Answer the following question strictly from the supplied sources. "
        "Cite every factual claim with [<node_id>] using the node_id attribute "
        "on each <source>. Sources with kind starting 'kg:' are live knowledge-"
        "graph query results (dated evidence); prefer them for temporal claims.",
        "",
        f"QUESTION: {question.strip()}",
        "",
    ]
    for i, ev in enumerate(evidence, start=1):
        node_id = f"kg-step-{i}-{ev['action']}"
        args_repr = json.dumps(ev["args"], ensure_ascii=False)
        parts.append(f'<source kind="kg:{ev["action"]}" title="{ev["action"]} {args_repr}" node_id="{node_id}">')
        parts.append(ev["content"])
        parts.append("</source>")
        parts.append("")
    for hit in hits:
        body = ""
        if hit.page_text:
            body = _strip_frontmatter(hit.page_text).strip()
        body = (body or hit.excerpt)[:1000]
        parts.append(f'<source kind="{hit.kind}" title="{hit.title}" node_id="{hit.node_id or ""}">')
        parts.append(body)
        parts.append("</source>")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def plan_and_answer(
    wiki: Any,
    question: str,
    *,
    top_k: int = 5,
    history: Optional[List[Dict[str, Any]]] = None,
    client: Any = None,
) -> Optional[Dict[str, Any]]:
    """Full plan→execute→synthesize pass. Returns an ``ask_project``-shaped
    envelope, or None when no LLM backend is usable / planning fails — the
    caller then falls back to the classic BM25 path."""

    try:
        return _plan_and_answer(wiki, question, top_k=top_k, history=history, client=client)
    except Exception as exc:  # noqa: BLE001 — planner bugs must never sink `ask`
        print(f"(ask planner error: {type(exc).__name__}: {exc} — falling back to wiki search)", file=sys.stderr)
        return None


def _plan_and_answer(
    wiki: Any,
    question: str,
    *,
    top_k: int,
    history: Optional[List[Dict[str, Any]]],
    client: Any,
) -> Optional[Dict[str, Any]]:
    if client is None:
        from .llm_json import build_rotating_client

        client = build_rotating_client()
    if client is None:
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    user = f"TODAY: {today}\nQUESTION: {question.strip()}"
    if history:
        prior = "\n".join(
            f"{t.get('role')}: {str(t.get('content'))[:300]}"
            for t in history[-4:]
            if t.get("role") in {"user", "assistant"}
        )
        if prior:
            user = f"Earlier turns:\n{prior}\n\n{user}"

    try:
        raw_plan = client.complete_json(system=_PLANNER_SYSTEM, user=user, schema_name="ask_retrieval_plan")
    except Exception as exc:  # noqa: BLE001 — fallback path is the safety net
        print(f"(ask planner failed: {type(exc).__name__} — falling back to wiki search)", file=sys.stderr)
        return None
    steps = _validated_steps(raw_plan)
    if not steps:
        return None
    reasoning = str(raw_plan.get("reasoning") or "") if isinstance(raw_plan, dict) else ""

    ctx = _ExecContext(wiki)
    evidence: List[Dict[str, Any]] = []
    hits: List[Any] = []
    executed: List[Dict[str, Any]] = []
    for step in steps:
        _before = len(ctx.executed)
        ok = True
        try:
            content, step_hits = _execute_step(step["action"], step["args"], ctx, top_k)
        except Exception as exc:  # noqa: BLE001 — a broken step must not sink the plan
            content, step_hits = f"(step failed: {type(exc).__name__}: {exc})", []
            ok = False
        evidence.append({"action": step["action"], "args": step["args"], "content": content})
        hits.extend(step_hits)
        # Index-aligned with ``steps``: what the model ASKED for stays in
        # steps, what actually ran is reported here — the same split
        # search_nodes uses for `mode` and compile_context for `knobs`.
        entry: Dict[str, Any] = {"action": step["action"], "ok": ok}
        if len(ctx.executed) > _before:
            entry.update(ctx.executed[-1])
        executed.append(entry)

    from .query import WikiQuery

    wq = WikiQuery(wiki.project_root, top_k=top_k)
    system_text = "\n\n".join(
        str(b.get("text", "")) for b in wq._system_blocks() if isinstance(b, dict) and b.get("text")
    )
    message = _build_synthesis_message(question, evidence, hits)
    if history:
        prior = "\n\n".join(
            f"{t.get('role')}: {t.get('content')}"
            for t in history
            if t.get("role") in {"user", "assistant"} and t.get("content")
        )
        if prior:
            message = f"Earlier in this conversation:\n{prior}\n\n{message}"

    try:
        body = client.complete_text(system=system_text, user=message)
    except Exception as exc:  # noqa: BLE001
        print(f"(ask synthesis failed: {type(exc).__name__} — falling back to wiki search)", file=sys.stderr)
        return None
    if not body or not body.strip():
        return None
    if not NODE_CITATION_RE.search(body):
        return None  # ungrounded prose — let the classic path report honestly

    id_to_name: Dict[str, str] = {h.node_id: h.title for h in hits if h.node_id and h.title}
    id_to_name.update(ctx.citation_names)
    for i, ev in enumerate(evidence, start=1):
        id_to_name[f"kg-step-{i}-{ev['action']}"] = ev["action"].replace("_", " ")
    body = rewrite_citations(body, id_to_name)

    envelope: Dict[str, Any] = {
        "hits": [h.to_dict() for h in hits],
        "answer": body.strip() + "\n",
        "model": "cli-oauth",
        "used_llm": True,
        "fallback_reason": None,
        "plan": {"reasoning": reasoning, "steps": steps, "executed": executed},
    }
    proposal = _validated_proposal(raw_plan)
    if proposal is not None:
        # A SUGGESTION, sitting beside the answer. Nothing has been written:
        # the caller submits it to graph_write itself, with provenance it
        # supplies — a mutation is never a side effect of a query.
        envelope["proposed_write"] = proposal
    return envelope
