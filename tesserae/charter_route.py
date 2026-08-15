"""Route one task to one chartered domain — and name nothing when it cannot.

``charter.py`` DERIVES the institution; this module is the read surface that
answers "which domain does this task belong to". They are separate files on
purpose: routing needs the retrieval stack, and the derive path deliberately
imports none of it, so a charter stays computable on a machine with no
embedding backend installed.

The honesty split, which is the load-bearing half of this surface
------------------------------------------------------------------
``charter.json`` is byte-idempotent. A route is NOT, and the payload says so
in itself rather than in a document nobody opens: the embedding lane varies
with whatever ``active_embedding_backend`` resolves to on this machine, and a
domain's row carries its brief once one has been written for it.

No compile writes one yet. ``charter.materialize_domain_brief`` is the only
writer of domain briefs and it has no caller in the pipeline, so on a project
today EVERY row is cold and ``warm_rows`` is 0 — the same condition
``graph_map``'s domain cards report as ``quality: "structural"``. That is
stated here, and reported as a number in the payload, rather than sold as a
corpus that thickens on its own. ``route_quality`` gives the backend, whether
it is semantic, and how many rows were warm, for the same reason
``search_nodes`` reports ``mode``: a caller that cannot see which machinery
answered cannot tell a strong answer from a lucky one. Every card also carries
``evidence`` — ``"lexical"``, ``"semantic"`` or ``"none"`` — because a route
that rests on embedding similarity alone is exactly the one that moves when
the backend does, and flattening the two would hide it.

The failure this module exists to refuse is a confident answer to a question
it could not place. Six capability overstatements have been fixed in this
tree; a router that always names a domain would be the seventh, and it would
be the worst of them, because a wrong domain does not read as an error — it
reads as an answer. So an unroutable task returns a payload that names NO
domain anywhere: empty ``path``, null ``brief``, null ``parent``, empty
``siblings``, ``routed: false`` and a ``note`` saying which condition fired.
There is deliberately no "best candidate" or "top score" field. A caller
cannot read a guess out of a refusal that contains no guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# The charter's OWN readers, not re-derivations of them. A brief's cache key,
# a domain's member set and the root set are each decided in exactly one place
# in ``charter.py``, and a router that spells any of them out for itself passes
# every test it has while missing every file the writer wrote — which is what
# this module did with ``read_warm_summary(cache_dir, tier, slug, ...)``
# against a writer that keys on ``CharterDomain:<slug>``.
from .charter import (
    _altitude_for,
    domain_members,
    live_child_slugs,
    live_divisions,
    live_parent_slug,
    read_domain_brief,
)
from .hierarchy import _structural_summary, undirected_degrees
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType

# ``_node_text`` and ``_tokenize`` are imported rather than re-derived for the
# same reason ``vector_cache`` imports the blob codec out of the sqlite store:
# they ARE the text hybrid_search scores. A local copy would drift from the
# scorer, and the evidence gate below — which decides whether a route exists
# at all — would then be reasoning about different tokens than the ranking it
# is gating.
from .retrieval.hybrid import (
    EMBED_CANDIDATE_MIN_COSINE,
    EmbeddingBackend,
    ScoredNode,
    _node_text,
    _tokenize,
    active_embedding_backend,
    backend_is_semantic,
    hybrid_search,
)

#: Altitudes a caller may pin the descent to. ``own_altitude`` on every
#: charter record is one of these (``charter._altitude_for``), plus the
#: literal ``"auto"`` meaning "descend as far as the evidence supports".
ROUTE_ALTITUDES: Tuple[str, ...] = ("auto", "division", "department", "team")

#: The type every synthetic corpus row carries. Which one it is does not
#: matter: ``_node_text`` folds ``type.value`` into every row's text, so the
#: token is present in ALL of them and the universal-token rule in
#: :func:`_evidence_tokens` discards it before any routing decision is made.
#: Without that rule the choice WOULD matter — a task mentioning "concept"
#: would give all 780 rows one identical lexical hit, admit the whole corpus
#: and let a slug tiebreak pick the winner, which is precisely the confident
#: non-answer this module refuses.
_ROW_TYPE = ResearchNodeType.CONCEPT

#: Depth order of the three altitude labels ``charter._altitude_for`` mints: a
#: division contains departments, which contain teams. ``altitude`` is a CAP on
#: this axis, so the walk needs the ORDER and not merely equality — see
#: :func:`_altitude_depth`.
_ALTITUDE_DEPTH: Dict[str, int] = {"division": 0, "department": 1, "team": 2}


def _live_domains(charter: dict) -> Dict[str, dict]:
    """Live domains only. Tombstones are records, not destinations.

    A retired domain keeps its last brief readable so an old citation can
    degrade to "this subject was reorganised" — but routing a NEW task into
    one would hand an agent a subject the institution no longer holds.
    """
    domains = charter.get("domains")
    if not isinstance(domains, dict):
        return {}
    return {
        slug: entry
        for slug, entry in domains.items()
        if isinstance(entry, dict) and entry.get("status") == "live"
    }


def _altitude_depth(entry: dict) -> int:
    """How deep on the altitude axis ``entry`` sits.

    ``own_altitude`` is read first because it is what the charter recorded.
    When it is missing or is not one of the three labels — a hand-edited
    record, or one written by an older schema — the label is RE-DERIVED
    through ``charter._altitude_for``, the same function that minted it,
    rather than treated as "not a stopping point". A cap that only bites when
    a label happens to be spelled correctly is not a cap: it is the equality
    test this replaced, which walked such a record straight past.
    """
    label = str(entry.get("own_altitude") or "")
    if label not in _ALTITUDE_DEPTH:
        try:
            label = _altitude_for(
                int(entry.get("tier") or 1), int(entry.get("member_count") or 0)
            )
        except (TypeError, ValueError):
            label = "team"
    return _ALTITUDE_DEPTH[label]


def _warm_brief(
    charter: dict,
    slug: str,
    by_id: Dict[str, ResearchNode],
    degrees: Dict[str, int],
    cache_dir: Optional[Path],
) -> Optional[Tuple[str, str, List[str]]]:
    """``slug``'s warm brief, through ``charter.read_domain_brief``. No LLM.

    A thin wrapper over the paired reader rather than a cache read of its own,
    and the delegation is the point. This module previously called
    ``read_warm_summary(cache_dir, tier, slug, members)`` directly, spelling
    out a cache convention its writer does not use: ``materialize_domain_brief``
    keys on ``CharterDomain:<slug>`` and orders the digest members by
    ``(-degree, id)``, while this read used the bare slug and the 25
    lowest-sorting ids. Two independent mismatches, so ``warm_rows`` was
    structurally 0 and ``brief.quality`` structurally ``"structural"`` — with
    a full green suite, because every test wrote the cache the same wrong way
    the reader read it. One reader, one authority; there is nothing left here
    to drift.

    ``None`` means "no brief to serve", never "something went wrong" — an
    absent cache, a digest mismatch and an unreadable file are one outcome,
    because none of them may present drifted prose as this domain's
    description. ``route_quality.warm_rows`` makes which happened visible.
    """
    if cache_dir is None:
        return None
    return read_domain_brief(charter, slug, by_id, degrees, cache_dir=cache_dir)


def _row_node(
    slug: str,
    entry: dict,
    by_id: Dict[str, ResearchNode],
    warm: Optional[Tuple[str, str, List[str]]],
) -> ResearchNode:
    """One corpus row: a synthetic node standing for one domain.

    Synthetic because ``hybrid_search`` ranks ``ResearchNode``s and the thing
    being ranked here is a charter record, not a graph node. The row never
    touches ``graph.json`` and never enters any sidecar; the vector cache it
    may warm is keyed on ``sha256(text)`` rather than node id, so a row cannot
    collide with a real node's vector either.

    ``id`` is the slug because ``_node_text`` tokenises it — a slug carries the
    anchor's words already, so repeating them in the description would only
    double their term frequency. ``metadata`` stays empty for the opposite
    reason: ``_node_text`` renders every key and value as text, which would put
    tier numbers and member counts into the lexical corpus as if they were
    subject matter.
    """
    anchor_id = str(entry.get("anchor_id") or "")
    anchor = by_id.get(anchor_id)
    name = anchor.name if anchor is not None else slug.replace("-", " ")
    description = ""
    if warm is not None:
        title, summary, tags = warm
        description = " ".join(part for part in (title, summary, " ".join(tags)) if part)
    return ResearchNode(id=slug, name=name, type=_ROW_TYPE, description=description)


def _universal_tokens(rows: Sequence[ResearchNode]) -> Set[str]:
    """Tokens every row carries, which therefore say nothing about where a
    task belongs.

    ``_node_text`` folds each row's node type into its text, so at minimum the
    type name is universal; a corpus can acquire others (a project whose every
    anchor name shares a word). BM25 already discounts them to ~zero IDF, but
    the LEXICAL lane is a substring test with no such discount: a task
    containing one universal token scores a hit on all 780 rows, every row is
    admitted, the fused scores are indistinguishable and a slug tiebreak picks
    a winner. That is a route with no evidence behind it, reported exactly
    like one with evidence — the failure this module exists to refuse.

    With fewer than two rows the notion is vacuous (every token is universal
    in a one-row corpus, which would refuse every route into a
    single-division charter), so the rule stands down.
    """
    if len(rows) < 2:
        return set()
    shared: Optional[Set[str]] = None
    for row in rows:
        tokens = set(_tokenize(_node_text(row)))
        shared = tokens if shared is None else (shared & tokens)
        if not shared:
            return set()
    return shared or set()


def _evidence_tokens(
    row: ResearchNode, query_tokens: Set[str], universal: Set[str]
) -> Set[str]:
    """Query tokens this row carries that actually discriminate."""
    return (query_tokens & set(_tokenize(_node_text(row)))) - universal


def _evidence_kind(
    item: ScoredNode, query_tokens: Set[str], universal: Set[str], semantic: bool
) -> str:
    """``"lexical"``, ``"semantic"`` or ``"none"`` — what put this row here.

    Strictly narrower than ``hybrid_search``'s own candidate gate, and
    deliberately so: that gate admits a row on a lexical hit of ANY kind,
    including one on a token shared by the whole corpus. Semantic evidence is
    accepted on its own terms — a real backend clearing
    ``EMBED_CANDIDATE_MIN_COSINE`` is the paraphrase case, and refusing it
    would make this router worse than the search it is built on. The hash stub
    is not semantic and gets no such credit.

    Which of the two it was is REPORTED rather than flattened, because they
    are not equally durable. Measured on the live 780-row charter: the
    one-word task "concept" is refused outright on the hash stub and routes on
    model2vec, where 238 rows clear the cosine floor. That route is not wrong
    — the floor is this repo's own calibrated judgement of "genuinely related
    on semantic evidence alone" — but it rests entirely on the lane that moves
    with the machine, and a caller reading ``evidence: "semantic"`` alongside
    ``evidenced_rows: 238`` can see a diffuse match for what it is. Inventing
    a second threshold to refuse it here would be minting a number the design
    never specified, which is the failure mode the re-scope roadmap names for
    CH-06.
    """
    if _evidence_tokens(item.node, query_tokens, universal):
        return "lexical"
    if semantic and item.per_lane.get("embedding", 0.0) >= EMBED_CANDIDATE_MIN_COSINE:
        return "semantic"
    return "none"


def _unrouted(note: str, altitude: str, quality: Dict[str, object]) -> Dict[str, object]:
    """The refusal shape. It names no domain, and that is the whole point.

    Every field a caller would read a destination out of is empty or null, so
    "could not place this task" cannot be misread as "placed it weakly". A
    ``candidates`` or ``best_score`` field here would undo the entire module:
    an agent under budget pressure reads the top of any list it is given.
    """
    quality = dict(quality)
    quality["altitude"] = altitude
    quality["altitude_reached"] = None
    quality["evidence"] = None
    return {
        "routed": False,
        "path": [],
        "brief": None,
        "parent": None,
        "siblings": [],
        "note": note,
        "route_quality": quality,
    }


def _card(
    slug: str,
    entry: dict,
    by_id: Dict[str, ResearchNode],
    score: float,
    evidence: str = "none",
) -> Dict[str, object]:
    anchor_id = str(entry.get("anchor_id") or "")
    anchor = by_id.get(anchor_id)
    return {
        "slug": slug,
        "tier": entry.get("tier"),
        "altitude": entry.get("own_altitude"),
        "anchor": anchor.name if anchor is not None else None,
        "member_count": entry.get("member_count"),
        "child_count": len(entry.get("child_slugs") or []),
        "unsplittable": bool(entry.get("unsplittable")),
        # This row's OWN fused score. Zero is meaningful and common on a
        # router: it means the task matched nothing in this domain's own text
        # and the descent came through it because the evidence is below it.
        # Comparable only inside one call — RRF ranks documents against each
        # other, it does not calibrate a confidence, and treating it as one
        # across calls or corpora is reading a number that was never emitted.
        "rrf_score": round(float(score), 6),
        # Which lane put this row here: "lexical" survives a backend change,
        # "semantic" does not, and "none" means the walk came through this
        # domain because the evidence is somewhere below it.
        "evidence": evidence,
    }


def charter_route(
    graph: ResearchGraph,
    charter: Optional[dict],
    task: str,
    *,
    altitude: str = "auto",
    summary_cache_dir: Optional[Path] = None,
    backend: Optional[EmbeddingBackend] = None,
    vector_cache: Optional[object] = None,
) -> Dict[str, object]:
    """Place ``task`` in the chartered institution, or report that it cannot be.

    One ``hybrid_search`` over the live domain rows scores the whole
    institution at once; the descent then walks that single score table from a
    division down, choosing at each level the child whose SUBTREE carries the
    best evidence and stopping where descending would stop improving it.

    Scoring the subtree rather than the row is a departure from a literal
    "rank the divisions, then rank their children" reading, and it is there to
    close a real hole: a division's row is its slug, its anchor name and its
    summary if warm, so a task naming a leaf's subject exactly — the case a
    router is most useful for — can match no division at all and be refused
    outright while the domain that holds it sits three levels down, scored and
    unreachable. Max-over-subtree keeps the walk beam-1 and deterministic
    while letting deep evidence choose the branch that leads to it.

    ``altitude`` CAPS how deep the walk may go, on the ordered axis
    ``division < department < team``: ``"auto"`` follows the evidence, and a
    named altitude stops at the first domain sitting at that level or past it.
    Past it, because a branch need not contain every label — a division whose
    only child is a small tier-2 domain is labelled ``team``, never
    ``department`` — and a cap that only fired on an exact label match capped
    nothing at all there. ``route_quality.altitude_reached`` therefore reports
    the level the walk ACTUALLY stopped at, which may be deeper than the one
    requested when the branch skips it, and shallower when the evidence runs
    out first. What is guaranteed is the ordering: a shallower request never
    returns a deeper domain than a deeper one, and neither goes past ``auto``.
    """
    if altitude not in ROUTE_ALTITUDES:
        # Fail loudly. Silently treating an unknown altitude as "auto" would
        # make a caller's pin a no-op that looks like it worked.
        raise ValueError(
            f"unknown altitude {altitude!r}; expected one of {', '.join(ROUTE_ALTITUDES)}"
        )

    embed_backend = backend if backend is not None else active_embedding_backend()
    semantic = backend_is_semantic(embed_backend)
    quality: Dict[str, object] = {
        # Stated in the payload, not in a doc: this value is best-effort while
        # the charter it reads is byte-idempotent, and the two must not be
        # read with the same confidence.
        "best_effort": True,
        "backend": embed_backend.name,
        "semantic": semantic,
        "corpus_rows": 0,
        "warm_rows": 0,
        "evidenced_rows": 0,
        "altitude": altitude,
        "altitude_reached": None,
        # Null until a domain is actually selected; a refusal has no evidence
        # to characterise, which is the point of it being a refusal.
        "evidence": None,
    }

    if charter is None:
        return _unrouted(
            "this project has no charter — nothing to route into. Its research "
            "layer may be below the one-read bound, or it may not have been "
            "compiled yet.",
            altitude,
            quality,
        )
    domains = _live_domains(charter)
    if not domains:
        return _unrouted("the charter holds no live domains.", altitude, quality)
    if not task.strip():
        # hybrid_search short-circuits an empty query to "preserve input
        # order, score 0", which would hand back the first domain by corpus
        # position as though it had been chosen.
        return _unrouted("no task text was given, so nothing was searched.", altitude, quality)

    by_id = {node.id: node for node in graph.nodes}
    degrees = undirected_degrees(graph)
    rows: List[ResearchNode] = []
    warm_by_slug: Dict[str, Tuple[str, str, List[str]]] = {}
    for slug in sorted(domains):
        warm = _warm_brief(charter, slug, by_id, degrees, summary_cache_dir)
        if warm is not None:
            warm_by_slug[slug] = warm
        rows.append(_row_node(slug, domains[slug], by_id, warm))
    quality["corpus_rows"] = len(rows)
    quality["warm_rows"] = len(warm_by_slug)

    corpus = ResearchGraph(nodes=rows, edges=[])
    result = hybrid_search(
        corpus,
        task,
        # No slice: the descent needs every admitted row's score, and a
        # truncated table would silently make a deep branch unreachable
        # depending on how many rows happened to rank above it.
        top_k=len(rows),
        backend=embed_backend,
        vector_cache=vector_cache,
    )
    query_tokens = set(_tokenize(task))
    universal = _universal_tokens(rows)
    scores: Dict[str, float] = {}
    evidence: Dict[str, str] = {}
    for item in result.scored:
        kind = _evidence_kind(item, query_tokens, universal, semantic)
        if kind != "none":
            scores[item.node.id] = float(item.score)
            evidence[item.node.id] = kind
    quality["evidenced_rows"] = len(scores)
    if not scores:
        return _unrouted(
            "no domain carries evidence for this task. The charter was "
            "searched and nothing matched beyond terms every domain shares.",
            altitude,
            quality,
        )

    subtree = _subtree_scores(domains, scores)
    # ``live_divisions`` and not "has no parent_slug", because those two
    # disagree on exactly the domain the ENTRY POINT surfaces: a live domain
    # whose parent was retired without it. ``graph_map()`` deliberately offers
    # such a domain at the root and ``compile_context(scope='domain:<slug>')``
    # resolves it, so a router that refused it would make an agent's own
    # starting menu contain a scope routing cannot reach — and would hide that
    # domain's whole subtree from every route there is.
    roots = live_divisions(charter)
    if not roots:
        return _unrouted(
            "the charter has no root domain — every live domain names a live "
            "parent, so child_slugs/parent_slug form a cycle rather than a tree.",
            altitude,
            quality,
        )
    current = min(roots, key=lambda slug: (-subtree.get(slug, 0.0), slug))
    if subtree.get(current, 0.0) <= 0.0:
        return _unrouted(
            "evidence exists in the charter but not under any live division, "
            "so there is no path to walk.",
            altitude,
            quality,
        )

    # A CEILING on the altitude axis, not a label to match. Stop-on-equality
    # capped nothing when the requested label was absent from the chosen
    # branch: a division whose only child is a small tier-2 "team" never
    # carries "department", so --altitude department walked to the leaf and
    # could land DEEPER than --altitude team, which is incoherent for a
    # parameter whose whole job is to bound depth. ``>=`` stops at the
    # requested level or at the first level past it, whichever comes first, so
    # depth is monotone in the request and never exceeds "auto".
    ceiling = _ALTITUDE_DEPTH[altitude] if altitude != "auto" else None
    path = [current]
    while True:
        entry = domains[current]
        if ceiling is not None and _altitude_depth(entry) >= ceiling:
            break
        children = [
            child
            for child in entry.get("child_slugs") or []
            if child in domains and child not in path
        ]
        if not children:
            break
        best = min(children, key=lambda slug: (-subtree.get(slug, 0.0), slug))
        # Descend only while descending buys evidence. Equality stops the walk
        # rather than continuing it: with no child scoring above what is
        # already held, a further step would be chosen by the slug tiebreak
        # alone, which is a coin flip wearing a path's clothes.
        if subtree.get(best, 0.0) <= scores.get(current, 0.0):
            break
        current = best
        path.append(current)

    selected = domains[current]
    quality["altitude_reached"] = selected.get("own_altitude")
    quality["evidence"] = evidence.get(current, "none")
    parent_slug = path[-2] if len(path) > 1 else live_parent_slug(charter, current)
    # "What else you could have gone to" means the siblings in the tree the
    # walk actually descended, so the pool is the parent's LIVE children — or,
    # at the top, the same ``roots`` the descent started from. Deriving it from
    # a raw ``parent_slug`` equality instead would give a domain orphaned by a
    # retired parent a sibling set of its fellow orphans while the divisions it
    # was ranked against went unlisted.
    pool = live_child_slugs(charter, parent_slug) if parent_slug else roots
    # Ordered by evidence, ties on slug — NOT alphabetically. The widest router
    # on the live charter has 73 siblings (a 15 KB block); alphabetical order
    # would put the near-misses wherever their names happened to fall, so a
    # client that shows the first few would show the least informative few.
    # Nothing is dropped: a routing answer that silently truncated its own
    # alternatives would be the same overstatement one level down.
    siblings = sorted(
        (slug for slug in pool if slug != current and slug in domains),
        key=lambda slug: (-scores.get(slug, 0.0), slug),
    )
    def _make(slug: str) -> Dict[str, object]:
        return _card(
            slug,
            domains[slug],
            by_id,
            scores.get(slug, 0.0),
            evidence.get(slug, "none"),
        )

    return {
        "routed": True,
        "path": [_make(slug) for slug in path],
        "brief": _brief(
            current,
            selected,
            domain_members(charter, current),
            by_id,
            degrees,
            warm_by_slug.get(current),
        ),
        "parent": _make(parent_slug) if parent_slug in domains else None,
        "siblings": [_make(slug) for slug in siblings],
        "route_quality": quality,
    }


def _subtree_scores(
    domains: Dict[str, dict], scores: Dict[str, float]
) -> Dict[str, float]:
    """Best evidence anywhere in each domain's subtree, itself included.

    Iterative and cycle-guarded rather than recursive: a hand-edited
    ``child_slugs`` cycle must not turn a read of ``charter.json`` into a
    RecursionError, and the live charter is four tiers deep so a stack is
    cheap.
    """
    out: Dict[str, float] = {}
    for slug in domains:
        best = scores.get(slug, 0.0)
        stack = list(domains[slug].get("child_slugs") or [])
        seen: Set[str] = {slug}
        while stack:
            current = str(stack.pop())
            if current in seen or current not in domains:
                continue
            seen.add(current)
            best = max(best, scores.get(current, 0.0))
            stack.extend(str(child) for child in domains[current].get("child_slugs") or [])
        out[slug] = best
    return out


def _brief(
    slug: str,
    entry: dict,
    members: Sequence[str],
    by_id: Dict[str, ResearchNode],
    degrees: Dict[str, int],
    warm: Optional[Tuple[str, str, List[str]]],
) -> Dict[str, object]:
    """What the routed domain is about, at whatever quality is actually available.

    ``quality`` carries the same meaning it does on a ``graph_map`` card and
    is reported for the same reason: a structural floor is an honest answer,
    and a caller must be able to tell it from prose an LLM wrote.
    """
    if warm is not None:
        title, summary, tags = warm
        quality = "llm"
    else:
        title, summary, tags = _structural_summary(members, by_id, degrees)
        quality = "structural"
    return {
        "slug": slug,
        "title": title,
        "summary": summary,
        "tags": list(tags),
        "quality": quality,
        "member_count": entry.get("member_count"),
    }
