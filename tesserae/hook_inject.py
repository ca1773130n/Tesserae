"""Prompt-time context injection for Claude Code hooks (roadmap R8). OPT-IN.

Tesserae's knowledge reaches an agent only when the agent decides to call an
MCP tool. This module is the push half: ``hooks/prompt-inject.sh`` pipes the
``UserPromptSubmit`` / ``PreToolUse`` (Read, Grep, Edit) hook payload in, and
it answers with the project's active decisions, gotchas and supersede warnings
that lexically match the prompt or the file being touched, behind a one-line
cost header, as ``hookSpecificOutput.additionalContext``.

Contract, every clause of which is load-bearing:

* **Lexical only.** BM25 (the retrieval lane's own ``_tokenize`` /
  ``_node_text`` / ``_bm25_scores``) over candidates read from the
  ``.tesserae/sqlite.db`` mirror. No LLM, no embedding backend, no network.
  ``graph.json`` is NOT loaded — at 27 MB, parsing it alone takes ~270 ms.
  The persisted BM25 postings sidecar is not used either: its read-through
  WRITES on a miss, and the candidate pool here is a few hundred nodes, which
  tokenise in memory in milliseconds.
* **Read-only.** The mirror is opened ``mode=ro`` with a 100 ms busy timeout
  (:meth:`SqliteGraphStore.open_readonly`). The only file written is the
  per-session dedupe list under the system temp dir, plus the hook log on
  error.
* **Fail-open.** Any error, including the deadline below, prints nothing and
  exits 0 — a broken memory layer must never block a prompt or a tool call —
  but the error is appended to ``.tesserae/.prompt-inject-hook.log``.
* **Bounded.** ~1.5k tokens (estimated at 4 chars/token) and a wall-clock
  deadline armed with ``SIGALRM``; the shell hook adds a hard 300 ms watchdog
  on top, which also covers interpreter start-up.
* **Never reads transcripts.** ``transcript_path`` in the payload is ignored.
* **No self-capture.** It spawns no LLM, so it writes no transcript of its own.
  Its output lands in the user's transcript as an ``attachment`` row of type
  ``hook_additional_context``, which session import never turns into turn text
  (``harness_sessions._claude_turns`` reads ``user``/``assistant`` rows only;
  pinned by ``tests/test_hook_inject.py``), so injected decisions are not
  re-extracted as new ones.

Benefit is unmeasured: the roadmap's A/B on task success was not run. That is
why it ships off — see ``hooks/prompt-inject.sh`` for the switches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .graph_filters import retracted_ids, superseded_ids
from .graph_stores.sqlite import SqliteGraphStore
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from .retrieval.grounding import STOPWORDS
from .retrieval.hybrid import _bm25_scores, _node_text, _tokenize

__all__ = [
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_TIMEOUT_MS",
    "INJECT_TYPES",
    "InjectItem",
    "add_arguments",
    "file_anchors",
    "main",
    "query_from_event",
    "render",
    "retrieve",
]

DEFAULT_BUDGET_TOKENS = 1500
#: Python-side deadline. The shell hook kills the process at 300 ms; this is
#: lower so that the common slow case ends in a LOGGED fail-open from inside
#: the process rather than an anonymous kill. The 50 ms gap is interpreter
#: start-up plus imports, which run before the deadline can be armed.
DEFAULT_TIMEOUT_MS = 250
#: Rough English-text ratio. Used only to turn the token budget into a
#: character budget; nothing here pretends to be a tokenizer.
CHARS_PER_TOKEN = 4
#: A hit must score at least this fraction of the best hit. Keeps a single
#: strong match from dragging in a tail of weak ones that only share a word.
DEFAULT_RELATIVE_FLOOR = 0.5
#: Distinct informative query terms a hit must contain (capped at the number
#: the query has). One shared word is not relevance.
MIN_MATCHED_TERMS = 2
#: Most items per event. A one-term match over a few hundred findings can
#: clear both floors 25 times; past a handful the block is a list nobody
#: reads, and it still costs its tokens on every prompt. Not tuned.
MAX_ITEMS = 8
#: Longest single item rendered, in characters.
MAX_ITEM_CHARS = 320
#: Long pasted prompts are cut to their first N informative terms before
#: scoring, which bounds the per-document cost.
MAX_QUERY_TERMS = 64
LOG_NAME = ".prompt-inject-hook.log"
TOOL_EVENTS = frozenset({"Read", "Grep", "Edit"})

#: What gets injected, and the label it is injected under. Decisions are what
#: an agent most often re-litigates; failures, takeaways and distilled gotchas
#: are what it most often re-steps on. Takeaways carry ~96% of this repo's
#: ``supersedes`` edges, so without them a supersede warning would almost
#: never fire.
INJECT_TYPES: Dict[ResearchNodeType, str] = {
    ResearchNodeType.SESSION_DECISION: "decision",
    ResearchNodeType.GOTCHA: "gotcha",
    ResearchNodeType.SESSION_FAILURE: "gotcha",
    ResearchNodeType.SESSION_TAKEAWAY: "takeaway",
}


class _Deadline(Exception):
    """Raised by the SIGALRM handler when the wall-clock budget is spent."""


@dataclass(frozen=True)
class InjectItem:
    node_id: str
    label: str
    text: str
    score: float
    #: ``"superseded"`` / ``"retracted"`` for a warning, ``None`` for an active item.
    warning: Optional[str] = None
    #: For a superseded item: the text of the node that replaced it.
    replaced_by: Optional[str] = None


# --------------------------------------------------------------------------- #
# Query                                                                       #
# --------------------------------------------------------------------------- #


def _path_terms(raw: object) -> str:
    """The searchable words of a file path.

    Its name, its stem in the three spellings an identifier is written in
    (``session-end`` / ``session_end`` → also ``session end`` and
    ``sessionend``, which is how ``SessionEnd`` tokenises). Parent
    directories are left out on purpose: ``tesserae/``, ``tests/``,
    ``retrieval/`` name an area, not the file, and as query terms they pulled
    in whatever else that area's sessions said.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    path = Path(raw)
    stem = path.stem
    split = re.sub(r"[-_.]+", " ", stem)
    parts = [path.name, stem, split, split.replace(" ", "")]
    return " ".join(part for part in parts if part)


