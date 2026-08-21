"""LoCoMo dataset — the loader, the category map, and the corpus freeze.

Offline and synthetic. Everything here can be checked without ``locomo10.json``
on disk, because the decisions being pinned are decisions about SHAPE: which
keys become a session, what a turn renders to, which categories are scorable,
and what a duplicated question is keyed on. Two cases additionally re-measure
the real file when it happens to be present, and skip when it is not — they are
the ratchet against a dataset that changed under the harness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.locomo.dataset import (
    ADVERSARIAL_CATEGORY,
    CATEGORY_NAMES,
    JUDGED_CATEGORIES,
    LocomoQuestion,
    category_counts,
    dataset_revision,
    is_malformed_evidence,
    load_conversations,
    parse_dia_ids,
    select_conversations,
)
from evals.locomo.run import DEFAULT_DATA

REAL_DATA = DEFAULT_DATA


def _payload(**overrides):
    payload = {
        "sample_id": "conv-test",
        "conversation": {
            "speaker_a": "Ada",
            "speaker_b": "Bo",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"dia_id": "D1:1", "speaker": "Ada", "text": "I bought a teal bike."},
                {"dia_id": "D1:2", "speaker": "Bo", "text": "Nice!",
                 "img_url": ["https://example.invalid/x.jpg"],
                 "blip_caption": "a photo of a bicycle",
                 "query": "teal bicycle photo"},
            ],
            # A date stamp with no dialogue beside it. The real file carries
            # these; counting them as sessions would invent documents.
            "session_9_date_time": "9:00 am on 1 June, 2023",
            "session_2": [
                {"dia_id": "D2:1", "speaker": "Ada", "text": "Rode it to work."},
            ],
        },
        "qa": [
            {"question": "What colour was the bike?", "answer": "teal",
             "evidence": ["D1:1"], "category": 4},
            {"question": "What did Ada never buy?", "evidence": ["D2:1"],
             "category": 5, "adversarial_answer": "a canoe"},
        ],
        "observation": {"should": "never be ingested"},
        "session_summary": {"should": "never be ingested"},
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, *payloads) -> Path:
    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps(list(payloads) or [_payload()]), encoding="utf-8")
    return path


def test_only_keys_with_dialogue_become_sessions(tmp_path):
    """A ``session_9_date_time`` with no ``session_9`` is not a session.

    The real file carries date stamps past the last session that has dialogue.
    Counting them would invent documents, inflate the retrieval denominator and
    lower every recall by dividing through a corpus that does not exist.
    """
    conversation = load_conversations(_write(tmp_path))[0]
    assert conversation.session_numbers == [1, 2]


def test_sessions_are_ordered_by_their_own_number(tmp_path):
    payload = _payload()
    payload["conversation"]["session_10"] = [
        {"dia_id": "D10:1", "speaker": "Ada", "text": "Ten."}]
    payload["conversation"]["session_10_date_time"] = "noon"
    conversation = load_conversations(_write(tmp_path, payload))[0]
    # Not string order — "session_10" sorts before "session_2" as text.
    assert conversation.session_numbers == [1, 2, 10]


def test_a_turn_renders_speaker_text_and_caption(tmp_path):
    conversation = load_conversations(_write(tmp_path))[0]
    first, second = conversation.sessions[0].turns
    assert first.render() == '[D1:1] Ada said, "I bought a teal bike."'
    assert second.render() == (
        '[D1:2] Bo said, "Nice!" and shared a photo of a bicycle')


def test_the_annotator_query_field_is_never_part_of_the_corpus(tmp_path):
    """``query`` is the annotator's image search string, not something said.

    A gold answer reachable only through it is a gold answer no memory system
    can legitimately retrieve, so it must not appear in any rendered turn.
    """
    conversation = load_conversations(_write(tmp_path))[0]
    rendered = "\n".join(t.render() for s in conversation.sessions for t in s.turns)
    assert "teal bicycle photo" not in rendered
    assert "example.invalid" not in rendered


def test_captions_are_in_the_corpus(tmp_path):
    """The other half of the freeze: captions are IN.

    The reference code has two paths that disagree about this, one of which
    counts zero captions because it gates on a key this release does not carry.
    Choosing is unavoidable; choosing silently is not.
    """
    conversation = load_conversations(_write(tmp_path))[0]
    rendered = "\n".join(t.render() for s in conversation.sessions for t in s.turns)
    assert "a photo of a bicycle" in rendered


def test_an_adversarial_question_has_no_gold_answer(tmp_path):
    """Its gold behaviour is to decline, so the gold list is empty.

    Returning ``["None"]`` would credit a system that answered the word "none"
    and refuse credit to one that correctly said it did not know.
    """
    conversation = load_conversations(_write(tmp_path))[0]
    adversarial = conversation.questions[1]
    assert adversarial.is_adversarial
    assert adversarial.gold_answers() == []
    assert adversarial.adversarial_answer == "a canoe"


def test_the_distractor_is_never_returned_as_gold(tmp_path):
    conversation = load_conversations(_write(tmp_path))[0]
    assert "a canoe" not in conversation.questions[1].gold_answers()


def test_the_adversarial_category_is_outside_the_judged_denominator():
    assert ADVERSARIAL_CATEGORY not in JUDGED_CATEGORIES
    assert set(JUDGED_CATEGORIES) | {ADVERSARIAL_CATEGORY} == set(CATEGORY_NAMES)


def test_the_category_map_is_the_published_one():
    """Derived from the reference harness's own branch comments, not guessed."""
    assert CATEGORY_NAMES == {
        1: "multi-hop", 2: "temporal", 3: "open-domain",
        4: "single-hop", 5: "adversarial",
    }


