"""Task 5: `tesserae project evolve` distills feedback; compile exposes the flag."""

from __future__ import annotations

import json
from pathlib import Path

from tesserae import cli
from tesserae.extraction_feedback import FeedbackEvent, append_events
from tesserae.extraction_guidance import MIN_EVENTS
from tesserae.project import ProjectWiki


def _init_project(tmp_path: Path) -> ProjectWiki:
    wiki = ProjectWiki(tmp_path)
    wiki.root.mkdir(parents=True, exist_ok=True)
    wiki.paths.config.write_text(json.dumps({"name": "evolve-test"}), encoding="utf-8")
    return wiki


def _seed_cluster(wiki: ProjectWiki, n: int) -> None:
    events = [
        FeedbackEvent(
            source="vault_override", target_extractor="doc_graph",
            node_type="Claim", field="description", action="replace",
            node_id=f"Claim:{i}", source_path="docs/a.md",
            before_value=f"verbose framing number {i}", after_value=f"concise {i}",
            negative_value=f"verbose framing number {i}",
        )
        for i in range(n)
    ]
    append_events(wiki.paths.extraction_feedback, events)


def test_evolve_cli_writes_guidance_markdown(tmp_path: Path, capsys):
    wiki = _init_project(tmp_path)
    _seed_cluster(wiki, MIN_EVENTS)

    # No LLM client is configured under tmp/test conditions; evolve must still
    # produce deterministic-fallback bullets and exit cleanly.
    rc = cli.main(["project", "evolve", "--project", str(tmp_path)])
    assert rc == 0

    guidance = wiki.paths.extraction_guidance.read_text(encoding="utf-8")
    assert "## Extractor:" in guidance
    assert "### Node Type: Claim" in guidance

    out = capsys.readouterr().out
    assert "guidance at" in out


def test_compile_help_lists_use_extraction_feedback(capsys):
    # `project compile --help` should advertise the new flag.
    try:
        cli.main(["project", "compile", "--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "--use-extraction-feedback" in out