def _tool_paths(event: Mapping[str, object]) -> List[str]:
    if event.get("hook_event_name") != "PreToolUse" or event.get("tool_name") not in TOOL_EVENTS:
        return []
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return []
    return [v for v in (tool_input.get("file_path"), tool_input.get("path")) if isinstance(v, str) and v.strip()]


def query_from_event(event: Mapping[str, object]) -> str:
    """Query text for one hook payload, or ``""`` when there is nothing to ask.

    ``UserPromptSubmit`` → the prompt. ``PreToolUse`` on Read / Grep / Edit →
    the file path's words, plus a Grep pattern. Any other event or tool → empty.
    ``transcript_path`` is never touched: this hook must not read transcripts.
    """
    if event.get("hook_event_name") == "UserPromptSubmit":
        prompt = event.get("prompt")
        return prompt if isinstance(prompt, str) else ""
    paths = _tool_paths(event)
    tool_input = event.get("tool_input")
    if not paths and not isinstance(tool_input, Mapping):
        return ""
    parts = [_path_terms(path) for path in paths]
    pattern = tool_input.get("pattern") if isinstance(tool_input, Mapping) else None
    if isinstance(pattern, str) and event.get("tool_name") in TOOL_EVENTS:
        parts.append(pattern)
    return " ".join(part for part in parts if part)


def file_anchors(event: Mapping[str, object]) -> List[str]:
    """File names a tool event names, casefolded, for exact-mention matching.

    A node that spells out the very file being opened is relevant however few
    query terms it shares, so these bypass the relevance floor in
    :func:`retrieve`. Names shorter than 4 characters are dropped: ``a.py``
    would match half the corpus by substring.
    """
    names = [Path(path).name.casefold() for path in _tool_paths(event)]
    return [name for name in dict.fromkeys(names) if len(name) >= 4]


