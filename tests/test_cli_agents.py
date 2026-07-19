"""`tesserae agents init|list|set-parent|rename` — the role-grade agent org registry CLI."""

from __future__ import annotations

import json

from tesserae.cli import main
from tesserae.harness_sessions import HarnessSession, HarnessSessionStore
from tesserae.project import ProjectWiki


def _registry_payload(proj):
    path = proj / ".tesserae" / "agents" / "registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_project(tmp_path):
    """Project with two imported sessions yielding >=2 role-distinct agents.

    The claude-code session carries a typed subagent descriptor (reviewer),
    so the observed keys span three agents across two roles — the Phase-1
    ship gate for `tesserae agents list`.
    """
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ProjectWiki.init(proj, sources=["docs"])
    store = HarnessSessionStore(proj / ".tesserae" / "harness_sessions")
    store.write_sessions(
        [
            HarnessSession(
                id="s1",
                slug="fix-bug",
                harness="claude-code",
                agent_label="Claude Code",
                project_name="proj",
                project_root=str(proj),
                started_at="2026-07-01T10:00:00Z",
                metadata={
                    "config_root": str(tmp_path / "claude-home"),
                    "subagents": [
                        {
                            "id": "claude-code:s1:agent-abc",
                            "title": "Review pass",
                            "type": "reviewer",
                        }
                    ],
                },
            ),
            HarnessSession(
                id="s2",
                slug="plan-work",
                harness="codex",
                agent_label="Codex",
                project_name="proj",
                project_root=str(proj),
                started_at="2026-07-02T10:00:00Z",
                metadata={"config_root": str(tmp_path / "codex-home")},
            ),
        ]
    )
    return proj


