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
  (:meth:`SqliteGraphStore.open_readonly`). The only files written are under
  the system temp dir — the per-session dedupe list and a JSON cache of the
  tokenised candidate pool keyed on ``graph.json`` (see :func:`load_pool`) —
  plus the hook log on error.
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
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .graph_filters import retracted_ids, superseded_ids
from .research_graph import ResearchEdge, ResearchGraph, ResearchNodeType
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
#: Python-side deadline, armed only once imports are done, so it bounds the
#: work (a SQLite busy wait, a huge prompt) and turns that into a LOGGED
#: fail-open. The hard cap is the shell hook's 300 ms watchdog, which also
#: covers interpreter start-up and imports — on a loaded machine those alone
#: can pass 50 ms, so a slow start ends in the watchdog's rc=143 log line.
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
#: Bump when the cached pool's shape or the text it is built from changes.
POOL_CACHE_VERSION = 1
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


def _mirror_paths(project_root: Path) -> Tuple[Path, Path]:
    """``(graph.json, sqlite.db)``, or raise when the mirror cannot be trusted.

    Same staleness rule as the MCP keyed read (``_keyed_store``): a
    ``graph.json`` NEWER than ``sqlite.db`` means the two did not come from the
    same compile, and the mirror may serve knowledge the graph has since
    dropped. Refuse rather than inject it. Checked on every call, cache or not.
    """
    tdir = project_root / ".tesserae"
    graph_path = tdir / "graph.json"
    db_path = tdir / "sqlite.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"no SQLite mirror at {db_path}; run `tesserae compile`")
    if not graph_path.is_file():
        raise FileNotFoundError(f"no graph at {graph_path}; run `tesserae compile`")
    if graph_path.stat().st_mtime > db_path.stat().st_mtime:
        raise RuntimeError(f"{graph_path} is newer than {db_path}; mirror may be stale, recompile")
    return graph_path, db_path


@dataclass
class _Pool:
    """The candidate corpus, pre-tokenised, in mirror order.

    Parallel lists rather than nodes: this is what gets cached, and a cache hit
    must not pay for rebuilding ResearchNode objects it never needed.
    """

    ids: List[str]
    labels: List[str]
    names: List[str]
    tokens: List[List[str]]
    #: Casefolded ``_node_text``, for exact file-name mentions.
    folded: List[str]
    #: Suppression edges touching the pool: ``[source, target, type]``.
    edges: List[List[str]]


def _build_pool(db_path: Path) -> _Pool:
    # Imported here, not at module scope: a cache hit never opens the store,
    # and on a loaded machine the import is a measurable share of the budget.
    from .graph_stores.sqlite import SqliteGraphStore

    store = SqliteGraphStore.open_readonly(db_path)
    nodes = []
    for node_type in INJECT_TYPES:
        nodes.extend(store.iterate_nodes(node_type.value))
    edges = store.read_suppression_edges(node.id for node in nodes)
    texts = [_node_text(node) for node in nodes]
    return _Pool(
        ids=[node.id for node in nodes],
        labels=[INJECT_TYPES[node.type] for node in nodes],
        names=[node.name for node in nodes],
        tokens=[_tokenize(text) for text in texts],
        folded=[text.casefold() for text in texts],
        edges=[[edge.source, edge.target, edge.type] for edge in edges],
    )


