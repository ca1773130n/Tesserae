"""Tests for the vault → graph overlay reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.markdown_projection import GraphMarkdownProjector
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from llm_wiki.vault_pull import (
    VaultOverride,
    VaultUserLinkChange,
    apply_overrides,
    apply_user_link_changes,
    compute_overrides,
    compute_user_link_changes,
    extract_description,
    extract_user_notes_block,
    extract_wikilink_slugs,
    parse_frontmatter,
    write_diverged_fields_report,
)
from llm_wiki.research_graph import ResearchNodeType
from llm_wiki.vault_snapshot import NodeSnapshot, write_snapshot


# ----------------------------------------------------------------- parsing


def test_parse_frontmatter_handles_scalar_list_and_nested_blocks() -> None:
    text = """---
node_id: Paper:test
title: A Paper
type: Paper
aliases: [arXiv:1, arXiv:2]
edges_out:
  uses: [gaussian-splatting]
  part_of: [vision]
arxiv_id: 2308.04079
year: 2026
on_arxiv: true
---

# A Paper
"""
    fm = parse_frontmatter(text)
    assert fm is not None
    assert fm["node_id"] == "Paper:test"
    assert fm["title"] == "A Paper"
    assert fm["aliases"] == ["arXiv:1", "arXiv:2"]
    assert fm["edges_out"] == {"uses": ["gaussian-splatting"], "part_of": ["vision"]}
    assert fm["arxiv_id"] == "2308.04079"  # stays a string (has a dot but isn't numeric overall)
    assert fm["year"] == 2026
    assert fm["on_arxiv"] is True


def test_parse_frontmatter_returns_none_without_block() -> None:
    assert parse_frontmatter("# Hello world\n\nBody only.") is None
    assert parse_frontmatter("---\n# missing closing fence") is None
    assert parse_frontmatter("") is None


def test_extract_description_handles_callout_layout() -> None:
    body = """# 3D Gaussian Splatting

> [!quote] Paper
> First sentence of the description.
> Second sentence here.

## Aliases

3DGS

## Outgoing
"""
    assert extract_description(body) == "First sentence of the description.\nSecond sentence here."


def test_extract_description_handles_plain_paragraph_layout() -> None:
    body = """# Gaussian Splatting

This is a methodological concept describing primitive-based rendering.

