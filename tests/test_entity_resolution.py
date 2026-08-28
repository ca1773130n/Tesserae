"""Entity resolution merges spellings, never meanings.

The guard that matters is specificity: `verify_claim`'s whole claim is that it
never asserts support the corpus does not make. Merging two DIFFERENT entities
into one node would manufacture edges nobody asserted, which is a false
SUPPORTED. Measured across thresholds 0.98 down to 0.80 on a 148-paper corpus,
specificity held at 1.000 — but these tests pin the properties that make that
true rather than trusting one corpus.
"""

from __future__ import annotations

import pytest

from tesserae.entity_resolution import (DEFAULT_SIMILARITY, ENTITY_TYPES,
                                        resolve_entities)
from tesserae.research_graph import (ResearchEdge, ResearchGraph, ResearchNode,
                                     ResearchNodeType)


class _Backend:
    """Deterministic stand-in: identical names are identical vectors, and a
    name containing 'other' is orthogonal to everything else."""

    def embed(self, texts):
        out = []
        for t in texts:
            base = [1.0, 0.0, 0.0]
            if "other" in t.lower():
                base = [0.0, 1.0, 0.0]
            out.append(base)
        return out


def _n(nid, name, typ="Dataset", aliases=()):
    return ResearchNode(id=nid, name=name, type=ResearchNodeType(typ),
                        aliases=list(aliases))


def test_two_spellings_of_one_entity_become_one_node():
    g, merged = resolve_entities(
        ResearchGraph(nodes=[_n("Dataset:a", "ImageNet corpus"),
                             _n("Dataset:b", "ImageNet corpora")], edges=[]),
        backend=_Backend(), threshold=0.9)
    assert merged == 1 and len(g.nodes) == 1


def test_the_merged_name_survives_as_an_alias():
    """Dropping it was measured to make things worse: a query spelling the
    merged-away name stopped resolving, so refusals ROSE from 226 to 254."""
    g, _ = resolve_entities(
        ResearchGraph(nodes=[_n("Dataset:a", "ImageNet corpus"),
                             _n("Dataset:b", "ImageNet corpora")], edges=[]),
        backend=_Backend(), threshold=0.9)
    survivor = g.nodes[0]
    both = {survivor.name} | {str(a) for a in survivor.aliases}
    assert {"ImageNet corpus", "ImageNet corpora"} <= both


def test_unrelated_entities_are_never_merged():
    """The guarantee. A merge here would invent support for a claim the corpus
    does not make."""
    g, merged = resolve_entities(
        ResearchGraph(nodes=[_n("Dataset:a", "ImageNet corpus"),
                             _n("Dataset:b", "other corpus entirely")], edges=[]),
        backend=_Backend(), threshold=0.9)
    assert merged == 0 and len(g.nodes) == 2


def test_evidence_spans_are_not_candidates():
    """Merging two spans that read alike would destroy the provenance spans
    exist to carry."""
    assert "EvidenceSpan" not in ENTITY_TYPES
    assert "SourceDocument" not in ENTITY_TYPES
    nodes = [_n("EvidenceSpan:a", "ImageNet corpus", "EvidenceSpan"),
             _n("EvidenceSpan:b", "ImageNet corpora", "EvidenceSpan")]
    g, merged = resolve_entities(ResearchGraph(nodes=nodes, edges=[]),
                                 backend=_Backend(), threshold=0.9)
    assert merged == 0


def test_edges_are_redirected_and_self_loops_dropped():
    g, _ = resolve_entities(
        ResearchGraph(
            nodes=[_n("Dataset:a", "ImageNet corpus"), _n("Dataset:b", "ImageNet corpora"),
                   _n("Metric:f1", "f1 measure", "Metric")],
            edges=[ResearchEdge(source="Dataset:a", type="uses_metric", target="Metric:f1"),
                   ResearchEdge(source="Dataset:b", type="uses_metric", target="Metric:f1"),
                   ResearchEdge(source="Dataset:a", type="extends", target="Dataset:b")]),
        backend=_Backend(), threshold=0.9)
    assert len(g.edges) == 1, "duplicate redirected edge deduped, self-loop dropped"
    assert g.edges[0].type == "uses_metric"


def test_it_is_deterministic_under_node_order():
    """The compile is byte-idempotent; a merge depending on dict order breaks it."""
    nodes = [_n("Dataset:a", "ImageNet corpus"), _n("Dataset:b", "ImageNet corpora"),
             _n("Dataset:c", "ImageNet corpuses")]
    a, _ = resolve_entities(ResearchGraph(nodes=nodes, edges=[]),
                            backend=_Backend(), threshold=0.9)
    b, _ = resolve_entities(ResearchGraph(nodes=list(reversed(nodes)), edges=[]),
                            backend=_Backend(), threshold=0.9)
    assert [n.id for n in a.nodes] == [n.id for n in b.nodes]


def test_a_zero_threshold_disables_the_pass_entirely():
    src = ResearchGraph(nodes=[_n("Dataset:a", "ImageNet corpus"),
                               _n("Dataset:b", "ImageNet corpora")], edges=[])
    g, merged = resolve_entities(src, backend=_Backend(), threshold=0)
    assert merged == 0 and g is src, "disabled must cost nothing, not merely do nothing"


def test_a_failing_embedder_does_not_fail_the_compile():
    """A missing or broken embedder leaves the graph unresolved — the behaviour
    before this pass existed — rather than aborting a multi-hour compile."""
    class Broken:
        def embed(self, texts):
            raise RuntimeError("no model")

    src = ResearchGraph(nodes=[_n("Dataset:a", "ImageNet corpus"),
                               _n("Dataset:b", "ImageNet corpora")], edges=[])
    g, merged = resolve_entities(src, backend=Broken(), threshold=0.9)
    assert merged == 0 and g is src


def test_the_default_threshold_is_the_conservative_end_of_the_measured_curve():
    """0.85 scored highest (40/213) but at 5.7x the merges. The default takes
    71% of the gain for 18% of the merges, because the curve was measured on one
    corpus and over-merging is the failure that cannot be undone."""
    assert DEFAULT_SIMILARITY >= 0.95
