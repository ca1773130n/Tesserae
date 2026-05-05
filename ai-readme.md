# LLM-Wiki — agent guide

This site is an auto-generated wiki layer over a research graph. Everything is content-hashed and idempotent; routes are stable across compiles.

## Where to look first

- `/llms.txt` — terse table of every wiki page.
- `/llms-full.txt` — full text of every wiki page (capped at ~5MB).
- `/graph.jsonld` — schema.org JSON-LD of every wiki-layer node.
- `/sitemap.xml` — every renderable URL with a last-modified timestamp.
- `/rss.xml` — the latest 30 synthesis pages (digests, weekly rollups).

## Per-page siblings

Every `path/foo.html` is paired with a `path/foo.txt` (plain text) and a `path/foo.json` (structured record). Use the `.json` for programmatic reads.

## Wiki-layer kinds

- `/sources/` — 69 page(s).
- `/papers/` — 47 page(s).
- `/repos/` — 127 page(s).
- `/concepts/` — 19 page(s).
- `/entities/` — 350 page(s).
- `/topics/` — 8 page(s).
- `/syntheses/` — 36 page(s).
- `/questions/` — 4 page(s).

## What is *not* surfaced

Code-graph nodes (CodeClass / CodeFunction / CodeModule / Dependency / SourceFile) and assertion-layer nodes (Claim variants / EvidenceSpan) live in `graph.json` for MCP and Cognee consumers, but they have no HTML route and no entry in `search-index.json`.
