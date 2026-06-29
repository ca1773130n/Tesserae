"""Cross-project federation MVP: identity-merge + deterministic federated recall."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae import federation as F
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _paper(pid, arxiv):
    return ResearchNode(id=pid, name=f"arXiv:{arxiv}", type=ResearchNodeType.PAPER,
                        metadata={"arxiv_id": arxiv})


def _concept(cid, desc):
    return ResearchNode(id=cid, name="Caching", type=ResearchNodeType.CONCEPT, description=desc)


def _g(prefix, desc):
    return ResearchGraph(
        nodes=[_paper(f"Paper:t:{prefix}", "1706.03762"), _concept(f"Concept:c:{prefix}", desc)],
        edges=[ResearchEdge(source=f"Concept:c:{prefix}", target=f"Paper:t:{prefix}", type="references")],
    )


# --- identity keys -------------------------------------------------------- #

def test_identity_key_per_type():
    assert F.identity_key(_paper("p", "1706.03762")) == ("Paper", "1706.03762")
    assert F.identity_key(ResearchNode(id="x", name="X", type=ResearchNodeType.CONCEPT)) is None
    assert F.identity_key(_paper("p", "")) is None  # no arxiv -> never merge
    repo = ResearchNode(id="r", name="R", type=ResearchNodeType.REPOSITORY,
                        metadata={"repo_url": "https://github.com/Owner/Repo.git"})
    assert F.identity_key(repo) == ("Repository", "github.com/owner/repo")
    repo2 = ResearchNode(id="r2", name="R", type=ResearchNodeType.REPOSITORY,
                         metadata={"github_repo": "Owner/Repo"})
    assert F.identity_key(repo2) == ("Repository", "github.com/owner/repo")  # same canonical
    ssh = ResearchNode(id="r3", name="R", type=ResearchNodeType.REPOSITORY,
                       metadata={"repo_url": "git@github.com:Owner/Repo.git"})
    assert F.identity_key(ssh) == ("Repository", "github.com/owner/repo")  # SSH == HTTPS
    bare = ResearchNode(id="r4", name="R", type=ResearchNodeType.REPOSITORY,
                        metadata={"repo_url": "https://github.com"})
    assert F.identity_key(bare) is None  # bare host -> no false identity key


def test_namespace_does_not_mutate_input():
    g = _g("a", "x")
    original_ids = [n.id for n in g.nodes]
    ns = F.namespace_graph(g, "alias")
    assert [n.id for n in g.nodes] == original_ids  # input untouched
    assert all(n.id.startswith("alias::") for n in ns.nodes)
    assert all(e.source.startswith("alias::") and e.target.startswith("alias::") for e in ns.edges)
    assert ns.nodes[0].metadata["federation_alias"] == "alias"


# --- merge --------------------------------------------------------------- #

def test_same_arxiv_merges_across_projects_distinct_concepts_do_not():
    fed, stats = F.federate_graphs([("work", _g("w", "work")), ("research", _g("r", "research"))])
    papers = [n for n in fed.nodes if n.type == ResearchNodeType.PAPER]
    concepts = [n for n in fed.nodes if n.type == ResearchNodeType.CONCEPT]
    assert len(papers) == 1 and stats["merged_groups"] == 1
    assert papers[0].metadata["federation_members"] == ["research", "work"]  # provenance
    assert papers[0].id == min("research::Paper:t:r", "work::Paper:t:w")  # smallest-id rep
    # same NAME ("Caching") but no identity key -> NOT merged (no false merge)
    assert len(concepts) == 2
    assert {c.id for c in concepts} == {"research::Concept:c:r", "work::Concept:c:w"}


def test_edges_repointed_to_merged_node_no_selfloops_or_dups():
    fed, _ = F.federate_graphs([("a", _g("a", "x")), ("b", _g("b", "y"))])
    paper_id = next(n.id for n in fed.nodes if n.type == ResearchNodeType.PAPER)
    # both projects' "references" edges now point at the one merged paper
    refs = [e for e in fed.edges if e.type == "references" and e.target == paper_id]
    assert len(refs) == 2  # one per project's Concept, distinct sources
    assert all(e.source != e.target for e in fed.edges)  # no self-loops


class _FakeBackend:
    """Deterministic orthogonal-unit embeddings keyed on content (no model2vec)."""
    name = "fake-test"

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "pagerank" in tl or "ppr" in tl:
                out.append([1.0, 0.0, 0.0])
            elif "banana" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def _concept_graph(nodes):
    return ResearchGraph(
        nodes=[ResearchNode(id=i, name=n, type=ResearchNodeType.CONCEPT, description=d)
               for i, n, d in nodes],
        edges=[],
    )


def test_semantic_links_bridge_related_cross_project_concepts_only():
    work = _concept_graph([
        ("Concept:ppr:w", "Personalized PageRank", "ranking"),
        ("Concept:ppr2:w", "PPR variant", "another pagerank node, SAME project"),
        ("Concept:ban:w", "Banana", "fruit"),
    ])
    research = _concept_graph([("Concept:ppr:r", "PPR algorithm", "pagerank ranking")])
    fed, stats = F.federate_graphs(
        [("work", work), ("research", research)],
        semantic=True, semantic_backend=_FakeBackend(), semantic_min_cosine=0.5,
    )
    sem = sorted((e.source, e.target) for e in fed.edges if e.type == "shares_concept_with")
    # research's PPR links to BOTH of work's PPR concepts (cross-project, similar);
    # the same-project PPR pair is NOT linked; banana has no cross-project match.
    assert stats["semantic_backend"] == "fake-test"
    assert sem == [
        ("research::Concept:ppr:r", "work::Concept:ppr2:w"),
        ("research::Concept:ppr:r", "work::Concept:ppr:w"),
    ]


def test_semantic_skipped_on_hash_stub():
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    g = _concept_graph([("Concept:a:w", "Alpha", "x"), ("Concept:b:r", "Beta", "y")])
    out, stats = F.add_semantic_links(g, backend=HashEmbeddingBackend())
    assert stats["semantic_added"] == 0 and "semantic_skipped" in stats
    assert len(out.edges) == 0  # no noise edges from the stub


class _UnnormBackend:
    """Returns NON-unit vectors so the test exercises L2-normalization."""
    name = "unnorm"

    def embed(self, texts):
        out = []
        for t in texts:
            if "AAA" in t:
                out.append([2.0, 0.0, 0.0])   # |v|=2
            elif "BBB" in t:
                out.append([1.0, 1.0, 0.0])   # |v|=sqrt2; true cosine to AAA = 0.707
            else:
                out.append([0.0, 0.0, 9.0])
        return out


def test_semantic_uses_true_cosine_not_raw_dot():
    work = _concept_graph([("Concept:a:w", "AAA", "x")])
    research = _concept_graph([("Concept:b:r", "BBB", "y")])
    # raw dot(AAA,BBB)=2 would pass 0.8; true cosine=0.707 must FAIL 0.8.
    _, s_hi = F.federate_graphs([("work", work), ("research", research)],
                                semantic=True, semantic_backend=_UnnormBackend(), semantic_min_cosine=0.8)
    assert s_hi["semantic_added"] == 0
    # at 0.7 the real cosine (0.707) passes.
    _, s_lo = F.federate_graphs([("work", work), ("research", research)],
                                semantic=True, semantic_backend=_UnnormBackend(), semantic_min_cosine=0.7)
    assert s_lo["semantic_added"] == 1


def test_semantic_skips_nodes_without_provenance():
    # Direct call on a NON-federated graph: nodes lack federation_alias -> empty
    # provenance -> never treated cross-project (no same-project false links).
    g = _concept_graph([("Concept:a", "AAA", "x"), ("Concept:b", "AAA", "x")])
    out, stats = F.add_semantic_links(g, backend=_UnnormBackend(), min_cosine=0.5)
    assert stats["semantic_added"] == 0
    assert not any(e.type == "shares_concept_with" for e in out.edges)


def test_semantic_type_list_covers_claims_and_session_findings():
    for value in ("ComparisonClaim", "CausalClaim", "SessionQuestion", "SessionTODO", "OpenQuestion"):
        assert value in F._SEMANTIC_TYPE_VALUES


def test_semantic_default_off_is_identity_only():
    a, b = _g("a", "x"), _g("b", "y")
    plain, _ = F.federate_graphs([("p", a), ("q", b)])
    assert not any(e.type == "shares_concept_with" for e in plain.edges)


def test_federation_is_byte_identical_regardless_of_order():
    a, b = _g("a", "x"), _g("b", "y")
    fed1, _ = F.federate_graphs([("alpha", a), ("beta", b)])
    fed2, _ = F.federate_graphs([("beta", b), ("alpha", a)])
    assert fed1.to_json() == fed2.to_json()


# --- loading (read-only) + recall via a stub registry -------------------- #

class _StubRegistry:
    def __init__(self, projects):
        self._projects = projects

    def list_projects(self):
        return {"projects": self._projects}


def _write_graph(path: Path, prefix: str, desc: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_g(prefix, desc).to_json(), encoding="utf-8")
    return path


def test_load_federated_graph_is_read_only(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "research")
    before = (gw.read_bytes(), gr.read_bytes())
    reg = _StubRegistry([
        {"name": "work", "graph_path": str(gw)},
        {"name": "research", "graph_path": str(gr)},
    ])
    fed, stats = F.load_federated_graph(["work", "research"], reg)
    assert stats["merged_groups"] == 1
    assert (gw.read_bytes(), gr.read_bytes()) == before  # source graphs untouched


def test_load_federated_graph_rejects_empty_and_unknown(tmp_path):
    reg = _StubRegistry([{"name": "work", "graph_path": str(tmp_path / "w.json")}])
    with pytest.raises(ValueError, match="at least one project"):
        F.load_federated_graph([], reg)
    with pytest.raises(ValueError, match="unknown project"):
        F.load_federated_graph(["nope"], reg)


def test_federated_recall_envelope_cross_references_projects(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "WORK-VIEW caching")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "RESEARCH-VIEW caching")
    reg = _StubRegistry([
        {"name": "work", "graph_path": str(gw)},
        {"name": "research", "graph_path": str(gr)},
    ])
    env = F.federated_recall(["work", "research"], "caching", semantic=False, registry=reg)
    assert env["scope"] == "federated"
    assert env["projects"] == ["research", "work"]
    assert env["synthesized"] is False  # deterministic, no LLM
    # citations are project-namespaced and span both projects
    cited = {c["node_id"].split("::", 1)[0] for c in env["citations"]}
    assert cited == {"research", "work"}


# --- v3: cache + status + explain ---------------------------------------- #

def test_semantic_link_cache_round_trips(tmp_path):
    work = _concept_graph([("Concept:ppr:w", "Personalized PageRank", "ranking")])
    research = _concept_graph([("Concept:ppr:r", "PPR algorithm", "pagerank ranking")])
    kw = dict(semantic=True, semantic_backend=_FakeBackend(), semantic_min_cosine=0.5,
              semantic_cache_dir=tmp_path)
    fed1, s1 = F.federate_graphs([("work", work), ("research", research)], **kw)
    assert s1["semantic_added"] == 1 and not s1.get("semantic_cached")
    assert list(tmp_path.glob("links-*.json"))  # cache written

    fed2, s2 = F.federate_graphs([("work", work), ("research", research)], **kw)
    assert s2.get("semantic_cached") is True and s2["semantic_added"] == 1
    links1 = sorted((e.source, e.target) for e in fed1.edges if e.type == "shares_concept_with")
    links2 = sorted((e.source, e.target) for e in fed2.edges if e.type == "shares_concept_with")
    assert links1 == links2  # cache reproduces the computed links


def test_federation_status_counts(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "research")
    reg = _StubRegistry([{"name": "work", "graph_path": str(gw)},
                         {"name": "research", "graph_path": str(gr)}])
    st = F.federation_status(["work", "research"], reg)
    assert st["projects"] == ["research", "work"]
    assert st["identity_merges"] == 1  # the shared arxiv paper merged
    # merged paper counts toward both projects; each also has its own Concept
    assert st["per_project_nodes"] == {"research": 2, "work": 2}


def test_federation_explain_merged_node(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "research")
    reg = _StubRegistry([{"name": "work", "graph_path": str(gw)},
                         {"name": "research", "graph_path": str(gr)}])
    merged_id = min("research::Paper:t:r", "work::Paper:t:w")
    res = F.federation_explain(merged_id, ["work", "research"], reg, semantic=False)
    assert res["merged_from_projects"] == ["research", "work"]  # spanned both
    assert len(res["links"]) == 2  # each project's Concept references it


def test_federation_explain_not_found_raises(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    reg = _StubRegistry([{"name": "work", "graph_path": str(gw)}])
    with pytest.raises(ValueError, match="not found"):
        F.federation_explain("does::not::exist", ["work"], reg, semantic=False)


def test_federation_explain_resolves_merged_away_id(tmp_path):
    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "research")
    reg = _StubRegistry([{"name": "work", "graph_path": str(gw)},
                         {"name": "research", "graph_path": str(gr)}])
    rep = min("research::Paper:t:r", "work::Paper:t:w")
    absorbed = max("research::Paper:t:r", "work::Paper:t:w")
    res = F.federation_explain(absorbed, ["work", "research"], reg, semantic=False)
    assert res["node"] == rep  # the merged-away id resolves to its representative


def _two_concept_projects():
    work = _concept_graph([("Concept:ppr:w", "Personalized PageRank", "ranking")])
    research = _concept_graph([("Concept:ppr:r", "PPR algorithm", "pagerank ranking")])
    return [("work", work), ("research", research)]


def test_semantic_cache_invalidates_on_param_change(tmp_path):
    graphs, fb = _two_concept_projects(), _FakeBackend()
    F.federate_graphs(graphs, semantic=True, semantic_backend=fb, semantic_min_cosine=0.5, semantic_cache_dir=tmp_path)
    first = set(tmp_path.glob("links-*.json"))
    # a different threshold must NOT reuse the prior key (full float, not rounded)
    _, s2 = F.federate_graphs(graphs, semantic=True, semantic_backend=fb, semantic_min_cosine=0.6, semantic_cache_dir=tmp_path)
    assert not s2.get("semantic_cached")
    assert set(tmp_path.glob("links-*.json")) != first


def test_corrupt_cache_is_treated_as_miss_not_poison(tmp_path):
    graphs, fb = _two_concept_projects(), _FakeBackend()
    F.federate_graphs(graphs, semantic=True, semantic_backend=fb, semantic_min_cosine=0.5, semantic_cache_dir=tmp_path)
    cache_file = next(tmp_path.glob("links-*.json"))
    cache_file.write_text('{"not": "a triple list"}', encoding="utf-8")  # valid JSON, wrong shape
    fed, s2 = F.federate_graphs(graphs, semantic=True, semantic_backend=fb, semantic_min_cosine=0.5, semantic_cache_dir=tmp_path)
    assert not s2.get("semantic_cached")  # corrupt -> miss -> recompute (no crash, no poison)
    links = sorted((e.source, e.target) for e in fed.edges if e.type == "shares_concept_with")
    assert links == [("research::Concept:ppr:r", "work::Concept:ppr:w")]


def test_load_federated_graph_memoizes_until_a_member_changes(tmp_path, monkeypatch):
    """The assembled federated graph is memoized in-process and self-invalidates
    the instant any member project's graph file changes (mtime/size)."""
    import tesserae.federation as Fed
    import tesserae.project as proj

    gw = _write_graph(tmp_path / "work" / ".tesserae" / "graph.json", "w", "work")
    gr = _write_graph(tmp_path / "research" / ".tesserae" / "graph.json", "r", "research")
    reg = _StubRegistry([
        {"name": "work", "graph_path": str(gw)},
        {"name": "research", "graph_path": str(gr)},
    ])
    Fed._FED_GRAPH_CACHE.clear()

    reads = {"n": 0}
    orig = proj.load_graph_file

    def spy(p):
        reads["n"] += 1
        return orig(p)

    monkeypatch.setattr(proj, "load_graph_file", spy)

    a = Fed.load_federated_graph(["work", "research"], reg)
    b = Fed.load_federated_graph(["work", "research"], reg)
    assert a is b            # cache HIT -> same object
    assert reads["n"] == 2   # only the first call read the 2 source files

    gw.write_text(gw.read_text() + "\n", encoding="utf-8")  # change size+mtime
    c = Fed.load_federated_graph(["work", "research"], reg)
    assert c is not a        # member changed -> rebuilt
    assert reads["n"] == 4

    Fed._FED_GRAPH_CACHE.clear()
    reads["n"] = 0
    monkeypatch.setenv("TESSERAE_NO_FEDERATION_CACHE", "1")
    d = Fed.load_federated_graph(["work", "research"], reg)
    e = Fed.load_federated_graph(["work", "research"], reg)
    assert d is not e and reads["n"] == 4  # env override disables the memo