def load_pool(project_root: Path, cache_dir: Optional[Path] = None) -> _Pool:
    """The candidate pool, from the per-project cache when it is current.

    Reading ~800 findings out of a 1 GB SQLite file and tokenising half a
    megabyte of their text costs 30-110 ms depending on the page cache, and the
    answer only changes when a compile republishes the graph. So the pool is
    cached under ``cache_dir`` keyed on ``graph.json``'s ``(st_mtime_ns,
    st_size)``: a compile rewrites that file by atomic rename and then the
    mirror, and the staleness guard refuses the window in between, so a pool
    can only ever be built from a mirror that matches the graph it is keyed on.
    ``sqlite.db``'s own mtime is no key — MCP reads write to it.

    The cache is JSON, never pickle, because it lives in a temp directory;
    :func:`_state_dir` refuses a directory another user owns. A corrupt or
    unreadable cache is logged and rebuilt.
    """
    graph_path, db_path = _mirror_paths(project_root)
    stat = graph_path.stat()
    key = [POOL_CACHE_VERSION, str(project_root.resolve()), stat.st_mtime_ns, stat.st_size]
    cache_file = None
    if cache_dir is not None:
        digest = hashlib.sha256(key[1].encode("utf-8")).hexdigest()[:24]
        cache_file = cache_dir / f"{digest}.pool.json"
        try:
            payload = json.loads(cache_file.read_bytes())
            if payload.get("key") == key:
                return _Pool(**payload["pool"])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as exc:
            _log(project_root, f"pool cache {cache_file} unreadable ({type(exc).__name__}: {exc}); rebuilding")
    pool = _build_pool(db_path)
    if cache_file is not None:
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "pool": pool.__dict__}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache_file)
    return pool


def _replacement(node_id: str, edges: Sequence[ResearchEdge], by_id: Mapping[str, int], names: Sequence[str]) -> Optional[str]:
    """Text of the newest node in ``node_id``'s supersede chain, if known."""
    winners = {edge.target: edge.source for edge in edges if edge.type == "supersedes"}
    current, seen = node_id, {node_id}
    while current in winners and winners[current] not in seen:
        current = winners[current]
        seen.add(current)
    if current == node_id or current not in by_id:
        return None
    return names[by_id[current]]


def retrieve(
    pool: _Pool,
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
    if not pool.ids:
        return []
    corpus = pool.tokens
    n_docs = len(corpus)
    doc_sets = [set(doc) for doc in corpus]
    anchored = {
        idx for idx, text in enumerate(pool.folded)
        if anchors and any(anchor in text for anchor in anchors)
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
        key=lambda pair: (-(pair[1] in anchored), -pair[0], pool.ids[pair[1]]),
    )
    if not ranked:
        return []
    unanchored = [score for score, idx in ranked if idx not in anchored]
    top = unanchored[0] if unanchored else 0.0
    edges = [ResearchEdge(source=src, target=dst, type=etype) for src, dst, etype in pool.edges]
    graph = ResearchGraph(nodes=[], edges=edges)
    superseded = superseded_ids(graph)
    retracted = retracted_ids(graph)
    by_id = {node_id: idx for idx, node_id in enumerate(pool.ids)}

    items: List[InjectItem] = []
    for score, idx in ranked:
        if len(items) >= max_items or (idx not in anchored and score < relative_floor * top):
            break
        node_id, label, name = pool.ids[idx], pool.labels[idx], pool.names[idx]
        if node_id in seen:
            continue
        if node_id in retracted:
            items.append(InjectItem(node_id, label, name, score, warning="retracted"))
        elif node_id in superseded:
            items.append(
                InjectItem(node_id, label, name, score, warning="superseded",
                           replaced_by=_replacement(node_id, edges, by_id, pool.names))
            )
        else:
            items.append(InjectItem(node_id, label, name, score))
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


def _state_dir(explicit: Optional[Path]) -> Path:
    """Where dedupe lists and the pool cache live; refuse one we do not own.

    On a shared ``/tmp`` another user could create this directory first and
    plant a pool cache, i.e. choose the text injected into this user's agent.
    """
    import tempfile

    path = explicit or Path(tempfile.gettempdir()) / "tesserae-hook-inject"
    if path.exists() and hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise PermissionError(f"{path} is owned by uid {path.stat().st_uid}, not this user; refusing it")
    return path


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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    state_dir = _state_dir(state_dir)
    session_id = event.get("session_id")
    seen_file = None
    if isinstance(session_id, str) and session_id:
        seen_file = _seen_path(state_dir, project_root.resolve(), session_id)
    seen = _read_seen(seen_file) if seen_file else set()
    items = retrieve(load_pool(project_root, state_dir), query, seen=seen, anchors=file_anchors(event),
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
                        help="Per-session dedupe + candidate-pool cache dir (default: <tmp>/tesserae-hook-inject)")
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
