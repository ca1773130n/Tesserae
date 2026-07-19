"""Role-grade agent identity + org registry (spec 2026-07-19 §3, Phase 1)."""
from __future__ import annotations

import json

import pytest

from tesserae.agent_identity import (
    AGENT_REGISTRY_RELPATH,
    AgentRegistry,
    DEFAULT_ROLE,
    ORG_ROOT,
    account_slug_for_root,
    build_agent_key,
    observed_agent_keys,
    resolve_agent_key,
    sanitize_agent_key,
    session_agent_keys,
)
from tesserae.harness_sessions import HarnessSession


def make_session(**overrides) -> HarnessSession:
    base = dict(
        id="claude-code:s1:s1",
        slug="s1",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo",
        project_root="/work/demo",
        started_at="2026-01-01T00:00:00Z",
        metadata={"config_root": "/home/u/.claude"},
    )
    base.update(overrides)
    return HarnessSession(**base)


# ---------------- account_slug_for_root ----------------


def test_account_slug_prefers_email_marker_filename(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    # Only the FILENAME is parsed — contents (tokens) must never be read, so
    # deliberately write non-JSON garbage where the OAuth payload would be.
    (root / "claude-dev@example.com.json").write_text("SECRET TOKENS", encoding="utf-8")
    assert account_slug_for_root(root) == "dev@example.com"


def test_account_slug_codex_marker_strips_plan_suffix(tmp_path):
    root = tmp_path / ".codex"
    root.mkdir()
    (root / "codex-dev@example.com-pro.json").write_text("{}", encoding="utf-8")
    assert account_slug_for_root(root) == "dev@example.com"


def test_account_slug_falls_back_to_basename(tmp_path):
    root = tmp_path / ".claude-work"
    root.mkdir()
    assert account_slug_for_root(root) == "claude-work"
    # No usable root at all -> stable sentinel, never a crash.
    assert account_slug_for_root(None) == "unknown"
    assert account_slug_for_root("") == "unknown"


def test_account_slug_is_path_independent(tmp_path):
    # Same account marker under two differently-named parents (a renamed $HOME)
    # must yield the same slug — identity survives directory moves.
    home_a = tmp_path / "home-a" / ".claude"
    home_b = tmp_path / "renamed-home" / ".claude"
    for root in (home_a, home_b):
        root.mkdir(parents=True)
        (root / "claude-dev@example.com.json").write_text("{}", encoding="utf-8")
    assert account_slug_for_root(home_a) == account_slug_for_root(home_b) == "dev@example.com"
    # Basename fallback is equally parent-independent.
    bare_a = tmp_path / "home-a" / ".codex-alt"
    bare_b = tmp_path / "renamed-home" / ".codex-alt"
    bare_a.mkdir()
    bare_b.mkdir()
    assert account_slug_for_root(bare_a) == account_slug_for_root(bare_b) == "codex-alt"


def test_account_slug_resolves_symlink_to_real_root(tmp_path):
    # ~/.claude is commonly a symlink to the active account dir; symlink and
    # real dir must resolve to ONE account, not two.
    real = tmp_path / ".claude-personal"
    real.mkdir()
    link = tmp_path / ".claude"
    link.symlink_to(real)
    assert account_slug_for_root(link) == account_slug_for_root(real) == "claude-personal"


# ---------------- resolve_agent_key priority tiers ----------------


def test_tier1_subagent_descriptor_type_wins(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register("claude-code:elsewhere:planner", match=[{"harness": "claude-code"}])
    session = make_session(metadata={"config_root": "/home/u/.claude"})
    descriptor = {"id": "claude-code:s1:agent-abc", "type": "reviewer"}
    # Descriptor type outranks the registry rule that would otherwise match.
    assert resolve_agent_key(session, registry, subagent=descriptor) == "claude-code:claude:reviewer"


def test_tier2_registry_match_rules(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register(
        "codex:me:planner",
        match=[{"harness": "codex", "cwd": "/work/*"}],
    )
    registry.register(
        "claude-code:me:doc-writer",
        match=[{"label": "Claude*", "slash_command": "/docs"}],
    )
    codex_session = make_session(harness="codex", agent_label="Codex", metadata={})
    assert resolve_agent_key(codex_session, registry) == "codex:me:planner"
    docs_session = make_session(commands_run=["/docs build --all"], metadata={})
    # slash_command matches on prefix-word, label on glob — all fields must fit.
    assert resolve_agent_key(docs_session, registry) == "claude-code:me:doc-writer"
    # A rule field that does not fit blocks the whole rule.
    other_cwd = make_session(harness="codex", project_root="/elsewhere/x", metadata={})
    assert resolve_agent_key(other_cwd, registry) == "codex:unknown:default"


def test_tier3_default_role_fallback():
    session = make_session(metadata={"config_root": "/nonexistent/.claude"})
    assert resolve_agent_key(session) == f"claude-code:claude:{DEFAULT_ROLE}"


def test_resolution_is_total_on_sparse_envelopes():
    # No registry, no config_root, empty metadata — still a stable key.
    bare = make_session(metadata={})
    assert resolve_agent_key(bare) == "claude-code:unknown:default"
    # Untyped subagent descriptor falls through to the session-level tiers.
    assert resolve_agent_key(bare, subagent={"id": "x"}) == "claude-code:unknown:default"


def test_key_is_path_independent_end_to_end(tmp_path):
    roots = []
    for parent in ("home-a", "renamed-home"):
        root = tmp_path / parent / ".claude"
        root.mkdir(parents=True)
        (root / "claude-dev@example.com.json").write_text("{}", encoding="utf-8")
        roots.append(root)
    keys = {
        resolve_agent_key(make_session(metadata={"config_root": str(root)}))
        for root in roots
    }
    assert keys == {"claude-code:dev@example.com:default"}


# ---------------- aliases + org root ----------------


def test_alias_maps_old_envelope_key_onto_canonical_agent(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register(
        "claude-code:me:reviewer",
        aliases=["claude-code:claude:reviewer"],
    )
    session = make_session(metadata={"config_root": "/home/u/.claude"})
    descriptor = {"type": "reviewer"}
    # The envelope-derived key (old account slug) lands on the canonical agent.
    assert resolve_agent_key(session, registry, subagent=descriptor) == "claude-code:me:reviewer"
    assert registry.resolve_alias("claude-code:claude:reviewer") == "claude-code:me:reviewer"
    assert registry.resolve_alias("codex:other:default") == "codex:other:default"


def test_implicit_org_root_parent(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    # Observed-but-undeclared agents and declared agents without a parent both
    # report to the implicit root — zero-config two-level org.
    assert registry.effective_parent("claude-code:me:default") == ORG_ROOT
    registry.register("claude-code:me:reviewer")
    assert registry.effective_parent("claude-code:me:reviewer") == ORG_ROOT


def test_set_parent_builds_org_chart(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register("claude-code:me:manager")
    registry.register("claude-code:me:reviewer")
    registry.set_parent("claude-code:me:reviewer", "claude-code:me:manager")
    assert registry.effective_parent("claude-code:me:reviewer") == "claude-code:me:manager"


# ---------------- registry load/save contracts ----------------


def test_registry_missing_file_yields_empty_default(tmp_path):
    registry = AgentRegistry(tmp_path / "nope" / "registry.json")
    assert registry.load() == {"version": 1, "agents": {}}


def test_registry_save_is_atomic_and_roundtrips(tmp_path):
    registry = AgentRegistry(tmp_path / AGENT_REGISTRY_RELPATH)
    registry.register("claude-code:me:reviewer", label="Code reviewer")
    # Atomic tmp-rename: real file present, no .tmp residue, parseable JSON.
    assert registry.path.exists()
    assert not registry.path.with_suffix(".tmp").exists()
    data = json.loads(registry.path.read_text(encoding="utf-8"))
    assert data["agents"]["claude-code:me:reviewer"]["label"] == "Code reviewer"
    assert registry.load() == data


def test_registry_load_rejects_corrupt_json(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt agent registry"):
        AgentRegistry(path).load()


def test_registry_load_rejects_unknown_parent(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "claude-code:me:reviewer": {"parent": "claude-code:me:ghost"},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown parent"):
        AgentRegistry(path).load()


def test_registry_load_rejects_unsanitized_keys_and_alias_collisions(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"agents": {"Claude Code:Me:Reviewer": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not sanitized"):
        AgentRegistry(path).load()
    path.write_text(
        json.dumps({
            "agents": {
                "claude-code:me:reviewer": {"aliases": ["codex:old:reviewer"]},
                "codex:me:reviewer": {"aliases": ["codex:old:reviewer"]},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="claimed by both"):
        AgentRegistry(path).load()


def test_registry_rejects_unknown_keys_in_mutations(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register("claude-code:me:reviewer")
    with pytest.raises(ValueError, match="Unknown agent"):
        registry.set_parent("claude-code:me:ghost", ORG_ROOT)
    with pytest.raises(ValueError, match="Unknown parent"):
        registry.set_parent("claude-code:me:reviewer", "claude-code:me:ghost")
    with pytest.raises(ValueError, match="unknown fields"):
        registry.register("codex:me:planner", match=[{"harnes": "codex"}])


def test_registry_load_rejects_self_parent_and_cycles(tmp_path):
    """Self-parents and hand-edited cycles must fail loud at load (spec §3.2)
    — a silently detached subtree is a silently wrong org chart."""
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({
            "agents": {"claude-code:me:reviewer": {"parent": "claude-code:me:reviewer"}},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parent cycle"):
        AgentRegistry(path).load()
    path.write_text(
        json.dumps({
            "agents": {
                "claude-code:me:a": {"parent": "claude-code:me:b"},
                "claude-code:me:b": {"parent": "claude-code:me:a"},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parent cycle"):
        AgentRegistry(path).load()


def test_registry_mutations_reject_self_parent_and_cycles(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register("claude-code:me:a")
    registry.register("claude-code:me:b")
    registry.register("claude-code:me:c")
    with pytest.raises(ValueError, match="own parent"):
        registry.set_parent("claude-code:me:a", "claude-code:me:a")
    with pytest.raises(ValueError, match="parent cycle"):
        registry.register("claude-code:me:a", parent="claude-code:me:a")
    # a -> b -> c is fine; c -> a would close the loop, at any chain depth.
    registry.set_parent("claude-code:me:a", "claude-code:me:b")
    registry.set_parent("claude-code:me:b", "claude-code:me:c")
    with pytest.raises(ValueError, match="would create a cycle"):
        registry.set_parent("claude-code:me:c", "claude-code:me:a")
    # The failed reparent left the registry untouched and loadable.
    assert registry.effective_parent("claude-code:me:c") == ORG_ROOT


def test_registry_save_is_byte_canonical_regardless_of_mutation_path(tmp_path):
    """registry.json is a determinism-bearing artifact: the same logical
    content must serialize to the same bytes no matter which mutation order
    produced it (register order used to leak insertion order into the file)."""
    reg_ab = AgentRegistry(tmp_path / "ab" / "registry.json")
    reg_ab.register("claude-code:me:a")
    reg_ab.register("claude-code:me:b")
    reg_ba = AgentRegistry(tmp_path / "ba" / "registry.json")
    reg_ba.register("claude-code:me:b")
    reg_ba.register("claude-code:me:a")
    assert reg_ab.path.read_bytes() == reg_ba.path.read_bytes()


def test_registry_duck_types_project_registry_list_projects(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    registry.register("codex:me:planner")
    registry.register("claude-code:me:reviewer", label="Code reviewer")
    listed = registry.list_projects()
    # Same sorted list-of-dicts envelope ProjectRegistry.list_projects returns.
    assert [entry["name"] for entry in listed["projects"]] == [
        "claude-code:me:reviewer",
        "codex:me:planner",
    ]
    assert listed["projects"][0]["label"] == "Code reviewer"
    assert listed["projects"][0]["parent"] == ORG_ROOT


# ---------------- corpus-level helpers ----------------


def test_session_agent_keys_covers_parent_and_subagents():
    session = make_session(
        metadata={
            "config_root": "/home/u/.claude",
            "subagents": [
                {"id": "a", "type": "reviewer"},
                {"id": "b", "type": "planner"},
                {"id": "c"},  # untyped -> falls back to the session default key
            ],
        },
    )
    assert session_agent_keys(session) == [
        "claude-code:claude:default",
        "claude-code:claude:planner",
        "claude-code:claude:reviewer",
    ]


def test_observed_agent_keys_yields_role_diversity():
    # The Phase-1 ship gate: a normal corpus must surface >= 2 distinct agents.
    sessions = [
        make_session(metadata={"config_root": "/home/u/.claude"}),
        make_session(
            id="claude-code:s2:s2",
            metadata={
                "config_root": "/home/u/.claude",
                "subagents": [{"id": "a", "type": "reviewer"}],
            },
        ),
        make_session(id="codex:s3", harness="codex", agent_label="Codex", metadata={"config_root": "/home/u/.codex"}),
    ]
    keys = observed_agent_keys(sessions)
    assert keys == [
        "claude-code:claude:default",
        "claude-code:claude:reviewer",
        "codex:codex:default",
    ]
    assert len(keys) >= 2


def test_sanitize_and_build_agent_key_lowercase_stable():
    # stable_id lowercases its seed, so keys are normalized at construction.
    assert build_agent_key("Claude-Code", "Dev@Example.COM", "Reviewer") == (
        "claude-code:dev@example.com:reviewer"
    )
    assert sanitize_agent_key("claude-code:me:reviewer") == "claude-code:me:reviewer"
    assert sanitize_agent_key("") == DEFAULT_ROLE
