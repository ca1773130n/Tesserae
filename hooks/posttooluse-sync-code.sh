#!/usr/bin/env bash
# Tesserae plugin — PostToolUse hook matching Edit | Write | MultiEdit.
# Re-runs ``tesserae project sync-code`` whenever the user edits any
# file, debounced to once every 30 seconds. The CodeGraph SQLite is
# updated continuously by its own MCP server / file watcher; this
# hook merely closes the loop on the Tesserae side so the typed
# code-graph.json keeps tracking those updates in near-real-time
# during an active coding session.
#
# Skip silently if:
#   - opted out via ``sync_code_on_edit: false``
#   - the project has no CodeGraph DB (``.codegraph/codegraph.db``)
#   - the last sync ran <30s ago (debounce via touch-file)
#   - another sync-code is already running (concurrent re-entry guard)
#   - tesserae binary isn't on PATH
#
# Always exits 0 — never blocks the user's edit.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

# Opt-out check (default: true).
if [[ "$(read_plugin_setting sync_code_on_edit)" != "true" ]]; then
  exit 0
fi

project_root="$(resolve_project_root)"
tdir="${project_root}/.tesserae"

# Project not initialised → silent.
[[ -d "$tdir" ]] || exit 0

codegraph_db="${project_root}/.codegraph/codegraph.db"
# No CodeGraph DB → silent skip (project doesn't use CodeGraph).
[[ -f "$codegraph_db" ]] || exit 0

# --------------------------------------------------------------------
# Debounce. Only re-sync if the last sync ran > 30 s ago. The
# touch-file is written AFTER dispatching the background sync, not
# before — so a sync that fails to launch leaves the timestamp old
# and the next edit retries. Portable BSD/GNU stat probing mirrors
# session-start.sh.
# --------------------------------------------------------------------
touch_file="${tdir}/.last-sync-code"
debounce_window=30
now=$(date -u +%s)

if [[ -f "$touch_file" ]]; then
  last_mtime=""
  if last_mtime=$(stat -f '%m' "$touch_file" 2>/dev/null); then
    :
  elif last_mtime=$(stat -c '%Y' "$touch_file" 2>/dev/null); then
    :
  else
    last_mtime=""
  fi
  if [[ -n "$last_mtime" && "$last_mtime" =~ ^[0-9]+$ ]]; then
    age=$(( now - last_mtime ))
    if (( age < debounce_window )); then
      log_to ".posttooluse-sync-hook.log" "debounced (last sync was ${age}s ago)"
      exit 0
    fi
  fi
fi

# Concurrent re-entry guard. Mirror session-start.sh's pgrep pattern
# so two simultaneous syncs for the same project don't pile up.
if pgrep -f "tesserae project sync-code.*${project_root}" >/dev/null 2>&1 \
   || pgrep -f "${project_root}.*tesserae project sync-code" >/dev/null 2>&1; then
  log_to ".posttooluse-sync-hook.log" "skipped: another sync-code is already running for ${project_root}"
  exit 0
fi

tesserae_bin=$(find_tesserae 2>/dev/null) || tesserae_bin=""
if [[ -z "$tesserae_bin" ]]; then
  log_to ".posttooluse-sync-hook.log" "skipped: tesserae binary not found"
  exit 0
fi

# Background the sync so the user's edit doesn't block. setsid
# (Linux) / nohup (macOS) detach from the session's process group
# so Claude Code can't reap us when SessionEnd fires.
log_file="${tdir}/.posttooluse-sync-hook.log"
cmd="echo \"==== \$(date -u +%FT%TZ) — posttooluse sync-code starting ====\"; \"$tesserae_bin\" project sync-code --project \"$project_root\" 2>&1 || echo \"(sync-code failed)\"; echo \"==== \$(date -u +%FT%TZ) — done ====\""
if command -v setsid >/dev/null 2>&1; then
  setsid sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
elif command -v nohup >/dev/null 2>&1; then
  nohup sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
else
  sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
fi
disown 2>/dev/null || true

# Touch the debounce marker AFTER dispatching, so a failed dispatch
# leaves the previous timestamp in place and the next edit retries.
: > "$touch_file" 2>/dev/null || true
touch "$touch_file" 2>/dev/null || true

exit 0
