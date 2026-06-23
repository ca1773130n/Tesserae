"""Web-clip ingestion: turn a browser clip (URL + content) into a tracked source.

A "clip" is content the user grabbed from a web page — the full readable text or a
highlighted selection — plus light metadata (title, note, tags). This module:

1. Renders it into a deterministic markdown file with YAML front-matter
   (``build_clip_markdown``), reusing the same front-matter helpers the URL
   fetcher uses so clips and fetched pages share one on-disk convention.
2. Persists the file under ``<project_root>/data/ingested/`` and feeds it into
   the normal ingest pipeline (``ingest_clip``), returning a compact report.
3. Optionally prepends a best-effort LLM TL;DR (``_summarize``) — never fatal.

Imports are kept light: the LLM layer is imported lazily inside ``_summarize`` so
that simply importing this module never pulls in optional/CLI-backed dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Reuse the URL-fetcher's deterministic slug + front-matter helpers so clips and
# fetched pages share one filename / front-matter convention on disk.
from .ingest.fetch import _render_frontmatter, _slugify, _yaml_scalar  # noqa: F401

# How much clip text we hand the summarizer. Bounded so a huge page can't blow
# up the prompt (and the LLM cost / latency) for a 2-sentence TL;DR.
_SUMMARY_INPUT_MAX = 8000


def build_clip_markdown(
    *,
    content: str,
    url: str,
    title: str,
    note: Optional[str] = None,
    tags: Optional[List[str]] = None,
    tldr_text: Optional[str] = None,
    clipped_at: Optional[str] = None,
) -> str:
    """Render a clip as a markdown document with YAML front-matter.

    Front-matter keys (sorted deterministically by ``_render_frontmatter``):
    ``source: web-clip``, ``url``, ``title``, and — when provided — ``note`` and
    ``clipped_at``. ``tags`` is rendered as a YAML list (sequence) rather than a
    scalar so downstream tooling reads it as a real list.

    Body sections, emitted in order when their content is present:
    ``## TL;DR`` (from ``tldr_text``), ``## Note`` (from ``note``), and always a
    final ``## Content`` section holding the clipped text.
    """
    meta = {
        "source": "web-clip",
        "url": url,
        "title": title,
    }
    if note:
        meta["note"] = note
    if clipped_at:
        meta["clipped_at"] = clipped_at

    # _render_frontmatter only renders scalar values; render the block from the
    # scalar meta, then splice the tags sequence in by hand so it stays a list.
    frontmatter = _render_frontmatter(meta)
    if tags:
        tag_lines = ["tags:"] + [f"  - {_yaml_scalar(tag)}" for tag in tags]
        # frontmatter ends with "---\n"; insert the tags block before the
        # closing delimiter so keys stay inside the front-matter fence.
        closing = "---\n"
        assert frontmatter.endswith(closing)
        frontmatter = frontmatter[: -len(closing)] + "\n".join(tag_lines) + "\n" + closing

    sections: List[str] = []
    if tldr_text:
        sections.append("## TL;DR\n\n" + tldr_text.strip())
    if note:
        sections.append("## Note\n\n" + note.strip())
    sections.append("## Content\n\n" + content.strip())

    return frontmatter + "\n" + "\n\n".join(sections) + "\n"


def _summarize(content: str) -> Optional[str]:
    """Best-effort bounded TL;DR via the CLI-backed LLM layer. ``None`` on failure.

    Uses ``run_claude_cli`` (no API key required). Imported lazily so this module
    imports cleanly even when the LLM layer / ``claude`` binary is unavailable.
    Any exception — missing binary, non-zero exit, timeout, import error — is
    swallowed and treated as "no summary".
    """
    text = (content or "").strip()
    if not text:
        return None
    try:
        # Lazy import: keeps top-level imports light and lets the module load
        # even if the extractor's dependencies are missing.
        from .llm_extractor import ClaudeCLIResearchExtractor, run_claude_cli

        prompt = (
            "Summarize the following in 2 sentences as a TL;DR. "
            "Return only the summary, no preamble:\n\n" + text[:_SUMMARY_INPUT_MAX]
        )
        # Reuse the extractor's config-dir discovery (explicit → env →
        # ~/.claude* → fallback) and try each dir on failure, mirroring how the
        # extractor recovers from auth/config issues across dirs.
        config_dirs = ClaudeCLIResearchExtractor().config_dirs
        last_error: Optional[Exception] = None
        for config_dir in config_dirs:
            try:
                out = run_claude_cli(prompt, config_dir, "sonnet", 60).strip()
                if out:
                    return out
            except Exception as exc:  # try the next config dir
                last_error = exc
        # Exhausted all config dirs without a usable summary — skip TL;DR.
        _ = last_error
        return None
    except Exception:
        # ANY failure (import error, missing binary, etc.) → no summary, never raise.
        return None


def ingest_clip(
    wiki,
    *,
    content: str,
    url: str,
    title: Optional[str] = None,
    note: Optional[str] = None,
    tags: Optional[List[str]] = None,
    tldr: bool = True,
    clipped_at: Optional[str] = None,
    lock_wait: Optional[float] = 60.0,
) -> dict:
    """Persist a web clip as a tracked source and run it through the ingest pipeline.

    ``wiki`` must be a ``tesserae.project.ProjectWiki`` (uses ``wiki.project_root``
    and is passed to ``ingest_sources``). When ``tldr`` is true a best-effort
    TL;DR is computed (``None`` on any failure) and prepended to the markdown.

    The clip is written to ``<project_root>/data/ingested/<slug>.md`` (slug derived
    from the URL) and ingested via ``ingest_sources(wiki, [path])``.

    Returns ``{status, path, tldr, node_count, edge_count}``.
    """
    # Lazy import: avoid pulling the orchestrator (and its transitive deps) into
    # context just by importing this module.
    from .ingest.orchestrator import ingest_sources

    effective_title = title or url
    clipped_at = clipped_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tldr_text = _summarize(content) if tldr else None

    markdown = build_clip_markdown(
        content=content,
        url=url,
        title=effective_title,
        note=note,
        tags=tags,
        tldr_text=tldr_text,
        clipped_at=clipped_at,
    )

    dest_dir = Path(wiki.project_root) / "data" / "ingested"
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Slug from the URL when present; raw-content clips (MCP path) may have no
    # URL, so fall back to the title. ``_slugify`` stays collision-safe either way.
    dest_path = dest_dir / f"{_slugify(url or effective_title)}.md"
    dest_path.write_text(markdown, encoding="utf-8")

    # Feed the persisted file through the normal ingest path. ingest_sources
    # returns a report dict with node_count / edge_count among other keys.
    #
    # The clip markdown is ALREADY on disk above, so if a compile/refresh is
    # holding the project lock we must not fail and lose the clip: wait up to
    # ``lock_wait`` seconds for the lock, and if it's still held, return a
    # "deferred" report (the file is saved; the next compile — or the engine
    # daemon — will ingest it) instead of a 500.
    from .locking import CompileLockHeldError

    try:
        report = ingest_sources(wiki, [str(dest_path)], lock_wait=lock_wait)
    except CompileLockHeldError as exc:
        return {
            "status": "deferred",
            "path": str(dest_path),
            "tldr": tldr_text,
            "node_count": 0,
            "edge_count": 0,
            "detail": (
                "clip saved but not yet ingested — a compile/refresh is running; "
                "it will be picked up by the next compile. " + str(exc)
            ),
        }

    return {
        "status": "ok",
        "path": str(dest_path),
        "tldr": tldr_text,
        "node_count": report.get("node_count", 0),
        "edge_count": report.get("edge_count", 0),
    }
