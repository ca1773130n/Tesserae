"""The durable candidate ledger: a human verdict survives the next run.

The behaviour under guard is one sentence — *a rejected pair stays rejected* —
and everything else here exists because there is a plausible implementation that
breaks it: a fresh observation overwriting a stored verdict, a prune dropping a
row whose pair stopped being surfaced, a key that carries the score or the
reason, an unrecognised status silently reading as pending.
"""

from __future__ import annotations

import json

import pytest

from tesserae.candidate_ledger import (
    CANDIDATE_LEDGER_FILENAME,
    CANDIDATE_LEDGER_SCHEMA_VERSION,
    SOURCE_EMBEDDING,
    SOURCE_TOKEN,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    STATUS_REJECTED,
    CandidateLedger,
    CandidateVerdict,
    candidate_ledger_path,
    load_candidate_ledger,
    pair_key,
    publish_candidate_ledger,
    record_decisions,
)
from tesserae.canonicalization import (
    GraphCanonicalizer,
    ReviewDecision,
    candidate_observations,
)
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

_A = "MethodologicalConcept:gs:test"
_B = "MethodologicalConcept:3d-gaussian-splatting:test"


def _ledger_file(tmp_path):
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)
    return candidate_ledger_path(tmp_path)


def _splatting_graph(description: str = "") -> ResearchGraph:
    """Two same-typed nodes sharing the token 'gaussian' — the token pass pairs them."""
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id=_A,
                name="Gaussian Splatting",
                type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                description=description,
            ),
            ResearchNode(
                id=_B,
                name="3D Gaussian Splatting",
                type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            ),
        ],
        edges=[],
    )


def _verdict(status: str, *, score: float = 0.9, source: str = SOURCE_TOKEN) -> CandidateVerdict:
    return CandidateVerdict(a=_A, b=_B, score=score, source=source, status=status)


# --------------------------------------------------------------- the headline


def test_a_rejected_pair_is_never_surfaced_again(tmp_path):
    """The whole step: "no, these are different" is asked once, not forever."""
    graph = _splatting_graph()

    first = GraphCanonicalizer().canonicalize(graph)
    # Order-insensitive on purpose: step 6 normalizes every generated pair to
    # (lo, hi) by id for determinism, and the ledger key sorts too (pair_key),
    # so which node lands on the left carries no meaning here.
    assert [tuple(sorted((i.left_node_id, i.right_node_id))) for i in first.review_items] == [
        tuple(sorted((_A, _B)))
    ]

    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, candidate_observations(first.review_items))
    queue = first.review_queue()
    decisions = [ReviewDecision(item_id=first.review_items[0].id, action="keep_separate")]
    record_decisions(path, queue.decision_verdicts(decisions), decided_by="ada")

    second = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(graph)

    assert second.review_items == []
    assert second.stats["review_rejected_suppressed"] == 1


def test_the_verdict_survives_churn_that_has_nothing_to_do_with_it(tmp_path):
    """Keyed on the node-id pair only, so unrelated edits cannot invalidate it.

    A node id is ``stable_id(type, name)``: a rewritten description, a new
    source or a different score leaves it alone. A ledger keyed on the score or
    the reason — both of which move between runs — would re-ask a question that
    had already been answered.
    """
    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.61)])
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")

    churned = _splatting_graph(description="rewritten upstream, twice, by someone else")
    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(churned)

    assert result.review_items == []


def test_a_rejected_row_is_never_pruned_when_its_pair_stops_being_surfaced(tmp_path):
    """Accumulated, not derived — the opposite of the merge ledger.

    If the row vanished the run its pair fell below threshold (or out of a block
    cap), the pair would come back UN-rejected the moment it reappeared. That is
    the failure mode, so publish is additive and prunes nothing.
    """
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")

    publish_candidate_ledger(
        path,
        [CandidateVerdict(a="Concept:x:1", b="Concept:y:2", score=0.7, source=SOURCE_EMBEDDING)],
    )

    ledger = load_candidate_ledger(tmp_path)
    assert ledger.status_for(_A, _B) == STATUS_REJECTED
    assert len(ledger) == 2


# --------------------------------------------------------------- pending drift


def test_a_pending_pair_resurfaces_carrying_its_prior_score(tmp_path):
    """Drift is visible, not silent: both the first score and today's are shown."""
    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.6100)])

    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(_splatting_graph())

    item = result.review_items[0]
    assert item.status == STATUS_PENDING
    assert item.prior_score == 0.61
    assert item.score != item.prior_score  # today's score, freshly computed
    assert "review_rejected_suppressed" not in result.stats


