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
