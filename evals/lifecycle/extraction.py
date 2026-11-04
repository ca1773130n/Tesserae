"""Stage 1 — extraction: fabrication and omission against HaluMem gold points.

The system under test is ``tesserae.session_graph_llm.extract_with_llm``, the
exact call ``compile`` makes per session, fed one HaluMem session's dialogue as
its transcript. Its findings are then matched to that session's gold memory
points by a judge:

* ``lexical`` — content-token recall of the gold point inside the finding,
  >= :data:`LEXICAL_MATCH`. Free and deterministic, and a WEAK proxy: it
  cannot see paraphrase, so it over-counts omission and fabrication alike.
  Its job is to make the pipeline runnable without spending, not to gate.
* ``llm:<model>`` — one call per session that names, for every finding, the
  gold point stating the same fact (``T#`` target, ``X#`` interference) or
  ``none``.

Per session, from those matches:

* a TARGET point is *covered* when any finding matched it — ``recall`` and its
  importance-weighted twin, ``omission_rate = 1 - recall``;
* a finding is *grounded* when it matched a target point. The rest split into
  ``interference_rate`` (it stored one of HaluMem's planted falsehoods) and
  ``ungrounded_rate`` (it matched no gold point at all);
* ``fabrication_rate = interference_rate + ungrounded_rate``. That is an UPPER
  bound: a finding can be true to the dialogue and still absent from the gold
  list, so this is not HaluMem's published "memory accuracy" and is not to be
  quoted beside MemOS's 80.91 without that caveat;
* ``false_memory_resistance = 1 - (interference points captured / interference
  points)``, HaluMem's FMR.

Test-retest items are the gold points (``covered`` / ``missed``) and the units
are sessions (their recall), so a re-run disagrees exactly where either the
extractor or the judge is unstable.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .datasets import HaluSession, HaluUser
from .llm import EXTRACTION_JUDGE_SYSTEM

logger = logging.getLogger(__name__)

LEXICAL_MATCH = 0.5

_STOP = frozenset(
    "a an the of to in on for and or but is are was were be been being with at by "
    "from as that this these those it its his her their he she they them user "
    "user's has have had do does did not no so very also about into than then".split()
)


def content_tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1}


def sample_sessions(users: Sequence[HaluUser], n: int, *, seed: str = "r1") -> List[HaluSession]:
    """``n`` sessions chosen by a seeded hash of their key — stable across
    machines and Python versions, unlike ``random`` on a set."""
    every = [s for u in users for s in u.sessions if s.memory_points]
    every.sort(key=lambda s: hashlib.sha256(f"{seed}:{s.key}".encode()).hexdigest())
    return every[:n]


def extract_session(session: HaluSession, client: Any) -> Tuple[List[str], dict]:
    """Findings Tesserae's session extractor produces for one HaluMem session."""
    from tesserae.harness_sessions import HarnessSession
    from tesserae.session_graph_llm import extract_with_llm

    hs = HarnessSession(
        id=f"halumem-{session.key}", slug=session.key.replace(":", "-"),
        harness="lifecycle-eval", agent_label="HaluMem", project_name="lifecycle-eval",
        project_root="", started_at=session.start_time,
    )
    stats: dict = {}
    findings = extract_with_llm(hs, session.turns(), [], client, cache_key=None, stats=stats)
    return [f.body for f in findings], stats


# ---------------------------------------------------------------------------
# Judges: finding index -> "T<i>" | "X<i>" | "none"
# ---------------------------------------------------------------------------


def _gold(session: HaluSession) -> Tuple[List[Any], List[Any]]:
    targets = [mp for mp in session.memory_points if mp.is_target]
    interference = [mp for mp in session.memory_points if not mp.is_target]
    return targets, interference


class LexicalJudge:
    name = "lexical"
    llm_calls = 0

    def config(self) -> dict:
        return {"judge": "lexical", "threshold": LEXICAL_MATCH,
                "note": "content-token recall of the gold point; weak proxy, not a gate"}

    def match(self, session: HaluSession, findings: Sequence[str]) -> Optional[List[str]]:
        targets, interference = _gold(session)
        gold = [(f"T{i}", content_tokens(mp.content)) for i, mp in enumerate(targets)]
        gold += [(f"X{i}", content_tokens(mp.content)) for i, mp in enumerate(interference)]
        labels = []
        for body in findings:
            ftoks = content_tokens(body)
            best, best_score = "none", 0.0
            for label, gtoks in gold:
                if not gtoks:
                    continue
                score = len(gtoks & ftoks) / len(gtoks)
                if score > best_score:
                    best, best_score = label, score
            labels.append(best if best_score >= LEXICAL_MATCH else "none")
        return labels


