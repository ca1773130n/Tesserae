#!/usr/bin/env bash
# Tesserae plugin — PostToolUse hook matching Edit | Write | MultiEdit.
# When the edited file path is under docs/, queue an incremental
# `tesserae project compile --changed-only`, debounced via a lock
# file to once per 60 seconds. Disabled by default; opt-in via
# .claude/tesserae.local.md frontmatter `hooks.posttooluse_edit: true`.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

if [[ "$(read_plugin_setting posttooluse_edit)" != "true" ]]; then
  exit 0
fi

# Path-based filtering happens HERE (matchers only match event/tool
# names, not paths — per the plugin manifest contract). We parse the
# stdin JSON's tool_input.file_path to decide whether to fire.
hook_input=$(cat)
file_path=$(echo "$hook_input" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
[[ -n "$file_path" ]] || exit 0

# Only react to edits under docs/. Path can be absolute
# (/repo/docs/foo.md), repo-rooted (docs/foo.md), or `./docs/foo.md`.
# Match all three forms.
case "$file_path" in
  docs/*|./docs/*|*/docs/*) ;;
  *) exit 0 ;;
esac

project_root="$(resolve_project_root)"
[[ -d "${project_root}/.tesserae" ]] || exit 0
tesserae_bin=$(find_tesserae) || exit 0

# Atomic-debounce via a lock DIRECTORY (mkdir is atomic on POSIX,
# unlike check-then-write on a file). Skip the recompile when another
# hook invocation grabbed the lock in the last 120 seconds. 120s
# (vs the original 60) gives a typical mid-sized changed-only compile
# room to finish before the next edit-burst kicks off another one.
lock_dir="${project_root}/.tesserae/.recompile.lock.d"
now=$(date -u +%s)
if mkdir "$lock_dir" 2>/dev/null; then
  # We grabbed the lock — write the timestamp inside it, drop the
  # lock after the compile starts so the next legitimate window
  # opens after the debounce.
  echo "$now" > "${lock_dir}/at"
else
  # Lock exists — check its age. Stale (>120s) → reclaim.
  last_run=$(cat "${lock_dir}/at" 2>/dev/null || echo 0)
  if [[ "$last_run" =~ ^[0-9]+$ ]] && (( now - last_run < 120 )); then
    log_to ".posttooluse-edit-hook.log" "debounced (last run was $(( now - last_run ))s ago) for $file_path"
    exit 0
  fi
  # Stale → forcibly reclaim.
  rm -rf "$lock_dir" 2>/dev/null
  mkdir "$lock_dir" 2>/dev/null || exit 0
  echo "$now" > "${lock_dir}/at"
fi

# Background the incremental compile so the user's edit doesn't block.
# `setsid` (Linux) or `nohup` (macOS fallback) detaches from the
# session's process group so Claude Code can't reap us when SessionEnd
# fires. `disown` alone isn't reliably enough for that case.
log_file="${project_root}/.tesserae/.posttooluse-edit-hook.log"
cmd="echo \"==== \$(date -u +%FT%TZ) — incremental recompile for ${file_path} ====\"; \"$tesserae_bin\" project compile --changed-only 2>&1 || echo \"(compile --changed-only failed)\"; rm -rf \"$lock_dir\""
if command -v setsid >/dev/null 2>&1; then
  setsid sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
elif command -v nohup >/dev/null 2>&1; then
  nohup sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
else
  sh -c "$cmd" >> "$log_file" 2>&1 < /dev/null &
fi
disown 2>/dev/null || true

exit 0
