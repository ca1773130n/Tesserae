"""Tests for :mod:`llm_wiki.site.auto_link` — the HTML auto-linker.

The tests build small ad-hoc :class:`AutoLinker` instances directly (no
``SiteContext`` round-trip required) so the assertions exercise the parser
+ wrapper rules without depending on the rest of the site stack.
"""

from __future__ import annotations

import re

import pytest

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.site.auto_link import AutoLinker, LinkTarget
from llm_wiki.site.pages import SiteContext


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _linker(*entries: tuple[str, str, str, str]) -> AutoLinker:
    """Build a deterministic AutoLinker from ``(name, kind, node_id, href)``
    tuples.

    Bypasses ``from_context`` so each test stays focused on a tiny target
    list, but uses the same internal class so the wrapper / scanner logic
    being exercised is the production code path.
    """
    targets = []
    for name, kind, node_id, href in entries:
        targets.append((name, LinkTarget(href=href, kind=kind, node_id=node_id, title=name)))
    # Sort longest-first, ties alpha — same rule as ``from_context``.
    targets.sort(key=lambda kv: (-len(kv[0]), kv[0].casefold()))
    by_lower: dict[str, LinkTarget] = {}
    for k, t in targets:
        by_lower.setdefault(k.casefold(), t)
    return AutoLinker(targets=tuple(targets), _by_lower=by_lower)


# ---------------------------------------------------------------------------
# basic linking
# ---------------------------------------------------------------------------


def test_two_distinct_names_each_link_once() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gaussian-splatting.html"),
        ("Mip-NeRF360", "concepts", "n2", "concepts/mip-nerf360.html"),
    )
    out = linker.linkify(
        "<p>We rely on Gaussian Splatting and Mip-NeRF360.</p>"
    )
    # Both names get wrapped.
    assert out.count('class="auto-link"') == 2
    assert 'href="concepts/gaussian-splatting.html"' in out
    assert 'href="concepts/mip-nerf360.html"' in out


def test_inside_existing_anchor_is_not_relinked() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gaussian-splatting.html"),
    )
    body = (
        '<p>We use <a href="papers/foo.html">Gaussian Splatting</a> daily.</p>'
    )
    out = linker.linkify(body)
    # The pre-existing anchor must be preserved verbatim.
    assert 'href="papers/foo.html">Gaussian Splatting</a>' in out
    # And no auto-link wrapper inside or around it.
    assert "auto-link" not in out


def test_inside_code_is_not_linked() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gaussian-splatting.html"),
    )
    out = linker.linkify("<p><code>Gaussian Splatting</code></p>")
    assert "auto-link" not in out
    assert "<code>Gaussian Splatting</code>" in out


def test_inside_pre_is_not_linked() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gaussian-splatting.html"),
    )
    out = linker.linkify("<pre>Gaussian Splatting fits here</pre>")
    assert "auto-link" not in out


def test_inside_h1_h2_h3_is_not_linked() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gaussian-splatting.html"),
    )
    for tag in ("h1", "h2", "h3"):
        out = linker.linkify(f"<{tag}>Gaussian Splatting overview</{tag}>")
        assert "auto-link" not in out, f"<{tag}> body must not be auto-linked"


def test_longest_match_beats_shorter_overlap() -> None:
    """``Gaussian Splatting Goes Consumer`` must beat ``Gaussian Splatting``
    when both are candidates (greedy longest match)."""
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gs.html"),
        ("Gaussian Splatting Goes Consumer", "papers", "n2", "papers/gsgc.html"),
    )
    out = linker.linkify(
        "<p>Today we discuss Gaussian Splatting Goes Consumer in detail.</p>"
    )
    # Long target wins.
    assert 'href="papers/gsgc.html"' in out
    assert "Gaussian Splatting Goes Consumer</a>" in out
    # And the shorter target was NOT applied (its node_id wasn't used).
    assert 'href="concepts/gs.html"' not in out


def test_per_page_cap_links_first_occurrence_only() -> None:
    """Three repeats of one node name yield a single auto-link wrapper."""
    linker = _linker(
        ("Algorithm", "concepts", "n1", "concepts/algorithm.html"),
    )
    body = (
        "<p>Algorithm one. Algorithm two. Algorithm three.</p>"
    )
    out = linker.linkify(body)
    assert out.count('class="auto-link"') == 1
    assert out.count("Algorithm</a>") == 1
    # The other two occurrences survive as plain text. Counts: 1 in the
    # title attribute, 1 inside the wrapper, 2 unwrapped trailing
    # mentions.
    assert out.count("Algorithm") == 4


def test_excluded_node_ids_skipped() -> None:
    """A page about node X must not auto-link its own name."""
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gs.html"),
    )
    out = linker.linkify(
        "<p>This page is about Gaussian Splatting itself.</p>",
        exclude_node_ids={"n1"},
    )
    assert "auto-link" not in out


def test_word_boundary_does_not_match_inside_other_words() -> None:
    """``Algorithm`` must not match inside ``Algorithms`` or ``MetaAlgorithm``."""
    # ``Algorithm`` (canonical) vs surrounding word characters.
    linker = _linker(
        ("Algorithm", "concepts", "n1", "concepts/algorithm.html"),
    )
    body = "<p>Algorithms are not Algorithm. MetaAlgorithm neither.</p>"
    out = linker.linkify(body)
    # Only the bare ``Algorithm`` token should match.
    assert out.count('class="auto-link"') == 1


