"""Structural org-parent inference — infer_org_parents (spec 2026-07-19 §3)."""
from __future__ import annotations

from tesserae.agent_identity import (
    DEFAULT_ROLE,
    ORG_ROOT,
    build_agent_key,
    infer_org_parents,
)


def _key(harness: str, account: str, role: str) -> str:
    return build_agent_key(harness, account, role)


def test_subagent_role_parents_to_sibling_default() -> None:
    main = _key("claude-code", "me", DEFAULT_ROLE)
    reviewer = _key("claude-code", "me", "reviewer")
    parents = infer_org_parents([main, reviewer])
    assert parents[reviewer] == main
    assert parents[main] == ORG_ROOT


def test_default_role_parents_to_org_root() -> None:
    main = _key("claude-code", "me", DEFAULT_ROLE)
    assert infer_org_parents([main]) == {main: ORG_ROOT}


def test_orphan_subagent_without_sibling_default_parents_to_org_root() -> None:
    reviewer = _key("claude-code", "me", "reviewer")
    parents = infer_org_parents([reviewer])
    assert parents[reviewer] == ORG_ROOT


def test_multi_account_isolation() -> None:
    a1_default = _key("claude-code", "a1@x.com", DEFAULT_ROLE)
    a1_reviewer = _key("claude-code", "a1@x.com", "reviewer")
    a2_default = _key("claude-code", "a2@x.com", DEFAULT_ROLE)
    a2_reviewer = _key("claude-code", "a2@x.com", "reviewer")
    parents = infer_org_parents([a1_default, a1_reviewer, a2_default, a2_reviewer])
    # a1's reviewer parents to a1's default, never a2's.
    assert parents[a1_reviewer] == a1_default
    assert parents[a2_reviewer] == a2_default
    assert parents[a1_default] == ORG_ROOT
    assert parents[a2_default] == ORG_ROOT


def test_multi_harness_isolation() -> None:
    # Same account slug, different harness — no cross-harness parenting.
    cc_default = _key("claude-code", "me", DEFAULT_ROLE)
    codex_reviewer = _key("codex", "me", "reviewer")
    parents = infer_org_parents([cc_default, codex_reviewer])
    # No codex:me:default observed, so the codex reviewer falls back to root.
    assert parents[codex_reviewer] == ORG_ROOT
    assert parents[cc_default] == ORG_ROOT


def test_totality_every_key_gets_a_parent() -> None:
    keys = [
        _key("claude-code", "me", DEFAULT_ROLE),
        _key("claude-code", "me", "reviewer"),
        _key("claude-code", "me", "planner"),
        _key("codex", "you", "scout"),
    ]
    parents = infer_org_parents(keys)
    assert set(parents) == set(keys)
    for value in parents.values():
        assert value  # never empty


def test_determinism_independent_of_input_order() -> None:
    keys = [
        _key("claude-code", "me", "reviewer"),
        _key("claude-code", "me", DEFAULT_ROLE),
        _key("codex", "you", "scout"),
    ]
    a = infer_org_parents(keys)
    b = infer_org_parents(list(reversed(keys)))
    c = infer_org_parents(keys + keys)  # duplicates collapse
    assert a == b == c


def test_org_root_and_malformed_keys_parent_to_root() -> None:
    # Non-canonical keys (not 3 components) never guess a sibling default.
    parents = infer_org_parents([ORG_ROOT, "loose", "a:b:c:d"])
    assert parents[ORG_ROOT] == ORG_ROOT
    assert parents["loose"] == ORG_ROOT
    assert parents["a:b:c:d"] == ORG_ROOT


def test_no_io_no_mutation_of_input() -> None:
    keys = [_key("claude-code", "me", "reviewer")]
    snapshot = list(keys)
    infer_org_parents(keys)
    assert keys == snapshot
