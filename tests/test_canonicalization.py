from tesserae.canonicalization import GraphCanonicalizer, ReviewDecision, ReviewQueue
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def test_canonicalizer_merges_alias_nodes_and_rewires_edges():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER)
    canonical = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting:canonical",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        aliases=["3DGS", "3D Gaussian Splatting"],
    )
    alias = ResearchNode(id="MethodologicalConcept:3dgs:alias", name="3DGS", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    task = ResearchNode(id="Task:novel-view-synthesis:test", name="Novel View Synthesis", type=ResearchNodeType.TASK)
    graph = ResearchGraph(
        nodes=[paper, canonical, alias, task],
        edges=[
            ResearchEdge(source=paper.id, target=alias.id, type="uses"),
            ResearchEdge(source=alias.id, target=task.id, type="addresses"),
        ],
    )

    result = GraphCanonicalizer().canonicalize(graph)

    names = [node.name for node in result.graph.nodes]
    assert names.count("Gaussian Splatting") == 1
    assert "3DGS" not in names
    gaussian = next(node for node in result.graph.nodes if node.name == "Gaussian Splatting")
    assert set(gaussian.aliases) >= {"3DGS", "3D Gaussian Splatting"}
    assert any(edge.source == paper.id and edge.target == gaussian.id and edge.type == "uses" for edge in result.graph.edges)
    assert any(edge.source == gaussian.id and edge.target == task.id and edge.type == "addresses" for edge in result.graph.edges)
    assert result.merged_nodes == {alias.id: gaussian.id}


def test_canonicalizer_creates_review_candidates_for_similar_unmerged_concepts():
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
            ResearchNode(id="MethodologicalConcept:3d-gaussian-splatting:test", name="3D Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
            ResearchNode(id="Task:nvs:test", name="Novel View Synthesis", type=ResearchNodeType.TASK),
        ],
        edges=[],
    )

    result = GraphCanonicalizer().canonicalize(graph)

    assert result.review_items
    item = result.review_items[0]
    assert item.left_name == "Gaussian Splatting"
    assert item.right_name == "3D Gaussian Splatting"
    assert item.reason == "similar_name"
    assert 0 < item.score <= 1


def test_review_queue_serializes_and_applies_merge_decisions():
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
            ResearchNode(id="MethodologicalConcept:3d-gaussian-splatting:test", name="3D Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
        ],
        edges=[ResearchEdge(source="MethodologicalConcept:3d-gaussian-splatting:test", target="MethodologicalConcept:gs:test", type="shares_concept_with")],
    )
    result = GraphCanonicalizer().canonicalize(graph)
    queue = ReviewQueue(result.review_items)
    payload = queue.model_dump()

    assert payload["items"][0]["status"] == "pending"
    decisions = [ReviewDecision(item_id=payload["items"][0]["id"], action="merge", canonical_node_id="MethodologicalConcept:gs:test")]
    merged = queue.apply_decisions(graph, decisions)

    assert [node.name for node in merged.nodes] == ["Gaussian Splatting"]
    assert merged.edges == []


# ------------------------------------------------- embedding review candidates


class _StubBackend:
    """Deterministic vector table keyed on the node NAME prefix of the text.

    The real backend recipe is ``f"{name}. {description}"``; matching on prefix
    keeps the fixture readable while exercising the same code path.
    """

    name = "stub-vectors"
    dim = 2

    def __init__(self, table, default=(0.0, 1.0)):
        self._table = table
        self._default = default

    def embed(self, texts):
        out = []
        for text in texts:
            vec = self._default
            for prefix, value in self._table.items():
                if text.startswith(prefix):
                    vec = value
                    break
            out.append(list(vec))
        return out


def _aldrin_graph():
    return ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:edwin:a", name="Edwin Aldrin", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="Concept:buzz:b", name="Buzz Aldrin", type=ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )


def _aldrin_backend():
    # Near-parallel but not identical: cosine ~0.9998.
    return _StubBackend({"Edwin Aldrin": (1.0, 0.0), "Buzz Aldrin": (1.0, 0.02)})


def test_semantic_review_finds_zero_string_overlap_duplicates():
    # 'Edwin Aldrin' / 'Buzz Aldrin' share NO token >= 3 chars except 'aldrin'?
    # They do share 'aldrin' — so use names the inverted index cannot pair.
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:edwin:a", name="Edwin Aldrin", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="Concept:buzz:b", name="Lunar Module Pilot", type=ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )
    backend = _StubBackend({"Edwin Aldrin": (1.0, 0.0), "Lunar Module Pilot": (1.0, 0.02)})

    baseline = GraphCanonicalizer().canonicalize(graph)
    assert baseline.review_items == []  # no shared token -> the string pass is blind

    result = GraphCanonicalizer(semantic=True, embedding_backend=backend).canonicalize(graph)
    semantic = [item for item in result.review_items if item.reason == "similar_embedding"]
    assert len(semantic) == 1
    assert {semantic[0].left_node_id, semantic[0].right_node_id} == {"Concept:edwin:a", "Concept:buzz:b"}
    assert semantic[0].score >= 0.60
    assert result.stats["semantic_added"] == 1