## Outgoing
"""
    assert extract_description(body) == "This is a methodological concept describing primitive-based rendering."


def test_extract_description_returns_empty_string_when_missing() -> None:
    assert extract_description("# Empty\n\n## Outgoing\n") == ""
    assert extract_description("") == ""


# ---------------------------------------------------------------- overrides


def _make_node(node_id: str, **kwargs) -> ResearchNode:
    defaults = dict(
        id=node_id,
        name="Foo",
        type=ResearchNodeType.CONCEPT,
        aliases=[],
        description="",
        metadata={},
    )
    defaults.update(kwargs)
    return ResearchNode(**defaults)


def test_compute_overrides_returns_empty_for_pristine_vault(tmp_path: Path) -> None:
    """A vault that exactly matches its snapshot produces no overrides."""
    node = _make_node("Concept:foo", name="Foo", description="A foo.")
    graph = ResearchGraph(nodes=[node], edges=[])
    vault = tmp_path / "vault"
    GraphMarkdownProjector().write_projection(graph, vault)
    snapshot = {node.id: NodeSnapshot.from_node(node)}

    overrides = compute_overrides(vault, snapshot, {node.id: node})
    assert overrides == []


def test_compute_overrides_detects_name_aliases_description_and_metadata(tmp_path: Path) -> None:
    node = _make_node(
        "Concept:bar",
        name="Bar",
        aliases=["B"],
        description="Original description.",
        metadata={"author": "neo", "year": 2025},
    )
    graph = ResearchGraph(nodes=[node], edges=[])
    vault = tmp_path / "vault"
    GraphMarkdownProjector().write_projection(graph, vault)

    # Snapshot the pristine projection state.
    snapshot = {node.id: NodeSnapshot.from_node(node)}

    # User edits every editable field in the projected file.
    page = vault / "concepts" / "bar.md"
    original = page.read_text(encoding="utf-8")
    edited = (
        original
        .replace("title: Bar", "title: Bar Renamed")
        .replace("aliases: [B]", "aliases: [B, B2]")
        .replace("Original description.", "Edited description.")
        .replace("author: neo", "author: neo-edited")
        .replace("year: 2025", "year: 2026")
    )
    page.write_text(edited, encoding="utf-8")

    overrides = compute_overrides(vault, snapshot, {node.id: node})
    by_field = {o.field: o for o in overrides}
    assert by_field["name"].vault_value == "Bar Renamed"
    assert by_field["aliases"].vault_value == ["B", "B2"]
    assert by_field["description"].vault_value == "Edited description."
    assert by_field["metadata.author"].vault_value == "neo-edited"
    assert by_field["metadata.year"].vault_value == 2026


def test_compute_overrides_skips_files_without_node_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "user-notes.md").write_text(
        "---\ntitle: My notes\n---\n\n# Personal notes outside any projection\n",
        encoding="utf-8",
    )
    overrides = compute_overrides(vault, {}, {})
    assert overrides == []


def test_compute_overrides_skips_node_id_not_in_snapshot(tmp_path: Path) -> None:
    """Newly-projected nodes have no snapshot baseline yet; skipped silently."""
    vault = tmp_path / "vault"
    (vault / "concepts").mkdir(parents=True)
    (vault / "concepts" / "new.md").write_text(
        "---\nnode_id: Concept:freshly-added\ntitle: Fresh\ntype: Concept\n---\n\n# Fresh\n",
        encoding="utf-8",
    )
    overrides = compute_overrides(vault, {}, {})
    assert overrides == []


def test_compute_overrides_handles_missing_vault_dir(tmp_path: Path) -> None:
    overrides = compute_overrides(tmp_path / "does-not-exist", {}, {})
    assert overrides == []


# ---------------------------------------------------------------- apply


def test_apply_overrides_mutates_target_node_only() -> None:
    a = _make_node("Concept:a", name="A", description="orig A")
    b = _make_node("Concept:b", name="B", description="orig B")
    graph = ResearchGraph(nodes=[a, b], edges=[])
    overrides = [
        VaultOverride(node_id="Concept:a", field="name", vault_value="A New", snapshot_value="A"),
        VaultOverride(node_id="Concept:a", field="description", vault_value="new A desc", snapshot_value="orig A"),
    ]
    new_graph = apply_overrides(graph, overrides)
    by_id = {n.id: n for n in new_graph.nodes}
    assert by_id["Concept:a"].name == "A New"
    assert by_id["Concept:a"].description == "new A desc"
    # B untouched.
    assert by_id["Concept:b"].name == "B"
    assert by_id["Concept:b"].description == "orig B"


def test_apply_overrides_creates_metadata_key_when_missing() -> None:
    node = _make_node("Concept:n", metadata={"existing": "v"})
    graph = ResearchGraph(nodes=[node], edges=[])
    new_graph = apply_overrides(graph, [
        VaultOverride(node_id=node.id, field="metadata.new_key", vault_value="hello", snapshot_value=None),
    ])
    out = new_graph.nodes[0]
    assert out.metadata == {"existing": "v", "new_key": "hello"}


def test_apply_overrides_silently_ignores_unknown_node_ids() -> None:
    graph = ResearchGraph(nodes=[_make_node("Concept:a")], edges=[])
    new_graph = apply_overrides(graph, [
        VaultOverride(node_id="Concept:doesnotexist", field="name", vault_value="x", snapshot_value="y"),
    ])
    assert new_graph.nodes[0].name == "Foo"  # unchanged


def test_apply_overrides_preserves_edges() -> None:
    a = _make_node("Concept:a")
    b = _make_node("Concept:b")
    edge = ResearchEdge(source=a.id, target=b.id, type="uses")
    graph = ResearchGraph(nodes=[a, b], edges=[edge])
    new_graph = apply_overrides(graph, [
        VaultOverride(node_id=a.id, field="name", vault_value="Aname", snapshot_value="Foo"),
    ])
    assert new_graph.edges == [edge]


# ---------------------------------------------------------------- report


def test_diverged_fields_report_empty_when_no_overrides(tmp_path: Path) -> None:
    path = tmp_path / "diverged-fields.md"
    write_diverged_fields_report([], path)
    text = path.read_text(encoding="utf-8")
    assert "# Diverged fields" in text
    assert "_No divergences detected._" in text


def test_diverged_fields_report_groups_by_node(tmp_path: Path) -> None:
    path = tmp_path / "diverged-fields.md"
    overrides = [
        VaultOverride(node_id="Concept:b", field="name", vault_value="B2", snapshot_value="B"),
        VaultOverride(node_id="Concept:a", field="aliases", vault_value=["x"], snapshot_value=[]),
        VaultOverride(node_id="Concept:a", field="description", vault_value="new", snapshot_value="old"),
    ]
    write_diverged_fields_report(overrides, path)
    text = path.read_text(encoding="utf-8")
    assert "Field overrides — 3 across 2 node(s)" in text
    # Nodes appear in sorted order, each with its overrides grouped.
    assert text.index("Concept:a") < text.index("Concept:b")
    assert "snapshot value: `'B'`" in text
    assert "vault value: `'B2'`" in text


# ---------------------------------------------------------------- user_link / Stub


def test_extract_user_notes_block_returns_inner_content() -> None:
    body = """# Heading

