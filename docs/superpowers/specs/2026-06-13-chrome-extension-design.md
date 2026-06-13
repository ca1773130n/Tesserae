# Tesserae Chrome Extension — Design

**Date:** 2026-06-13
**Status:** Approved (brainstorming complete)

## Goal

A "Clip to Tesserae" Chrome extension (MV3). One click — from a context-menu
item or a keyboard shortcut — captures the current page's readable content (or
the active selection), lets the user add an optional note, and ingests it into
the active Tesserae project so the engine compiles it into the typed graph.
This feeds the **autonomous ingestion** pillar from the browser.

## Capture scope

- **Readable article** extracted with Mozilla Readability, converted to clean
  markdown with Turndown. Page metadata captured: title, url, byline/author,
  published date, site name.
- **Selection override:** if the user has text selected, clip only the
  selection (still as markdown) instead of the whole article.
- **Distilled TL;DR** (HN-style summary): a toggle, **default ON**. Synthesized
  server-side via Tesserae's existing LLM layer; embedded at the top of the
  saved markdown and returned to the popup for display. Never blocks the clip —
  on timeout/failure the clip is saved without a TL;DR.
- **Optional user note:** free-text the user types in the popup, stored in the
  saved markdown.

## Architecture & data flow

```
Web page
  ├─ content script  : Readability → Turndown → markdown + metadata + selection
  ├─ popup           : optional Note + tags + TL;DR toggle, "Clip" button
  └─ service worker  : POST http://localhost:8765/api/clip { url,title,meta,content,selection,note,tags,tldr }
Tesserae (serve.py, localhost)
  └─ POST /api/clip  : → clip.ingest_clip(...)            ← SAME CORE as the MCP tool
Core: tesserae/clip.py::ingest_clip(wiki,*,content,url,title,note,tags,tldr)
  → build markdown (frontmatter + ## TL;DR + ## Note + ## Content)
  → write into <project>/data/ingested/<slug>.md   (existing canonical corpus dir)
  → ingest_sources(wiki, [path], ...) → compile (synchronous)
  → optional TL;DR via existing llm_synthesis layer (bounded; failure = skip)
  → return { status, path, tldr, node_count, edge_count }
MCP tool `ingest`        : agents call the same ingest_clip core
Popup shows returned TL;DR + saved confirmation (or "Tesserae not running")
```

No new "clips" directory is invented: clipped content lands in the project's
existing `data/ingested/` corpus, exactly where `ingest_sources` already places
fetched/ingested sources, so a later full compile reproduces it identically.

## Components & file ownership (disjoint, so parallel build is conflict-free)

### Backend (Tesserae Python repo)

1. **`tesserae/clip.py`** (new) — pure, testable core.
   - `build_clip_markdown(*, content, url, title, note, tags, tldr_text, clipped_at) -> str`
     — YAML frontmatter (`source: web-clip`, `url`, `title`, `tags`, `note`,
     `clipped_at`), then `## TL;DR` (if present), `## Note` (if present),
     `## Content`. Reuse `fetch.py`'s `_slugify` / `_yaml_scalar` helpers.
   - `ingest_clip(wiki, *, content, url, title=None, note=None, tags=None, tldr=True, clipped_at=None) -> dict`
     — synthesize TL;DR (when `tldr`), build markdown, write
     `data/ingested/<slug>.md`, call `ingest_sources(wiki, [path])`, return a
     report dict `{status, path, tldr, node_count, edge_count}`.
   - `_summarize(content) -> str | None` — bounded call into the existing LLM
     synthesis layer; returns `None` on any failure/timeout (clip still saved).

2. **`tesserae/serve.py`** (edit, owned by serve agent) — add to the existing
   `_AskAwareHandler`:
   - `do_OPTIONS` — CORS preflight; allow `chrome-extension://*` and localhost
     origins, methods `POST, OPTIONS`, header `Content-Type`.
   - `POST /api/clip` branch in `do_POST` — parse JSON body, resolve the served
     project root → wiki, call `clip.ingest_clip(...)`, reply via the existing
     `_send_json` with the report. Mirror the existing `/api/ask` route style.
     Add the same CORS header to `_send_json` responses for the clip route.

3. **`tesserae/mcp_server.py`** (edit, owned by mcp agent) — register a new
   `ingest` tool (list entry + dispatch branch) that wraps `clip.ingest_clip`
   on the active project. Inputs: `content` (required), `url`, `title`, `note`,
   `tags`, `tldr` (default true). Mirror the existing `compile_context` tool's
   registration + dispatch shape exactly.

### Extension (new `extension/` directory at repo root)

4. **`extension/manifest.json`** — MV3. `permissions: [activeTab, scripting,
   storage, contextMenus]`; `host_permissions: ["http://localhost/*",
   "http://127.0.0.1/*"]`; `commands` with `clip-page` default
   `Ctrl+Shift+S` / `Command+Shift+S`; background service worker; options page;
   action popup; icons.
5. **`extension/src/content.js`** — bundled Readability + Turndown; extract
   article or selection; return structured payload to the worker.
6. **`extension/src/background.js`** — service worker: context-menu item,
   command handler, message routing, `POST /api/clip`, error surfacing.
7. **`extension/src/popup.html` / `popup.js`** — title, content preview, Note
   textarea, tags input, TL;DR toggle, Clip button → shows returned TL;DR +
   success, or "Tesserae not running — start `tesserae serve`".
8. **`extension/src/options.html` / `options.js`** — endpoint URL (default
   `http://localhost:8765`), default tags, default capture mode, default
   TL;DR toggle; persisted via `chrome.storage.sync`.
9. **`extension/icons/`** — 16/48/128 px icons.
10. **`extension/vendor/`** — pinned Readability.js + Turndown.js (bundled, no
    network at runtime).
11. **`extension/README.md`** — load-unpacked instructions + shortcut rebinding.

### Tests & docs

12. **`tests/test_clip.py`** (new) — unit tests for `build_clip_markdown`
    (frontmatter, TL;DR/Note sections present/absent, selection vs article) and
    `ingest_clip` against a tmp project (monkeypatch the summarizer; assert file
    written under `data/ingested/` and report shape).
13. **`tests/test_serve_clip.py`** (new) — spin a `ThreadingTCPServer` with the
    ask-aware handler against a tmp project (mirror `test_mcp_server_ask.py` /
    existing serve tests); POST `/api/clip`; assert 200 + report + CORS header;
    assert `OPTIONS` preflight returns the CORS headers.
14. **`tests/test_mcp_ingest.py`** (new) — call the `ingest` MCP tool against a
    tmp project; assert a node lands in the graph.
15. **`docs/integrations/chrome-extension.md`** (new) + a README link.

## Error handling

- Endpoint unreachable → popup shows a clear "start `tesserae serve`" message.
- TL;DR synthesis timeout/failure → clip saved without TL;DR, `tldr: null`.
- Oversized page → content capped (e.g. 200 KB) before POST.
- Non-localhost origin or non-JSON body → 400/403 from the handler.
- No active/served project → handler returns a 409 with guidance.

## Scope guardrails (YAGNI)

- Localhost only, single user, origin check only (no auth/tokens).
- Chrome/Chromium MV3 only — no Firefox/Edge port in v1.
- No graph browsing inside the extension (separate, not selected).
- No cloud sync. Selection clip overrides full-article when text is selected.

## Testing strategy

Python tests run under the repo's existing `pytest` setup. Extension verified
by load-unpacked + a manual clip against a running `tesserae serve` (documented
in the extension README); a tiny payload-builder assertion is included inline.
