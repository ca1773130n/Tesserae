#!/usr/bin/env bash
# Print a compact Tesserae project status: graph node/edge counts,
# last-compile timestamp, session count. Pure read — never mutates.
# Designed for `/tesserae:status` in the Claude Code plugin.

set -uo pipefail

# Resolve project root: an explicit positional arg wins; otherwise
# walk upward from $PWD looking for .tesserae/, fall back to the git
# toplevel, finally fall back to $PWD itself.
if [[ -n "${1:-}" ]]; then
  project_root="$1"
else
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
    else
      project_root="$PWD"
    fi
  fi
fi
tdir="${project_root}/.tesserae"

if [[ ! -d "$tdir" ]]; then
  echo "Tesserae status: no .tesserae/ found at or above ${project_root}" >&2
  echo "  Run /tesserae:setup or 'tesserae project setup' to initialise this project."
  exit 1
fi

# Distinguish the four states the user can be in:
#   not initialized       — no .tesserae/ at all (returned above already)
#   initialized, not compiled yet — .tesserae/ exists but graph.json doesn't
#   parser unavailable    — graph.json exists but neither jq nor python3 can parse it
#   compiled              — graph.json parses; show counts
nodes="?"
edges="?"
graph_state="compiled"
if [[ ! -f "${tdir}/graph.json" ]]; then
  graph_state="initialized, not compiled yet"
  nodes="0"
  edges="0"
elif command -v jq >/dev/null 2>&1; then
  nodes=$(jq -r '.nodes | length' "${tdir}/graph.json" 2>/dev/null || echo "?")
  edges=$(jq -r '.edges | length' "${tdir}/graph.json" 2>/dev/null || echo "?")
elif command -v python3 >/dev/null 2>&1; then
  counts=$(python3 -c "
import json, sys
try:
    g = json.load(open(sys.argv[1]))
    print(len(g.get('nodes', [])), len(g.get('edges', [])))
except Exception:
    print('? ?')
" "${tdir}/graph.json" 2>/dev/null || echo "? ?")
  nodes="${counts% *}"
  edges="${counts#* }"
else
  graph_state="parser unavailable (install jq or python3 for counts)"
fi

# Last build timestamp from the JSONL ledger. The real field name is
# `built_at` (per tesserae/project.py); we also accept legacy `timestamp`
# and `at` for forward-compat with older ledgers.
last_build="never"
if [[ -f "${tdir}/.build-history.jsonl" ]]; then
  # Scan backward for the most recent line that parses + carries a
  # known timestamp field, so a single corrupt line doesn't poison
  # the whole status print.
  if command -v jq >/dev/null 2>&1; then
    # Reverse the ledger so we read newest-first. macOS uses `tail -r`;
    # GNU coreutils uses `tac`. Prefer the portable form when available.
    if command -v tac >/dev/null 2>&1; then
      reversed=$(tac "${tdir}/.build-history.jsonl" 2>/dev/null)
    else
      reversed=$(tail -r "${tdir}/.build-history.jsonl" 2>/dev/null)
    fi
    last_build=$(printf '%s\n' "$reversed" | \
      while IFS= read -r line; do
        ts=$(echo "$line" | jq -r '.built_at // .timestamp // .at // empty' 2>/dev/null)
        if [[ -n "$ts" && "$ts" != "null" ]]; then
          echo "$ts"
          break
        fi
      done)
    [[ -z "$last_build" ]] && last_build="unknown (history corrupt)"
  else
    # Best-effort grep over the last 5 lines for a known timestamp field.
    last_build=$(tail -n 5 "${tdir}/.build-history.jsonl" 2>/dev/null | \
      grep -oE '"(built_at|timestamp|at)"[[:space:]]*:[[:space:]]*"[^"]+"' | \
      tail -1 | sed -E 's/.*"([^"]+)"$/\1/')
    [[ -z "$last_build" ]] && last_build="unknown"
  fi
fi

# Session count from the harness_sessions manifest.
sessions=0
if [[ -f "${tdir}/harness_sessions/manifest.json" ]]; then
  if command -v jq >/dev/null 2>&1; then
    sessions=$(jq -r '.sessions | length' "${tdir}/harness_sessions/manifest.json" 2>/dev/null || echo "0")
  else
    sessions=$(grep -c '"id"' "${tdir}/harness_sessions/manifest.json" 2>/dev/null || echo "0")
  fi
fi

# Vault path if configured.
vault="(not configured)"
if [[ -f "${tdir}/config.json" ]] && command -v jq >/dev/null 2>&1; then
  vault=$(jq -r '.obsidian.vault_path // "(not configured)"' "${tdir}/config.json" 2>/dev/null || echo "(not configured)")
fi

cat <<EOT
Tesserae status — ${project_root}
  graph:         ${graph_state} — ${nodes} nodes, ${edges} edges
  last compile:  ${last_build}
  sessions:      ${sessions}
  obsidian:      ${vault}

Quick actions:
  /tesserae:refresh         — import sessions + recompile + sync vault
  /tesserae:ask "…"         — query the compiled graph
  /tesserae:serve           — preview the static site
EOT
