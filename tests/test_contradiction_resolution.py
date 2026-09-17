"""KB-04: LLM-arbitrated contradiction resolution mints a resolved_by edge.

A scripted LLM client arbitrates a detected contradicting pair into a
deterministic ``resolved_by`` edge (source=loser, target=winner). The
verdict is cached on disk content-keyed, so a second pass with a fresh
empty client mints the SAME edge with ZERO LLM calls. lint then demotes
the resolved pair to ``info`` and raises an unresolved pair to ``warning``.

Pair detection itself is pinned two ways: seeded random graphs must give
exactly what the old every-pair scan (copied below as an oracle) gave, and
tens of thousands of unmarked claims must not be compared pairwise.

Deterministic: scripted client (no network), fixed node content and seeds.
The only wall-clock use is a generous upper bound in the scale tests; the
operation counts are what those tests pin.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pytest

from tesserae import lint as lint_module
from tesserae.lint import LintFinding, WikiLinter
from tesserae.memory import contradiction
from tesserae.memory.contradiction import (
    RESOLVED_BY_EDGE,
    detect_contradicting_pairs,
    run_contradiction_resolution,
)
from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


class _ScriptedClient:
    """LLMJsonClient stub returning scripted ``complete_json`` responses."""

    def __init__(self, responses: List[Optional[Union[dict, list]]]):
        self._responses = list(responses)
        self.calls: int = 0

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        self.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


def _claim(node_id: str, name: str, desc: str, source_path: str) -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description=desc,
        source_path=source_path,
    )


@pytest.fixture
def contradicting_graph() -> ResearchGraph:
    # left carries "outperforms"; right carries "is outperformed by"; they
    # share the model+benchmark topic and come from different sources.
    a = _claim(
        "PerformanceClaim:a",
        "Model X beats Y on GLUE",
        "Model X outperforms Model Y on the GLUE benchmark.",
        source_path="docs/paper-a.md",
    )
    b = _claim(
        "PerformanceClaim:b",
        "Model X loses to Y on GLUE",
        "Model X is outperformed by Model Y on the GLUE benchmark.",
        source_path="docs/paper-b.md",
    )
    return ResearchGraph(nodes=[a, b], edges=[])


def test_detect_finds_the_pair(contradicting_graph: ResearchGraph) -> None:
    pairs = detect_contradicting_pairs(contradicting_graph)
    assert len(pairs) == 1
    left, right = pairs[0]
    assert left.id == "PerformanceClaim:a"
    assert right.id == "PerformanceClaim:b"


def test_resolution_mints_resolved_by_edge(
    contradicting_graph: ResearchGraph, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "contradiction_cache"
    client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "Paper A used the standard split.",
            }
        ]
    )
    out, conf = run_contradiction_resolution(
        contradicting_graph, llm=client, cache_dir=cache_dir
    )
    assert client.calls == 1

    resolved = [e for e in out.edges if e.type == RESOLVED_BY_EDGE]
    assert len(resolved) == 1
    edge = resolved[0]
    # source == loser, target == winner.
    assert edge.source == "PerformanceClaim:b"
    assert edge.target == "PerformanceClaim:a"
    assert conf["PerformanceClaim:a"] == "high"
    assert conf["PerformanceClaim:b"] == "low"


def test_warm_cache_mints_same_edge_with_zero_llm_calls(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "contradiction_cache"

    # Cold run populates the content-keyed cache.
    cold_graph = _two_claim_graph()
    cold_client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "A is canonical.",
            }
        ]
    )
    run_contradiction_resolution(cold_graph, llm=cold_client, cache_dir=cache_dir)
    assert cold_client.calls == 1
    assert list(cache_dir.glob("*.json"))

    # Warm run: a FRESH graph, an EMPTY scripted client -> verdict from disk.
    warm_graph = _two_claim_graph()
    warm_client = _ScriptedClient([])
    out, _conf = run_contradiction_resolution(
        warm_graph, llm=warm_client, cache_dir=cache_dir
    )
    assert warm_client.calls == 0, "warm cache must skip the LLM"
    resolved = [e for e in out.edges if e.type == RESOLVED_BY_EDGE]
    assert len(resolved) == 1
    assert resolved[0].source == "PerformanceClaim:b"
    assert resolved[0].target == "PerformanceClaim:a"


def test_no_client_is_no_op(contradicting_graph: ResearchGraph, tmp_path: Path) -> None:
    out, conf = run_contradiction_resolution(
        contradicting_graph, llm=None, cache_dir=tmp_path / "cache"
    )
    assert [e for e in out.edges if e.type == RESOLVED_BY_EDGE] == []
    assert conf == {}


def test_lint_severity_resolved_info_unresolved_warning(
    tmp_path: Path,
) -> None:
    # Resolved pair -> the lint check demotes to info.
    resolved_graph = _two_claim_graph()
    client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "A wins.",
            }
        ]
    )
    resolved_graph, _ = run_contradiction_resolution(
        resolved_graph, llm=client, cache_dir=tmp_path / "cache"
    )
    resolved_findings = _contradiction_findings(resolved_graph)
    assert len(resolved_findings) == 1
    assert resolved_findings[0].severity == "info"

    # Unresolved pair (no resolved_by edge) -> warning.
    unresolved_findings = _contradiction_findings(_two_claim_graph())
    assert len(unresolved_findings) == 1
    assert unresolved_findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_claim_graph() -> ResearchGraph:
    a = _claim(
        "PerformanceClaim:a",
        "Model X beats Y on GLUE",
        "Model X outperforms Model Y on the GLUE benchmark.",
        source_path="docs/paper-a.md",
    )
    b = _claim(
        "PerformanceClaim:b",
        "Model X loses to Y on GLUE",
        "Model X is outperformed by Model Y on the GLUE benchmark.",
        source_path="docs/paper-b.md",
    )
    return ResearchGraph(nodes=[a, b], edges=[])


def _contradiction_findings(graph: ResearchGraph, tmp_path: Path = None):
    """Run lint's contradiction check directly over the graph's dicts.

    Avoids scaffolding a whole project: ``_check_contradicting_claims`` is a
    pure function of ``nodes_by_id`` + ``edges`` dicts (the same shape
    WikiLinter.run() feeds it from graph.json).
    """
    import tempfile

    linter = WikiLinter(tempfile.gettempdir())
    nodes_by_id = {n.id: _node_dict(n) for n in graph.nodes}
    edges = [
        {"source": e.source, "target": e.target, "type": e.type}
        for e in graph.edges
    ]
    return [
        f
        for f in linter._check_contradicting_claims(nodes_by_id, edges)
        if f.code == "CONTRADICTING_CLAIMS"
    ]


def _node_dict(node: ResearchNode) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "type": node.type.value,
        "description": node.description,
        "source_path": node.source_path,
    }


# ---------------------------------------------------------------------------
# Pair detection: same answer as the every-pair scan, without its cost
# ---------------------------------------------------------------------------


def _every_pair_detect(graph: ResearchGraph) -> List[Tuple[ResearchNode, ResearchNode]]:
    """``detect_contradicting_pairs`` before bucketing, kept as the oracle."""
    candidates = sorted(
        (
            n
            for n in graph.nodes
            if contradiction._kind(n) in contradiction._CLAIM_KINDS
        ),
        key=lambda n: n.id,
    )
    pairs: List[Tuple[ResearchNode, ResearchNode]] = []
    seen = set()
    for i, first in enumerate(candidates):
        first_text = first_lower = None  # lazy
        for second in candidates[i + 1 :]:
            if first.source_path and first.source_path == second.source_path:
                continue
            if first_text is None:
                first_text = contradiction._node_text(first)
                first_lower = first_text.lower()
            second_text = contradiction._node_text(second)
            second_lower = second_text.lower()
            left_marker = contradiction._LEFT_MARKER
            right_marker = contradiction._RIGHT_MARKER
            if left_marker in first_lower and right_marker in second_lower:
                left, right = first, second
                left_text, right_text = first_text, second_text
            elif left_marker in second_lower and right_marker in first_lower:
                left, right = second, first
                left_text, right_text = second_text, first_text
            else:
                continue
            shared = contradiction._topic_tokens(left_text) & contradiction._topic_tokens(
                right_text
            )
            if len(shared) < 2:
                continue
            key = tuple(sorted([left.id, right.id]))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((left, right))
    pairs.sort(key=lambda pr: (pr[0].id, pr[1].id))
    return pairs


def _every_pair_lint(
    nodes_by_id: Dict[str, dict], edges: List[dict]
) -> Iterable[LintFinding]:
    """lint's ``_check_contradicting_claims`` before bucketing, as the oracle."""
    resolved_winner: Dict[Tuple[str, str], str] = {}
    for edge in edges or []:
        if edge.get("type") != "resolved_by":
            continue
        src = edge.get("source")
        tgt = edge.get("target")
        if not isinstance(src, str) or not isinstance(tgt, str):
            continue
        resolved_winner[tuple(sorted([src, tgt]))] = tgt
    candidates = [
        (nid, node)
        for nid, node in nodes_by_id.items()
        if node.get("type") in ("PerformanceClaim", "ComparisonClaim")
    ]
    candidates.sort(key=lambda kv: kv[0])
    seen = set()
    for i, (left_id, left) in enumerate(candidates):
        left_text = lint_module._claim_text(left)
        if "outperforms" not in left_text.lower():
            continue
        for j in range(i + 1, len(candidates)):
            right_id, right = candidates[j]
            if left.get("source_path") and left.get("source_path") == right.get("source_path"):
                continue
            right_text = lint_module._claim_text(right)
            if "is outperformed by" not in right_text.lower():
                continue
            shared = set(lint_module._topic_tokens(left_text)) & set(
                lint_module._topic_tokens(right_text)
            )
            if len(shared) < 2:
                continue
            pair = tuple(sorted([left_id, right_id]))
            if pair in seen:
                continue
            seen.add(pair)
            winner_id = resolved_winner.get(pair)
            if winner_id is not None:
                winner_name = (nodes_by_id.get(winner_id) or {}).get("name")
                yield LintFinding(
                    severity="info",
                    code="CONTRADICTING_CLAIMS",
                    message=(
                        f"Two claims contradicted each other; resolved by "
                        f"{winner_name!r}: {left.get('name')!r} vs "
                        f"{right.get('name')!r}."
                    ),
                    node_id=left_id,
                    suggested_fix="Resolution recorded via resolved_by edge.",
                )
            else:
                yield LintFinding(
                    severity="warning",
                    code="CONTRADICTING_CLAIMS",
                    message=(
                        f"Two claims appear to contradict each other: "
                        f"{left.get('name')!r} vs {right.get('name')!r}."
                    ),
                    node_id=left_id,
                    suggested_fix="Manually review both source documents and reconcile.",
                )


