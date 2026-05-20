#!/usr/bin/env bash
# Tesserae plugin — SessionEnd hook.
# Backgrounds `tesserae sessions discover --import && tesserae project
# compile` so the conversation just-ended becomes graph nodes for the
# next session. Always exit 0 — never block session close.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

if [[ "$(read_plugin_setting session_end)" != "true" ]]; then
  exit 0
fi

project_root="$(resolve_project_root)"
[[ -d "${project_root}/.tesserae" ]] || exit 0

tesserae_bin=$(find_tesserae) || {
  log_to ".session-end-hook.log" "skipped: tesserae binary not found on PATH"
  exit 0
}

# Skip if another tesserae compile/import is already running for THIS
# project — otherwise every session close stacks another heavy compile
# on top of the in-flight one and starves the box. Match on the
# project_root path so a different Tesserae project's compile doesn't
# block this one.
if pgrep -f "tesserae project (compile|sessions discover).*${project_root}" >/dev/null 2>&1 \
   || pgrep -f "${project_root}.*tesserae project (compile|sessions discover)" >/dev/null 2>&1; then
  log_to ".session-end-hook.log" "skipped: a tesserae compile/import is already running for ${project_root}"
  exit 0
fi
# Broader fallback: if ANY tesserae compile is grinding (e.g. spawned
# without an explicit cwd arg), still skip — concurrent compiles on the
# same .tesserae/ collide.
if pgrep -f "tesserae project compile" >/dev/null 2>&1; then
  log_to ".session-end-hook.log" "skipped: another tesserae project compile is already running"
  exit 0
fi

# Background the import+compile. Use `setsid` (Linux) or `nohup`
# (macOS) to detach from the session's process group — without this,
# Claude Code reaps the backgrounded process when SessionEnd
# returns and the compile gets killed before it finishes.
log_file="${project_root}/.tesserae/.session-end-hook.log"
cmd="echo \"==== \$(date -u +%FT%TZ) — session-end refresh starting ====\"; \"$tesserae_bin\" project sessions discover --import 2>&1 || echo \"(project sessions discover --import failed; continuing to compile anyway)\"; \"$tesserae_bin\" project compile 2>&1 || echo \"(project compile failed)\"; echo \"==== \$(date -u +%FT%TZ) — done ====\""
if command -v setsid >/dev/null 2>&1; then
  setsid sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
elif command -v nohup >/dev/null 2>&1; then
  nohup sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
else
  sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
fi
disown 2>/dev/null || true

exit 0