def test_a_first_sighting_has_no_prior_score(tmp_path):
    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(_splatting_graph())

    assert result.review_items[0].prior_score is None


def test_the_stored_score_is_the_first_one_and_never_drifts(tmp_path):
    """Re-observing a pending pair must not rewrite the score it entered at.

    Otherwise "prior score" means last run's score, drift is only ever one run
    wide, and the file churns on every backend nudge.
    """
    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.61)])
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.94)])

    assert load_candidate_ledger(tmp_path).prior_score(_A, _B) == 0.61


def test_no_ledger_leaves_todays_queue_byte_identical(tmp_path):
    """A project with no verdicts sees exactly today's queue."""
    graph = _splatting_graph()
    without = GraphCanonicalizer().canonicalize(graph)
    empty = GraphCanonicalizer(candidate_ledger=CandidateLedger()).canonicalize(graph)

    assert [i.model_dump() for i in empty.review_items] == [
        i.model_dump() for i in without.review_items
    ]


# --------------------------------------------------------------- confirmed ≠ merged


def test_a_confirmed_pair_is_surfaced_with_its_status_and_never_auto_merged(tmp_path):
    """Only the pending third state was borrowed; the auto-merge band was not.

    A stored ``confirmed`` that merged on the next run would be an auto-merge
    with extra steps — and the measurement in canonicalization.py says no cosine
    threshold earns one. ``apply_decisions`` stays the only thing that merges.
    """
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_CONFIRMED)], decided_by="ada")

    graph = _splatting_graph()
    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(graph)

    assert result.review_items[0].status == STATUS_CONFIRMED
    assert result.merged_nodes == {}
    assert {n.id for n in result.graph.nodes} == {_A, _B}


# --------------------------------------------------------------- write ordering


def test_a_stored_verdict_beats_a_fresh_observation(tmp_path):
    """The direction that matters: an observation is the question, not the answer."""
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")

    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.99)])

    record = load_candidate_ledger(tmp_path).record_for(_A, _B)
    assert record.status == STATUS_REJECTED
    assert record.decided_by == "ada"


def test_a_reviewer_can_change_their_mind(tmp_path):
    """The one write allowed to overwrite a stored row is another human verdict."""
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")
    record_decisions(path, [(_A, _B, STATUS_CONFIRMED)], decided_by="grace")

    record = load_candidate_ledger(tmp_path).record_for(_A, _B)
    assert (record.status, record.decided_by) == (STATUS_CONFIRMED, "grace")


def test_pair_key_is_order_independent(tmp_path):
    """The two passes emit endpoints in different orders; one pair, one row."""
    assert pair_key(_B, _A) == pair_key(_A, _B) == tuple(sorted((_A, _B)))

    path = _ledger_file(tmp_path)
    record_decisions(path, [(_B, _A, STATUS_REJECTED)], decided_by="ada")

    ledger = load_candidate_ledger(tmp_path)
    assert len(ledger) == 1
    assert ledger.is_rejected(_A, _B)


def test_decisions_ignore_unknown_item_ids_rather_than_failing_the_run(tmp_path):
    """apply_decisions validates a decision file; recording must not also fail it."""
    result = GraphCanonicalizer().canonicalize(_splatting_graph())
    verdicts = result.review_queue().decision_verdicts(
        [ReviewDecision(item_id="review:similar_name:nope", action="merge")]
    )
    assert verdicts == []


# --------------------------------------------------------------- attribution


def test_decided_by_is_recorded_not_guessed(tmp_path):
    """"We do not know who decided this" and "$USER decided this" are different
    claims, so an unattributed verdict says so rather than inventing an actor."""
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)])

    record = load_candidate_ledger(tmp_path).record_for(_A, _B)
    assert record.decided_by == ""
    assert record.decided_at  # a decision is always stamped


def test_a_verdict_carries_the_version_that_wrote_it(tmp_path):
    path = _ledger_file(tmp_path)
    record_decisions(
        path, [(_A, _B, STATUS_REJECTED)], decided_at="2026-08-14T00:00:00+00:00",
        tesserae_version="9.9.9",
    )

    record = load_candidate_ledger(tmp_path).record_for(_A, _B)
    assert record.tesserae_version == "9.9.9"
    assert record.decided_at == "2026-08-14T00:00:00+00:00"