def test_match_is_case_insensitive_but_preserves_original_casing() -> None:
    linker = _linker(
        ("Gaussian Splatting", "concepts", "n1", "concepts/gs.html"),
    )
    out = linker.linkify("<p>Some folks write gaussian splatting in lowercase.</p>")
    # We DO link the lowercase form (case-insensitive match).
    assert "auto-link" in out
    # The wrapped text preserves the user's original casing.
    assert ">gaussian splatting</a>" in out


def test_depth_prefix_adjusts_href() -> None:
    linker = _linker(
        ("Algo", "concepts", "n1", "concepts/algo.html"),
    )
    out = linker.linkify("<p>Use Algo here.</p>", depth=2)
    assert 'href="../../concepts/algo.html"' in out


def test_short_names_are_not_registered() -> None:
    """Two-letter names like ``GS`` would auto-link too aggressively, so
    the registration step skips them."""
    # ``from_context`` skips names < 3 chars; the helper here mirrors the
    # production pipeline by filtering up front.
    linker = _linker(
        ("GS", "concepts", "n1", "concepts/gs.html"),
        ("Diffusion", "concepts", "n2", "concepts/diffusion.html"),
    )
    # We bypassed the ``from_context`` filter on purpose so we can prove
    # the LINKIFIER itself doesn't blow up — but the production filter
    # below in ``test_from_context_skips_short_names`` covers the actual
    # behaviour.
    out = linker.linkify("<p>GS uses Diffusion at scale.</p>")
    # Diffusion still resolves; GS link presence is incidental.
    assert 'href="concepts/diffusion.html"' in out


def test_cjk_word_match_works_for_korean_alongside_ascii() -> None:
    """ASCII + Hangul keys both match using the unicode word-boundary."""
    linker = _linker(
        ("4D Gaussian Splatting", "concepts", "n1", "concepts/4d-gs.html"),
        ("적용 사례", "concepts", "n2", "concepts/eg.html"),
    )
    body = "<p>4D Gaussian Splatting 적용 사례 are described here.</p>"
    out = linker.linkify(body)
    assert 'href="concepts/4d-gs.html"' in out
    assert 'href="concepts/eg.html"' in out


def test_attribute_text_is_not_linked() -> None:
    """A node name appearing inside an attribute value must not be wrapped."""
    linker = _linker(
        ("Algorithm", "concepts", "n1", "concepts/algorithm.html"),
    )
    # ``alt="Algorithm here"`` sits inside an attribute and must survive
    # untouched.
    body = '<p><img alt="Algorithm here" src="x.png"> The Algorithm wins.</p>'
    out = linker.linkify(body)
    # The attribute must be preserved byte-for-byte.
    assert 'alt="Algorithm here"' in out
    # And only the body-text occurrence gets wrapped.
    assert out.count('class="auto-link"') == 1


# ---------------------------------------------------------------------------
# integration: from_context()
# ---------------------------------------------------------------------------


def _ctx_with_concept(name: str) -> SiteContext:
    node = ResearchNode(
        id=f"Concept:{name}",
        name=name,
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(nodes=[node], edges=[])
    return SiteContext.build(
        graph=graph,
        wiki_pages_by_kind={
            "sources": [], "concepts": [], "entities": [], "papers": [],
            "repos": [], "topics": [], "syntheses": [], "questions": [],
        },
    )


def test_from_context_registers_concept_nodes() -> None:
    ctx = _ctx_with_concept("Gaussian Splatting")
    linker = AutoLinker.from_context(ctx)
    keys = [k for k, _ in linker.targets]
    assert "Gaussian Splatting" in keys


def test_from_context_skips_short_names() -> None:
    """Names shorter than 3 chars are filtered to avoid over-linking."""
    ctx = _ctx_with_concept("GS")
    linker = AutoLinker.from_context(ctx)
    keys = [k for k, _ in linker.targets]
    assert "GS" not in keys


def test_from_context_includes_aliases() -> None:
    node = ResearchNode(
        id="Concept:gs",
        name="Gaussian Splatting",
        type=ResearchNodeType.CONCEPT,
        aliases=["3D Gaussian Splatting", "GSplats"],
    )
    graph = ResearchGraph(nodes=[node], edges=[])
    ctx = SiteContext.build(
        graph=graph,
        wiki_pages_by_kind={
            "sources": [], "concepts": [], "entities": [], "papers": [],
            "repos": [], "topics": [], "syntheses": [], "questions": [],
        },
    )
    linker = AutoLinker.from_context(ctx)
    keys = [k for k, _ in linker.targets]
    assert "Gaussian Splatting" in keys
    assert "3D Gaussian Splatting" in keys
    assert "GSplats" in keys


def test_linkify_idempotent_under_repeat() -> None:
    """Running ``linkify`` twice on the same input must yield the same output
    on the FIRST pass — and must not double-wrap on the second pass."""
    linker = _linker(
        ("Algorithm", "concepts", "n1", "concepts/algorithm.html"),
    )
    once = linker.linkify("<p>Algorithm one.</p>")
    twice = linker.linkify(once)
    # The wrapper inside the auto-link is itself an <a>, which is in the
    # skip set: a re-run must not nest.
    assert twice.count('class="auto-link"') == 1


def test_walker_preserves_html_comments_and_doctype() -> None:
    linker = _linker(
        ("Algorithm", "concepts", "n1", "concepts/algorithm.html"),
    )
    body = (
        "<!-- comment with Algorithm -->"
        "<p>Algorithm here.</p>"
        "<!--Algorithm again-->"
    )
    out = linker.linkify(body)
    # Comments stay intact.
    assert "<!-- comment with Algorithm -->" in out
    assert "<!--Algorithm again-->" in out
    # Body text gets one wrapper.
    assert out.count('class="auto-link"') == 1