# Small vocabularies so random texts often share two topic tokens, and marker
# spellings that do and do not count (case, the bare past participle).
_WORDS = [
    "model", "x", "y", "GLUE,", "squad", "(bert)", "resnet", "imagenet.",
    "accuracy", "the", "on", "baseline", "vit", "dtu", "top-1", "by",
]
_MARKERS = [
    "outperforms", "is outperformed by", "OUTPERFORMS", "Is Outperformed By",
    "outperformed", "is outperformed", "outperformsx",
]
_SOURCES = [None, "", "docs/a.md", "docs/b.md", "docs/c.md"]


def _random_text(rng: random.Random) -> str:
    words = rng.choices(_WORDS, k=rng.randint(0, 6))
    for _ in range(rng.choice([0, 0, 1, 1, 2])):
        words.insert(rng.randint(0, len(words)), rng.choice(_MARKERS))
    return " ".join(words)


def _random_metadata(rng: random.Random) -> dict:
    roll = rng.random()
    if roll < 0.2:
        return {"evidence": _random_text(rng)}
    if roll < 0.3:
        return {"text": _random_text(rng)}
    return {}


def _random_graph(rng: random.Random) -> ResearchGraph:
    # A small id pool, so several nodes often share an id.
    pool = [f"PerformanceClaim:{c}" for c in rng.sample("abcdefghijklmnop", k=rng.randint(1, 12))]
    kinds = [
        ResearchNodeType.PERFORMANCE_CLAIM,
        ResearchNodeType.COMPARISON_CLAIM,
        ResearchNodeType.MODEL,
    ]
    nodes = [
        ResearchNode(
            id=rng.choice(pool),
            name=_random_text(rng),
            type=rng.choice(kinds),
            description=_random_text(rng),
            source_path=rng.choice(_SOURCES),
            metadata=_random_metadata(rng),
        )
        for _ in range(rng.randint(0, 14))
    ]
    if nodes and rng.random() < 0.2:
        nodes.append(rng.choice(nodes))  # the same node object twice
    rng.shuffle(nodes)
    return ResearchGraph(nodes=nodes, edges=[])


