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

exit 0