def _informative_terms(query: str) -> List[str]:
    """Distinct query tokens worth matching on, in first-seen order."""
    out: List[str] = []
    for token in _tokenize(query):
        if len(token) < 3 or token in STOPWORDS or token.isdigit() or token in out:
            continue
        out.append(token)
        if len(out) >= MAX_QUERY_TERMS:
            break
    return out


# --------------------------------------------------------------------------- #
# Retrieval                                                                   #
# --------------------------------------------------------------------------- #


def open_mirror(project_root: Path) -> SqliteGraphStore:
    """The project's SQLite mirror, read-only, or raise.

    Same staleness rule as the MCP keyed read (``_keyed_store``): a
    ``graph.json`` NEWER than ``sqlite.db`` means the two did not come from the
    same compile, and the mirror may serve knowledge the graph has since
    dropped. Refuse rather than inject it.
    """
    tdir = project_root / ".tesserae"
    graph_path = tdir / "graph.json"
    db_path = tdir / "sqlite.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"no SQLite mirror at {db_path}; run `tesserae compile`")
    if graph_path.is_file() and graph_path.stat().st_mtime > db_path.stat().st_mtime:
        raise RuntimeError(f"{graph_path} is newer than {db_path}; mirror may be stale, recompile")
    return SqliteGraphStore.open_readonly(db_path)


def _candidates(store: SqliteGraphStore) -> Tuple[List[ResearchNode], ResearchGraph]:
    nodes: List[ResearchNode] = []
    for node_type in INJECT_TYPES:
        nodes.extend(store.iterate_nodes(node_type.value))
    edges = store.read_suppression_edges(node.id for node in nodes)
    return nodes, ResearchGraph(nodes=nodes, edges=edges)


def _replacement(node_id: str, graph: ResearchGraph, by_id: Mapping[str, ResearchNode]) -> Optional[str]:
    """Text of the newest node in ``node_id``'s supersede chain, if known."""
    winners = {edge.target: edge.source for edge in graph.edges if edge.type == "supersedes"}
    current, seen = node_id, {node_id}
    while current in winners and winners[current] not in seen:
        current = winners[current]
        seen.add(current)
    if current == node_id or current not in by_id:
        return None
    return by_id[current].name


def retrieve(
    store: SqliteGraphStore,
    query: str,
    *,
    seen: Optional[Set[str]] = None,
    anchors: Sequence[str] = (),
    strict: bool = False,
    relative_floor: float = DEFAULT_RELATIVE_FLOOR,
    max_items: int = MAX_ITEMS,
) -> List[InjectItem]:
    """Ranked, floor-filtered items for ``query``, best first, excluding ``seen``.

    Scored over the WHOLE candidate pool (active and suppressed alike) so a
    superseded decision that matches is reported as a warning pointing at its
    replacement, instead of silently vanishing. A node whose text contains one
    of ``anchors`` (see :func:`file_anchors`) passes both floors.

    ``strict`` (tool events) always demands :data:`MIN_MATCHED_TERMS` matched
    terms, even from a one-word query: ``cli.py`` reduces to the single term
    ``cli``, and one shared word is exactly what the floor exists to refuse.
    Such a file is then served only by nodes that name it (``anchors``).
    """
    seen = seen or set()
    terms = _informative_terms(query)
    if not terms:
        return []
    nodes, graph = _candidates(store)
    if not nodes:
        return []
    texts = [_node_text(node) for node in nodes]
    corpus = [_tokenize(text) for text in texts]
    n_docs = len(corpus)
    doc_sets = [set(doc) for doc in corpus]
    anchored = {
        idx for idx, text in enumerate(texts)
        if anchors and any(anchor in text.casefold() for anchor in anchors)
    }
    df = {term: sum(1 for doc in doc_sets if term in doc) for term in terms}
    # A term in half the pool or more has non-positive classical (Robertson)
    # IDF: it cannot tell candidates apart, so it does not count toward the
    # matched-terms floor. It still contributes to the BM25 score. Terms
    # absent from the pool DO count toward how many must match: a query of
    # four words of which the corpus knows one is not a one-word query.
    discriminative = [t for t in terms if df[t] < n_docs / 2]
    if not discriminative and not anchored:
        return []
    need = MIN_MATCHED_TERMS if strict else min(MIN_MATCHED_TERMS, len(discriminative))
    scores = _bm25_scores(terms, corpus)

    def passes(idx: int) -> bool:
        if idx in anchored:
            return True
        return need > 0 and sum(1 for t in discriminative if t in doc_sets[idx]) >= need

    ranked = sorted(
        ((score, idx) for idx, score in enumerate(scores) if (score > 0 or idx in anchored) and passes(idx)),
        key=lambda pair: (-(pair[1] in anchored), -pair[0], nodes[pair[1]].id),
    )
    if not ranked:
        return []
    unanchored = [score for score, idx in ranked if idx not in anchored]
    top = unanchored[0] if unanchored else 0.0
    superseded = superseded_ids(graph)
    retracted = retracted_ids(graph)
    by_id = {node.id: node for node in nodes}

    items: List[InjectItem] = []
    for score, idx in ranked:
        if len(items) >= max_items or (idx not in anchored and score < relative_floor * top):
            break
        node = nodes[idx]
        if node.id in seen:
            continue
        label = INJECT_TYPES[node.type]
        if node.id in retracted:
            items.append(InjectItem(node.id, label, node.name, score, warning="retracted"))
        elif node.id in superseded:
            items.append(
                InjectItem(node.id, label, node.name, score, warning="superseded",
                           replaced_by=_replacement(node.id, graph, by_id))
            )
        else:
            items.append(InjectItem(node.id, label, node.name, score))
    return items


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


