#!/usr/bin/env bash
# Tesserae plugin — UserPromptSubmit + PreToolUse(Read|Grep|Edit) hook.
#
# Pushes the project's active decisions, gotchas and supersede warnings that
# lexically match the prompt (or the file being read/grepped/edited) into the
# agent's context, via `python -m tesserae.hook_inject`. Lexical only: no LLM,
# no embedding call, read-only on .tesserae/sqlite.db. See that module's
# docstring for the full contract.
#
# OFF by default — its benefit on task success has not been measured. Opt in
# with either:
#   TESSERAE_HOOK_INJECT=1                       (environment), or
#   hooks.prompt_inject: true                    (.claude/tesserae.local.md)
#
# Fail-open: any failure prints nothing and exits 0, so a prompt or tool call
# is never blocked. Failures are logged to .tesserae/.prompt-inject-hook.log.
# A watchdog kills the Python injector at TESSERAE_HOOK_INJECT_TIMEOUT_MS
# (default 300) wall-clock, interpreter start-up included. It does NOT bound
# the shell prelude before it (bash start-up, opt-in check, project-root walk,
# binary lookup, payload spool). Measured on macOS under load average 13-21,
# where a bare `bash -c exit` took 12 ms: ~68 ms median from start to the
# injector's launch (two mktemp + cat are ~34 ms of it), and 25-30 ms for the
# default-off path, which every plugin user pays on every prompt and
# Read/Grep/Edit.

set -uo pipefail
# Tesserae's own `claude -p` calls (compile extraction, synthesis) load plugin
# hooks too and inherit the opt-in switches; the spawners mark them with this
# variable. Injecting there would feed the KB its own decisions as "new" input.
[[ -n "${TESSERAE_INTERNAL_LLM:-}" ]] && exit 0
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

hook_inject_enabled || exit 0

project_root="$(resolve_project_root)" || exit 0
[[ -n "$project_root" && -f "${project_root}/.tesserae/sqlite.db" ]] || exit 0
log_rel=".prompt-inject-hook.log"

tesserae_bin=$(find_tesserae) || { log_to "$log_rel" "skipped: tesserae binary not found"; exit 0; }
# Absolute before the `cd` below: a relative PATH entry makes `command -v`
# return a relative path, which would no longer resolve from bin_dir.
[[ "$tesserae_bin" == /* ]] || tesserae_bin="${PWD}/${tesserae_bin}"

# Fast path: run the module with the interpreter the `tesserae` launcher uses.
# `tesserae hook inject` is the same code but imports the whole CLI first
# (~200 ms), which alone would spend most of the budget.
# It runs from the launcher's own bin dir (the `cd` below), not the hook's
# cwd: `python -m` puts the cwd — the user's project, which may be a cloned
# untrusted repo — first on sys.path, so a `json.py` there would run in place
# of the stdlib on every prompt. bin_dir is the launcher's directory as found
# (for a pipx/uv/Homebrew symlink, the symlink's dir: user-owned, not the
# project). Not `-I`: that also drops user site-packages, where a
# `pip install --user` tesserae lives (find_tesserae probes those).
cmd=("$tesserae_bin" hook inject)
first_line=$(head -n 1 "$tesserae_bin" 2>/dev/null || true)
if [[ "$first_line" == '#!'*python* ]]; then
  read -r -a interp <<< "${first_line#\#!}"
  cmd=("${interp[@]}" -m tesserae.hook_inject)
fi
bin_dir=$(dirname "$tesserae_bin")

timeout_ms="${TESSERAE_HOOK_INJECT_TIMEOUT_MS:-300}"
[[ "$timeout_ms" =~ ^[0-9]+$ ]] || timeout_ms=300
# Python's own deadline (armed after its imports) sits 50 ms under the
# watchdog; it bounds the retrieval work, the watchdog bounds everything.
py_timeout_ms=$(( timeout_ms > 100 ? timeout_ms - 50 : timeout_ms ))
timeout_s=$(printf '%d.%03d' $(( timeout_ms / 1000 )) $(( timeout_ms % 1000 )))

in_file=$(mktemp "${TMPDIR:-/tmp}/tesserae-inject-in.XXXXXX") || { log_to "$log_rel" "skipped: mktemp failed"; exit 0; }
out_file=$(mktemp "${TMPDIR:-/tmp}/tesserae-inject-out.XXXXXX") || { rm -f "$in_file"; log_to "$log_rel" "skipped: mktemp failed"; exit 0; }
trap 'rm -f "$in_file" "$out_file"' EXIT
# A backgrounded command gets /dev/null as stdin when job control is off, so
# the payload is spooled to a file first rather than inherited.
cat > "$in_file"

# `exec` keeps $! the injector's own PID, which the watchdog relies on.
( cd "$bin_dir" && exec "${cmd[@]}" --project "$project_root" --timeout-ms "$py_timeout_ms" ) \
  < "$in_file" > "$out_file" 2>> "${project_root}/.tesserae/${log_rel}" &
pid=$!
# Watchdog. It only ever signals the PID this script just launched. That PID
# stays a zombie, so cannot be reused, until `wait` below reaps it, and the
# watchdog is stopped on the very next line: the reuse window is the gap
# between two shell statements, not the timeout.
# Two details are load-bearing. Its stdio goes to /dev/null: a `sleep` still
# holding the hook's stdout keeps the pipe open, and Claude Code (like any
# caller) waits for EOF, so every prompt would pay the full timeout. And its
# TERM trap kills its own `sleep`, so stopping it leaves no orphan behind.
(
  trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
  sleep "$timeout_s" &
  sleeper=$!
  wait "$sleeper"
  kill -TERM "$pid" 2>/dev/null
) > /dev/null 2>&1 &
watchdog=$!
wait "$pid"
rc=$?
kill "$watchdog" 2>/dev/null
wait "$watchdog" 2>/dev/null

if (( rc == 0 )); then
  cat "$out_file"
else
  log_to "$log_rel" "injector exited rc=${rc} (143 = killed by the ${timeout_ms} ms watchdog); injected nothing"
fi
exit 0