def test_the_semicolon_rule_is_opt_in_and_takes_the_first_clause():
    question = LocomoQuestion(
        question="q", category=3, evidence=["D1:1"], conversation="c",
        answer="a bakery; a florist",
    )
    assert question.gold_answers() == ["a bakery; a florist"]
    assert question.gold_answers(split_semicolon=True) == ["a bakery"]


def test_the_semicolon_rule_does_not_touch_other_categories():
    question = LocomoQuestion(
        question="q", category=1, evidence=["D1:1"], conversation="c",
        answer="a bakery; a florist",
    )
    assert question.gold_answers(split_semicolon=True) == ["a bakery; a florist"]


@pytest.mark.parametrize(
    "evidence, expected",
    [
        ("D1:3", [(1, 3)]),
        ("D8:6; D9:17", [(8, 6), (9, 17)]),
        ("D9:1 D4:4 D4:6", [(9, 1), (4, 4), (4, 6)]),
        ("D:11:26", []),
        ("D", []),
        ("", []),
    ],
)
def test_evidence_parsing_recovers_what_it_can_and_no_more(evidence, expected):
    """Four of the six malformed strings in the real file recover; two do not.

    ``"D"`` names no turn and ``"D:11:26"`` names two possible turns, and
    inventing either would attribute a gold answer to a document the parser
    chose.
    """
    assert parse_dia_ids(evidence) == expected


@pytest.mark.parametrize("evidence", ["D8:6; D9:17", "D", "D:11:26", "D9:1 D4:4"])
def test_repaired_annotations_are_flagged_as_malformed(evidence):
    assert is_malformed_evidence(evidence)


def test_a_clean_annotation_is_not_flagged():
    assert not is_malformed_evidence("D1:3")
    # Surrounding whitespace is not an annotation error worth counting.
    assert not is_malformed_evidence(" D1:3 ")


def test_an_ambiguous_id_is_left_unread_rather_than_guessed():
    """``D:11:26`` could be D11:26 or D1:126, so it resolves to nothing.

    Picking one would repair an answer key by guessing, and the guess would be
    indistinguishable from an annotation in the report.
    """
    assert parse_dia_ids("D:11:26") == []
    assert is_malformed_evidence("D:11:26")


def test_selecting_an_unknown_conversation_refuses(tmp_path):
    conversations = load_conversations(_write(tmp_path))
    with pytest.raises(KeyError):
        select_conversations(conversations, ["conv-99"])


def test_selecting_nothing_returns_everything(tmp_path):
    conversations = load_conversations(_write(tmp_path))
    assert select_conversations(conversations, None) == conversations


