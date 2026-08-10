# Harness session history

<!-- translations:start -->
<p align="center"><a href="i18n/session-history.ko.md">한국어</a> · <a href="i18n/session-history.zh.md">中文</a> · <a href="i18n/session-history.ja.md">日本語</a> · <a href="i18n/session-history.ru.md">Русский</a> · <a href="i18n/session-history.es.md">Español</a> · <a href="i18n/session-history.fr.md">Français</a> · <a href="../i18n/session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae can import local AI-agent transcripts and render them as project memory under the static site's `sessions/` section.

This feature is intentionally separate from `export harness`:

- `export harness` is outbound context for tools such as Claude Code, Codex, Gemini, Cursor, Kiro, and OpenCode.
- `sessions ...` is inbound history: it normalizes prior Claude Code/Codex sessions for the current project, stores them under `.tesserae/harness_sessions/`, and lets `export site` publish session index/detail pages.

## Two ways in: batch import and live monitoring

Session ingestion is no longer batch-only. There are two paths into the same
normalized store:

- **Batch import** — `sessions discover/import` scans transcript roots
  on demand and writes one-shot. This page documents that flow below.
- **Live monitoring** — the supervisor daemon (`tesserae engine`) runs a
  `SessionTailer` that watches *this project's own*
  Claude Code and Codex transcripts and ingests new turns as they land. Each
  tick seeks to a persisted per-file byte offset, reads only the new bytes,
  and stores complete turns into the SQLite `HarnessSessionsDB`
  (`.tesserae/sqlite.db`) **before** enqueuing a debounced recompile, so the
  compile always reads consistent state. The tailer is scoped to the project's
  own sessions (Claude `projects/<slug>/*.jsonl`; Codex filtered by cwd) and
  resumes from stored offsets after a restart without replaying turns.

Run the live loop with:

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` runs the same ingest → compile → project pipeline
once, in-process, without starting the long-lived watcher (pass
`--no-sessions` to skip the harness-session discovery scan).

## Privacy model

Both ingestion paths are explicit: the live tailer only runs while you keep
`tesserae engine` alive, and batch discovery only writes with
`--import`. A normal `tesserae compile` or `tesserae export site` reads
already-normalized sessions from `.tesserae/harness_sessions/` and the live
records in `.tesserae/sqlite.db`, but it does not surprise-scrape private
harness transcript directories on its own.

Imported session records are local project artifacts. Review them before publishing a public site, especially if your transcripts may include secrets, private paths, customer data, or unreleased code.

Turn text is copied into node names and descriptions, and those are serialized
into `graph.json` and every projection of it — so **home directories are
redacted on the way in**. `/Users/<name>` and `/home/<name>` never reach the
graph, which matters because a path is the one PII that appears in almost every
transcript without anyone intending it to.

## What a session turn becomes

For every *significant* transition in a session — a tool call, or a substantive
assistant action, not chatter — the LLM-free `Event` pass mints one node
capturing `{turn_id, actor, action, brief state-change}` and links consecutive
events with `precedes` edges, so a session's dynamic state can be replayed in
order. The pass never calls a model, never raises on bad input, and is
byte-idempotent: every minted id, body and `first_seen_at` is content-derived,
so a rerun produces identical nodes and edges.

**A tool result is a turn.** Exit codes and error flags survive ingest and land
on the `Event` node, so the graph can distinguish a command that *failed* from
one that merely ran. Before this, an agent reading its own history saw that it
had run `pytest` and had no idea whether the suite passed — which is the
difference between a log and a memory.

### The `recovers` edge

From two **observed** outcomes in a single session, Tesserae derives the one
causal edge in its vocabulary: a tool call that reported failure, and a later
call — same tool, same program family, same working directory, same operand,
with no success on that operand in between — that reported success. The
succeeding `Event` is the source, the failing one the target; both turn ids are
named in the evidence, and `metadata["basis"]` names every dimension the two
calls had to agree on.

`CAUSAL_EDGE_TYPES` contains exactly one member, and that is deliberate. A
survey of four leading agent-memory systems found that not one of them derives
a causal edge: two infer their strongest link from co-occurrence, one takes an
LLM's word for an open vocabulary of relation labels with no verification, and
one has no edges at all. The failure this narrowness exists to avoid is
shipping a `caused_by` that is really a `happened_near` — in a graph the two
are indistinguishable, and the wrong one gets read as evidence.

The anchor is the **operand**, not the command, because commands vary in ways
that don't matter (flags, ordering) while the thing being acted on is what a
retry is actually retrying.

## Discover and import local sessions

From the project root:

```bash
tesserae sessions discover --import
```

Discovery scans local Claude Code and Codex transcript roots that belong to the current project working directory. Use `--root` to scan a specific config directory, and repeat `--harness` to limit discovery:

```bash
tesserae sessions discover \
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
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Each input may contain one session object or a list of session objects.