def _carries_both(node: ResearchNode) -> bool:
    lower = contradiction._node_text(node).lower()
    return contradiction._LEFT_MARKER in lower and contradiction._RIGHT_MARKER in lower


def test_detect_matches_every_pair_scan_on_random_graphs() -> None:
    graphs_with_pairs = both_marker_pairs = duplicate_id_pairs = 0
    for seed in range(1500):
        graph = _random_graph(random.Random(seed))
        expected = _every_pair_detect(graph)
        actual = detect_contradicting_pairs(graph)
        # Identity, not equality: the same node objects in the same roles.
        assert [(id(l), id(r)) for l, r in actual] == [
            (id(l), id(r)) for l, r in expected
        ], f"seed {seed}"
        if expected:
            graphs_with_pairs += 1
            ids = [n.id for n in graph.nodes]
            if len(set(ids)) < len(ids):
                duplicate_id_pairs += 1
        both_marker_pairs += sum(
            1 for l, r in expected if _carries_both(l) and _carries_both(r)
        )
    # The random graphs must reach the cases the equivalence is about.
    assert graphs_with_pairs > 300
    assert both_marker_pairs > 20
    assert duplicate_id_pairs > 100


def test_lint_matches_every_pair_scan_on_random_graphs() -> None:
    graphs_with_findings = resolved = 0
    for seed in range(1500):
        rng = random.Random(seed)
        graph = _random_graph(rng)
        nodes_by_id = {
            n.id: {
                "id": n.id,
                "name": rng.choice([n.name, n.name, None, 7]),
                "type": n.type.value,
                "description": n.description,
                "source_path": n.source_path,
                "metadata": n.metadata,
            }
            for n in graph.nodes
        }
        ids = list(nodes_by_id)
        edges = [
            {
                "source": rng.choice(ids + [None]),
                "target": rng.choice(ids),
                "type": rng.choice(["resolved_by", "resolved_by", "cites"]),
            }
            for _ in range(rng.randint(0, 4) if ids else 0)
        ]
        expected = list(_every_pair_lint(nodes_by_id, edges))
        actual = list(
            WikiLinter("/nonexistent")._check_contradicting_claims(nodes_by_id, edges)
        )
        assert actual == expected, f"seed {seed}"
        graphs_with_findings += bool(expected)
        resolved += sum(1 for f in expected if f.severity == "info")
    assert graphs_with_findings > 200
    assert resolved > 10


