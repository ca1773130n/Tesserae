#!/usr/bin/env bash
# Three-step refresh: import sessions → compile → sync vault.
# Stop-on-failure semantics (`set -euo pipefail`), explicit step labels
# so partial-run state is obvious from the output, deterministic
# one-line summary at the end parsed from the compile output.

set -uo pipefail

# Resolve project root: walk upward from $PWD looking for .tesserae/,
# fall back to `git rev-parse --show-toplevel`, finally fall back to $PWD.
project_root=""
candidate="$PWD"
while [[ "$candidate" != "/" && -n "$candidate" ]]; do
  if [[ -d "${candidate}/.tesserae" ]]; then
    project_root="$candidate"
    break
  fi
  candidate="$(dirname "$candidate")"
done
if [[ -z "$project_root" ]]; then
  if git_root=$(git rev-parse --show-toplevel 2>/dev/null) && [[ -d "${git_root}/.tesserae" ]]; then
    project_root="$git_root"
  fi
fi
if [[ -z "$project_root" ]]; then
  echo "tesserae-refresh: no .tesserae/ found at or above $PWD" >&2
  echo "  Run /tesserae:setup first to initialise this project."
  exit 1
fi

echo "=== /tesserae:refresh against ${project_root} ==="
cd "$project_root"

# ---- Step 1: import sessions ---------------------------------------------
echo
echo "[1/3] tesserae sessions discover --import"
if ! tesserae sessions discover --import; then
  echo "tesserae-refresh: sessions discover --import failed; aborting" >&2
  exit 2
fi

# ---- Step 2: compile (capture output so we can parse the summary) --------
echo
echo "[2/3] tesserae project compile"
compile_log=$(mktemp -t tesserae-refresh.XXXXXX)
trap 'rm -f "$compile_log"' EXIT
if ! tesserae project compile 2>&1 | tee "$compile_log"; then
  echo "tesserae-refresh: project compile failed; aborting" >&2
  exit 3
fi

# ---- Step 3: vault sync --------------------------------------------------
echo
echo "[3/3] tesserae project obsidian-sync"
sync_log=$(mktemp -t tesserae-sync.XXXXXX)
trap 'rm -f "$compile_log" "$sync_log"' EXIT
if ! tesserae project obsidian-sync 2>&1 | tee "$sync_log"; then
  # Vault sync is optional — a project might not configure Obsidian. Log
  # but don't fail the whole refresh.
  echo "tesserae-refresh: obsidian-sync failed (may be unconfigured); continuing." >&2
fi

# ---- Summary -------------------------------------------------------------
# The compile output's standard final line is:
#   Compiled project wiki: processed=NNN skipped=NNN nodes=NNN edges=NNN
# Grep for the values; fall back to "?" when absent (e.g. compile path changed).
summary_line=$(grep -E "^Compiled project wiki" "$compile_log" | tail -1 || true)
nodes=$(echo "$summary_line" | grep -oE 'nodes=[0-9]+' | head -1 | cut -d= -f2)
edges=$(echo "$summary_line" | grep -oE 'edges=[0-9]+' | head -1 | cut -d= -f2)
processed=$(echo "$summary_line" | grep -oE 'processed=[0-9]+' | head -1 | cut -d= -f2)
sessions=$(grep -oE 'Imported harness sessions: [0-9]+' "$compile_log" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || echo "0")
orphans=$(grep -oE 'pruned [0-9]+ orphan' "$sync_log" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || echo "0")

echo
echo "=== refresh complete ==="
printf 'nodes=%s edges=%s processed=%s sessions=%s vault_orphans_pruned=%s\n' \
  "${nodes:-?}" "${edges:-?}" "${processed:-?}" "${sessions:-0}" "${orphans:-0}"
