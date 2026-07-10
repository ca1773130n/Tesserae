# Daily session chunks — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="i18n/session-chunks.ko.md">한국어</a> · <a href="i18n/session-chunks.zh.md">中文</a> · <a href="i18n/session-chunks.ja.md">日本語</a> · <a href="i18n/session-chunks.ru.md">Русский</a> · <a href="i18n/session-chunks.es.md">Español</a> · <a href="i18n/session-chunks.fr.md">Français</a> · <a href="i18n/session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
Windowed session queries — `tesserae summary`, `tesserae decisions`, and the
`ask` planner's activity actions — used to re-parse every in-window Claude
Code / Codex transcript on every call. The daily chunk store persists each
normalised turn **once**, bucketed by KST day label, so a fully covered past
day is served from SQLite instead of a raw rescan. Measured on a real
multi-thousand-session corpus this makes windowed summaries **~20x faster**.

The store is one SQLite file, `.tesserae/session_chunks.db` (WAL,
short-lived connection per operation): a `turns` table indexed by day, a
`day_coverage` table recording which `(day, harness)` pairs are complete, and
a `meta` table with the schema version.

## What writes it

1. **Live — the engine tailer.** While `tesserae engine` runs, the session
   tailer appends turns to the store as it tails them, per poll, and upserts
   coverage for the affected days (`source: "tailer"`). The write path is
   append-only, idempotent against re-delivered turns, and never raises into
   the daemon loop. There is deliberately **no SessionEnd hook writer** —
   backgrounded SessionEnd writers pile up (a recorded failure mode).
2. **Backfill.** Two entry points walk existing transcripts and fill history
   (`source: "backfill"`):
   - `tesserae refresh` runs a backfill automatically as part of its
     sessions-import step, so the first refresh after upgrading populates the
     store with no extra action.
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` runs it
     explicitly; `--since` bounds how far back to walk (default: full
     history).

   Backfill takes a **non-blocking** flock on
   `.tesserae/session_chunks.lock` with skip-if-held semantics — a concurrent
   backfill (or an engine already holding it) makes the second caller skip
   cleanly instead of queueing. Backfill upserts are keyed on
   `(session_path, ts, role, hash(text))`, so tailer rows and backfill rows
   never duplicate each other. A one-day overlap on incremental backfills
   heals turns that landed after a day's coverage was first claimed.

## What reads it

The fast path lives at the single scan choke point
(`activity_summary.iter_project_transcripts` / `scan_messages`), so everything
downstream inherits it transparently:

- `tesserae summary` (including its embedded decisions gathering)
- `tesserae decisions`
- `tesserae ask` — the planner's `activity_summary` / `decisions` actions
- MCP `activity_summary` and `query_decisions`
- the live-sessions view

## Coverage rule: today is always raw-scanned

A window is served from chunks only when **all** of the following hold:

1. it is an exact KST-aligned single day;
2. that day is **strictly before today** — today is still being written, so it
   always takes the raw transcript scan;
3. a `day_coverage` row exists for **every** requested harness on that day.

Anything else falls back to the raw scan for that window.

## The raw-scan fallback guarantee

The chunk store is an accelerator, never a source of truth:

- Any DB error, a missing/corrupt file, or a `schema_version` mismatch yields
  **nothing** from the chunk path — the caller's raw transcript scan proceeds
  exactly as before. A schema mismatch drops and rebuilds the store empty;
  coverage vanishes with it, so the fallback stays correct.
- Days without coverage (for example, the engine wasn't running and no
  backfill has happened) silently take the slow path. Correct, but the speedup
  disappears — `tesserae doctor` reports coverage gaps in the recent window
  and points at `tesserae sessions chunk-backfill` (see
  [doctor.md](doctor.md)).
- **Parity invariant:** for a fully covered day, chunk-served turns are equal
  to what the raw scan would have produced (same timestamp, role, name, text,
  session key, and harness).

## Operational notes

- Keep `tesserae engine` running and past days stay covered live; otherwise an
  occasional `tesserae refresh` (or explicit `chunk-backfill`) closes the
  gaps.
- The store is per project, lives under `.tesserae/`, and can always be
  deleted safely — the next backfill rebuilds it, and readers fall back to raw
  scans in the meantime.
