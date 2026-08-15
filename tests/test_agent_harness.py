import json

import pytest

from tesserae.agent_harness import (
    AgentHarnessAdapter,
    SUPPORTED_AGENT_HARNESSES,
    render_harness_context,
)
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def harness_sample_graph():
    paper = ResearchNode(id="Paper:harness", name="Harness Paper", type=ResearchNodeType.PAPER, source_path="notes/harness.md")
    method = ResearchNode(id="Method:gs", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Harness Paper uses Gaussian Splatting.")],
    )


def test_agent_harness_adapter_writes_common_manifest_and_provider_files(tmp_path):
    output = tmp_path / "agent_harness"

    written = AgentHarnessAdapter(project_name="demo_wiki").write_harness(
        graph=harness_sample_graph(),
        output_dir=output,
        mcp_command="python3",
        mcp_args=["-m", "tesserae.mcp_server", "--graph", "/abs/graph.json"],
        targets=["claude-code", "codex", "gemini", "kiro", "cursor", "opencode"],
    )

    assert output / "TESSERAE.md" in written
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_name"] == "demo_wiki"
    assert manifest["supported_targets"] == SUPPORTED_AGENT_HARNESSES
    assert manifest["node_count"] == 2
    assert manifest["edge_count"] == 1
    assert manifest["mcp"]["args"] == ["-m", "tesserae.mcp_server", "--graph", "/abs/graph.json"]

    assert (output / "claude" / "CLAUDE.md").exists()
    assert (output / "codex" / "AGENTS.md").exists()
    assert (output / "gemini" / "GEMINI.md").exists()
    assert (output / "kiro" / ".kiro" / "steering" / "tesserae.md").exists()
    assert (output / "cursor" / ".cursor" / "rules" / "tesserae.mdc").exists()
    assert (output / "opencode" / "AGENTS.md").exists()
    assert "Gaussian Splatting" in (output / "TESSERAE.md").read_text(encoding="utf-8")


def test_agent_harness_adapter_rejects_unknown_targets(tmp_path):
    try:
        AgentHarnessAdapter().write_harness(harness_sample_graph(), tmp_path, targets=["unknown-agent"])
    except ValueError as exc:
        assert "unknown-agent" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# --------------------------------------------------------------- topic scoping (07-04)


