#!/usr/bin/env bash
# Tesserae plugin — SessionStart hook.
# Pure read. Prints a one-line graph summary + last-compile timestamp
# at session start. Suggests /tesserae:refresh if the last compile
# was more than 7 days ago. Exit 0 always — never blocks session
# start.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

# Opt-out check.
if [[ "$(read_plugin_setting session_start)" != "true" ]]; then
  exit 0
fi

project_root="$(resolve_project_root)"
tdir="${project_root}/.tesserae"

# Project not initialised → silent. Don't add noise to every session
# start on every cwd the user happens to be in.
[[ -d "$tdir" ]] || exit 0
[[ -f "${tdir}/graph.json" ]] || exit 0

# Best-effort graph counts.
nodes="?"; edges="?"
if command -v jq >/dev/null 2>&1; then
  nodes=$(jq -r '.nodes | length' "${tdir}/graph.json" 2>/dev/null || echo "?")
  edges=$(jq -r '.edges | length' "${tdir}/graph.json" 2>/dev/null || echo "?")
fi

# Last build timestamp + age check.
last_build=""
days_old="?"
if [[ -f "${tdir}/.build-history.jsonl" ]] && command -v jq >/dev/null 2>&1; then
  if command -v tac >/dev/null 2>&1; then
    reversed=$(tac "${tdir}/.build-history.jsonl" 2>/dev/null)
  else
    reversed=$(tail -r "${tdir}/.build-history.jsonl" 2>/dev/null)
  fi
  last_build=$(printf '%s\n' "$reversed" | while IFS= read -r line; do
    ts=$(echo "$line" | jq -r '.built_at // .timestamp // .at // empty' 2>/dev/null)
    if [[ -n "$ts" && "$ts" != "null" ]]; then echo "$ts"; break; fi
  done)
  if [[ -n "$last_build" ]]; then
    # Compute age in days using portable BSD/GNU date probing.
    if date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_build" "+%s" >/dev/null 2>&1; then
      last_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_build" "+%s")
    elif date -d "$last_build" "+%s" >/dev/null 2>&1; then
      last_epoch=$(date -d "$last_build" "+%s")
    else
      last_epoch=""
    fi
    if [[ -n "${last_epoch:-}" ]]; then
      now_epoch=$(date -u "+%s")
      days_old=$(( (now_epoch - last_epoch) / 86400 ))
    fi
  fi
fi

# Compose the one-liner.
msg="tesserae: ${nodes} nodes, ${edges} edges"
if [[ -n "$last_build" ]]; then
  msg="$msg, last compile ${last_build}"
fi
echo "$msg"

# Stale warning.
if [[ "$days_old" != "?" ]] && (( days_old > 7 )); then
  echo "  ⚠ last compile was ${days_old} days ago — consider /tesserae:refresh"
fi

# --------------------------------------------------------------------
# Live sync-code: background ``tesserae project sync-code`` when the
# CodeGraph SQLite is newer than our derived code-graph.json. This
# delivers the "keeps updating" story for the polyglot code graph
# without forcing the user to remember to re-run sync-code manually.
# CodeGraph's MCP server already auto-watches files; this hook just
# closes the loop on the Tesserae side.
#
# Skip silently if:
#   - opted out via ``sync_code_on_start: false``
#   - the project doesn't use CodeGraph (no .codegraph/codegraph.db)
#   - code-graph.json is already at-or-newer than the DB
#   - tesserae binary isn't on PATH
#   - another sync-code is already running (concurrent re-entry guard)
# --------------------------------------------------------------------
if [[ "$(read_plugin_setting sync_code_on_start)" == "true" ]]; then
  codegraph_db="${project_root}/.codegraph/codegraph.db"
  code_graph_json="${tdir}/code-graph.json"

  if [[ -f "$codegraph_db" ]]; then
    needs_sync=false
    if [[ ! -f "$code_graph_json" ]]; then
      needs_sync=true
    else
      # Portable mtime comparison: BSD `stat -f %m` (macOS) vs GNU
      # `stat -c %Y` (Linux). Fall back to ``[[ A -nt B ]]`` if neither
      # stat flavour responds — POSIX-y enough for ext4/apfs.
      db_mtime=""
      json_mtime=""
      if db_mtime=$(stat -f '%m' "$codegraph_db" 2>/dev/null) \
         && json_mtime=$(stat -f '%m' "$code_graph_json" 2>/dev/null); then
        :
      elif db_mtime=$(stat -c '%Y' "$codegraph_db" 2>/dev/null) \
           && json_mtime=$(stat -c '%Y' "$code_graph_json" 2>/dev/null); then
        :
      else
        db_mtime=""
      fi
      if [[ -n "$db_mtime" && -n "$json_mtime" ]]; then
        (( db_mtime > json_mtime )) && needs_sync=true
      elif [[ "$codegraph_db" -nt "$code_graph_json" ]]; then
        needs_sync=true
      fi
    fi

    if $needs_sync; then
      tesserae_bin=$(find_tesserae 2>/dev/null) || tesserae_bin=""
      if [[ -z "$tesserae_bin" ]]; then
        log_to ".session-start-hook.log" "sync-code skipped: tesserae binary not found"
      elif pgrep -f "tesserae project sync-code.*${project_root}" >/dev/null 2>&1 \
           || pgrep -f "${project_root}.*tesserae project sync-code" >/dev/null 2>&1; then
        log_to ".session-start-hook.log" "sync-code skipped: another sync-code is already running for ${project_root}"
      else
        log_file="${tdir}/.session-start-hook.log"
        # Pass --project explicitly so the spawned CLI uses the
        # resolved project root rather than $PWD — required when
        # Claude opens a session in a subdirectory of the project.
        cmd="echo \"==== \$(date -u +%FT%TZ) — session-start sync-code starting ====\"; \"$tesserae_bin\" project sync-code --project \"$project_root\" 2>&1 || echo \"(sync-code failed)\"; echo \"==== \$(date -u +%FT%TZ) — done ====\""
        if command -v setsid >/dev/null 2>&1; then
          setsid sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
        elif command -v nohup >/dev/null 2>&1; then
          nohup sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
        else
          sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
        fi
        disown 2>/dev/null || true
        echo "  ⟳ syncing code-graph from CodeGraph (background)"
      fi
    fi
  fi
fi

exit 0
