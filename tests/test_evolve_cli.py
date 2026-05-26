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


# ---------------------------------------------------------------------------
# Codex P2 finding 2: evolve must preserve human curation (edits + deletions)
# and only ADD genuinely-new clusters, rather than rebuilding from scratch.
# ---------------------------------------------------------------------------


def _bullet_texts(wiki: ProjectWiki):
    from tesserae.guidance_markdown import parse_guidance
    md = wiki.paths.extraction_guidance.read_text(encoding="utf-8")
    return [b.text for b in parse_guidance(md)]


def test_evolve_preserves_user_edited_bullet_text(tmp_path: Path):
    wiki = _init_project(tmp_path)
    _seed_cluster(wiki, MIN_EVENTS)

    wiki.evolve()  # first pass: deterministic fallback writes one bullet
    from tesserae.guidance_markdown import parse_guidance, render_guidance
    md = wiki.paths.extraction_guidance.read_text(encoding="utf-8")
    assert "Users repeatedly corrected" in md  # fresh phrasing present

    # User hand-edits the bullet text (the cluster comment / hash is preserved
    # because parse→render round-trips the machine identity).
    bullets = parse_guidance(md)
    assert len(bullets) == 1 and bullets[0].cluster_hash
    bullets[0].text = "MY HAND-CURATED RULE: never touch this."
    wiki.paths.extraction_guidance.write_text(render_guidance(bullets), encoding="utf-8")

    wiki.evolve()  # second pass with identical events
    texts = _bullet_texts(wiki)
    assert "MY HAND-CURATED RULE: never touch this." in texts
    assert not any("Users repeatedly corrected" in t for t in texts)


def test_evolve_does_not_resurrect_deleted_bullet(tmp_path: Path):
    wiki = _init_project(tmp_path)
    _seed_cluster(wiki, MIN_EVENTS)

    wiki.evolve()  # first pass writes one bullet + one cache-file (the ledger)
    assert len(_bullet_texts(wiki)) == 1

    # User deletes the bullet entirely (empty the guidance file).
    wiki.paths.extraction_guidance.write_text(
        "# Tesserae Extraction Guidance\n", encoding="utf-8"
    )

    wiki.evolve()  # same events — the deleted cluster must STAY gone
    assert _bullet_texts(wiki) == []


def test_evolve_adds_brand_new_cluster(tmp_path: Path):
    wiki = _init_project(tmp_path)
    _seed_cluster(wiki, MIN_EVENTS)
    wiki.evolve()
    assert len(_bullet_texts(wiki)) == 1

    # Add events for a DIFFERENT cluster (different node_type) → must appear.
    new_events = [
        FeedbackEvent(
            source="vault_override", target_extractor="doc_graph",
            node_type="Method", field="description", action="replace",
            node_id=f"Method:{i}", source_path="docs/b.md",
            before_value=f"long {i}", after_value=f"short {i}",
            negative_value=f"long {i}",
        )
        for i in range(MIN_EVENTS)
    ]
    append_events(wiki.paths.extraction_feedback, new_events)

    wiki.evolve()
    md = wiki.paths.extraction_guidance.read_text(encoding="utf-8")
    assert "### Node Type: Claim" in md
    assert "### Node Type: Method" in md
    assert len(_bullet_texts(wiki)) == 2
