#!/usr/bin/env bash
# Tesserae plugin — PreToolUse hook matching Bash. Intercepts agent-
# initiated `tesserae project compile` calls; when the graph already
# has more than 5000 nodes the compile will take minutes, so we surface
# Claude Code's permission dialog via the JSON-output protocol rather
# than letting the agent burn time silently.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
. "${HERE}/_lib.sh"

# Opt-out: emit a no-op JSON response so the tool call proceeds.
if [[ "$(read_plugin_setting pretooluse_compile)" != "true" ]]; then
  echo '{"permissionDecision": "allow"}'
  exit 0
fi

hook_input=$(cat)
command=$(echo "$hook_input" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Only inspect commands that actually invoke project compile. Anything
# else gets the default permission flow.
if [[ "$command" != *"tesserae project compile"* ]]; then
  echo '{"permissionDecision": "allow"}'
  exit 0
fi

project_root="$(resolve_project_root)"
graph_file="${project_root}/.tesserae/graph.json"

# If no graph yet, the first compile is fast — let it through.
if [[ ! -f "$graph_file" ]]; then
  echo '{"permissionDecision": "allow"}'
  exit 0
fi

# Read node count. If jq is missing or the file is unparseable, default
# to letting the compile proceed (less annoying than blocking on a
# parser failure).
nodes=0
if command -v jq >/dev/null 2>&1; then
  nodes=$(jq -r '.nodes | length' "$graph_file" 2>/dev/null || echo "0")
fi

# Per-session confirmation lock — once the user has approved a large
# compile in this session, don't ask again.
if (( nodes > 5000 )); then
  # NB: we do NOT pre-touch a confirmation lock here. Doing so would
  # mean a user who DECLINES the dialog has the next compile silently
  # auto-allowed. Claude Code doesn't surface a post-decision callback
  # we can hook into to write the lock only on accept, so we always
  # ask — the cost is one extra prompt per large compile, the benefit
  # is consistent behaviour with the user's stated preference.
  cat <<JSON
{
  "permissionDecision": "ask",
  "systemMessage": "Tesserae graph has ${nodes} nodes; project compile will take several minutes. Proceed?"
}
JSON
  exit 0
fi

echo '{"permissionDecision": "allow"}'
exit 0
