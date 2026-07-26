"""OKF import/export: lossless round-trip, tolerant foreign import, v0.2 conformance."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tesserae.okf import (
    OKF_VERSION,
    okf_trust_tier,
    read_okf_bundle,
    write_okf_bundle,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _graph() -> ResearchGraph:
    nodes = [
        ResearchNode(id="n1", name="Attention", type=ResearchNodeType.CONCEPT,
                     description="A mechanism.", aliases=["attn"],
                     metadata={"weight": 3, "tags": ["nlp"]}),
        ResearchNode(id="n2", name="Transformer", type=ResearchNodeType.MODEL,
                     description="Uses attention.", source_path="papers/x.md"),
        ResearchNode(id="n3", name="ghost", type=ResearchNodeType.STUB),  # excluded
    ]
    edges = [ResearchEdge(source="n2", target="n1", type="uses", evidence="sec 3")]
    return ResearchGraph(nodes=nodes, edges=edges)


def _fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1]) or {}


# --------------------------------------------------------------------------- #
# v0.1 backward-compatibility suite (unchanged behaviour)                       #
# --------------------------------------------------------------------------- #


def _same(a, b):
    """Semantic equality across a YAML round-trip.

    PyYAML resolves an unquoted ``2026-09-23`` to ``datetime.date`` and an
    unquoted ``2026-06-25T09:00:00Z`` to ``datetime``. We normalize those to ISO
    strings on import, because a datetime cannot survive ``graph.to_json()`` —
    the reason the CLI used to crash on the spec's own Appendix A bundle.
    Re-emitting therefore writes the quoted string form: the same date, still
    valid YAML. Compare meaning, not the parsed Python type.
    """
    import datetime as _dt

    if isinstance(a, (_dt.datetime, _dt.date)):
        a = a.isoformat()
    if isinstance(b, (_dt.datetime, _dt.date)):
        b = b.isoformat()
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_round_trip_is_lossless(tmp_path: Path):
    g = _graph()
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)

    # Stub excluded; the two real nodes survive with identity intact.
    by_id = {n.id: n for n in back.nodes}
    assert set(by_id) == {"n1", "n2"}
    assert by_id["n1"].type == ResearchNodeType.CONCEPT
    assert by_id["n1"].aliases == ["attn"]
    assert by_id["n1"].metadata == {"weight": 3, "tags": ["nlp"]}
    assert by_id["n2"].description == "Uses attention."
    assert by_id["n2"].source_path == "papers/x.md"
    # Typed edge survives with evidence; targets the same node.
    assert [(e.source, e.type, e.target, e.evidence) for e in back.edges] == [
        ("n2", "uses", "n1", "sec 3")
    ]


def test_export_is_deterministic(tmp_path: Path):
    g = _graph()
    write_okf_bundle(g, tmp_path / "a")
    write_okf_bundle(g, tmp_path / "b")
    a = sorted(p.relative_to(tmp_path / "a").as_posix() for p in (tmp_path / "a").rglob("*.md"))
    b = sorted(p.relative_to(tmp_path / "b").as_posix() for p in (tmp_path / "b").rglob("*.md"))
    assert a == b
    for rel in a:
        assert (tmp_path / "a" / rel).read_text() == (tmp_path / "b" / rel).read_text()
    assert "index.md" in a and "log.md" in a  # reserved files emitted


def test_same_name_nodes_round_trip_without_collision(tmp_path: Path):
    # Two distinct nodes with the same name+type would collide on one concept
    # file; both must survive (codex BLOCKER).
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="a1", name="Cache", type=ResearchNodeType.CONCEPT, description="one"),
            ResearchNode(id="a2", name="Cache", type=ResearchNodeType.CONCEPT, description="two"),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)
    assert {n.id for n in back.nodes} == {"a1", "a2"}
    assert {n.description for n in back.nodes} == {"one", "two"}


def test_edge_metadata_round_trips(tmp_path: Path):
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="n1", name="A", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="n2", name="B", type=ResearchNodeType.MODEL),
        ],
        edges=[ResearchEdge(source="n2", target="n1", type="uses",
                            metadata={"weight": 2, "note": "x"})],
    )
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)
    assert back.edges[0].metadata == {"weight": 2, "note": "x"}


def test_reexport_drops_deleted_nodes(tmp_path: Path):
    write_okf_bundle(_graph(), tmp_path)
    smaller = ResearchGraph(
        nodes=[ResearchNode(id="n1", name="Attention", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    write_okf_bundle(smaller, tmp_path)  # re-export into the SAME dir
    back = read_okf_bundle(tmp_path)
    assert {n.id for n in back.nodes} == {"n1"}  # n2 (deleted) must not linger


def test_symlink_outside_root_is_skipped(tmp_path: Path):
    import os as _os
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "real.md").write_text("---\ntype: Concept\nname: Real\n---\n\nhi\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("---\ntype: Concept\nname: Secret\n---\n\nleak\n", encoding="utf-8")
    try:
        _os.symlink(secret, bundle / "link.md")
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable")
    g = read_okf_bundle(bundle)
    assert {n.name for n in g.nodes} == {"Real"}  # symlinked file outside root not read


def test_foreign_bundle_best_effort(tmp_path: Path):
    # A hand-authored OKF bundle: no x_tesserae, an unknown type, a body link,
    # a broken link, and a file with no type (must be skipped).
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "graphs.md").write_text(
        "---\ntype: WeirdCustomType\nname: Graphs\n---\n\n"
        "See [Search](../search.md) and [missing](./nope.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "search.md").write_text(
        "---\ntype: Concept\nname: Search\n---\n\nLexical search.\n", encoding="utf-8"
    )
    (tmp_path / "junk.md").write_text("no frontmatter here\n", encoding="utf-8")

    g = read_okf_bundle(tmp_path)
    by_id = {n.id: n for n in g.nodes}
    # junk.md (no type) skipped; the other two imported.
    assert set(by_id) == {"topics/graphs", "search"}
    # Unknown type degrades to Concept, original preserved.
    assert by_id["topics/graphs"].type == ResearchNodeType.CONCEPT
    assert by_id["topics/graphs"].metadata["okf_type"] == "WeirdCustomType"
    # Valid body link -> references edge; broken link dropped.
    assert [(e.source, e.type, e.target) for e in g.edges] == [
        ("topics/graphs", "references", "search")
    ]


# --------------------------------------------------------------------------- #
# §11 conformance: a consumer MUST NOT reject                                   #
# --------------------------------------------------------------------------- #

def test_consumer_rejects_nothing_per_section_11(tmp_path: Path):
    """§11: unknown type, unknown keys, missing optional families, broken links."""
    (tmp_path / "a.md").write_text(
        "---\n"
        "type: Totally Made Up Type\n"          # unknown type
        "title: A\n"
        "wibble: {deep: [1, 2]}\n"              # unknown additional key
        "---\n\n"
        "Links to [nowhere](./ghost.md).\n",    # broken cross-link
        encoding="utf-8",
    )
    # A concept carrying JUST `type` is fully conformant (§4.1) — no title, no
    # description, no trust/lifecycle/provenance family at all.
    (tmp_path / "bare.md").write_text("---\ntype: Reference\n---\n", encoding="utf-8")

    g = read_okf_bundle(tmp_path)  # no index.md either (§11: not a rejection)
    by_id = {n.id: n for n in g.nodes}
    assert set(by_id) == {"a", "bare"}
    assert by_id["a"].metadata["okf_type"] == "Totally Made Up Type"
    assert by_id["a"].metadata["okf"]["wibble"] == {"deep": [1, 2]}
    assert by_id["bare"].name == "bare"          # §4.1: derive a title from the filename
    assert by_id["bare"].metadata.get("okf") is None
    assert g.edges == []                          # the broken link yields no edge


def test_subdir_index_and_log_are_reserved(tmp_path: Path):
    """§3.1: index.md/log.md are reserved at ANY level, not just the bundle root.

    The realistic shape is §3's "a subdirectory within a larger repository": a
    v0.1 bundle (Tesserae's own included) nested one level down, whose reserved
    files carry `type: index`/`type: log` frontmatter. Keyed on the concept path
    those pass the root-only reserved check and become phantom concepts whose
    listing links turn into spurious `references` edges.
    """
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "index.md").write_text(
        "---\ntype: index\nname: Tables\n---\n\n# Tables\n\n* [Orders](orders.md) - one row per order\n",
        encoding="utf-8",
    )
    (tmp_path / "tables" / "log.md").write_text(
        "---\ntype: log\nname: Changelog\n---\n\n# Changelog\n\n## 2026-01-01\n* **Creation**\n",
        encoding="utf-8",
    )
    (tmp_path / "tables" / "orders.md").write_text(
        "---\ntype: BigQuery Table\ntitle: Orders\n---\n\nOne row per order.\n", encoding="utf-8"
    )
    g = read_okf_bundle(tmp_path)
    assert {n.id for n in g.nodes} == {"tables/orders"}
    assert g.edges == []  # no phantom index -> orders edge


def test_spec_title_key_is_read(tmp_path: Path):
    """§4.1: `title` is the spec's display-name key; `name` is our v0.1 invention."""
    (tmp_path / "revenue.md").write_text(
        "---\ntype: Metric\ntitle: Revenue\n---\n\n# Definition\n\nRecognized revenue.\n",
        encoding="utf-8",
    )
    g = read_okf_bundle(tmp_path)
    assert [n.name for n in g.nodes] == ["Revenue"]


def test_bare_verified_mapping_is_read_as_one_element_list(tmp_path: Path):
    """§5.2/§11 MUST: a bare `verified` mapping is a one-element list."""
    (tmp_path / "m.md").write_text(
        "---\ntype: Metric\ntitle: M\n"
        "verified: { by: human:ahormati, at: '2026-06-25T09:00:00Z' }\n---\n\nx\n",
        encoding="utf-8",
    )
    g = read_okf_bundle(tmp_path)
    verified = g.nodes[0].metadata["okf"]["verified"]
    assert verified == [{"by": "human:ahormati", "at": "2026-06-25T09:00:00Z"}]
    # §5.3 tiers, inferred not stored.
    assert okf_trust_tier(verified) == "human-reviewed"
    assert okf_trust_tier({"by": "human:x", "at": "2026-01-01"}) == "human-reviewed"
    assert okf_trust_tier([{"by": "process:finance-nightly", "at": "2026-01-01"}]) == "machine-confirmed"
    assert okf_trust_tier([{"by": "agent/gemini-2.5-pro", "at": "2026-01-01"}]) == "machine-confirmed"
    assert okf_trust_tier(None) == "unverified"
    assert okf_trust_tier([]) == "unverified"


def test_unknown_frontmatter_keys_survive_round_trip(tmp_path: Path):
    """§4.1 SHOULD: preserve unknown keys when round-tripping."""
    src = tmp_path / "in"
    src.mkdir()
    (src / "orders.md").write_text(
        "---\n"
        "type: BigQuery Table\n"
        "title: Customer Orders\n"
        "description: One row per completed customer order.\n"
        "resource: https://console.cloud.google.com/bigquery?t=orders\n"
        "tags: [sales, orders]\n"
        "status: draft\n"
        "stale_after: 2026-09-23\n"
        "generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }\n"
        "verified:\n"
        "  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }\n"
        "sources:\n"
        "  - id: ga4-schema\n"
        "    resource: https://example.com/schema\n"
        "    title: GA4 schema\n"
        "    usage_count: 5000\n"
        "usage_window: { from: 2026-06-01, to: 2026-06-30 }\n"
        "---\n\nOne row per completed order.\n",
        encoding="utf-8",
    )
    graph = read_okf_bundle(src)
    out = tmp_path / "out"
    write_okf_bundle(graph, out)
    fm = _fm(out / "concepts" / "customer-orders.md")

    original = yaml.safe_load((src / "orders.md").read_text().split("---\n", 2)[1])
    for key in ("description", "resource", "tags", "status", "stale_after",
                "generated", "verified", "sources", "usage_window"):
        assert _same(fm[key], original[key]), key
    # The foreign `type` is NOT downgraded to Concept on the way out.
    assert fm["type"] == "BigQuery Table"
    assert fm["title"] == "Customer Orders"


def test_attested_computation_contract_survives_round_trip(tmp_path: Path):
    """§10: we never PRODUCE attestation, but we must never corrupt someone's."""
    src = tmp_path / "in"
    src.mkdir()
    (src / "revenue.md").write_text(
        "---\n"
        "type: Attested Computation\n"
        "title: Revenue for fiscal year\n"
        "runtime: bigquery\n"
        "parameters:\n"
        "  - { name: year, type: integer, required: true }\n"
        "computation: references/computations/revenue.sql\n"
        "executor:\n"
        "  resource: references/skills/run-on-bq.md\n"
        "  receipt: [job_id, executed_sql, result]\n"
        "attester:\n"
        "  resource: references/attesters/revenue.py\n"
        "---\n\n# Computation\n\n    SELECT 1\n",
        encoding="utf-8",
    )
    graph = read_okf_bundle(src)
    assert graph.nodes[0].metadata["okf_type"] == "Attested Computation"
    out = tmp_path / "out"
    write_okf_bundle(graph, out)
    fm = _fm(out / "concepts" / "revenue-for-fiscal-year.md")
    original = yaml.safe_load((src / "revenue.md").read_text().split("---\n", 2)[1])
    for key in ("runtime", "parameters", "computation", "executor", "attester"):
        assert _same(fm[key], original[key]), key


def test_legacy_v01_timestamp_and_citations(tmp_path: Path):
    """§13.1: legacy `timestamp` -> generated.at ladder; `# Citations` -> sources."""
    src = tmp_path / "in"
    src.mkdir()
    (src / "income.md").write_text(
        "---\ntype: Metric\ntitle: Income statement\n"
        "timestamp: '2026-05-28T22:53:05+00:00'\n---\n\n"
        "# Definition\nRevenue and gross profit.\n\n"
        "# Citations\n"
        "- https://wiki.acme/finance/fpa-handbook\n"
        "- https://wiki.acme/finance/revenue-recognition\n",
        encoding="utf-8",
    )
    graph = read_okf_bundle(src)
    node = graph.nodes[0]
    assert node.metadata["updated_at"] == "2026-05-28T22:53:05+00:00"
    assert node.metadata["okf"]["sources"] == [
        {"resource": "https://wiki.acme/finance/fpa-handbook"},
        {"resource": "https://wiki.acme/finance/revenue-recognition"},
    ]
    assert "Citations" not in node.description
    assert "fpa-handbook" not in node.description

    out = tmp_path / "out"
    write_okf_bundle(graph, out)
    fm = _fm(out / "concepts" / "income-statement.md")
    assert fm["timestamp"] == "2026-05-28T22:53:05+00:00"  # re-emitted verbatim
    assert fm["generated"]["at"] == "2026-05-28T22:53:05+00:00"  # ladder picked it up


# --------------------------------------------------------------------------- #
# v0.2 producer                                                                 #
# --------------------------------------------------------------------------- #

def _session(sid: str, started: str, ended: str) -> ResearchNode:
    return ResearchNode(
        id=sid, name=f"Session {sid}", type=ResearchNodeType.SESSION,
        metadata={"session_id": sid, "started_at": started, "ended_at": ended,
                  "project_root": "/work/proj"},
    )


def test_generated_by_is_never_human(tmp_path: Path):
    """Anti-trust-laundering guard: no `human:` actor, no `verified` key, ever."""
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="a", name="Agent written", type=ResearchNodeType.CONCEPT,
                         metadata={"agent_key": "claude-code", "first_seen_at": "2026-02-01T00:00:00Z"}),
            ResearchNode(id="b", name="Extracted", type=ResearchNodeType.SESSION_INSIGHT,
                         metadata={"extractor": "session-llm", "first_seen_at": "2026-02-02T00:00:00Z"}),
            ResearchNode(id="c", name="Plain", type=ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path)
    actors = {}
    for path in sorted(tmp_path.rglob("*.md")):
        if path.name in {"index.md", "log.md"}:
            continue
        fm = _fm(path)
        assert "verified" not in fm, path
        actors[fm["title"]] = fm["generated"]["by"]
    assert actors == {
        "Agent written": "claude-code/tesserae-agent-write",
        "Extracted": "process:tesserae-session-llm",
        "Plain": "process:tesserae-compile",
    }
    assert not any(a.startswith("human:") for a in actors.values())
    assert "verified" not in tmp_path.joinpath("concepts/plain.md").read_text()
    assert _fm(tmp_path / "concepts" / "agent-written.md")["generated"]["at"] == "2026-02-01T00:00:00Z"