def test_marker_names_and_topic_rule_are_unchanged() -> None:
    # HypePaper imports the markers and _node_text; agent_distill imports
    # _share_topic.
    assert contradiction._LEFT_MARKER == "outperforms"
    assert contradiction._RIGHT_MARKER == "is outperformed by"
    node = ResearchNode(
        id="c",
        name="n",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="d",
        metadata={"evidence": "e"},
    )
    assert contradiction._node_text(node) == "n d e"
    rng = random.Random(0)
    for _ in range(500):
        a, b = _random_text(rng), _random_text(rng)
        old = len(contradiction._topic_tokens(a) & contradiction._topic_tokens(b)) >= 2
        assert contradiction._share_topic(a, b) is old
        old_lint = len(set(lint_module._topic_tokens(a)) & set(lint_module._topic_tokens(b))) >= 2
        assert lint_module._share_topic(a, b) is old_lint


_UNMARKED = 50_000
_OUTPERFORMS = 3_000


def _scale_claims(outperformed_by: int) -> List[ResearchNode]:
    """Mostly unmarked claims, 3,000 ``outperforms`` claims, a few reversed.

    Reversed claim ``k`` shares its two topic tokens with ``outperforms``
    claim ``k`` only, so the expected answer is one pair per reversed claim.
    """
    unmarked = [
        _claim(
            f"PerformanceClaim:u{n:05d}",
            f"Model u{n} on bench{n}",
            f"Model u{n} reaches 90 accuracy on bench{n}.",
            source_path=f"docs/u{n}.md",
        )
        for n in range(_UNMARKED)
    ]
    outperforms = [
        _claim(
            f"PerformanceClaim:l{i:04d}",
            f"method{i} beats rival{i}",
            f"method{i} outperforms rival{i}.",
            source_path="docs/left.md",
        )
        for i in range(_OUTPERFORMS)
    ]
    reversed_claims = [
        _claim(
            f"PerformanceClaim:r{k}",
            f"method{k} loses to rival{k}",
            f"method{k} is outperformed by rival{k}.",
            source_path="docs/right.md",
        )
        for k in range(outperformed_by)
    ]
    return unmarked + outperforms + reversed_claims