def _clip(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _line(item: InjectItem) -> str:
    if item.warning == "superseded":
        tail = f" → now: {_clip(item.replaced_by)}" if item.replaced_by else ""
        return f"- ⚠ superseded {item.label}: {_clip(item.text)}{tail} [{item.node_id}]"
    if item.warning == "retracted":
        return f"- ⚠ retracted {item.label}: {_clip(item.text)} [{item.node_id}]"
    return f"- {item.label}: {_clip(item.text)} [{item.node_id}]"


def render(items: Sequence[InjectItem], *, budget_tokens: int, elapsed_ms: float) -> Tuple[str, List[str]]:
    """``(text, emitted_ids)``. Empty text when nothing fits or nothing matched.

    Warnings go first: being told a decision was replaced is worth more than a
    fourth active one. Items are added whole, in rank order, until the next
    would overrun the budget — never cut mid-line.
    """
    budget_chars = max(0, budget_tokens) * CHARS_PER_TOKEN
    ordered = [i for i in items if i.warning] + [i for i in items if not i.warning]
    lines: List[str] = []
    emitted: List[str] = []
    used = 0
    for item in ordered:
        line = _line(item)
        if used + len(line) + 1 > budget_chars:
            continue
        lines.append(line)
        emitted.append(item.node_id)
        used += len(line) + 1
    if not lines:
        return "", []
    body = "\n".join(lines)
    header = (
        f"[tesserae inject · {len(lines)} item{'s' if len(lines) != 1 else ''} · "
        f"~{(len(body) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN} tokens · "
        f"{elapsed_ms:.0f} ms · lexical BM25, no LLM · node_context <id> for detail]"
    )
    return f"{header}\n<tesserae-context>\n{body}\n</tesserae-context>", emitted


# --------------------------------------------------------------------------- #
# Per-session dedupe + logging                                                #
# --------------------------------------------------------------------------- #


def _seen_path(state_dir: Path, project_root: Path, session_id: str) -> Path:
    key = hashlib.sha256(f"{project_root}\x00{session_id}".encode("utf-8")).hexdigest()[:24]
    return state_dir / f"{key}.seen"


def _read_seen(path: Path) -> Set[str]:
    if not path.is_file():
        return set()
    return {line for line in path.read_text(encoding="utf-8").splitlines() if line}


def _append_seen(path: Path, ids: Sequence[str]) -> None:
    if not ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(f"{node_id}\n" for node_id in ids))


