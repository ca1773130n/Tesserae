"""Tests for ``tesserae project ingest-code`` (feature A / impl-code-graph).

Covers the new typed code-graph slice: CodeFile / CodeModule / CodeClass /
CodeFunction / CodeMethod nodes plus contains / calls / imports /
inherits_from / declared_in edges. Plus idempotency (re-running the
extractor over the same fixture yields the same graph) and the CLI seam
that writes ``.tesserae/code-graph.json``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tesserae.code_graph_extractor import (
    CodeGraphExtractor,
    DEFAULT_EXCLUDES,
    write_code_graph,
)
from tesserae.research_graph import ResearchNodeType

FIXTURE = """\
import json
from collections import OrderedDict


class Base:
    def greet(self):
        return "hi"


class Greeter(Base):
    def __init__(self, name):
        self.name = name

    def greet(self):
        payload = json.dumps({"name": self.name})
        return helper(payload)


def helper(value):
    return OrderedDict([("value", value)])


def main():
    g = Greeter("world")
    return g.greet()
"""


def _write_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    pkg = project / "demo_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "greet.py").write_text(FIXTURE, encoding="utf-8")
    # Excluded dirs must not be walked.
    venv = project / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "junk.py").write_text("def should_not_appear():\n    pass\n", encoding="utf-8")
    worktrees = project / ".worktrees" / "feat-x"
    worktrees.mkdir(parents=True)
    (worktrees / "also_junk.py").write_text("def nope():\n    pass\n", encoding="utf-8")
    return project


def test_extractor_mints_all_five_node_types_and_five_edge_types(tmp_path):
    project = _write_fixture(tmp_path)

    result = CodeGraphExtractor(project).extract()
    graph = result.graph

    # All five required node types appear.
    type_names = {node.type for node in graph.nodes}
    for required in (
        ResearchNodeType.CODE_FILE,
        ResearchNodeType.CODE_MODULE,
        ResearchNodeType.CODE_CLASS,
        ResearchNodeType.CODE_FUNCTION,
        ResearchNodeType.CODE_METHOD,
    ):
        assert required in type_names, f"missing node type: {required}"

    # All five required edge types appear (note: "contains" is shared with
    # other extractors, but we still want to see it minted here).
    edge_types = {edge.type for edge in graph.edges}
    for required in ("contains", "calls", "imports", "inherits_from", "declared_in"):
        assert required in edge_types, f"missing edge type: {required}"


def test_extractor_resolves_calls_and_inheritance_locally(tmp_path):
    project = _write_fixture(tmp_path)
    result = CodeGraphExtractor(project).extract()
    graph = result.graph

    by_name = {node.name: node for node in graph.nodes}
    assert by_name["Base"].type == ResearchNodeType.CODE_CLASS
    assert by_name["Greeter"].type == ResearchNodeType.CODE_CLASS
    assert by_name["helper"].type == ResearchNodeType.CODE_FUNCTION
    assert by_name["Greeter.greet"].type == ResearchNodeType.CODE_METHOD
    assert by_name["demo_pkg.greet"].type == ResearchNodeType.CODE_MODULE
    assert by_name["demo_pkg/greet.py"].type == ResearchNodeType.CODE_FILE

    edge_triples = {(edge.source, edge.type, edge.target) for edge in graph.edges}

    # Greeter inherits_from Base.
    assert (by_name["Greeter"].id, "inherits_from", by_name["Base"].id) in edge_triples

    # Greeter.greet calls helper() (resolved through the local symbol index).
    assert (by_name["Greeter.greet"].id, "calls", by_name["helper"].id) in edge_triples

    # main() calls Greeter (constructor call resolves to the class node).
    assert (by_name["main"].id, "calls", by_name["Greeter"].id) in edge_triples

    # File imports both json and collections.
    deps = {edge.target for edge in graph.edges if edge.type == "imports"}
    json_id = by_name["json"].id
    collections_id = by_name["collections"].id
    assert json_id in deps and collections_id in deps

    # declared_in points class/function/method at the CodeFile.
    file_id = by_name["demo_pkg/greet.py"].id
    for sym in ("Base", "Greeter", "helper", "main", "Greeter.greet"):
        assert (by_name[sym].id, "declared_in", file_id) in edge_triples


def test_extractor_skips_excluded_directories(tmp_path):
    project = _write_fixture(tmp_path)
    result = CodeGraphExtractor(project).extract()

    names = {node.name for node in result.graph.nodes}
    assert "should_not_appear" not in names
    assert "nope" not in names
    # .venv and .worktrees both pruned by DEFAULT_EXCLUDES.
    assert ".venv" in DEFAULT_EXCLUDES
    assert ".worktrees" in DEFAULT_EXCLUDES
    assert result.skipped_dirs >= 2


def test_extractor_is_idempotent(tmp_path):
    project = _write_fixture(tmp_path)

    first = CodeGraphExtractor(project).extract().graph
    second = CodeGraphExtractor(project).extract().graph

    assert first.to_json(sort_keys=True) == second.to_json(sort_keys=True)


def test_write_code_graph_atomic_round_trip(tmp_path):
    project = _write_fixture(tmp_path)
    graph = CodeGraphExtractor(project).extract().graph

    target = tmp_path / ".tesserae" / "code-graph.json"
    write_code_graph(graph, target)

    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "nodes" in payload and "edges" in payload
    # No leftover .tmp files from the atomic-write dance.
    leftovers = list(target.parent.glob("code-graph.json.tmp.*"))
    assert leftovers == []


def test_cli_project_ingest_code_writes_default_output(tmp_path):
    project = _write_fixture(tmp_path)

    # Invoke via project_main directly (avoids subprocess flake on PATH).
    from tesserae.cli import project_main

    rc = project_main(["ingest-code", "--project", str(project)])
    assert rc == 0

    out = project / ".tesserae" / "code-graph.json"
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    types = {node["type"] for node in payload["nodes"]}
    assert "CodeFile" in types
    assert "CodeMethod" in types
    edge_types = {edge["type"] for edge in payload["edges"]}
    assert {"contains", "imports", "declared_in"}.issubset(edge_types)


# ---------------------------------------------------------------------------
# Regression: codex review (PR #2)
# ---------------------------------------------------------------------------


def test_same_named_symbols_across_files_stay_distinct(tmp_path):
    """P1 regression: two modules each defining ``def main()`` and
    ``class Config`` must mint two CodeFunction / two CodeClass nodes,
    and the per-file ``contains`` / ``declared_in`` edges must NOT be
    cross-rewired through the research-graph's aggressive same-type
    dedup (which previously keyed on display name only and collapsed
    the second module's symbols into the first).
    """

    project = tmp_path / "dup_demo"
    pkg = project / "dup_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        "class Config:\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    return Config()\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        "class Config:\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    return Config()\n",
        encoding="utf-8",
    )

    graph = CodeGraphExtractor(project).extract().graph

    # Two distinct CodeFunction nodes named "main" — one per module.
    main_fns = [
        n
        for n in graph.nodes
        if n.type == ResearchNodeType.CODE_FUNCTION and n.name == "main"
    ]
    assert len(main_fns) == 2, [n.id for n in main_fns]

    # Two distinct CodeClass nodes named "Config" — one per module.
    cfg_classes = [
        n
        for n in graph.nodes
        if n.type == ResearchNodeType.CODE_CLASS and n.name == "Config"
    ]
    assert len(cfg_classes) == 2, [n.id for n in cfg_classes]

    # Each module's ``contains`` edge to its own ``main`` must survive
    # (the dedup pass used to collapse the two ``main`` nodes into one
    # and rewrite both modules' edges onto a single survivor).
    by_id = {n.id: n for n in graph.nodes}
    contains_targets_by_module: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.type != "contains":
            continue
        src = by_id.get(edge.source)
        tgt = by_id.get(edge.target)
        if src is None or tgt is None:
            continue
        if src.type != ResearchNodeType.CODE_MODULE:
            continue
        contains_targets_by_module.setdefault(src.name, set()).add(tgt.id)

    a_targets = contains_targets_by_module["dup_pkg.a"]
    b_targets = contains_targets_by_module["dup_pkg.b"]
    # Each module owns its own main / Config nodes; no shared id.
    assert a_targets.isdisjoint(b_targets), (a_targets & b_targets)

    # And each module's ``main`` calls its module-local ``Config``,
    # NOT the other module's — sanity-check the call edges weren't
    # cross-rewired either. The extractor stamps ``source_path`` with
    # the relative file path, so we discriminate by that.
    main_a = next(n for n in main_fns if n.source_path == "dup_pkg/a.py")
    main_b = next(n for n in main_fns if n.source_path == "dup_pkg/b.py")
    cfg_a = next(n for n in cfg_classes if n.source_path == "dup_pkg/a.py")
    cfg_b = next(n for n in cfg_classes if n.source_path == "dup_pkg/b.py")
    call_edges = {(e.source, e.type, e.target) for e in graph.edges if e.type == "calls"}
    assert (main_a.id, "calls", cfg_a.id) in call_edges
    assert (main_b.id, "calls", cfg_b.id) in call_edges
    # And there must be no cross-module call leak.
    assert (main_a.id, "calls", cfg_b.id) not in call_edges
    assert (main_b.id, "calls", cfg_a.id) not in call_edges


def test_top_level_function_pass_skips_methods_with_same_name(tmp_path):
    """P2 regression: when a module defines both ``def foo()`` at the
    top level and ``class A: def foo(self): bar()`` as a method, the
    top-level call-scan pass used to walk every ``FunctionDef`` in the
    tree (including nested methods), then re-attribute the method's
    body to the top-level ``foo`` because ``file_syms.functions["foo"]``
    happened to exist. The correct behaviour: only ``A.foo`` calls
    ``bar``; ``foo`` does not.
    """

    project = tmp_path / "nested_demo"
    pkg = project / "nested_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "shadow.py").write_text(
        "def bar():\n"
        "    return 1\n"
        "\n"
        "def foo():\n"
        "    return 0\n"
        "\n"
        "class A:\n"
        "    def foo(self):\n"
        "        return bar()\n",
        encoding="utf-8",
    )

    graph = CodeGraphExtractor(project).extract().graph
    by_name = {n.name: n for n in graph.nodes}
    foo_fn = by_name["foo"]
    a_foo = by_name["A.foo"]
    bar_fn = by_name["bar"]
    assert foo_fn.type == ResearchNodeType.CODE_FUNCTION
    assert a_foo.type == ResearchNodeType.CODE_METHOD
    assert bar_fn.type == ResearchNodeType.CODE_FUNCTION

    call_edges = {(e.source, e.type, e.target) for e in graph.edges if e.type == "calls"}
    # The method correctly calls bar.
    assert (a_foo.id, "calls", bar_fn.id) in call_edges
    # The top-level foo must NOT — it has an empty body that returns 0.
    assert (foo_fn.id, "calls", bar_fn.id) not in call_edges