class _CallCounter:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> None:
        self.calls = 0
        real = getattr(module, name)

        def counted(*args: Any) -> Any:
            self.calls += 1
            return real(*args)

        monkeypatch.setattr(module, name, counted)


def test_detect_returns_nothing_without_a_reversed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The production shape: thousands of ``outperforms`` claims and not one
    # ``is outperformed by`` claim. No pair can exist, so nothing is compared.
    graph = ResearchGraph(nodes=_scale_claims(outperformed_by=0), edges=[])
    texts = _CallCounter(monkeypatch, contradiction, "_node_text")
    tokenized = _CallCounter(monkeypatch, contradiction, "_topic_tokens")
    started = time.monotonic()
    assert detect_contradicting_pairs(graph) == []
    assert time.monotonic() - started < 5
    assert texts.calls == _UNMARKED + _OUTPERFORMS
    assert tokenized.calls == 0


def test_detect_compares_only_marked_claims_at_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = ResearchGraph(nodes=_scale_claims(outperformed_by=5), edges=[])
    texts = _CallCounter(monkeypatch, contradiction, "_node_text")
    tokenized = _CallCounter(monkeypatch, contradiction, "_topic_tokens")
    started = time.monotonic()
    pairs = detect_contradicting_pairs(graph)
    # The every-pair scan needs ~1.4 billion comparisons here.
    assert time.monotonic() - started < 5
    assert [(l.id, r.id) for l, r in pairs] == [
        (f"PerformanceClaim:l{k:04d}", f"PerformanceClaim:r{k}") for k in range(5)
    ]
    assert texts.calls == _UNMARKED + _OUTPERFORMS + 5
    assert tokenized.calls == _OUTPERFORMS + 5


def test_lint_compares_only_marked_claims_at_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linter = WikiLinter("/nonexistent")
    for reversed_count in (0, 5):
        nodes_by_id = {
            n.id: _node_dict(n) for n in _scale_claims(outperformed_by=reversed_count)
        }
        texts = _CallCounter(monkeypatch, lint_module, "_claim_text")
        tokenized = _CallCounter(monkeypatch, lint_module, "_topic_tokens")
        started = time.monotonic()
        findings = list(linter._check_contradicting_claims(nodes_by_id, []))
        # The old scan rebuilt claim text ~80 million times here.
        assert time.monotonic() - started < 5
        assert [f.node_id for f in findings] == [
            f"PerformanceClaim:l{k:04d}" for k in range(reversed_count)
        ]
        assert texts.calls == len(nodes_by_id)
        assert tokenized.calls == (_OUTPERFORMS + reversed_count if reversed_count else 0)
        monkeypatch.undo()
