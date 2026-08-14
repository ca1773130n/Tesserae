"""One blocking layer for every pairwise pass over graph nodes.

Two passes in this package compare nodes pairwise: canonicalization's
string-similarity review builder (:meth:`GraphCanonicalizer._build_review_items`)
and the supersede pass's candidate generator
(:func:`tesserae.memory.supersede._candidate_pairs`). Both are quadratic in
the set they scan, and until this module existed only the first one blocked —
the second compared every pair in a finding group with no bound at all, which
is exactly the shape the ``neo4j-graphrag`` resolvers are criticized for.

Two properties make the shared layer safe to depend on:

* **The cap truncates by sorted id, never by iteration order.** A capped pass
  that dropped whichever nodes happened to arrive last would make a compile a
  function of input ordering, and ``graph.json`` would stop being reproducible.
* **The caller supplies the tokenizer, and it must be at least as permissive
  as the tokenizer its own scorer uses.** Blocking drops every pair that
  shares no token, so a blocker coarser than its scorer silently deletes true
  matches. That is why the supersede pass hands in its own Jaccard tokenizer
  instead of reusing canonicalization's three-character name split: Jaccard
  counts two-character tokens, so the split would drop pairs the scorer would
  have accepted.

Type scoping is structural rather than a post-filter: blocks are keyed on
``(node type, token)``, so a cross-type pair is never generated. This is
deliberately weaker than the merge-refusal families in
``research_graph._merge_same_type_aliased_duplicates`` — those refuse whole
types from *fusing*, and a blocker feeding a pass that only mints an edge
(supersede keeps both sides) must not inherit a refusal written for a pass
that deletes one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Sequence, Set, Tuple

from .research_graph import ResearchNode

#: Largest block a pairwise pass will scan. Inherited from
#: ``GraphCanonicalizer.max_block``, which is where the number was first
#: chosen; a block at the cap still costs ~1.1M comparisons.
DEFAULT_MAX_BLOCK = 1500


@dataclass(frozen=True)
class BlockedPairs:
    """Candidate pairs plus enough to report a cap rather than hide one.

    ``capped_blocks`` is the count of blocks truncated at ``max_block``. A
    caller that ignores it turns a silently narrowed pass into an invisible
    one, which is the failure mode this codebase keeps fixing.
    """

    pairs: List[Tuple[ResearchNode, ResearchNode]]
    max_block: int
    capped_blocks: int = 0


def _type_value(node: ResearchNode) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def blocked_pairs(
    nodes: Sequence[ResearchNode],
    *,
    tokenizer: Callable[[str], Iterable[str]],
    max_block: int = DEFAULT_MAX_BLOCK,
) -> BlockedPairs:
    """Every ``(lo, hi)`` node pair sharing a type AND at least one token.

    Pairs come back ordered by ``(lo.id, hi.id)`` with ``lo.id < hi.id``, and
    each pair appears once however many tokens the two names share. The order
    is a total one so a caller can append derived edges in it without making
    the artifact depend on dict iteration.
    """
    by_id: Dict[str, ResearchNode] = {}
    postings: Dict[Tuple[str, str], List[str]] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
        type_value = _type_value(node)
        # set(): one posting per token, so a name repeating a word does not
        # pair the node with itself.
        for token in set(tokenizer(node.name or "")):
            if not token:
                continue
            postings.setdefault((type_value, token), []).append(node.id)

    capped_blocks = 0
    seen: Set[Tuple[str, str]] = set()
    for key in sorted(postings):
        block = sorted(set(postings[key]))
        if len(block) > max_block:
            block = block[:max_block]  # truncate by sorted id, never by iteration order
            capped_blocks += 1
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                seen.add((block[i], block[j]))

    return BlockedPairs(
        pairs=[(by_id[lo], by_id[hi]) for lo, hi in sorted(seen)],
        max_block=max_block,
        capped_blocks=capped_blocks,
    )
