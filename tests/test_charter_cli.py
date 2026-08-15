"""tests/test_charter_cli.py — `tesserae domains status` (Task 9)."""
from __future__ import annotations

import json
from pathlib import Path

from tesserae.charter import build_charter, write_charter
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def _graph() -> ResearchGraph:
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_domains_status_prints_the_tree(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    write_charter(tmp_path, build_charter(_graph()))

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "division" in out
    assert "members" in out


def test_domains_status_json_is_machine_readable(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    write_charter(tmp_path, build_charter(_graph()))

    rc = main(["domains", "status", "--project", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["reorg_seq"] == 0
    assert payload["domains"]


def test_domains_status_says_so_when_there_is_no_charter(tmp_path: Path, capsys):
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out + capsys.readouterr().err

    assert rc == 0
    assert "no charter" in out.lower()


def test_domains_status_tolerates_a_cycle_in_child_slugs(tmp_path: Path, capsys):
    """A cyclic charter.json must not hang or blow the recursion limit.

    charter.json is a plain file on disk, editable by a person or a bad
    hand-merge — build_charter/succeed only guarantee a tree for what THEY
    write, not for what a caller later reads. This feature has already
    shipped two other unbounded-recursion defects during review (split()'s
    dense-clique stall, and succeed()'s slug-collision guard); a renderer
    that walks child_slugs with no cycle check would be a third instance of
    the identical failure class, just triggered by user-editable JSON
    instead of the graph. The guard tracks slugs already seen on the current
    path and stops descending into one a second time.
    """
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    # Hand-built rather than produced by build_charter/succeed: those always
    # emit a tree, so a cycle can only arise here, standing in for a
    # corrupted or hand-edited charter.json.
    domain_template = {
        "tier": 1,
        "own_altitude": "division",
        "anchor_id": "",
        "direct_member_ids": [],
        "member_count": 1,
        "reorg_seq": 0,
        "status": "live",
        "transition": "founded",
        "unsplittable": False,
    }
    charter = {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "a": {**domain_template, "parent_slug": None, "child_slugs": ["b"]},
            "b": {**domain_template, "tier": 2, "own_altitude": "team",
                  "parent_slug": "a", "child_slugs": ["a"]},
        },
        "member_index": {},
    }
    write_charter(tmp_path, charter)

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    # Pins the guard's actual behaviour (each slug rendered exactly once),
    # not merely "the process didn't hang".
    assert out.count("a  (division, 1 members)") == 1
    assert out.count("b  (team, 1 members)") == 1


def test_domains_status_marks_a_child_slug_that_names_no_domain(tmp_path: Path, capsys):
    """MINOR: `_render` folded "already on this path" (a cycle) and "no such
    domain" (corruption) into one silent `return`, so a charter in exactly the
    Critical-1 state — a division erased by the intake write, its children left
    behind naming a parent that no longer holds them — rendered as a perfectly
    healthy tree. The dangling child simply did not appear.

    A renderer whose job is to let an operator inspect the institution must
    make corruption visible; a cycle is a legitimate stop, a missing domain is
    a finding.
    """
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    charter = {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "alpha": {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": ["vanished"], "anchor_id": "Concept:a",
                "direct_member_ids": [], "member_count": 1, "reorg_seq": 0,
                "status": "live", "transition": "founded", "unsplittable": False,
            },
        },
        "member_index": {},
    }
    write_charter(tmp_path, charter)

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "vanished" in out, "a dangling child must not render as a healthy tree"
    assert "missing" in out.lower()


def test_domains_status_reports_a_corrupt_charter_as_an_error(tmp_path: Path, capsys):
    """IMPORTANT 3, at the CLI boundary: an unreadable charter must not be
    reported with the reassuring "no charter yet" message an absent one gets,
    and must not exit 0 — a caller scripting against this would treat a
    corrupted institution as a project that simply had not been compiled."""
    from tesserae.cli import main
    from tesserae.charter import charter_path

    path = charter_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"domains": ', encoding="utf-8")

    rc = main(["domains", "status", "--project", str(tmp_path)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "no charter yet" not in (captured.out + captured.err).lower()
    assert str(path) in (captured.out + captured.err)


def test_domains_status_prints_the_clock_and_what_it_does_not_cover(
    tmp_path: Path, capsys
):
    """A bare date on a domain dated by a fraction of its members reads as
    coverage it does not have — 340 of 780 domains on the live corpus are
    dated over a strict subset. The undated share is printed for the same
    reason ``facts_as_of`` returns ``undated_included``.
    """
    import dataclasses

    from tesserae.cli import main

    graph = _graph()
    graph = ResearchGraph(
        nodes=[
            dataclasses.replace(node, metadata={"first_seen_at": "2026-05-01"})
            if node.id == "Concept:a0"
            else node
            for node in graph.nodes
        ],
        edges=graph.edges,
    )
    (tmp_path / ".tesserae").mkdir(parents=True)
    charter = build_charter(graph)
    write_charter(tmp_path, charter)

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "through 2026-05-01" in out
    assert "undated)" in out, "the fraction the date does not cover must be visible"
    undated = sum(1 for e in charter["domains"].values() if e["quality"] == "undated")
    assert f"undated={undated}" in out, "the census belongs in the footer"


def test_domains_status_renders_a_charter_written_before_the_clock_existed(
    tmp_path: Path, capsys
):
    """The renderer must not invent a date for a charter that carries none —
    charter.json predates these keys on every project chartered earlier."""
    from tesserae.cli import main

    (tmp_path / ".tesserae").mkdir(parents=True)
    charter = build_charter(_graph())
    for entry in charter["domains"].values():
        for key in ("distilled_through", "quality", "undated_member_count"):
            entry.pop(key)
    write_charter(tmp_path, charter)

    rc = main(["domains", "status", "--project", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "through" not in out and "undated=0" in out
