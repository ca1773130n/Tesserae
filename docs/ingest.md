# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="i18n/ingest.ko.md">한국어</a> · <a href="i18n/ingest.zh.md">中文</a> · <a href="i18n/ingest.ja.md">日本語</a> · <a href="i18n/ingest.ru.md">Русский</a> · <a href="i18n/ingest.es.md">Español</a> · <a href="i18n/ingest.fr.md">Français</a> · <a href="../i18n/ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Merge a single document file or URL into the knowledge base.

## Usage

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` is one or more local file paths or `http(s)` URLs. URLs are fetched, converted to
markdown, and persisted under `data/ingested/<slug>.md` with provenance front-matter
(`source_url`, `fetched_at`, `content_sha256`, and `arxiv_id` when detected), then merged.
Local files from outside the project are copied into `data/ingested/` so they become tracked
sources (a later full compile reproduces them identically).

URL ingest requires the optional extra:

    pip install tesserae[ingest-url]

## How it works

By default `ingest` merges the new source via an incremental compile — it does not re-extract
the whole corpus — and the result is byte-identical to a full compile (an automatic
full-recompile fallback guarantees correctness for any case the incremental path cannot handle).
Pass `--exact` to force a full recompile of the whole corpus.

## Flags

- `--exact` — force a full recompile of the whole corpus.
- `--dry-run` — fetch and report what would be ingested; write no graph.
- `--title` — title override, useful for bare URLs.
- `--source-kind` — override the source classification.

## Building the concept layer (`--extractor`)

By default, extraction is **deterministic** — fast, no LLM, no API keys, and
byte-stable — but structural: it captures sources, sections, and explicit links,
leaving a sparse *concept* layer. To mint the richer layer (concepts, claims,
capabilities, technical terms, evidence spans, and typed edges between them),
run `compile` with an LLM extractor:

    # every doc through the Claude CLI (no API key — uses your `claude` login)
    tesserae compile --extractor claude-cli

    # cost-aware: only matching docs through Claude, the rest deterministic
    tesserae compile --extractor selective-claude \
      --claude-include "docs/**/*.md" --claude-limit 20

The same flags work on `tesserae extract <paths>` (standalone) and
`tesserae compile <paths>` (ad-hoc path ingest).

**Tuning:**

- `--claude-include <glob>` — for `selective-claude`, which files go through
  Claude (repeat for several; patterns match anywhere in the absolute path, e.g.
  `"*docs/superpowers*"`).
- `--claude-limit N` — cap how many files reach Claude (the rest stay deterministic).
- `--claude-timeout S` — per-file timeout in seconds (default 180; **raise to
  ~600 for large design docs** — they generate big JSON and can exceed the default).
- `--claude-model` / `--claude-config-dir` — model and Claude CLI account.

**Robust on real corpora.** One noisy or slow document never aborts the whole
compile: an edge or node type outside the controlled vocabulary is dropped, a
file that exceeds the timeout falls back to deterministic extraction for that
file, and a transient invalid generation is retried (the model is
non-deterministic, so a re-call almost always validates). The deterministic
default and a clean `--extractor` run otherwise produce the same structural graph.

## Related commands

- `tesserae compile` (no args) re-extracts the whole tracked corpus.
- `tesserae ingest <x>` adds one source incrementally.
- `tesserae code ingest` mints a code graph from Python source (a different command).
