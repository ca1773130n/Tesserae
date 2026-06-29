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

## The concept layer (`--extractor`)

Tesserae is an LLM wiki, so `compile` builds the **concept/claim layer by
default** (`--extractor llm`): it reads each document through your configured LLM
provider — **codex / claude / Anthropic API**, per `llm_provider` — and mints
concepts, claims, capabilities, technical terms, evidence spans, and the typed
edges between them. That's the layer that lets the graph answer *"what idea is
this, and how does it relate"*, not just *"which file said it"*.

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

If no LLM backend is configured/authed, compile degrades to the **deterministic**
extractor (structural only — sources, sections, explicit links) and warns. You can
also ask for it explicitly — it's fast, key-free, and byte-stable, the CI /
reproducible mode:

    tesserae compile --extractor deterministic

**Cost-aware (`selective-llm`)** — route only matching docs through the LLM, the
rest deterministic:

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

The same flags work on `tesserae extract <paths>` (standalone) and
`tesserae compile <paths>` (ad-hoc path ingest).

**Tuning:**

- `--llm-provider codex|claude|anthropic` — override the provider (default:
  `llm_provider` in config).
- `--llm-model` — model for the extractor (default: the provider's default).
- `--llm-include <glob>` — for `selective-llm`, which files go through the LLM
  (repeat for several; patterns match anywhere in the absolute path, e.g.
  `"*docs/superpowers*"`).
- `--llm-limit N` — cap how many files reach the LLM (the rest stay deterministic).

**No default timeout.** A large design doc generates a lot of JSON and can take
minutes; extraction runs to completion rather than being silently cut off (a
timeout is opt-in only).

**Robust on real corpora.** One noisy or slow document never aborts the whole
compile: an LLM failure on a doc (auth, error, an unparseable generation) falls
back to the deterministic baseline for *that* doc, an edge or node type outside the
controlled vocabulary is dropped, and content-keyed caching means a re-compile of
unchanged docs reuses the prior extraction.

> The `claude-cli` / `selective-claude` extractor names (and the `--claude-*`
> flags) are deprecated aliases for `llm` / `selective-llm` (and `--llm-*`); they
> still work but emit a deprecation note.

## Related commands

- `tesserae compile` (no args) re-extracts the whole tracked corpus.
- `tesserae ingest <x>` adds one source incrementally.
- `tesserae code ingest` mints a code graph from Python source (a different command).