def test_an_empty_file_raises_rather_than_scoring_nothing(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_conversations(path)


def test_a_file_with_no_dialogue_raises(tmp_path):
    path = tmp_path / "shapeless.json"
    path.write_text(json.dumps([{"sample_id": "x", "conversation": {}, "qa": []}]),
                    encoding="utf-8")
    with pytest.raises(ValueError):
        load_conversations(path)


def test_the_revision_is_a_digest_of_the_bytes(tmp_path):
    """A declared revision must be an observation, not a version string.

    Two files with different content must not declare the same revision, or
    ``--score`` would happily re-grade answers against a changed answer key.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _write(tmp_path / "a", _payload())
    changed = _payload()
    changed["qa"][0]["answer"] = "crimson"
    second = _write(tmp_path / "b", changed)
    assert dataset_revision(first).startswith("sha256:")
    assert dataset_revision(first) == dataset_revision(first)
    assert dataset_revision(first) != dataset_revision(second)


# --------------------------------------------------------------------------
# The ratchet against the real file. Skips when it is not on this machine.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_DATA.is_file(), reason="locomo10.json not on disk")
def test_the_real_file_still_holds_what_was_measured():
    """Counts measured directly from ``locomo10.json`` during this build.

    A ratchet, not decoration: every denominator in the report — 1,986 total,
    1,540 scorable, 446 adversarial — is derived from these, and a dataset that
    changed under the harness would move all of them silently.
    """
    conversations = load_conversations(REAL_DATA)
    assert len(conversations) == 10
    assert sum(len(c.sessions) for c in conversations) == 272
    assert sum(c.n_turns for c in conversations) == 5882
    assert sum(len(c.questions) for c in conversations) == 1986
    assert category_counts(conversations) == {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}
    scorable = sum(1 for c in conversations for q in c.questions
                   if q.category in JUDGED_CATEGORIES)
    assert scorable == 1540


@pytest.mark.skipif(not REAL_DATA.is_file(), reason="locomo10.json not on disk")
def test_almost_every_adversarial_question_carries_no_gold_answer():
    """444 of 446 — which is why the judged denominator is 1,540 and not 1,986."""
    conversations = load_conversations(REAL_DATA)
    adversarial = [q for c in conversations for q in c.questions if q.is_adversarial]
    assert len(adversarial) == 446
    assert sum(1 for q in adversarial if q.answer is None) == 444


@pytest.mark.skipif(not REAL_DATA.is_file(), reason="locomo10.json not on disk")
def test_every_size_quoted_in_a_docstring_is_reproduced_here():
    """The reproduction script for the numbers this package's docstrings cite.

    Numbers from a one-off script must not live in a docstring: the script gets
    deleted, the docstring does not, and nobody can tell later whether a figure
    was measured or remembered. So the script IS this test, and it fails if the
    figures stop being true.
    """
    import statistics

    from evals.locomo.adapter import render_session

    conversations = load_conversations(REAL_DATA)
    sessions = [s for c in conversations for s in c.sessions]

    text_chars = sorted(s.chars for s in sessions)
    assert round(statistics.mean(text_chars)) == 2922
    assert statistics.median(text_chars) == 2670.5
    assert (min(text_chars), max(text_chars)) == (1179, 6008)

    staged = sorted(len(render_session(s)) for s in sessions)
    assert round(statistics.mean(staged)) == 3553
    assert statistics.median(staged) == 3247
    assert (min(staged), max(staged)) == (1558, 7275)
    # The claim EVIDENCE_SOURCE_CHARS = 8,000 rests on: no document is larger.
    assert max(staged) < 8_000

    whole = sorted(sum(len(render_session(s)) for s in c.sessions)
                   for c in conversations)
    assert (min(whole), max(whole)) == (57807, 116077)
    from tesserae.llm_chunking import CHUNK_CHAR_BUDGET
    assert min(whole) > CHUNK_CHAR_BUDGET   # all ten would be split

    captioned = sum(1 for s in sessions for t in s.turns if t.blip_caption)
    assert captioned == 1226

    per_conversation = [len(c.sessions) for c in conversations]
    assert (min(per_conversation), max(per_conversation)) == (19, 32)
    turns = [c.n_turns for c in conversations]
    assert (min(turns), max(turns)) == (369, 689)
    questions = [len(c.questions) for c in conversations]
    assert (min(questions), max(questions)) == (105, 260)


@pytest.mark.skipif(not REAL_DATA.is_file(), reason="locomo10.json not on disk")
def test_the_duplicate_questions_are_still_there():
    """Twelve repeats, one of them under two contradictory categories.

    This is why ``scoring.question_key`` keys on position rather than text.
    """
    from collections import Counter

    conversations = load_conversations(REAL_DATA)
    repeats = 0
    contradictory = []
    for conversation in conversations:
        counts = Counter(q.question.strip() for q in conversation.questions)
        for text, count in counts.items():
            if count == 1:
                continue
            repeats += count - 1
            categories = {q.category for q in conversation.questions
                          if q.question.strip() == text}
            if len(categories) > 1:
                contradictory.append((conversation.sample_id, text, categories))
    assert repeats == 12
    assert contradictory == [
        ("conv-30", "What did Gina receive from a dance contest?", {4, 5})]