class LLMExtractionJudge:
    name = "llm"

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        #: Replies that named no usable label for some finding; those findings
        #: are scored ``none`` (ungrounded) and counted here so a judge that
        #: stopped answering cannot pass for an extractor that fabricates.
        self.unparseable = 0

    def config(self) -> dict:
        return {"judge": f"llm:{self.model}", "prompt": "EXTRACTION_JUDGE_SYSTEM",
                "calls_per_session": 1}

    def match(self, session: HaluSession, findings: Sequence[str]) -> Optional[List[str]]:
        if not findings:
            return []
        targets, interference = _gold(session)
        lines = ["TARGET points:"]
        lines += [f"T{i}: {mp.content}" for i, mp in enumerate(targets)] or ["(none)"]
        lines += ["", "INTERFERENCE points:"]
        lines += [f"X{i}: {mp.content}" for i, mp in enumerate(interference)] or ["(none)"]
        lines += ["", "Extracted memories:"]
        lines += [f"F{i}: {body}" for i, body in enumerate(findings)]
        lines += [
            "",
            'Return JSON {"matches": [{"finding": <F index as int>, "point": "T<i>" | "X<i>" | "none"}]} '
            "with exactly one entry per extracted memory.",
        ]
        reply = self.client.complete_json(
            system=EXTRACTION_JUDGE_SYSTEM, user="\n".join(lines),
            schema_name="lifecycle_extraction_match", cache_key=None,
        )
        if not isinstance(reply, dict):
            return None
        valid = {f"T{i}" for i in range(len(targets))} | {f"X{i}" for i in range(len(interference))}
        labels = ["none"] * len(findings)
        seen = set()
        for entry in reply.get("matches") or []:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("finding"))
            except (TypeError, ValueError):
                continue
            point = str(entry.get("point") or "none").strip().upper()
            if 0 <= idx < len(findings):
                seen.add(idx)
                labels[idx] = point if point in valid else "none"
        missing = len(findings) - len(seen)
        if missing:
            self.unparseable += missing
            logger.warning("extraction judge left %d of %d findings unlabelled in %s",
                           missing, len(findings), session.key)
        return labels


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class SessionScore:
    key: str
    findings: int
    target_points: int
    covered: int
    weighted_total: float
    weighted_covered: float
    grounded: int
    interference_findings: int
    ungrounded: int
    interference_points: int
    interference_captured: int
    #: gold point id -> "covered" | "missed" — the test-retest items
    items: Dict[str, str] = field(default_factory=dict)

    @property
    def recall(self) -> float:
        return self.covered / self.target_points if self.target_points else 1.0


def score_session(session: HaluSession, findings: Sequence[str], labels: Sequence[str]) -> SessionScore:
    if len(labels) != len(findings):
        raise ValueError(f"{session.key}: {len(labels)} labels for {len(findings)} findings")
    targets, interference = _gold(session)
    hit_t = {int(lab[1:]) for lab in labels if lab.startswith("T")}
    hit_x = {int(lab[1:]) for lab in labels if lab.startswith("X")}
    items = {}
    for i in range(len(targets)):
        items[f"{session.key}:T{i}"] = "covered" if i in hit_t else "missed"
    for i in range(len(interference)):
        items[f"{session.key}:X{i}"] = "covered" if i in hit_x else "missed"
    return SessionScore(
        key=session.key,
        findings=len(findings),
        target_points=len(targets),
        covered=len(hit_t),
        weighted_total=sum(mp.importance for mp in targets),
        weighted_covered=sum(mp.importance for i, mp in enumerate(targets) if i in hit_t),
        grounded=sum(1 for lab in labels if lab.startswith("T")),
        interference_findings=sum(1 for lab in labels if lab.startswith("X")),
        ungrounded=sum(1 for lab in labels if lab == "none"),
        interference_points=len(interference),
        interference_captured=len(hit_x),
        items=items,
    )


def aggregate(scores: Sequence[SessionScore]) -> dict:
    def ratio(num: float, den: float) -> Optional[float]:
        return round(num / den, 4) if den else None

    findings = sum(s.findings for s in scores)
    targets = sum(s.target_points for s in scores)
    covered = sum(s.covered for s in scores)
    interference_points = sum(s.interference_points for s in scores)
    recall = ratio(covered, targets)
    interference_rate = ratio(sum(s.interference_findings for s in scores), findings)
    ungrounded_rate = ratio(sum(s.ungrounded for s in scores), findings)
    return {
        "sessions": len(scores),
        "findings": findings,
        "target_points": targets,
        "recall": recall,
        "omission_rate": None if recall is None else round(1 - recall, 4),
        "weighted_recall": ratio(sum(s.weighted_covered for s in scores),
                                 sum(s.weighted_total for s in scores)),
        "interference_rate": interference_rate,
        "ungrounded_rate": ungrounded_rate,
        "fabrication_rate": (None if interference_rate is None
                             else round(interference_rate + ungrounded_rate, 4)),
        "interference_points": interference_points,
        "false_memory_resistance": (
            None if not interference_points
            else round(1 - sum(s.interference_captured for s in scores) / interference_points, 4)
        ),
    }


def run_once(sessions: Sequence[HaluSession], extractor: Any, judge: Any) -> dict:
    """One full pass: extract every session, judge it, score it.

    A session whose extraction call failed outright, or whose judge call
    failed, is EXCLUDED and listed — scoring it would count an outage as
    omission, which is the failure mode that made the old metric unusable.
    """
    scores: List[SessionScore] = []
    excluded: List[dict] = []
    for session in sessions:
        findings, stats = extract_session(session, extractor)
        if stats.get("calls") and stats.get("failed") == stats.get("calls"):
            excluded.append({"session": session.key, "reason": "every extraction call failed"})
            logger.warning("extraction: every call failed for %s; excluded", session.key)
            continue
        labels = judge.match(session, findings)
        if labels is None:
            excluded.append({"session": session.key, "reason": "judge call failed"})
            logger.warning("extraction judge failed for %s; excluded", session.key)
            continue
        scores.append(score_session(session, findings, labels))
    labels_out: Dict[str, str] = {}
    for s in scores:
        labels_out.update(s.items)
    return {
        "metrics": aggregate(scores),
        "excluded": excluded,
        "labels": labels_out,
        "unit_rates": {s.key: round(s.recall, 4) for s in scores},
    }
