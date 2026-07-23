"""Tests for :func:`tesserae.context_compiler.fit_to_budget` (invariant CTX-01).

The helper is the single budget enforcer for every LLM-facing MCP response
(§5.3): per-entry truncation to ``min(len(entry), budget_chars // 8)``,
deterministic input order, greedy admission that stops before overflow, exactly
one O(1) continuation line when entries are dropped, and ``budget_chars <= 0``
as the uncapped passthrough (compile_context's ``budget=0`` invariant).
"""

from __future__ import annotations

from tesserae.context_compiler import (
    DEFAULT_HEADER_RESERVE,
    BudgetFit,
    fit_to_budget,
)


def test_empty_input_is_empty_fit() -> None:
    fit = fit_to_budget([], 32_000)
    assert fit.entries == []
    assert fit.dropped == 0
    assert fit.cursor == 0
    assert fit.continuation is None


def test_budget_zero_is_uncapped_passthrough() -> None:
    entries = ["x" * 100_000, "y" * 50_000]
    fit = fit_to_budget(entries, 0)
    # Entries pass through byte-identical: no truncation, no continuation.
    assert fit.entries == entries
    assert fit.dropped == 0
    assert fit.continuation is None


def test_single_giant_entry_truncated_to_per_entry_cap() -> None:
    budget = 32_000
    fit = fit_to_budget(["g" * 1_000_000], budget)
    # The per-entry cap (budget // 8) admits even a graph-sized entry.
    assert len(fit.entries) == 1
    assert len(fit.entries[0]) <= budget // 8
    assert fit.dropped == 0
    assert fit.continuation is None


def test_exact_fit_boundary_admits_without_continuation() -> None:
    budget = 8_000
    available = budget - DEFAULT_HEADER_RESERVE
    # Seven 1000-char entries + one 400-char entry == available exactly.
    entries = ["a" * 1_000] * 7 + ["b" * (available - 7_000)]
    fit = fit_to_budget(entries, budget)
    assert fit.entries == entries
    assert sum(len(e) for e in fit.entries) == available
    assert fit.dropped == 0
    assert fit.continuation is None


def test_one_char_past_boundary_drops_with_continuation() -> None:
    budget = 8_000
    available = budget - DEFAULT_HEADER_RESERVE
    entries = ["a" * 1_000] * 7 + ["b" * (available - 7_000), "c"]
    fit = fit_to_budget(entries, budget)
    assert len(fit.entries) == 8
    assert fit.dropped == 1
    assert fit.cursor == 8
    assert fit.continuation == "+1 more, cursor=8"


def test_greedy_admission_stops_at_first_overflow() -> None:
    # Order is a contract: a later small entry must NOT leapfrog an earlier
    # overflowing one — kept entries are always a prefix of the input.
    budget = 8_000
    available = budget - DEFAULT_HEADER_RESERVE  # 7400; per-entry cap 1000
    entries = ["a" * 900] * 8 + ["b" * 900, "c" * 5]
    fit = fit_to_budget(entries, budget)
    # 8 x 900 = 7200 fits; the 9th overflows and the tiny 10th must NOT
    # leapfrog it even though it would fit.
    assert fit.entries == entries[:8]
    assert fit.dropped == 2
    assert fit.continuation == "+2 more, cursor=8"


def test_header_reserve_is_respected() -> None:
    budget = 4_000
    entries = ["e" * 300] * 50
    fit = fit_to_budget(entries, budget)
    assert sum(len(e) for e in fit.entries) <= budget - DEFAULT_HEADER_RESERVE
    assert fit.dropped == 50 - len(fit.entries)


def test_deterministic_across_calls() -> None:
    entries = [f"entry-{i} " * (i + 1) for i in range(64)]
    first = fit_to_budget(entries, 6_000)
    second = fit_to_budget(entries, 6_000)
    assert first == second
    assert isinstance(first, BudgetFit)


def test_render_mode_matches_legacy_distill_loop() -> None:
    # The parameterized render mode must reproduce agent_distill's
    # assemble-then-truncate loop byte-for-byte (§5.3: artifact bytes are the
    # oracle). Replicate the legacy loop here and compare.
    entries = list(range(100))

    def render(kept, dropped):
        return "e" * (40 * len(kept)) + f"[+{dropped} truncated]"

    budget = 2_000

    legacy_entries = list(entries)
    legacy_truncated = 0
    while True:
        rendered = render(legacy_entries, legacy_truncated)
        if len(rendered) <= budget or not legacy_entries:
            break
        drop = max(1, min(len(legacy_entries), 32))
        legacy_entries = legacy_entries[:-drop]
        legacy_truncated += drop

    fit = fit_to_budget(entries, budget, render=render)
    assert fit.entries == legacy_entries
    assert fit.dropped == legacy_truncated
    assert fit.payload == rendered
    assert fit.continuation == f"+{legacy_truncated} more, cursor={len(legacy_entries)}"


def test_render_mode_under_budget_never_drops() -> None:
    fit = fit_to_budget([1, 2, 3], 1_000, render=lambda kept, dropped: "k" * len(kept))
    assert fit.entries == [1, 2, 3]
    assert fit.dropped == 0
    assert fit.payload == "kkk"
    assert fit.continuation is None


def test_render_mode_budget_zero_renders_once_uncapped() -> None:
    calls = []

    def render(kept, dropped):
        calls.append((list(kept), dropped))
        return "r" * 10_000

    fit = fit_to_budget(["a", "b"], 0, render=render)
    assert fit.entries == ["a", "b"]
    assert fit.dropped == 0
    assert fit.payload == "r" * 10_000
    assert calls == [(["a", "b"], 0)]
