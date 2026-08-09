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

MAX_STEPS = 5
_EVIDENCE_CLIP = 2500  # chars per evidence block fed to synthesis

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
        '{"kind": "insight|decision|question|todo|hypothesis|takeaway", "limit": int<=20}',
        "Findings extracted from sessions, newest first. Omit kind for all "
        "kinds. Best for 'what did we learn/decide' style questions.",
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
    "- Compute concrete ISO dates from TODAY when a primitive takes since/until.\n"
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


_FINDING_TYPES = {
    "insight": "SessionInsight",
    "decision": "SessionDecision",
    "question": "SessionQuestion",
    "todo": "SessionTODO",
    "hypothesis": "SessionHypothesis",
    "takeaway": "SessionTakeaway",
    "failure": "SessionFailure",
}


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
    for step in steps:
        try:
            content, step_hits = _execute_step(step["action"], step["args"], ctx, top_k)
        except Exception as exc:  # noqa: BLE001 — a broken step must not sink the plan
            content, step_hits = f"(step failed: {type(exc).__name__}: {exc})", []
        evidence.append({"action": step["action"], "args": step["args"], "content": content})
        hits.extend(step_hits)

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
    for i, ev in enumerate(evidence, start=1):
        id_to_name[f"kg-step-{i}-{ev['action']}"] = ev["action"].replace("_", " ")
    body = rewrite_citations(body, id_to_name)

    return {
        "hits": [h.to_dict() for h in hits],
        "answer": body.strip() + "\n",
        "model": "cli-oauth",
        "used_llm": True,
        "fallback_reason": None,
        "plan": {"reasoning": reasoning, "steps": steps},
    }
