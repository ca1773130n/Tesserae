# Harness session history

LLM-Wiki can import local AI-agent transcripts and render them as project memory under the static site's `sessions/` section.

This feature is intentionally separate from `export-agent-harness`:

- `export-agent-harness` is outbound context for tools such as Claude Code, Codex, Gemini, Cursor, Kiro, and OpenCode.
- `project sessions ...` is inbound history: it normalizes prior Claude Code/Codex sessions for the current project, stores them under `.llm-wiki/harness_sessions/`, and lets `project build-site` publish session index/detail pages.

## Privacy model

Session import is explicit. A normal `project compile` or `project build-site` reads already-normalized sessions from `.llm-wiki/harness_sessions/`, but it does not surprise-scrape private harness transcript directories.

Imported session records are local project artifacts. Review them before publishing a public site, especially if your transcripts may include secrets, private paths, customer data, or unreleased code.

## Discover and import local sessions

From the project root:

```bash
llm_wiki project sessions discover --import
```

Discovery scans local Claude Code and Codex transcript roots that belong to the current project working directory. Use `--root` to scan a specific config directory, and repeat `--harness` to limit discovery:

```bash
llm_wiki project sessions discover \
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
llm_wiki project sessions import path/to/session.json path/to/more-sessions.json
```

Each input may contain one session object or a list of session objects.

## List imported sessions

```bash
llm_wiki project sessions list
```

Sessions are stored below:

```text
.llm-wiki/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

## Build the static session pages

After importing sessions, rebuild the site:

```bash
llm_wiki project build-site
```

The site emits:

```text
.llm-wiki/site/sessions/index.html
.llm-wiki/site/sessions/<project>/<session>.html
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

1. Run `llm_wiki project sessions list` and confirm the count is expected.
2. Inspect `.llm-wiki/harness_sessions/` for sensitive content.
3. Rebuild with `llm_wiki project build-site`.
4. Open `sessions/index.html` and at least one session detail page locally.
5. Confirm tool blocks are collapsed by default and raw tool payloads are acceptable to publish.
6. Deploy with `llm_wiki project deploy --build` once the source tree is committed.
