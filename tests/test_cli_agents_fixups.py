"""Usability fixups on the layered-agent CLI (v0.21.x follow-up).

Two fail-loud regressions:

* ``tesserae agents drill "<empty>"`` (e.g. ``drill "$UNSET_VAR"``) must fail
  loud — clean stderr + exit 1 — instead of leaking the ``drill_down``
  ``ValueError`` traceback.
* ``agent_view._missing_artifact_error`` must separate the owner from the key
  list (``child of <owner>: <key>``) and singularize when exactly one key is
  missing (``agent X has …`` / ``child … has …``).
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.agent_view import _missing_artifact_error
from tesserae.cli import main

from tests.test_agent_distill import _base_graph


def _project_with_l0(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    (project / ".tesserae" / "config.json").write_text(
        json.dumps({"name": "proj", "sources": [], "external_tools": [], "memory_backends": {}}),
        encoding="utf-8",
    )
    (project / ".tesserae" / "graph.json").write_text(
        _base_graph().to_json(indent=2), encoding="utf-8"
    )
    return project


# --------------------------------------------------------------------------- drill guard


def test_agents_drill_empty_node_id_fails_loud(tmp_path, capsys):
    project = _project_with_l0(tmp_path)
    capsys.readouterr()

    rc = main(["agents", "drill", "", "--project", str(project)])
    assert rc == 1
    captured = capsys.readouterr()
    # Clean fail-loud line on stderr — no traceback — and nothing on stdout.
    assert "node_id" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


# --------------------------------------------------------------------------- error copy


def test_missing_artifact_error_single_no_owner():
    msg = str(_missing_artifact_error(["claude-code:me:default"], owner=""))
    assert msg.startswith("agent claude-code:me:default has no distilled artifact")
    assert "tesserae distill --agent claude-code:me:default" in msg


def test_missing_artifact_error_single_with_owner():
    msg = str(
        _missing_artifact_error(["claude-code:me:reviewer"], owner="claude-code:me:default")
    )
    # Owner and key are separated by ": " (not run together) and singularized.
    assert msg.startswith(
        "child of claude-code:me:default: claude-code:me:reviewer has no distilled artifact"
    )


def test_missing_artifact_error_multiple_with_owner():
    msg = str(
        _missing_artifact_error(
            ["claude-code:me:reviewer", "claude-code:me:planner"],
            owner="claude-code:me:default",
        )
    )
    assert msg.startswith(
        "children of claude-code:me:default: "
        "claude-code:me:reviewer, claude-code:me:planner have no distilled artifact"
    )
