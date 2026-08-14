"""The shared blocking layer both pairwise passes now run through.

Two passes compare nodes pairwise — canonicalization's review builder and
memory.supersede's candidate generator — and until this layer existed only
one of them was bounded. What is pinned here is the part that makes a bounded
pass safe to put in front of a compile: the cap truncates by SORTED ID, so a
capped run does not depend on input ordering, and the caller's tokenizer
decides which pairs survive, so a pass can block losslessly against its own
scorer.
"""

from __future__ import annotations

from typing import List

from tesserae.blocking import DEFAULT_MAX_BLOCK, blocked_pairs
from tesserae.canonicalization import _block_tokens
from tesserae.memory.supersede import _tokenise
from tesserae.research_graph import ResearchNode, ResearchNodeType


def _concept(node_id: str, name: str) -> ResearchNode:
    return ResearchNode(
        id=node_id, name=name, type=ResearchNodeType.METHODOLOGICAL_CONCEPT
    )


def _ids(pairs) -> List[tuple]:
    return [(a.id, b.id) for a, b in pairs]


def test_only_token_sharing_pairs_survive():
    nodes = [
        _concept("C:a", "gaussian splatting"),
        _concept("C:b", "gaussian rendering"),
        _concept("C:c", "novel view synthesis"),
    ]
    out = blocked_pairs(nodes, tokenizer=_block_tokens)
    assert _ids(out.pairs) == [("C:a", "C:b")]
    assert out.capped_blocks == 0


def test_pairs_are_id_ordered_and_deduplicated():
    """A pair sharing several tokens appears once, in ``(lo.id, hi.id)`` form —
    the total order a caller can append derived edges in without the artifact
    depending on dict iteration."""
    nodes = [
        _concept("C:z", "gaussian splatting radiance"),
        _concept("C:a", "gaussian splatting"),
    ]
    out = blocked_pairs(nodes, tokenizer=_block_tokens)
    assert _ids(out.pairs) == [("C:a", "C:z")]


def test_cross_type_pairs_are_never_generated():
    nodes = [
        _concept("C:a", "gaussian splatting"),
        ResearchNode(id="Task:a", name="gaussian splatting", type=ResearchNodeType.TASK),
    ]
    assert blocked_pairs(nodes, tokenizer=_block_tokens).pairs == []


def test_cap_truncates_by_sorted_id_not_arrival_order():
    """The determinism guarantee: two orderings of the same nodes give the
    same capped block. Truncating by iteration order instead would make a
    capped compile a function of how the nodes arrived."""
    # One token, so capped_blocks counts exactly one truncated block.
    nodes = [_concept(f"C:{i}", "sharedtoken") for i in "abcd"]
    forward = blocked_pairs(nodes, tokenizer=_block_tokens, max_block=2)
    reverse = blocked_pairs(list(reversed(nodes)), tokenizer=_block_tokens, max_block=2)

    assert _ids(forward.pairs) == [("C:a", "C:b")]
    assert _ids(reverse.pairs) == _ids(forward.pairs)
    assert forward.capped_blocks == reverse.capped_blocks == 1
    assert forward.max_block == 2


def test_uncapped_run_reports_no_cap():
    # One token, so capped_blocks counts exactly one truncated block.
    nodes = [_concept(f"C:{i}", "sharedtoken") for i in "abcd"]
    out = blocked_pairs(nodes, tokenizer=_block_tokens, max_block=DEFAULT_MAX_BLOCK)
    assert out.capped_blocks == 0
    assert len(out.pairs) == 6


def test_tokenizer_choice_decides_what_survives():
    """The contract callers must honour: a blocker coarser than its own scorer
    deletes true matches. Two-character tokens are invisible to
    canonicalization's >=3-char split and visible to supersede's Jaccard
    tokenizer, which is why the supersede pass does not reuse the split."""
    nodes = [_concept("C:a", "ab cd"), _concept("C:b", "ab cd ef")]
    assert blocked_pairs(nodes, tokenizer=_block_tokens).pairs == []
    assert _ids(blocked_pairs(nodes, tokenizer=_tokenise).pairs) == [("C:a", "C:b")]