def test_an_observation_carries_no_clock_and_no_actor(tmp_path):
    """A pending row is a question. Stamping it would make the file churn every
    run and would attribute an actor to something no human touched."""
    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING)])

    record = load_candidate_ledger(tmp_path).record_for(_A, _B)
    assert (record.decided_by, record.decided_at, record.tesserae_version) == ("", "", "")


def test_republishing_an_unchanged_set_is_byte_identical(tmp_path):
    path = _ledger_file(tmp_path)
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING)])
    first = path.read_bytes()
    publish_candidate_ledger(path, [_verdict(STATUS_PENDING, score=0.42)])

    assert path.read_bytes() == first


# --------------------------------------------------------------- untrusted file


def test_a_corrupt_ledger_reads_as_empty_never_raises(tmp_path):
    path = _ledger_file(tmp_path)
    path.write_text("{not json", encoding="utf-8")

    assert len(load_candidate_ledger(tmp_path)) == 0
    # ...and canonicalization still runs, with today's queue intact.
    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(_splatting_graph())
    assert len(result.review_items) == 1


def test_an_unknown_status_row_is_dropped_not_downgraded_to_pending(tmp_path):
    """A typo'd verdict must not silently re-ask a question it had answered."""
    path = _ledger_file(tmp_path)
    path.write_text(
        json.dumps(
            {
                "schema_version": CANDIDATE_LEDGER_SCHEMA_VERSION,
                "records": [
                    {"a": _A, "b": _B, "score": 0.9, "source": SOURCE_TOKEN, "status": "rejcted"}
                ],
            }
        ),
        encoding="utf-8",
    )

    ledger = load_candidate_ledger(tmp_path)
    assert len(ledger) == 0
    assert ledger.record_for(_A, _B) is None


def test_an_unrecognised_schema_version_reads_as_absent(tmp_path):
    path = _ledger_file(tmp_path)
    path.write_text(
        json.dumps({"schema_version": 999, "records": [_verdict(STATUS_REJECTED).as_json()]}),
        encoding="utf-8",
    )

    assert len(load_candidate_ledger(tmp_path)) == 0


def test_recording_an_unknown_status_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown candidate status"):
        record_decisions(_ledger_file(tmp_path), [(_A, _B, "maybe")])


def test_the_ledger_is_a_tesserae_sidecar_and_never_node_metadata(tmp_path):
    """Node metadata survives an incremental compile and vanishes on a full one —
    the leak class this repo has hit four times. The verdict lives in a file."""
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")
    assert path.name == CANDIDATE_LEDGER_FILENAME
    assert path.parent.name == ".tesserae"

    result = GraphCanonicalizer(
        candidate_ledger=load_candidate_ledger(tmp_path)
    ).canonicalize(_splatting_graph())
    for node in result.graph.nodes:
        for key in ("status", "decided_by", "decided_at", "prior_score"):
            assert key not in (node.metadata or {})


def test_candidate_observations_map_the_reason_to_a_stable_source(tmp_path):
    class _Stub:
        name = "stub-vectors"
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] if "Edwin" in t else [1.0, 0.02] for t in texts]

    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:edwin:a", name="Edwin Aldrin", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="Concept:pilot:b", name="Lunar Module Pilot", type=ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )
    result = GraphCanonicalizer(semantic=True, embedding_backend=_Stub()).canonicalize(graph)

    sources = {v.source for v in candidate_observations(result.review_items)}
    assert sources == {SOURCE_EMBEDDING}


def test_an_embedding_pair_cannot_re_enter_through_the_semantic_lane(tmp_path):
    """The filter runs AFTER both passes: the embedding pass suppresses pairs the
    token pass emitted, so filtering the token items first would let a rejected
    pair back in through the other lane."""

    class _Identical:
        name = "stub-vectors"
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _B, STATUS_REJECTED)], decided_by="ada")

    result = GraphCanonicalizer(
        semantic=True,
        embedding_backend=_Identical(),
        candidate_ledger=load_candidate_ledger(tmp_path),
    ).canonicalize(_splatting_graph())

    assert result.review_items == []


def test_a_self_pair_is_never_stored(tmp_path):
    """A pair of one is not a pair — the writer applies the reader's own rule."""
    path = _ledger_file(tmp_path)
    record_decisions(path, [(_A, _A, STATUS_REJECTED)])

    assert len(load_candidate_ledger(tmp_path)) == 0
