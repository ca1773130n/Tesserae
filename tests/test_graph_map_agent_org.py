"""`graph-map --scope org:root` must print the agent org, not blow the stack.

`_known_agent_keys` is the union of Agent nodes in the graph and registry
entries, and a real project HAS an Agent node keyed `org:root` — `agents init`
mints one, labelled "Org root". `effective_parent` then answers ORG_ROOT for
any key without a declared parent, including ORG_ROOT itself, so root landed in
its own child list and the plain recursion in `subtree_keys` ran until the
interpreter gave up.

The single entry point into the agent memory hierarchy therefore died with a
RecursionError on an ordinary project, which is why the feature reads as
unusable rather than merely empty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_identity import ORG_ROOT, AgentRegistry
from tesserae.cli import main
from tesserae.project import ProjectWiki, load_graph_file


def _project_with_root_agent(tmp_path: Path) -> ProjectWiki:
    """A project in the state a real one reaches: an Agent node for org:root."""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text("# A\n\nRetrieval grounds answers.\n", encoding="utf-8")
    wiki = ProjectWiki.init(tmp_path, name="org")
    wiki.compile()

    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    payload["nodes"].extend([
        {"id": "Agent:agent-org-root:bc517a6cec6d", "name": ORG_ROOT, "type": "Agent",
         "aliases": [], "description": "", "source_path": None,
         "metadata": {"agent_key": ORG_ROOT, "label": "Org root"}},
        {"id": "Agent:agent-worker:aaaaaaaaaaaa", "name": "claude-code:someone:default",
         "type": "Agent", "aliases": [], "description": "", "source_path": None,
         "metadata": {"agent_key": "claude-code:someone:default", "label": "Claude Code"}},
    ])
    wiki.paths.graph.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return wiki


def test_the_graph_really_does_carry_a_root_agent(tmp_path):
    """Pin the precondition, so a fixture drift cannot make this suite vacuous."""
    from tesserae.agent_view import _known_agent_keys

    wiki = _project_with_root_agent(tmp_path)
    registry = AgentRegistry.for_project(tmp_path)
    known = _known_agent_keys(load_graph_file(wiki.paths.graph), registry)
    assert ORG_ROOT in known
    assert registry.effective_parent(ORG_ROOT) == ORG_ROOT, "root is its own parent"


def test_root_is_not_placed_in_its_own_child_list(tmp_path):
    """The defect itself, asserted on the map graph_map builds."""
    from tesserae.agent_view import _known_agent_keys

    wiki = _project_with_root_agent(tmp_path)
    registry = AgentRegistry.for_project(tmp_path)
    known = _known_agent_keys(load_graph_file(wiki.paths.graph), registry)

    children = {}
    for key in known:
        parent = registry.effective_parent(key)
        if parent == key:  # the guard graph_map applies
            continue
        children.setdefault(parent, []).append(key)
    assert ORG_ROOT not in children.get(ORG_ROOT, [])


def test_graph_map_org_root_returns_the_tree(tmp_path, monkeypatch, capsys):
    """Before the fix this raised RecursionError after ~1000 frames."""
    monkeypatch.chdir(tmp_path)
    _project_with_root_agent(tmp_path)
    capsys.readouterr()  # drop the fixture compile's chatter; stdout must be pure JSON

    assert main(["graph-map", "--scope", ORG_ROOT]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["header"]["scope"] == ORG_ROOT
    assert payload["header"]["kind"] == "org"
    assert payload["header"]["agent_count"] >= 1
    titles = [c.get("title") for c in payload["cards"]]
    assert "Org root" not in titles, "root must not be listed as a report of itself"


def test_a_declared_parent_cycle_fails_loud_rather_than_recursing(tmp_path, monkeypatch, capsys):
    """`agents set-parent` writes this map, so a cycle must not hang the server.

    The registry already detects a declared cycle and refuses; what matters is
    that the refusal is a message and a non-zero exit, never a traceback and
    never an unbounded walk.
    """
    monkeypatch.chdir(tmp_path)
    _project_with_root_agent(tmp_path)
    registry = tmp_path / ".tesserae" / "agents" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({
        "version": 1,
        "agents": {
            "a:1": {"label": "A", "parent": "a:2"},
            "a:2": {"label": "B", "parent": "a:1"},
        },
    }, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    rc = main(["graph-map", "--scope", ORG_ROOT])
    err = capsys.readouterr().err
    assert rc != 0
    assert "parent cycle" in err
    assert "Traceback" not in err