def test_index_and_log_follow_sections_8_9(tmp_path: Path):
    g = ResearchGraph(
        nodes=[
            _session("s1", "2026-03-01T10:00:00Z", "2026-03-01T11:00:00Z"),
            _session("s2", "2026-04-05T10:00:00Z", "2026-04-05T11:00:00Z"),
            ResearchNode(id="n1", name="Attention", type=ResearchNodeType.CONCEPT,
                         description="A mechanism. And more prose."),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path)

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    # §8/§12: the ONLY frontmatter permitted in an index.md is okf_version, and
    # only at the bundle root.
    assert yaml.safe_load(index.split("---\n", 2)[1]) == {"okf_version": OKF_VERSION}
    assert "type:" not in index.split("---\n", 2)[1]
    assert "* [Attention](concepts/attention.md) - A mechanism." in index

    log = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert not log.startswith("---")  # §9: log.md carries no frontmatter
    headings = [ln for ln in log.splitlines() if ln.startswith("## ")]
    assert headings == ["## 2026-04-05", "## 2026-03-01"]  # ISO, newest first
    for h in headings:
        assert re.match(r"^## \d{4}-\d{2}-\d{2}$", h)


def test_superseded_node_is_deprecated_with_stale_after(tmp_path: Path):
    old = ResearchNode(id="old", name="Old take", type=ResearchNodeType.SESSION_TAKEAWAY,
                       metadata={"first_seen_at": "2026-01-01T00:00:00Z"})
    new = ResearchNode(id="new", name="New take", type=ResearchNodeType.SESSION_TAKEAWAY,
                       metadata={"first_seen_at": "2026-03-15T00:00:00Z"})
    g = ResearchGraph(nodes=[old, new],
                      edges=[ResearchEdge(source="new", target="old", type="supersedes")])
    write_okf_bundle(g, tmp_path / "a")
    fm_old = _fm(tmp_path / "a" / "sessions" / "old-take.md")
    assert fm_old["status"] == "deprecated"
    assert fm_old["stale_after"] == "2026-03-15"
    assert "status" not in _fm(tmp_path / "a" / "sessions" / "new-take.md")

    # Superseder PREDATES the superseded node: the boundary is degenerate, so
    # stale_after is omitted rather than emitted backwards (temporal.py's guard).
    backwards = ResearchGraph(
        nodes=[old, ResearchNode(id="new", name="New take", type=ResearchNodeType.SESSION_TAKEAWAY,
                                 metadata={"first_seen_at": "2025-06-01T00:00:00Z"})],
        edges=[ResearchEdge(source="new", target="old", type="supersedes")],
    )
    write_okf_bundle(backwards, tmp_path / "b")
    fm_b = _fm(tmp_path / "b" / "sessions" / "old-take.md")
    assert fm_b["status"] == "deprecated"
    assert "stale_after" not in fm_b


def test_sources_resource_is_project_relative(tmp_path: Path):
    g = ResearchGraph(
        nodes=[
            _session("s1", "2026-03-01T10:00:00Z", "2026-03-01T11:00:00Z"),
            ResearchNode(id="d1", name="Paper", type=ResearchNodeType.PAPER,
                         source_path="/work/proj/data/papers/x/paper.md",
                         metadata={"analysis_date": "2026-02-10", "arxiv_id": "2604.19741"}),
            ResearchNode(id="d2", name="Elsewhere", type=ResearchNodeType.PAPER,
                         source_path="/etc/passwd.md"),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path)
    fm = _fm(tmp_path / "papers" / "paper.md")
    assert fm["sources"] == [{
        "id": "paper", "resource": "data/papers/x/paper.md",
        "title": "paper.md", "last_modified": "2026-02-10",
    }]
    assert fm["resource"] == "https://arxiv.org/abs/2604.19741"
    # A path we cannot relativise is OMITTED, never emitted raw: a conformant
    # consumer would read it as bundle-relative (§6.2) and it leaks a home dir.
    assert "sources" not in _fm(tmp_path / "papers" / "elsewhere.md")
    # No absolute path escapes into the OKF-standard surface. `x_tesserae` is
    # excluded on purpose: it is Tesserae's private lossless channel (a foreign
    # consumer ignores it), and `source_path` there is the node's real identity.
    for path in sorted(tmp_path.rglob("*.md")):
        if path.name in {"index.md", "log.md"}:
            continue
        fm = _fm(path)
        fm.pop("x_tesserae", None)
        assert "/work/proj" not in yaml.safe_dump(fm), path
        assert "/etc/passwd" not in yaml.safe_dump(fm), path


def test_usage_count_and_window_from_discussed_in(tmp_path: Path):
    g = ResearchGraph(
        nodes=[
            _session("s1", "2026-03-01T10:00:00Z", "2026-03-02T11:00:00Z"),
            _session("s2", "2026-04-05T10:00:00Z", "2026-04-06T11:00:00Z"),
            ResearchNode(id="d1", name="Doc", type=ResearchNodeType.SOURCE_DOCUMENT,
                         source_path="/work/proj/docs/a.md"),
            ResearchNode(id="d2", name="Lonely", type=ResearchNodeType.SOURCE_DOCUMENT,
                         source_path="/work/proj/docs/b.md"),
        ],
        edges=[
            ResearchEdge(source="d1", target="s1", type="discussed_in"),
            ResearchEdge(source="d1", target="s2", type="discussed_in"),
        ],
    )
    write_okf_bundle(g, tmp_path)
    fm = _fm(tmp_path / "papers" / "doc.md")
    assert fm["sources"][0]["usage_count"] == 2
    assert fm["usage_window"] == {"from": "2026-03-01", "to": "2026-04-06"}
    lonely = _fm(tmp_path / "papers" / "lonely.md")
    assert "usage_count" not in lonely["sources"][0]
    assert "usage_window" not in lonely


def test_export_never_reads_the_clock_or_the_filesystem(tmp_path: Path):
    """`last_modified` comes from in-graph dates, never `os.stat().st_mtime`."""
    import os
    import time

    real = tmp_path / "proj" / "docs"
    real.mkdir(parents=True)
    doc = real / "a.md"
    doc.write_text("v1\n", encoding="utf-8")
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="s1", name="S", type=ResearchNodeType.SESSION,
                         metadata={"session_id": "s1", "started_at": "2026-03-01T10:00:00Z",
                                   "ended_at": "2026-03-01T11:00:00Z",
                                   "project_root": str(tmp_path / "proj")}),
            ResearchNode(id="d1", name="Doc", type=ResearchNodeType.SOURCE_DOCUMENT,
                         source_path=str(doc), description="Prose.",
                         metadata={"frontmatter_date": "2026-01-09"}),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path / "a")
    doc.write_text("v2 rewritten\n", encoding="utf-8")
    os.utime(doc, (time.time() + 86400, time.time() + 86400))
    write_okf_bundle(g, tmp_path / "b")

    rels = sorted(p.relative_to(tmp_path / "a").as_posix() for p in (tmp_path / "a").rglob("*.md"))
    assert rels == sorted(p.relative_to(tmp_path / "b").as_posix() for p in (tmp_path / "b").rglob("*.md"))
    for rel in rels:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel
    assert _fm(tmp_path / "a" / "papers" / "doc.md")["sources"][0]["last_modified"] == "2026-01-09"