def test_semantic_pass_never_auto_merges():
    # Precision over recall, pinned: a later "just auto-merge above 0.9" cannot
    # land silently. Every merge still comes from an alias or a human decision.
    graph = _aldrin_graph()
    plain = GraphCanonicalizer().canonicalize(graph)
    semantic = GraphCanonicalizer(semantic=True, embedding_backend=_aldrin_backend()).canonicalize(graph)

    assert semantic.merged_nodes == {}
    assert semantic.graph.to_json() == plain.graph.to_json()


def test_semantic_review_items_are_byte_stable_across_runs():
    import json as _json

    # Two pairs that TIE on (score, left_name, right_name) because the same two
    # display names exist under two different node types. The ids are chosen so
    # that id order ('aa*' < 'zz*') is the OPPOSITE of emission order (blocks run
    # in sorted type-value order, so Concept is emitted before Task). Only a sort
    # key that carries the node ids gives a total, emission-independent order.
    nodes = [
        ResearchNode(id="zz-1", name="Alpha", type=ResearchNodeType.CONCEPT),
        ResearchNode(id="zz-2", name="Beta", type=ResearchNodeType.CONCEPT),
        ResearchNode(id="aa-1", name="Alpha", type=ResearchNodeType.TASK),
        ResearchNode(id="aa-2", name="Beta", type=ResearchNodeType.TASK),
    ]
    vectors = {"Alpha": (1.0, 0.0), "Beta": (1.0, 0.02)}
    graph = ResearchGraph(nodes=nodes, edges=[])

    def run():
        backend = _StubBackend(vectors)
        result = GraphCanonicalizer(semantic=True, embedding_backend=backend).canonicalize(graph)
        return _json.dumps(result.review_queue().model_dump(), sort_keys=False)

    first = run()
    assert first == run()
    items = GraphCanonicalizer(
        semantic=True, embedding_backend=_StubBackend(vectors)
    ).canonicalize(graph).review_items
    assert [(i.left_node_id, i.right_node_id) for i in items] == [("aa-1", "aa-2"), ("zz-1", "zz-2")]

    # Block cap truncates by SORTED ID, not by iteration order.
    def run_capped():
        backend = _StubBackend(vectors)
        result = GraphCanonicalizer(
            semantic=True, embedding_backend=backend, max_block=1
        ).canonicalize(graph)
        return _json.dumps(result.review_queue().model_dump(), sort_keys=False)

    assert run_capped() == run_capped()


def test_semantic_degrades_to_today_without_backend(monkeypatch):
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    monkeypatch.setattr(
        "tesserae.retrieval.hybrid.active_embedding_backend",
        lambda *a, **k: HashEmbeddingBackend(),
    )
    graph = _aldrin_graph()
    plain = GraphCanonicalizer().canonicalize(graph)
    degraded = GraphCanonicalizer(semantic=True).canonicalize(graph)

    assert degraded.review_items == plain.review_items
    assert "tesserae[semantic]" in str(degraded.stats["semantic_skipped"])


def test_semantic_candidates_respect_type_blocking():
    # Cross-type fusion is the worst class of silent merge: identical vectors
    # across types must still produce nothing.
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="Model:x:a", name="Orion", type=ResearchNodeType.MODEL),
            ResearchNode(id="Dataset:y:b", name="Apollo", type=ResearchNodeType.DATASET),
        ],
        edges=[],
    )
    backend = _StubBackend({}, default=(1.0, 0.0))  # everything identical
    result = GraphCanonicalizer(semantic=True, embedding_backend=backend).canonicalize(graph)
    assert [item for item in result.review_items if item.reason == "similar_embedding"] == []


def test_semantic_items_are_capped_and_deduped_against_string_items():
    nodes = [
        ResearchNode(id=f"Concept:n{idx:03d}:t", name=f"Concept Number {idx:03d}", type=ResearchNodeType.CONCEPT)
        for idx in range(300)
    ]
    graph = ResearchGraph(nodes=nodes, edges=[])
    backend = _StubBackend({}, default=(1.0, 0.0))  # mutually identical

    result = GraphCanonicalizer(
        semantic=True, embedding_backend=backend, max_semantic_items=5
    ).canonicalize(graph)
    semantic = [item for item in result.review_items if item.reason == "similar_embedding"]
    assert len(semantic) == 5
    assert result.stats["semantic_capped_at"] == 5
    assert len({item.id for item in result.review_items}) == len(result.review_items)


def test_semantic_pass_does_not_duplicate_a_string_pass_pair():
    # 'Gaussian Splatting' is a substring of '3D Gaussian Splatting', so the
    # token pass already emits this pair. The semantic pass must stay silent on
    # it — one pair, one review item, string reason wins.
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:gs:a", name="Gaussian Splatting", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="Concept:3dgs:b", name="3D Gaussian Splatting", type=ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )
    backend = _StubBackend({}, default=(1.0, 0.0))  # would happily pair them
    result = GraphCanonicalizer(semantic=True, embedding_backend=backend).canonicalize(graph)

    reasons = [item.reason for item in result.review_items]
    assert reasons == ["similar_name"]
    assert result.stats["semantic_added"] == 0