## How the store is written

Every record in `.tesserae/harness_sessions/` carries a **`producer`** — the importer that wrote it. `sessions discover --import` stamps `tesserae:discover`; `sessions import <path>` stamps `tesserae:import`. **A writer may only touch records it produced**: it prunes only its own, and it will not overwrite another producer's record for the same session — the incoming write is skipped and reported as `Left alone (written by another producer)`.

That rule exists because provenance is the only thing that actually separates importers. Two of them routinely describe the *same* session: Tesserae's local scan mints a plain record from a transcript under `~/.claude`, while an orchestrator exports that same session carrying the agent identity only it knows. Both derive the same filename from the session id, so they collide. Neither the transcript's location nor the harness name can tell them apart — which is why the earlier root-scoped fixes for [#104](https://github.com/ca1773130n/Tesserae/issues/104) did not work, and why 0.28.6 still lost such records two ways: deleted when the scan no longer found the transcript, silently overwritten when it did.

If you write into this store from your own tool, use `tesserae sessions import <file>` and your records are protected from that point on. Nothing else is required.

Scope narrows further, as a second gate: a record is pruned only if its transcript also lives under a root this run scanned and its harness was one it scanned. So `--harness codex` leaves claude-code records alone even though `~/.claude` was walked.

### Several machines sharing one project directory

Every record also carries a **`host`** — the machine that harvested it. **A host prunes only what it harvested.**

This is a genuinely separate axis from `producer`, and the gates above cannot stand in for it. When several servers each run Claude Code and share a disk, they share `.tesserae` too — but each one sees only its own local transcripts. Every host's scan stamps the same `tesserae:discover`, and every host's `~/.claude` resolves to the same path string, so the producer gate and the scope gate *both pass* on a machine that never saw the transcript. It then deletes another machine's record and reports success. Records now carry the harvesting host, and pruning requires it to match.

The host id lives in `~/.tesserae/host_id` — per machine, not in the shared project directory — and is generated once on first use. Override it with `TESSERAE_HOST_ID`. It is a persisted id rather than the hostname on purpose: a fleet built from one image reuses hostnames, and a hostname collision would silently hand one machine's records to another.

The **write** path is deliberately host-blind. Two hosts can only write the same session when both can see the transcript, so the write is idempotent and simply re-stamps ownership onto whichever host last proved it could see it. Gating writes by host instead would freeze a decommissioned machine's records forever with no way to reclaim them.

Records written before this field carry no host. They are unowned on this axis and survive any host's prune until `--adopt-unowned` claims them — the same rule `producer` already uses, and the reason it matters here is that *every* record written by 0.28.7 carries a producer and no host, so the producer gate would abstain and nothing else would protect them.

Three behaviours worth knowing:

- **Records written before 0.28.7 carry no producer.** They are unowned, so no importer prunes or overwrites them — safe, but discovery will not refresh them either. `sessions discover --import --adopt-unowned` claims them for discovery. Run it once if Tesserae's own scan is the only thing writing this store; do **not** run it if another tool writes here too, since it hands your records to discovery.
- An empty discovery never prunes. A scan that finds nothing — wrong `HOME`, detached harness roots — merges instead of wiping.
- A discovery that removes or preserves records prints both counts next to the import count, so the store cannot change size inside a line that only reports growth.

## List imported sessions

```bash
tesserae sessions list
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
read offsets the tailer resumes from. `tesserae sessions list` reports the
combined view.

## Build the static session pages

After importing sessions, rebuild the site:

```bash
tesserae export site
```

The site emits:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

The generated site links Sessions from the global rail, the home Browse cards, search entries, and each session detail page's breadcrumb trail.

## Fast transcript search (memex)

When you `tesserae serve` the site, the **sessions dashboard** gains a full-text
search box over every indexed Claude/Codex transcript, backed by
[`nicosuave/memex`](https://github.com/nicosuave/memex) (BM25). Results show
`project · role · date · score` plus a matching snippet.

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

It is **optional and graceful**: with no `memex` binary (or index) the box shows
a clear, actionable message and the rest of the dashboard is unaffected. The
search endpoint (`GET /api/transcript-search`) is gated to same-origin/loopback
callers so a visited web page can't probe your local history.

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

1. Run `tesserae sessions list` and confirm the count is expected.
2. Inspect `.tesserae/harness_sessions/` for sensitive content.
3. Rebuild with `tesserae export site`.
4. Open `sessions/index.html` and at least one session detail page locally.
5. Confirm tool blocks are collapsed by default and raw tool payloads are acceptable to publish.
6. Deploy with `tesserae export site --deploy` once the source tree is committed.
