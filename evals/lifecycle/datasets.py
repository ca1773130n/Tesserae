"""Pinned sources and loaders for the three lifecycle stages.

Every file is pinned to a Hugging Face dataset REVISION and its LFS sha256, so
two runs months apart read the same bytes or refuse. Files land in a cache
OUTSIDE the repository (``~/.cache/tesserae/evals/lifecycle`` unless
``TESSERAE_EVAL_CACHE`` says otherwise); a cache path inside the repo is
refused, because a 277MB JSON is exactly what an unrelated ``git add -A``
sweeps up.

Nothing here downloads unless :func:`fetch` is called with
``allow_download=True`` — the runner's ``fetch`` verb is the only caller that
does. A missing file is a :class:`~evals.qa.run_qa_eval.Skip` naming that verb.

What each loader returns, measured on the pinned files (2026-11-05):

* **HaluMem-Medium** — 20 users, 1,387 sessions, 14,948 memory points
  (``secondary`` 10,451 / ``interference`` 2,648 / ``system`` 1,849);
  3,122 carry ``is_update == "True"``. Only 920 of their 3,788
  ``original_memories`` strings equal an earlier memory point verbatim — the
  rest are paraphrases, so the update stage mints the original text as its
  own node rather than assuming it exists.
* **FactConsolidation** (MemoryAgentBench ``Conflict_Resolution``) — 8 records,
  ``factconsolidation_{sh,mh}_{6k,32k,64k,262k}``, 100 questions each. The 6k
  context is 455 numbered facts; :func:`conflict_pairs` finds 161 disjoint
  (newer, older) pairs in it (837 pairs over 2,310 facts at 32k, 7,236 over 18,332 at
  262k). A last-relation-word regex finds a 143-pair subset of those and misses
  objects that contain a relation word ("... educated at University of ...").
* **LongMemEval** (cleaned) — 500 questions; ``knowledge-update`` is 78 of them
  and abstention (``question_id`` ending ``_abs``) is 30, 6 of which are also
  knowledge-update, so the two subsets together are 102 questions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..qa.run_qa_eval import Skip

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Source:
    """One pinned file: where it lives upstream and what its bytes must be."""

    repo: str
    revision: str
    path: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return (
            f"https://huggingface.co/datasets/{self.repo}/resolve/"
            f"{self.revision}/{self.path}"
        )


#: Revisions and LFS oids read from the Hugging Face tree API on 2026-11-05.
SOURCES: Dict[str, Source] = {
    "halumem-medium": Source(
        "IAAR-Shanghai/HaluMem", "cb04336aa1b732d4b24f5186c552456b4099806e",
        "HaluMem-Medium.jsonl", 33_511_525,
        "486fbc130a5c8781a2af27ffa508a1d7855245137aa449c193ac4d29c45634e7",
    ),
    "halumem-long": Source(
        "IAAR-Shanghai/HaluMem", "cb04336aa1b732d4b24f5186c552456b4099806e",
        "HaluMem-Long.jsonl", 106_535_674,
        "dfdbed570b402b7b8c17e0d7808fc6f3ae7a53b6144f18feb16bbdd3f55cb0c9",
    ),
    "factconsolidation": Source(
        "ai-hyz/MemoryAgentBench", "7ea066982b140a19337e17e60d45d4076e042faf",
        "data/Conflict_Resolution-00000-of-00001.parquet", 1_491_588,
        "24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45",
    ),
    "lme-oracle": Source(
        "xiaowu0162/longmemeval-cleaned", "98d7416c24c778c2fee6e6f3006e7a073259d48f",
        "longmemeval_oracle.json", 15_388_478,
        "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
    ),
    "lme-s": Source(
        "xiaowu0162/longmemeval-cleaned", "98d7416c24c778c2fee6e6f3006e7a073259d48f",
        "longmemeval_s_cleaned.json", 277_383_467,
        "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
    ),
}


def default_cache_dir() -> Path:
    raw = (os.environ.get("TESSERAE_EVAL_CACHE") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "tesserae" / "evals" / "lifecycle"


def refuse_in_repo(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise Skip(
            f"refusing to write lifecycle-eval files inside the repository: {resolved}",
            "use a directory outside the repo — the defaults are "
            "~/.cache/tesserae/evals/lifecycle (data, work) and "
            "~/.tesserae/evals/lifecycle (results)",
        )


def cached_path(name: str, cache_dir: Optional[Path] = None) -> Path:
    src = SOURCES[name]
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    return root / src.repo.replace("/", "__") / src.revision / src.path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, cache_dir: Optional[Path] = None, *, allow_download: bool = False) -> Path:
    """Path to the pinned file ``name``, downloading it only when allowed.

    A cached file is trusted on its size; a fresh download is checked against
    the pinned sha256 before it is renamed into place, so a truncated or
    substituted file never becomes the cached one.
    """
    if name not in SOURCES:
        raise Skip(f"unknown dataset {name!r}", f"pick one of {sorted(SOURCES)}")
    src = SOURCES[name]
    dest = cached_path(name, cache_dir)
    refuse_in_repo(dest)
    if dest.exists():
        size = dest.stat().st_size
        if size != src.size:
            raise ValueError(
                f"{dest} is {size} bytes; the pinned {src.repo}@{src.revision[:8]} "
                f"file is {src.size}. Delete it and run the fetch verb again."
            )
        return dest
    if not allow_download:
        raise Skip(
            f"{name} is not cached at {dest}",
            f"uv run python -m evals.lifecycle.run fetch {name}",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(src.url, timeout=120) as resp, part.open("wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            out.write(block)
    got = _sha256(part)
    if got != src.sha256:
        part.unlink()
        raise ValueError(
            f"{src.url} has sha256 {got}, pinned {src.sha256} — the upstream "
            f"revision changed or the download was corrupted; nothing was cached"
        )
    part.rename(dest)
    return dest


# ---------------------------------------------------------------------------
# HaluMem
# ---------------------------------------------------------------------------


def _strict_bool(raw: Any, *, where: str) -> bool:
    """HaluMem writes ``is_update`` as the STRING ``"True"``/``"False"``.

    ``bool("False")`` is True, which is the bug this exists to prevent; any
    other spelling is a schema change and raises rather than being guessed.
    """
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip()
    if text == "True":
        return True
    if text == "False":
        return False
    raise ValueError(f"{where}: is_update={raw!r} is neither 'True' nor 'False'")


@dataclass(frozen=True)
class MemoryPoint:
    index: int
    content: str
    memory_type: str
    source: str  # primary | secondary | interference | system
    is_update: bool
    original_memories: Tuple[str, ...]
    importance: float

    @property
    def is_target(self) -> bool:
        """A point the system SHOULD store. ``interference`` points are the
        distractors HaluMem plants in assistant turns; storing one is a false
        memory, not recall."""
        return self.source != "interference"


@dataclass(frozen=True)
class HaluSession:
    user_id: str
    index: int
    start_time: str
    dialogue: Tuple[Mapping[str, Any], ...]
    memory_points: Tuple[MemoryPoint, ...]

    @property
    def key(self) -> str:
        return f"{self.user_id}:{self.index:04d}"

    def turns(self) -> List[Dict[str, str]]:
        """Dialogue in the ``{role, text}`` shape ``session_graph_llm`` reads."""
        return [
            {"role": str(t.get("role") or "?"), "text": str(t.get("content") or "")}
            for t in self.dialogue
        ]


@dataclass(frozen=True)
class HaluUser:
    uuid: str
    sessions: Tuple[HaluSession, ...]


def _parse_halumem_user(record: Mapping[str, Any]) -> HaluUser:
    uuid = str(record["uuid"])
    sessions: List[HaluSession] = []
    for s_idx, raw in enumerate(record.get("sessions") or []):
        points = []
        for mp in raw.get("memory_points") or []:
            points.append(
                MemoryPoint(
                    index=int(mp["index"]),
                    content=str(mp["memory_content"]),
                    memory_type=str(mp.get("memory_type") or ""),
                    source=str(mp.get("memory_source") or ""),
                    is_update=_strict_bool(mp.get("is_update"), where=f"{uuid} session {s_idx}"),
                    original_memories=tuple(str(o) for o in (mp.get("original_memories") or [])),
                    importance=float(mp.get("importance") or 0.0),
                )
            )
        sessions.append(
            HaluSession(
                user_id=uuid,
                index=s_idx,
                start_time=str(raw.get("start_time") or ""),
                dialogue=tuple(dict(t) for t in (raw.get("dialogue") or [])),
                memory_points=tuple(points),
            )
        )
    return HaluUser(uuid=uuid, sessions=tuple(sessions))


def load_halumem(path: Path, *, max_users: Optional[int] = None) -> List[HaluUser]:
    """Read a HaluMem JSONL file (one user per line)."""
    users: List[HaluUser] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            users.append(_parse_halumem_user(json.loads(line)))
            if max_users is not None and len(users) >= max_users:
                break
    if not users:
        raise ValueError(f"{path} holds no HaluMem users")
    return users


# ---------------------------------------------------------------------------
# FactConsolidation (MemoryAgentBench, Conflict_Resolution split)
# ---------------------------------------------------------------------------

_FACT_LINE = re.compile(r"^(\d+)\. (.+)$", re.MULTILINE)

#: Words a relation template ends on ("... was born in the city of", "... is
#: married to", "... was founded by"). A fact's subject-relation KEY is a word
#: prefix ending on one of these.
_RELATION_TAIL = frozenset(
    {"is", "to", "of", "in", "by", "for", "as", "with", "at", "was", "from", "on"}
)
#: A remainder that still holds a verb has cut the template before the
#: relation ("The capital of | Romania is Rajanpur"), so the prefix is not a key.
_VERB_IN_REMAINDER = re.compile(
    r"\b(is|was|are|were|plays|played|died|born|located|founded|married|"
    r"associated|worked|educated|created|developed|speaks|holds|famous|"
    r"employed|affiliated|produced|written|citizen)\b"
)


@dataclass(frozen=True)
class FactGroup:
    source: str
    facts: Tuple[str, ...]
    questions: Tuple[str, ...]
    answers: Tuple[Tuple[str, ...], ...]

    @property
    def hops(self) -> str:
        return "mh" if "_mh_" in self.source else "sh"


def parse_facts(context: str) -> List[str]:
    """Numbered facts in order; raises unless they run 0..n-1 without a gap,
    because the index IS the recency signal and a gap means a misparse."""
    found = _FACT_LINE.findall(context)
    for expected, (number, _) in enumerate(found):
        if int(number) != expected:
            raise ValueError(f"fact numbering breaks at {number} (expected {expected})")
    if not found:
        raise ValueError("context holds no numbered facts")
    return [text.strip() for _, text in found]


def _candidate_keys(fact: str) -> List[str]:
    words = fact.rstrip(".").split(" ")
    keys = []
    for i in range(len(words) - 1):
        if words[i] not in _RELATION_TAIL:
            continue
        remainder = " ".join(words[i + 1:])
        if _VERB_IN_REMAINDER.search(remainder):
            continue
        keys.append(" ".join(words[: i + 1]))
    return keys


def conflict_pairs(facts: Sequence[str]) -> List[Tuple[int, int]]:
    """``(newer, older)`` index pairs that state one subject-relation twice.

    SILVER labels, derived — MemoryAgentBench ships no conflict pairs. Each
    fact's key is its LONGEST subject-relation prefix that another fact shares,
    so "Joan Didion was educated at University of California, Berkeley" pairs
    with "... educated at Baldwin Wallace University" on "Joan Didion was
    educated at", not on the spurious "... at University of". Within a key
    group every later fact supersedes every earlier one; the larger index is
    the newer fact, which is MemoryAgentBench's own rule.
    """
    per_fact = [_candidate_keys(f) for f in facts]
    holders: Dict[str, List[int]] = {}
    for idx, keys in enumerate(per_fact):
        for key in keys:
            holders.setdefault(key, []).append(idx)
    chosen: Dict[str, List[int]] = {}
    for idx, keys in enumerate(per_fact):
        shared = [k for k in keys if len(holders[k]) > 1]
        if not shared:
            continue
        best = max(shared, key=len)
        chosen.setdefault(best, []).append(idx)
    pairs: List[Tuple[int, int]] = []
    for members in chosen.values():
        if len(members) < 2:
            continue
        ordered = sorted(members)
        for j, newer in enumerate(ordered):
            for older in ordered[:j]:
                pairs.append((newer, older))
    return sorted(pairs)


def load_factconsolidation(parquet: Path, *, sources: Optional[Iterable[str]] = None) -> List[FactGroup]:
    import pyarrow.parquet as pq  # optional; `uv run --with pyarrow`

    wanted = set(sources) if sources else None
    groups: List[FactGroup] = []
    for row in pq.read_table(str(parquet)).to_pylist():
        source = str((row.get("metadata") or {}).get("source") or "")
        if not source.startswith("factconsolidation"):
            continue
        if wanted is not None and source not in wanted:
            continue
        groups.append(
            FactGroup(
                source=source,
                facts=tuple(parse_facts(row.get("context") or "")),
                questions=tuple(str(q) for q in row.get("questions") or []),
                answers=tuple(tuple(str(a) for a in ans) for ans in row.get("answers") or []),
            )
        )
    if not groups:
        raise ValueError(
            f"no factconsolidation records{' matching ' + str(sorted(wanted)) if wanted else ''} in {parquet}"
        )
    return groups


# ---------------------------------------------------------------------------
# LongMemEval (cleaned): knowledge-update + abstention
# ---------------------------------------------------------------------------

LME_SUBSETS = ("knowledge-update", "abstention")


@dataclass(frozen=True)
class LmeQuestion:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: str
    #: ``(date, turns)`` per haystack session, in haystack order. ``has_answer``
    #: is dropped at load: it marks the gold evidence turn, and nothing that
    #: builds memory may read it.
    sessions: Tuple[Tuple[str, Tuple[Mapping[str, str], ...]], ...] = field(default_factory=tuple)

    @property
    def is_abstention(self) -> bool:
        return self.question_id.endswith("_abs")

    @property
    def subsets(self) -> Tuple[str, ...]:
        out = []
        if self.question_type == "knowledge-update":
            out.append("knowledge-update")
        if self.is_abstention:
            out.append("abstention")
        return tuple(out)


def _lme_question(raw: Mapping[str, Any]) -> LmeQuestion:
    dates = list(raw.get("haystack_dates") or [])
    sessions_raw = list(raw.get("haystack_sessions") or [])
    if len(dates) != len(sessions_raw):
        raise ValueError(
            f"{raw.get('question_id')}: {len(dates)} dates for {len(sessions_raw)} sessions"
        )
    sessions = tuple(
        (
            str(date),
            tuple({"role": str(t.get("role") or "?"), "text": str(t.get("content") or "")} for t in turns),
        )
        for date, turns in zip(dates, sessions_raw)
    )
    return LmeQuestion(
        question_id=str(raw["question_id"]),
        question_type=str(raw["question_type"]),
        question=str(raw["question"]),
        answer=str(raw["answer"]),
        question_date=str(raw.get("question_date") or ""),
        sessions=sessions,
    )


def load_lme(path: Path, *, subsets: Sequence[str] = LME_SUBSETS) -> List[LmeQuestion]:
    """LongMemEval questions belonging to any of ``subsets``."""
    unknown = set(subsets) - set(LME_SUBSETS)
    if unknown:
        raise ValueError(f"unknown LME subset(s) {sorted(unknown)}; known: {LME_SUBSETS}")
    with Path(path).open(encoding="utf-8") as fh:
        records = json.load(fh)
    out: List[LmeQuestion] = []
    for raw in records:
        q = _lme_question(raw)
        if set(q.subsets) & set(subsets):
            out.append(q)
    if not out:
        raise ValueError(f"{path} holds no questions in subsets {list(subsets)}")
    return out