def test_imports_the_specs_own_canonical_bundle(tmp_path):
    """§ Appendix A, verbatim shapes. The CLI used to die on all three:

    1. YAML resolves unquoted `at:` / `stale_after:` to datetime/date, which
       reached metadata["okf"] and made graph.to_json() raise
       "Object of type datetime is not JSON serializable" — §11 conformance held
       inside the library and was thrown away by the only shipped consumer.
    2. The §13.1 timestamp fallback tested isinstance(str), so it silently
       no-opped on exactly the unquoted form the spec writes.
    3. §6.1's RECOMMENDED bundle-relative link (`/tables/customers.md`) resolved
       via os.path.join, which discards its first argument when the second is
       absolute — a spec-following bundle imported as disconnected nodes.
    """
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "customers.md").write_text(
        "---\n"
        "type: Table\n"
        "title: Customers\n"
        "generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }\n"
        "verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }\n"
        "stale_after: 2026-09-23\n"
        "---\n\n# Overview\nOne row per customer.\n",
        encoding="utf-8",
    )
    (tmp_path / "orders.md").write_text(
        "---\ntype: Table\ntitle: Orders\ntimestamp: 2026-05-28T14:30:00Z\n---\n\n"
        "# Overview\nSee the [customers table](/tables/customers.md).\n",
        encoding="utf-8",
    )

    graph = read_okf_bundle(tmp_path)

    assert len(graph.nodes) == 2
    # (3) bundle-relative link resolved
    assert len(graph.edges) == 1, "spec-RECOMMENDED absolute link produced no edge"
    # (1) unquoted timestamps cannot break serialization
    assert graph.to_json(), "graph is not JSON-serializable"
    cust = next(n for n in graph.nodes if "customer" in n.id.lower())
    okf = cust.metadata.get("okf") or {}
    assert okf["stale_after"] == "2026-09-23"
    # §5.2 MUST: a bare `verified` mapping reads as a one-element list
    assert isinstance(okf["verified"], list) and len(okf["verified"]) == 1
    assert okf["verified"][0]["by"] == "human:ahormati"
    # (2) legacy unquoted `timestamp` still orders the concept
    orders = next(n for n in graph.nodes if "order" in n.id.lower())
    assert orders.metadata.get("updated_at"), "unquoted legacy timestamp was dropped"