def harness_topic_graph():
    """A graph with two distinct topic neighborhoods so PPR scoping selects
    different node sets for different topics."""
    nodes = [
        ResearchNode(id="Paper:render", name="Gaussian Splatting Rendering",
                     type=ResearchNodeType.PAPER, description="Real-time radiance field rendering."),
        ResearchNode(id="Concept:splat", name="Gaussian Splatting",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Point-based rendering primitive."),
        ResearchNode(id="Concept:raster", name="Rasterization",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Projecting splats to screen space."),
        ResearchNode(id="Paper:lang", name="Transformer Language Modeling",
                     type=ResearchNodeType.PAPER, description="Attention-based sequence modeling."),
        ResearchNode(id="Concept:attn", name="Self Attention",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Scaled dot-product attention mechanism."),
        ResearchNode(id="Concept:token", name="Tokenization",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Splitting text into subword tokens."),
    ]
    edges = [
        ResearchEdge(source="Paper:render", target="Concept:splat", type="uses", evidence="rendering uses splatting"),
        ResearchEdge(source="Concept:splat", target="Concept:raster", type="uses", evidence="splatting rasterizes"),
        ResearchEdge(source="Paper:lang", target="Concept:attn", type="uses", evidence="LM uses attention"),
        ResearchEdge(source="Concept:attn", target="Concept:token", type="uses", evidence="attention over tokens"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_harness_default_unchanged():
    """No topic => deterministic static top-12 brief (two calls are identical)."""
    graph = harness_topic_graph()
    a = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    b = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    assert a == b
    # The static path lists every node (only 6, < 12) under Representative nodes.
    assert "Gaussian Splatting" in a
    assert "Self Attention" in a


def test_harness_topic_changes_brief():
    """A topic-scoped brief differs from the static (no-topic) brief."""
    graph = harness_topic_graph()
    static = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    scoped = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="gaussian splatting rendering"
    )
    assert scoped != static


def test_harness_context_points_agents_at_wiki_entrypoint():
    text = render_harness_context(
        "demo", harness_sample_graph(), "python3", ["-m", "tesserae.mcp_server"]
    )
    assert ".tesserae/wiki/index.md" in text
    assert "start at `.tesserae/wiki/index.md`" in text


def test_harness_brief_names_graph_map_canonical_entry_point():
    """Descent PR10: the TESSERAE.md brief names graph_map as the canonical
    entry point for graph navigation (project brief AND pointer blocks)."""
    text = render_harness_context(
        "demo", harness_sample_graph(), "python3", ["-m", "tesserae.mcp_server"]
    )
    assert "`graph_map`" in text
    assert "canonical entry point" in text
    from tesserae.agent_harness import render_pointer_block as _pointer

    assert "`graph_map`" in _pointer("demo")
    assert "`graph_map`" in _pointer("demo", "harness:acct:role")


def test_harness_topic_distinguishes_topics():
    """Two different topics over distinct neighborhoods yield different briefs."""
    graph = harness_topic_graph()
    splat = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="gaussian splatting rendering"
    )
    lang = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="transformer attention language"
    )
    assert splat != lang


# --------------------------------------------------------------- pointer install (07-09)


from tesserae.agent_harness import (
    POINTER_BEGIN,
    POINTER_END,
    install_instruction_pointer,
    render_pointer_block,
)


def test_pointer_block_is_deterministic():
    a, b = render_pointer_block("demo"), render_pointer_block("demo")
    assert a == b
    assert a.startswith(POINTER_BEGIN) and a.endswith(POINTER_END)
    assert ".tesserae/graph.json" in a and "TESSERAE.md" in a


def test_install_pointer_creates_agents_md_when_neither_exists(tmp_path):
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "created"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text == render_pointer_block("demo") + "\n"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_install_pointer_appends_to_existing_files_preserving_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n\nkeep me\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "appended"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nkeep me\n")
    assert POINTER_BEGIN in text and text.endswith(POINTER_END + "\n")


def test_install_pointer_is_idempotent_second_run_no_write(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    install_instruction_pointer(tmp_path, "demo")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "current"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # byte-idempotent


def test_install_pointer_refreshes_stale_block_in_place(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "top\n\n" + POINTER_BEGIN + "\nOLD STALE BODY\n" + POINTER_END + "\n\nbottom\n",
        encoding="utf-8",
    )
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "updated"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "OLD STALE BODY" not in text
    assert text.startswith("top\n\n") and text.endswith("\n\nbottom\n")
    assert render_pointer_block("demo") in text


def test_install_pointer_skips_claude_md_with_agents_include(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "skipped-include"}
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_install_pointer_leaves_malformed_markers_untouched(tmp_path):
    body = "x\n" + POINTER_BEGIN + "\nno end marker\n"
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == body


def test_install_pointer_bails_on_orphan_begin_before_complete_pair(tmp_path):
    """A stray BEGIN before a real pair must NOT splice orphan-BEGIN → first-END
    (that would silently delete the user content between them)."""
    body = (
        "docs mention " + POINTER_BEGIN + " as an example\n\n"
        "precious user content\n\n"
        + POINTER_BEGIN + "\nstale\n" + POINTER_END + "\n"
    )
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # untouched


def test_install_pointer_bails_on_duplicate_marker_pairs(tmp_path):
    body = (
        POINTER_BEGIN + "\nfirst copy\n" + POINTER_END + "\n\n"
        "between\n\n"
        + POINTER_BEGIN + "\nsecond copy\n" + POINTER_END + "\n"
    )
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # untouched


def test_install_pointer_coexists_with_foreign_marker_blocks(tmp_path):
    """Other managed blocks (e.g. HarnessSync) must survive both append and
    refresh untouched — our splice only ever moves bytes between OUR markers."""
    foreign = (
        "<!-- [harness-sync:start rules] -->\n"
        "synced rules body\n"
        "<!-- [harness-sync:end rules] -->\n"
    )
    (tmp_path / "AGENTS.md").write_text("# Mine\n\n" + foreign, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert foreign in text  # foreign block byte-identical
    # Now make our block stale and refresh: foreign block still untouched.
    stale = text.replace(render_pointer_block("demo"),
                         POINTER_BEGIN + "\nstale\n" + POINTER_END)
    (tmp_path / "AGENTS.md").write_text(stale, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "updated"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert foreign in text
    assert text.startswith("# Mine\n\n" + foreign)
    assert render_pointer_block("demo") in text and "stale" not in text


# --------------------------------------------------------- ## Divisions (CH-04)


def _ids(count: int, tag: str) -> list:
    return [f"Concept:{tag}{i}" for i in range(count)]


def _charter(*records) -> dict:
    """A charter in the shape ``build_charter`` writes, from ``(slug, **keys)``.

    Hand-built rather than compiled: the block under test is a projection of
    charter.json, and a test that had to derive one could only cover whatever
    partition Louvain happened to find on a fixture.
    ``test_division_rows_reads_keys_build_charter_actually_writes`` is what
    keeps this shape honest against the real producer.

    ``member_count`` is present and deliberately left at 0 while
    ``direct_member_ids`` carries the real members: the block counts the live
    subtree the way ``graph_map``'s domain card does, so a fixture whose
    stored count disagrees with its own membership is what proves it.
    """
    domains = {}
    for slug, record in records:
        domains[slug] = {
            "tier": 1,
            "status": "live",
            "parent_slug": None,
            "child_slugs": [],
            "anchor_id": "",
            "direct_member_ids": [],
            "member_count": 0,
            **record,
        }
    return {"version": 1, "reorg_seq": 0, "domains": domains, "member_index": {}}


def _chartered_graph() -> ResearchGraph:
    nodes = [
        ResearchNode(id="Topic:vision", name="Computer Vision", type=ResearchNodeType.RESEARCH_TOPIC),
        ResearchNode(id="Topic:lang", name="Language Modeling", type=ResearchNodeType.RESEARCH_TOPIC),
    ]
    return ResearchGraph(nodes=nodes, edges=[])


def _render(charter=None, graph=None) -> str:
    return render_harness_context(
        "demo",
        graph if graph is not None else _chartered_graph(),
        "python3",
        ["-m", "tesserae.mcp_server"],
        charter=charter,
    )


def _block_slugs(text: str) -> list:
    """The slugs of the rendered ``## Divisions`` block, in rendered order."""
    if "## Divisions" not in text:
        return []
    body = text.split("## Divisions", 1)[1].split("\n## ", 1)[0]
    return [
        line.split("`")[1]
        for line in body.splitlines()
        if line.startswith("- `")
    ]


def test_no_charter_leaves_the_harness_brief_byte_identical():
    """The below-the-bound case: no charter, no block, not one byte moved."""
    without = _render()
    assert without == _render(charter=None)
    assert "## Divisions" not in without
    # An empty domains dict is still "nothing to route through", not an empty
    # section header with no lines under it.
    assert _render(charter=_charter()) == without


def test_divisions_block_lists_live_divisions_in_slug_order_not_size_order():
    """Slug order, the same rule ``graph_map()``'s root uses.

    The fixture is built so the two orderings DISAGREE: ``computer-vision``
    holds 3 members and ``language-modeling`` 9, so name order puts the
    smaller one first and a test asserting it cannot pass by accident under
    the old ``(-member_count, slug)`` rule. Size order is the dendrogram
    root's rule and the one this entry point exists to replace.
    """
    charter = _charter(
        ("language-modeling", {"anchor_id": "Topic:lang", "direct_member_ids": _ids(9, "l")}),
        ("computer-vision", {"anchor_id": "Topic:vision", "direct_member_ids": _ids(3, "v")}),
        # A department: tier 2 AND a live parent. Reachable by descending a
        # division, so not itself one.
        ("cv-detection", {
            "tier": 2, "parent_slug": "computer-vision",
            "anchor_id": "Topic:vision", "direct_member_ids": _ids(99, "d"),
        }),
        # A tombstone names where a slug went; it is not a place to go.
        ("a-tombstone", {
            "status": "retired", "anchor_id": "Topic:lang",
            "direct_member_ids": _ids(50, "t"),
        }),
    )
    text = _render(charter)
    assert _block_slugs(text) == ["computer-vision", "language-modeling"]
    assert "- `computer-vision` — 3 members — Computer Vision" in text
    assert "- `language-modeling` — 9 members — Language Modeling" in text


def test_divisions_block_counts_the_live_subtree_not_the_stored_member_count():
    """The count is what ``graph_map``'s domain card reports as ``size``.

    A retired child keeps the frozen member snapshot it had when it was last
    live, so folding it in would report one id under two domains. The stored
    ``member_count`` here says 900; the live subtree holds 3.
    """
    charter = _charter(
        ("atlas", {
            "anchor_id": "Topic:vision", "member_count": 900,
            "child_slugs": ["atlas-live", "atlas-dead"],
            "direct_member_ids": ["Concept:a0"],
        }),
        ("atlas-live", {
            "tier": 2, "parent_slug": "atlas",
            "direct_member_ids": ["Concept:a1", "Concept:a2"],
        }),
        ("atlas-dead", {
            "tier": 2, "parent_slug": "atlas", "status": "retired",
            "direct_member_ids": ["Concept:a1", "Concept:a9"],
        }),
    )
    assert "- `atlas` — 3 members — Computer Vision" in _render(charter)


def test_divisions_block_sits_between_graph_summary_and_representative_nodes():
    """Step 4's placement: the answer to "what is this project about" has to
    come before the twelve type-then-alphabetically sorted nodes, not after."""
    text = _render(
        _charter(("computer-vision", {"anchor_id": "Topic:vision", "direct_member_ids": _ids(9, "v")}))
    )
    assert text.index("## Graph summary") < text.index("## Divisions") < text.index("## Representative nodes")


def test_divisions_block_keeps_a_division_whose_anchor_is_not_in_this_graph():
    """intake has no anchor at all, and the charter is derived before three
    passes rebind the graph — so an unresolvable anchor must cost the NAME,
    never the line. A dropped division is a domain an agent cannot reach."""
    charter = _charter(
        ("intake", {"anchor_id": "", "direct_member_ids": _ids(7, "i")}),
        ("gone", {"anchor_id": "Topic:evaporated", "direct_member_ids": _ids(3, "g")}),
        ("one", {"anchor_id": "Topic:evaporated", "direct_member_ids": _ids(1, "o")}),
    )
    text = _render(charter)
    assert "- `gone` — 3 members\n" in text
    assert "- `intake` — 7 members\n" in text
    assert "- `one` — 1 member\n" in text
    # And a nameless 7,581-member census does not get to open the file just
    # for being the biggest thing in it.
    assert _block_slugs(text) == ["gone", "intake", "one"]


def test_divisions_block_order_does_not_depend_on_charter_dict_order():
    """The block is written into a file whose byte-idempotence is the house
    invariant, and dict order is whatever the producer's recursion order was."""
    rows = [
        ("alpha", {"anchor_id": "Topic:vision", "direct_member_ids": _ids(5, "a")}),
        ("beta", {"anchor_id": "Topic:lang", "direct_member_ids": _ids(5, "b")}),
    ]
    a = _render(_charter(*rows))
    b = _render(_charter(*reversed(rows)))
    assert a == b
    assert _block_slugs(a) == ["alpha", "beta"]


def test_division_rows_reads_keys_build_charter_actually_writes():
    """Anti-drift: the block reads these keys off a record, and nothing else
    in the tree would notice if build_charter renamed one — the harness would
    just quietly render every division as an unnamed zero."""
    from tesserae.agent_harness import _division_rows
    from tesserae.charter import build_charter

    charter = build_charter(harness_topic_graph())
    record = next(iter(charter["domains"].values()))
    assert {"status", "parent_slug", "child_slugs", "anchor_id", "direct_member_ids"} <= set(record)
    rows = _division_rows(charter, harness_topic_graph())
    assert rows and all(count > 0 for _slug, count, _name in rows)


def test_write_harness_reads_the_charter_from_the_project_root(tmp_path):
    """The path derivation is the part that can silently no-op in production
    while every unit test on an ad-hoc export dir still passes."""
    from tesserae.charter import write_charter

    write_charter(
        tmp_path,
        _charter(("computer-vision", {"anchor_id": "Topic:vision", "direct_member_ids": _ids(9, "v")})),
    )
    output = tmp_path / ".tesserae" / "agent_harness"
    AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_chartered_graph(), output_dir=output, targets=["claude-code"]
    )
    text = (output / "TESSERAE.md").read_text(encoding="utf-8")
    assert "- `computer-vision` — 9 members — Computer Vision" in text
    # Every target file is derived from the same string, so the block reaches
    # the file the agent actually loads.
    assert "computer-vision" in (output / "claude" / "CLAUDE.md").read_text(encoding="utf-8")


def _write_harness_over(tmp_path, raw: str) -> str:
    charter_file = tmp_path / ".tesserae" / "charter" / "charter.json"
    charter_file.parent.mkdir(parents=True, exist_ok=True)
    charter_file.write_text(raw, encoding="utf-8")
    output = tmp_path / ".tesserae" / "agent_harness"
    AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_chartered_graph(), output_dir=output, targets=["claude-code"]
    )
    return (output / "TESSERAE.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("{ truncated", id="not-json"),
        pytest.param('["alpha"]', id="not-an-object"),
        pytest.param('{"version": 1, "domains": ["alpha"]}', id="domains-not-a-mapping"),
        pytest.param('{"version": 1, "domains": "alpha"}', id="domains-is-a-string"),
    ],
)
def test_write_harness_survives_a_charter_it_cannot_read(tmp_path, raw):
    """A mangled charter.json must not take down every agent's entry file.

    Each of these parses further than the last, and the last two are the ones
    that used to escape as AttributeError from inside the block rather than as
    CharterUnreadable from ``read_charter``. The compile pass that derives
    charter.json has already reported the damage loudly.
    """
    assert "## Divisions" not in _write_harness_over(tmp_path, raw)


def test_write_harness_still_renders_a_charter_with_one_mangled_record(tmp_path):
    """A record that is not a mapping costs that domain, not the whole block:
    ``read_charter`` validates the file's shape, the readers skip the record."""
    charter = _charter(("computer-vision", {"anchor_id": "Topic:vision", "direct_member_ids": _ids(9, "v")}))
    charter["domains"]["broken"] = "live"
    text = _write_harness_over(tmp_path, json.dumps(charter))
    assert _block_slugs(text) == ["computer-vision"]


def test_the_block_and_graph_map_agree_on_an_orphaned_charter(tmp_path):
    """The two entry points an agent reads must list the same divisions.

    ``atlas`` is retired without its child, so ``atlas-core`` is a live tier-2
    domain whose parent is a tombstone — an orphan. ``graph_map()`` surfaces
    it at the root (a domain hidden from every descent path is a slice of the
    graph nothing can reach), and keying this block on ``tier == 1`` showed 1
    division where graph_map showed 2.
    """
    from tests.test_graph_map_charter import (
        _call,
        _charter_payload,
        _fixture_graph,
        _make_project,
    )

    charter = _charter_payload()
    charter["domains"]["atlas"]["status"] = "retired"
    charter["domains"]["atlas"]["transition"] = "retired"
    project = _make_project(tmp_path, charter)

    # The orphan is exactly what the old tier rule dropped.
    assert charter["domains"]["atlas-core"]["tier"] == 2
    assert [s for s, r in charter["domains"].items() if r["status"] == "live" and r["tier"] == 1] == ["zephyr"]

    root = _call(project)
    from_graph_map = [c["scope_id"].split("domain:", 1)[1] for c in root["cards"]]

    output = project["root"] / ".tesserae" / "agent_harness"
    AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_fixture_graph(), output_dir=output, targets=["claude-code"]
    )
    from_block = _block_slugs((output / "TESSERAE.md").read_text(encoding="utf-8"))

    assert from_graph_map == ["atlas-core", "zephyr"]
    assert from_block == from_graph_map
