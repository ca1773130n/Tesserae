# Harness session history

<!-- translations:start -->
<p align="center"><a href="i18n/session-history.ko.md">한국어</a> · <a href="i18n/session-history.zh.md">中文</a> · <a href="i18n/session-history.ja.md">日本語</a> · <a href="i18n/session-history.ru.md">Русский</a> · <a href="i18n/session-history.es.md">Español</a> · <a href="i18n/session-history.fr.md">Français</a> · <a href="../i18n/session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae can import local AI-agent transcripts and render them as project memory under the static site's `sessions/` section.

This feature is intentionally separate from `export-agent-harness`:

- `export-agent-harness` is outbound context for tools such as Claude Code, Codex, Gemini, Cursor, Kiro, and OpenCode.
- `project sessions ...` is inbound history: it normalizes prior Claude Code/Codex sessions for the current project, stores them under `.tesserae/harness_sessions/`, and lets `project build-site` publish session index/detail pages.

## Two ways in: batch import and live monitoring

Session ingestion is no longer batch-only. There are two paths into the same
normalized store:

- **Batch import** — `project sessions discover/import` scans transcript roots
  on demand and writes one-shot. This page documents that flow below.
- **Live monitoring** — the supervisor daemon (`project engine`, alias
  `project daemon`) runs a `SessionTailer` that watches *this project's own*
  Claude Code and Codex transcripts and ingests new turns as they land. Each
  tick seeks to a persisted per-file byte offset, reads only the new bytes,
  and stores complete turns into the SQLite `HarnessSessionsDB`
  (`.tesserae/sqlite.db`) **before** enqueuing a debounced recompile, so the
  compile always reads consistent state. The tailer is scoped to the project's
  own sessions (Claude `projects/<slug>/*.jsonl`; Codex filtered by cwd) and
  resumes from stored offsets after a restart without replaying turns.

Run the live loop with:

```bash
tesserae project engine        # watch sources, coalesce bursts, auto-recompile
tesserae project engine --once # single drain cycle then exit (deterministic)
```

`tesserae project refresh` runs the same ingest → compile → project pipeline
once, in-process, without starting the long-lived watcher (pass
`--skip-sessions` to skip the harness-session discovery scan).

## Privacy model

Both ingestion paths are explicit: the live tailer only runs while you keep
`project engine`/`daemon` alive, and batch discovery only writes with
`--import`. A normal `project compile` or `project build-site` reads
already-normalized sessions from `.tesserae/harness_sessions/` and the live
records in `.tesserae/sqlite.db`, but it does not surprise-scrape private
harness transcript directories on its own.

Imported session records are local project artifacts. Review them before publishing a public site, especially if your transcripts may include secrets, private paths, customer data, or unreleased code.

## Discover and import local sessions

From the project root:

```bash
tesserae project sessions discover --import
```

Discovery scans local Claude Code and Codex transcript roots that belong to the current project working directory. Use `--root` to scan a specific config directory, and repeat `--harness` to limit discovery:

```bash
tesserae project sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Without `--import`, discovery prints what it found without writing normalized session records.

## Import normalized JSON directly

If another tool has already produced normalized `HarnessSession` JSON, import one file or a list of files:

```bash
tesserae project sessions import path/to/session.json path/to/more-sessions.json
```

Each input may contain one session object or a list of session objects.

## List imported sessions

```bash
tesserae project sessions list
```

Sessions are stored below:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Live-monitored sessions are additionally tracked in the SQLite
`HarnessSessionsDB` (`.tesserae/sqlite.db`), which also persists the per-file
read offsets the tailer resumes from. `project sessions list` reports the
combined view.

## Build the static session pages

After importing sessions, rebuild the site:

```bash
tesserae project build-site
```

The site emits:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

The generated site links Sessions from the global rail, the home Browse cards, search entries, and each session detail page's breadcrumb trail.

## Session detail page layout

Session detail pages use the shared static-site shell rather than a standalone transcript dump. They include:

- hero and stat strip;
- high-level summary;
- timeline and size metadata;
- decisions, files, commands, tools, and errors when present;
- collapsed subagent tree;
- turn-by-turn user/assistant conversation;
- collapsed tool-use blocks attached under the preceding assistant turn;
- a left conversation rail that links to `#turn-N` anchors.

Conversation markdown is rendered through the site markdown renderer. Semantic surfaces such as inline code, explicit command/tag markup, paths, filenames, and hashtags are decorated as compact chips; random capitalized nouns are not auto-chipped.

Current transcript typography:

| Surface | Selector | Size |
|---|---|---|
| Conversation markdown prose | `.session-turn-text`, prose children | `8px` |
| Generic conversation code fences | `.session-turn-text pre` | `10px` |
| Bash/shell fenced code content | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## Publishing checklist for sessions

Before deploying a public site that includes sessions:

1. Run `tesserae project sessions list` and confirm the count is expected.
2. Inspect `.tesserae/harness_sessions/` for sensitive content.
3. Rebuild with `tesserae project build-site`.
4. Open `sessions/index.html` and at least one session detail page locally.
5. Confirm tool blocks are collapsed by default and raw tool payloads are acceptable to publish.
6. Deploy with `tesserae project deploy --build` once the source tree is committed.