> [!quote] Paper
> description

## Outgoing
- thing

<!-- user-notes:start -->

My notes about [[gaussian-splatting]] and [[NeRF]].

<!-- user-notes:end -->
"""
    assert "[[gaussian-splatting]]" in extract_user_notes_block(body)
    assert "My notes about" in extract_user_notes_block(body)


def test_extract_user_notes_block_returns_empty_when_markers_missing() -> None:
    assert extract_user_notes_block("# No markers here") == ""
    assert extract_user_notes_block("<!-- user-notes:start -->\nbut no end marker") == ""


def test_extract_wikilink_slugs_dedupes_and_handles_alias_syntax() -> None:
    text = "Foo [[a]] bar [[b]] baz [[a]] qux [[c|My Display]] end"
    assert extract_wikilink_slugs(text) == ["a", "b", "c"]


def test_compute_user_link_changes_adds_new_link_for_known_target(tmp_path: Path) -> None:
    paper = _make_node("Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    target = _make_node("Concept:t", name="Target")
    graph = ResearchGraph(nodes=[paper, target], edges=[])
    vault = tmp_path / "vault"
    GraphMarkdownProjector().write_projection(graph, vault)

    # User adds a wikilink in the paper's user-notes block.
    page = vault / "papers" / "paper.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "<!-- user-notes:start -->\n\n<!-- user-notes:end -->",
            "<!-- user-notes:start -->\n\nSee also [[target]] for context.\n\n<!-- user-notes:end -->",
        ),
        encoding="utf-8",
    )
    slug_by_id = {paper.id: "paper", target.id: "target"}
    changes = compute_user_link_changes(vault, graph, slug_by_id)
    assert len(changes) == 1
    assert changes[0].action == "add"
    assert changes[0].source_node_id == "Paper:p"
    assert changes[0].target_slug == "target"
    assert changes[0].target_node_id == "Concept:t"


def test_compute_user_link_changes_flags_unknown_slug_for_stub_creation(tmp_path: Path) -> None:
    paper = _make_node("Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    graph = ResearchGraph(nodes=[paper], edges=[])
    vault = tmp_path / "vault"
    GraphMarkdownProjector().write_projection(graph, vault)

    page = vault / "papers" / "paper.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "<!-- user-notes:start -->\n\n<!-- user-notes:end -->",
            "<!-- user-notes:start -->\n\nReferences [[no-such-slug]].\n\n<!-- user-notes:end -->",
        ),
        encoding="utf-8",
    )
    slug_by_id = {paper.id: "paper"}
    changes = compute_user_link_changes(vault, graph, slug_by_id)
    assert len(changes) == 1
    assert changes[0].target_slug == "no-such-slug"
    assert changes[0].target_node_id is None  # signals Stub creation


def test_apply_user_link_changes_creates_stub_for_unknown_targets() -> None:
    paper = _make_node("Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    graph = ResearchGraph(nodes=[paper], edges=[])
    changes = [
        VaultUserLinkChange(
            source_node_id="Paper:p",
            target_slug="unknown",
            target_node_id=None,
            action="add",
        ),
    ]
    new_graph = apply_user_link_changes(graph, changes)
    stub = next(n for n in new_graph.nodes if n.type == ResearchNodeType.STUB)
    assert stub.id == "Stub:unknown"
    assert stub.metadata["vault_slug"] == "unknown"
    edge = next(e for e in new_graph.edges if e.type == "user_link")
    assert edge.source == "Paper:p"
    assert edge.target == "Stub:unknown"


def test_apply_user_link_changes_remove_drops_matching_edge() -> None:
    paper = _make_node("Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    target = _make_node("Concept:t", name="T")
    graph = ResearchGraph(
        nodes=[paper, target],
        edges=[ResearchEdge(source=paper.id, target=target.id, type="user_link")],
    )
    changes = [
        VaultUserLinkChange(
            source_node_id="Paper:p",
            target_slug="t",
            target_node_id="Concept:t",
            action="remove",
        ),
    ]
    new_graph = apply_user_link_changes(graph, changes)
    assert all(e.type != "user_link" for e in new_graph.edges)


def test_apply_user_link_changes_is_idempotent_for_existing_edge() -> None:
    paper = _make_node("Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    target = _make_node("Concept:t", name="T")
    existing_edge = ResearchEdge(source=paper.id, target=target.id, type="user_link")
    graph = ResearchGraph(nodes=[paper, target], edges=[existing_edge])
    changes = [
        VaultUserLinkChange(
            source_node_id="Paper:p",
            target_slug="t",
            target_node_id="Concept:t",
            action="add",
        ),
    ]
    new_graph = apply_user_link_changes(graph, changes)
    user_links = [e for e in new_graph.edges if e.type == "user_link"]
    assert len(user_links) == 1  # no duplicate


def test_diverged_fields_report_emits_user_link_section(tmp_path: Path) -> None:
    path = tmp_path / "diverged-fields.md"
    changes = [
        VaultUserLinkChange(
            source_node_id="Paper:p",
            target_slug="known",
            target_node_id="Concept:k",
            action="add",
        ),
        VaultUserLinkChange(
            source_node_id="Paper:p",
            target_slug="unknown",
            target_node_id=None,
            action="add",
        ),
        VaultUserLinkChange(
            source_node_id="Paper:q",
            target_slug="gone",
            target_node_id="Concept:g",
            action="remove",
        ),
    ]
    write_diverged_fields_report([], path, changes)
    text = path.read_text(encoding="utf-8")
    assert "User-link edges — 2 added, 1 removed" in text
    assert "1 of the added link(s) target unknown slugs" in text
    assert "[[known]]" in text
    assert "[[unknown]]" in text
    assert "[[gone]]" in text


# ---------------------------------------------------------------- end-to-end


def test_end_to_end_pristine_to_edit_to_apply(tmp_path: Path) -> None:
    """Smoke the whole loop: project → snapshot → edit → diff → apply."""
    node = _make_node("Concept:end2end", name="Orig", description="Orig desc.")
    graph = ResearchGraph(nodes=[node], edges=[])
    vault = tmp_path / "vault"
    GraphMarkdownProjector().write_projection(graph, vault)
    snap = {node.id: NodeSnapshot.from_node(node)}

    page = vault / "concepts" / "orig.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        .replace("title: Orig", "title: Edited Name")
        .replace("Orig desc.", "Edited description body."),
        encoding="utf-8",
    )

    overrides = compute_overrides(vault, snap, {node.id: node})
    assert len(overrides) == 2  # name + description

    new_graph = apply_overrides(graph, overrides)
    final = new_graph.nodes[0]
    assert final.name == "Edited Name"
    assert final.description == "Edited description body."
