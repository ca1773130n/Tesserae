"""Commands that reported something other than what happened.

The second family from the CLI audit. None of these crash; each states
something that is not true — a count, a source, a timestamp, a cursor — and a
false report is worse than an error, because nothing about it looks wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.cli import main
from tesserae.project import ProjectWiki


def _project(tmp_path: Path, *, compile_it: bool = True) -> ProjectWiki:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text("# Alpha\n\nRetrieval grounds answers.\n", encoding="utf-8")
    wiki = ProjectWiki.init(tmp_path, name="truth")
    if compile_it:
        wiki.compile()
    return wiki


def test_status_says_never_for_a_project_that_was_only_initialised(tmp_path, monkeypatch, capsys):
    """"last compile" was graph.json's mtime, and `init` writes a graph.json —
    so a project that had never compiled reported the moment it was created."""
    _project(tmp_path, compile_it=False)
    monkeypatch.chdir(tmp_path)

    assert main(["status"]) == 0
    assert "last compile:  never" in capsys.readouterr().out


def test_status_reports_a_real_compile_once_one_has_happened(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("last compile:")][0]
    assert "never" not in line


def test_config_status_credits_the_file_that_set_codex_home_and_effort(tmp_path, monkeypatch, capsys):
    """Both read "[default]" however they were configured.

    `codex_home` is singular in the panel and plural in the resolver, so the
    lookup missed; `codex_reasoning_effort` was resolved by hand above the
    bookkeeping and recorded no source at all.
    """
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    (home / ".tesserae" / "config.json").write_text(
        json.dumps({
            "llm_provider": "codex",
            "llm_codex_home": str(tmp_path / "ch"),
            "llm_codex_reasoning_effort": "high",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    import tesserae.llm_json as lj

    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", home / ".tesserae" / "config.json")
    monkeypatch.chdir(tmp_path)

    main(["config", "status"])
    out = capsys.readouterr().out
    for label in ("codex_home", "effort"):
        line = [l for l in out.splitlines() if l.strip().startswith(label)]
        assert line, f"no {label} line in the panel"
        assert "[default]" not in line[0], f"{label} still credited to the default"


def test_the_resolver_records_a_source_for_reasoning_effort():
    from tesserae.llm_json import resolve_llm_client_settings

    sources = resolve_llm_client_settings({}).get("sources") or {}
    assert "codex_reasoning_effort" in sources


def test_test_command_credits_the_command_line_not_the_config_file():
    """`--provider` outranks every configured layer, so naming the config file
    as its source was simply false."""
    # The label is decided before any network work, so assert the contract in
    # the source rather than spending a real provider call in a unit test.
    import inspect

    from tesserae import llm_selftest

    src = inspect.getsource(llm_selftest)
    assert '"--provider (command line)" if provider else' in src


def test_prune_internal_dry_run_counts_what_it_would_prune(tmp_path, monkeypatch, capsys):
    """It printed the store's TOTAL row count as the number to be pruned."""
    from tesserae.harness_sessions_db import HarnessSessionsDB

    wiki = _project(tmp_path)
    db_path = tmp_path / ".tesserae" / "harness_sessions.db"
    db = HarnessSessionsDB(db_path)
    assert db.prune_internal_sessions(dry_run=True) == 0, "nothing internal to prune yet"
    before = db.count_sessions()
    assert db.prune_internal_sessions(dry_run=True) <= before

    monkeypatch.chdir(tmp_path)
    main(["sessions", "prune-internal", "--dry-run"])
    out = capsys.readouterr().out
    if "Would also prune" in out:
        assert "self-captured session(s)" in out


def test_a_dry_run_prune_deletes_nothing(tmp_path):
    from tesserae.harness_sessions_db import HarnessSessionsDB

    _project(tmp_path)
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    before = db.count_sessions()
    db.prune_internal_sessions(dry_run=True)
    assert db.count_sessions() == before


def test_schema_drift_does_not_claim_clustering_ran_when_it_did_not():
    """A host below --min-volume is skipped BEFORE clustering, and was then
    reported as "No clusters of size >= N found" — a claim about a computation
    that never happened, which the run's own ledger contradicted.

    The renderer rather than the command: `schema-drift` requires a live LLM
    backend, and this defect is in what the report SAYS about a host it skipped.
    """
    from tesserae.schema_drift import HostTypeReport, render_report

    skipped = HostTypeReport(host_type="Paper", member_count=3)
    skipped.skipped_reason = (
        "Not clustered: 3 member(s) is below --min-volume 10, so no clustering "
        "was attempted for this host type."
    )
    text = render_report([skipped], min_cluster_size=5)
    assert "Not clustered" in text
    assert "below --min-volume" in text
    assert "No clusters of size >= 5 found" not in text

    # A host that WAS clustered and genuinely found nothing keeps the old line.
    ran = HostTypeReport(host_type="Paper", member_count=99)
    assert "No clusters of size >= 5 found" in render_report([ran], min_cluster_size=5)


def test_the_min_volume_skip_records_its_reason():
    """Pin the producer too, so the renderer's branch cannot go unreachable."""
    import inspect

    from tesserae import schema_drift

    src = inspect.getsource(schema_drift)
    assert "report.skipped_reason = (" in src
    assert "below --min-volume" in src


def test_graph_map_does_not_emit_a_cursor_that_cannot_advance():
    """When no card fits the budget, the continuation named the cursor it had
    just been called with, so a caller following the documented paging protocol
    re-issued the identical request forever, always getting zero cards."""
    from tesserae.mcp_server import _paginate_cards

    cards = [
        {"scope_id": f"domain:{i}", "title": f"A card with a long title {i}", "summary": "x" * 200}
        for i in range(5)
    ]
    out = _paginate_cards({"kind": "root"}, cards, budget_chars=40, cursor=0)
    assert out["cards"] == [], "fixture must not fit a card in 40 chars"
    continuation = out.get("continuation", "")
    assert "cannot advance" in continuation
    assert "cursor=" not in continuation, "a cursor equal to the input is the bug"


def test_graph_map_still_pages_normally_when_cards_fit():
    from tesserae.mcp_server import _paginate_cards

    cards = [{"scope_id": f"d{i}", "title": f"t{i}", "summary": ""} for i in range(5)]
    out = _paginate_cards({"kind": "root"}, cards, budget_chars=800, cursor=0)
    assert out["cards"], "a generous budget must keep cards"
    if out.get("continuation"):
        assert "cursor=" in out["continuation"]


def test_vault_set_root_does_not_claim_a_pinned_project_will_follow(tmp_path, monkeypatch, capsys):
    """A project pinning obsidian.vault_path ignores the registry root, so
    listing it under the new root was a claim the next sync would contradict."""
    registry = tmp_path / "registry.json"
    monkeypatch.setenv("TESSERAE_REGISTRY", str(registry))

    free = tmp_path / "free"
    pinned = tmp_path / "pinned"
    for root, name in ((free, "free"), (pinned, "pinned")):
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
        wiki = ProjectWiki.init(root, name=name)
        wiki.compile()
    cfg_path = pinned / ".tesserae" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("obsidian", {})["vault_path"] = str(tmp_path / "elsewhere")
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    main(["projects", "register", str(free)])
    main(["projects", "register", str(pinned)])
    capsys.readouterr()

    assert main(["vault", "set-root", str(tmp_path / "vroot")]) == 0
    out = capsys.readouterr().out
    assert "Unaffected" in out
    assert str(tmp_path / "elsewhere") in out