def _log(project_root: Optional[Path], message: str, log_file: Optional[Path] = None) -> None:
    """Append one line to the hook log. Never raises: it runs inside the fail-open path.

    Without a ``.tesserae/`` dir and no explicit ``log_file`` the line goes to
    stderr, which the shell hook appends to the same log.
    """
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {message}\n"
    target = log_file
    if target is None and project_root is not None and (project_root / ".tesserae").is_dir():
        target = project_root / ".tesserae" / LOG_NAME
    try:
        if target is None:
            raise OSError("no hook log location")
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        sys.stderr.write(line)


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #


def _on_alarm(signum, frame):  # pragma: no cover - exercised through main()
    raise _Deadline()


def run(
    event: Mapping[str, object],
    project_root: Path,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    state_dir: Optional[Path] = None,
    started: Optional[float] = None,
) -> str:
    """The JSON line to print for ``event``, or ``""``. Raises on error."""
    t0 = time.perf_counter() if started is None else started
    query = query_from_event(event)
    if not query.strip():
        return ""
    session_id = event.get("session_id")
    seen_file = None
    if isinstance(session_id, str) and session_id:
        seen_file = _seen_path(state_dir or Path(tempfile.gettempdir()) / "tesserae-hook-inject",
                               project_root.resolve(), session_id)
    seen = _read_seen(seen_file) if seen_file else set()
    items = retrieve(open_mirror(project_root), query, seen=seen, anchors=file_anchors(event),
                     strict=event.get("hook_event_name") == "PreToolUse")
    text, emitted = render(items, budget_tokens=budget_tokens,
                           elapsed_ms=(time.perf_counter() - t0) * 1000)
    if not text:
        return ""
    if seen_file:
        _append_seen(seen_file, emitted)
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": str(event.get("hook_event_name")),
                                "additionalContext": text}},
        ensure_ascii=False,
    )


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--project", default=".", help="Project root holding .tesserae/ (default: cwd)")
    parser.add_argument("--budget-tokens", type=int, default=DEFAULT_BUDGET_TOKENS,
                        help=f"Injected-context budget, ~{CHARS_PER_TOKEN} chars/token (default {DEFAULT_BUDGET_TOKENS})")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help=f"Wall-clock deadline; on expiry print nothing and exit 0 (default {DEFAULT_TIMEOUT_MS})")
    parser.add_argument("--state-dir", default=None,
                        help="Per-session dedupe dir (default: <tmp>/tesserae-hook-inject)")
    parser.add_argument("--log-file", default=None,
                        help=f"Error log (default: <project>/.tesserae/{LOG_NAME})")
    return parser


def run_args(args: argparse.Namespace, stdin=None) -> int:
    """Fail-open driver shared by ``python -m`` and ``tesserae hook inject``.

    Always returns 0. Prints the hook JSON on success, nothing otherwise.
    """
    t0 = time.perf_counter()
    project_root = Path(args.project)
    log_file = Path(args.log_file) if args.log_file else None
    armed = hasattr(signal, "setitimer") and args.timeout_ms > 0
    event_name = "?"
    try:
        if armed:
            signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, args.timeout_ms / 1000.0)
        raw = (stdin or sys.stdin).read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            raise ValueError(f"hook payload is {type(event).__name__}, expected an object")
        event_name = str(event.get("hook_event_name") or "?")
        out = run(event, project_root, budget_tokens=args.budget_tokens,
                  state_dir=Path(args.state_dir) if args.state_dir else None, started=t0)
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if out:
            sys.stdout.write(out + "\n")
    except _Deadline:
        _log(project_root, f"{event_name}: timeout after {args.timeout_ms} ms; injected nothing", log_file)
    except Exception as exc:  # fail-open by contract; logged, never raised
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        _log(project_root, f"{event_name}: {type(exc).__name__}: {exc}; injected nothing", log_file)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = add_arguments(argparse.ArgumentParser(
        prog="python -m tesserae.hook_inject",
        description="Prompt-time context for Claude Code hooks: reads the hook JSON on stdin.",
    ))
    return run_args(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