def test_agents_init_writes_proposed_registry(tmp_path, capsys):
    proj = _seed_project(tmp_path)

    assert main(["agents", "init", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "3 agent(s)" in out

    payload = _registry_payload(proj)
    assert payload["version"] == 1
    assert sorted(payload["agents"]) == [
        "claude-code:claude-home:default",
        "claude-code:claude-home:reviewer",
        "codex:codex-home:default",
    ]
    # Everyone parented to the implicit root; labels come from the envelope
    # (agent_label for parent sessions, descriptor type for subagents).
    for entry in payload["agents"].values():
        assert entry["parent"] == "org:root"
    assert payload["agents"]["claude-code:claude-home:default"]["label"] == "Claude Code"
    assert payload["agents"]["claude-code:claude-home:reviewer"]["label"] == "reviewer"


def test_agents_init_refuses_overwrite_without_force(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    assert main(["agents", "init", "--project", str(proj)]) == 0

    capsys.readouterr()
    assert main(["agents", "init", "--project", str(proj)]) == 1
    err = capsys.readouterr().err
    assert "--force" in err

    assert main(["agents", "init", "--project", str(proj), "--force"]) == 0


def test_agents_init_empty_corpus_writes_empty_registry(tmp_path, capsys):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    ProjectWiki.init(proj, sources=["docs"])

    assert main(["agents", "init", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "0 agent(s)" in out
    assert _registry_payload(proj) == {"version": 1, "agents": {}}


def test_agents_list_shows_role_distinct_agents(tmp_path, capsys):
    proj = _seed_project(tmp_path)

    # Observed-only listing works before any registry exists.
    assert main(["agents", "list", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "Agents: 3" in out
    assert "claude-code:claude-home:reviewer" in out
    assert "[observed]" in out

    # After init the same keys are registered, with parent + session counts.
    main(["agents", "init", "--project", str(proj)])
    capsys.readouterr()
    assert main(["agents", "list", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "[registered]" in out
    assert "parent=org:root" in out
    assert "sessions=1" in out


def test_agents_list_json(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])

    capsys.readouterr()
    assert main(["agents", "list", "--project", str(proj), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["key"] for row in rows] == [
        "claude-code:claude-home:default",
        "claude-code:claude-home:reviewer",
        "codex:codex-home:default",
    ]
    reviewer = next(r for r in rows if r["key"].endswith(":reviewer"))
    assert reviewer["parent"] == "org:root"
    assert reviewer["sessions"] == 1
    assert reviewer["registered"] is True


def test_agents_set_parent_happy_path(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])

    capsys.readouterr()
    rc = main(
        [
            "agents",
            "set-parent",
            "claude-code:claude-home:reviewer",
            "claude-code:claude-home:default",
            "--project",
            str(proj),
        ]
    )
    assert rc == 0
    assert "now reports to claude-code:claude-home:default" in capsys.readouterr().out
    payload = _registry_payload(proj)
    assert (
        payload["agents"]["claude-code:claude-home:reviewer"]["parent"]
        == "claude-code:claude-home:default"
    )


def test_agents_set_parent_registers_observed_unregistered(tmp_path, capsys):
    # No `agents init` — both ends exist only in session history; set-parent
    # registers them on the fly instead of demanding registry ceremony.
    proj = _seed_project(tmp_path)

    rc = main(
        [
            "agents",
            "set-parent",
            "claude-code:claude-home:reviewer",
            "codex:codex-home:default",
            "--project",
            str(proj),
        ]
    )
    assert rc == 0
    payload = _registry_payload(proj)
    assert (
        payload["agents"]["claude-code:claude-home:reviewer"]["parent"]
        == "codex:codex-home:default"
    )
    assert payload["agents"]["codex:codex-home:default"]["parent"] == "org:root"


def test_agents_set_parent_fails_loud_on_unknowns(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])

    capsys.readouterr()
    rc = main(["agents", "set-parent", "nope:nope:nope", "org:root", "--project", str(proj)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Unknown agent: nope:nope:nope" in err
    assert "Known agents:" in err

    rc = main(
        [
            "agents",
            "set-parent",
            "codex:codex-home:default",
            "nope:nope:nope",
            "--project",
            str(proj),
        ]
    )
    assert rc == 1
    assert "Unknown parent agent: nope:nope:nope" in capsys.readouterr().err

    # Self-parenting is rejected by the registry, surfaced as a CLI error.
    rc = main(
        [
            "agents",
            "set-parent",
            "codex:codex-home:default",
            "codex:codex-home:default",
            "--project",
            str(proj),
        ]
    )
    assert rc == 1
    assert "cannot be its own parent" in capsys.readouterr().err


def test_agents_rename_migrates_dir_registry_and_children(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])
    main(
        [
            "agents",
            "set-parent",
            "claude-code:claude-home:reviewer",
            "codex:codex-home:default",
            "--project",
            str(proj),
        ]
    )
    # Phase-2-style per-agent artifact dir that must move with the rename.
    old_dir = proj / ".tesserae" / "agents" / "codex:codex-home:default"
    old_dir.mkdir(parents=True)
    (old_dir / "notes.json").write_text("{}", encoding="utf-8")

    capsys.readouterr()
    rc = main(
        [
            "agents",
            "rename",
            "codex:codex-home:default",
            "codex:me:default",
            "--project",
            str(proj),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Renamed agent: codex:codex-home:default -> codex:me:default" in out

    assert not old_dir.exists()
    assert (proj / ".tesserae" / "agents" / "codex:me:default" / "notes.json").is_file()

    payload = _registry_payload(proj)
    assert "codex:codex-home:default" not in payload["agents"]
    entry = payload["agents"]["codex:me:default"]
    # Old key survives as an alias so already-imported envelope keys still
    # resolve; the reparented child follows the rename.
    assert entry["aliases"] == ["codex:codex-home:default"]
    assert (
        payload["agents"]["claude-code:claude-home:reviewer"]["parent"] == "codex:me:default"
    )


def test_agents_rename_fails_loud(tmp_path, capsys):
    proj = _seed_project(tmp_path)
    main(["agents", "init", "--project", str(proj)])

    capsys.readouterr()
    rc = main(["agents", "rename", "nope:nope:nope", "codex:me:default", "--project", str(proj)])
    assert rc == 1
    assert "Unknown agent: nope:nope:nope" in capsys.readouterr().err

    rc = main(
        [
            "agents",
            "rename",
            "codex:codex-home:default",
            "claude-code:claude-home:reviewer",
            "--project",
            str(proj),
        ]
    )
    assert rc == 1
    assert "Agent already declared" in capsys.readouterr().err

    # Nothing was mutated by the failed renames.
    assert sorted(_registry_payload(proj)["agents"]) == [
        "claude-code:claude-home:default",
        "claude-code:claude-home:reviewer",
        "codex:codex-home:default",
    ]
