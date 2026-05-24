# Shared helpers for Tesserae plugin hooks. Sourced by every hook
# script — kept POSIX-friendly so hooks work under either bash or the
# user's shell of choice.
#
# Two contracts every hook upholds:
#   1. Missing `tesserae` binary → exit 0 silently after logging.
#   2. Per-project opt-out via .claude/tesserae.local.md frontmatter.

# --------------------------------------------------------------------
# find_tesserae — locate the CLI binary. Probes PATH first, then the
# common pipx/pip --user locations. Echoes the absolute path on
# success; non-empty exit code on miss.
# --------------------------------------------------------------------
find_tesserae() {
  if command -v tesserae >/dev/null 2>&1; then
    command -v tesserae
    return 0
  fi
  local project_root
  project_root=$(resolve_project_root 2>/dev/null || echo "$PWD")
  # Probe project-local venvs first, then user-wide locations.
  for candidate in \
    "${project_root}/.venv/bin/tesserae" \
    "${project_root}/venv/bin/tesserae" \
    "$HOME/.local/bin/tesserae" \
    "$HOME/Library/Python/3.13/bin/tesserae" \
    "$HOME/Library/Python/3.12/bin/tesserae" \
    "$HOME/Library/Python/3.11/bin/tesserae" \
    "$HOME/Library/Python/3.10/bin/tesserae" \
    "/opt/homebrew/bin/tesserae" \
    "/usr/local/bin/tesserae"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# --------------------------------------------------------------------
# resolve_project_root — walk upward from $PWD looking for .tesserae/,
# fall back to git toplevel, finally $PWD itself.
# --------------------------------------------------------------------
resolve_project_root() {
  local candidate="$PWD"
  while [[ "$candidate" != "/" && -n "$candidate" ]]; do
    if [[ -d "${candidate}/.tesserae" ]]; then
      echo "$candidate"
      return 0
    fi
    candidate="$(dirname "$candidate")"
  done
  local git_root
  if git_root=$(git rev-parse --show-toplevel 2>/dev/null) && [[ -d "${git_root}/.tesserae" ]]; then
    echo "$git_root"
    return 0
  fi
  echo "$PWD"
  return 0
}

# --------------------------------------------------------------------
# read_plugin_setting <key> — parse the project's
# .claude/tesserae.local.md frontmatter for one hooks.<key> value.
# Recognises: hooks.session_start, hooks.session_end,
# hooks.posttooluse_edit, hooks.pretooluse_compile.
# Echoes "true" or "false". Defaults applied when the file or the
# key is missing:
#   session_start=true, session_end=true,
#   posttooluse_edit=false, pretooluse_compile=true.
# --------------------------------------------------------------------
read_plugin_setting() {
  local key="$1"
  local project_root
  project_root=$(resolve_project_root)
  local settings_file="${project_root}/.claude/tesserae.local.md"

  # Defaults — match the spec.
  local default_value
  case "$key" in
    session_start|session_end|pretooluse_compile) default_value="true" ;;
    # The CodeGraph adapter ships a fast, idempotent sync — opt-in by
    # default so projects that use ``tesserae project sync-code`` get
    # an always-fresh code-graph.json without per-project setup. Set
    # ``sync_code_on_start: false`` in tesserae.local.md to disable.
    sync_code_on_start) default_value="true" ;;
    # PostToolUse(Edit|Write|MultiEdit) re-runs ``tesserae project
    # sync-code`` after every edit, debounced to once every 30s.
    # Default on so the typed code-graph keeps tracking CodeGraph
    # updates in near-real-time. Opt-out via
    # ``sync_code_on_edit: false`` in tesserae.local.md.
    sync_code_on_edit) default_value="true" ;;
    posttooluse_edit) default_value="false" ;;
    *) default_value="false" ;;
  esac

  if [[ ! -f "$settings_file" ]]; then
    echo "$default_value"
    return 0
  fi

  # Extract the YAML frontmatter (the block between the two `---`
  # markers at the top of the file). Reuse the sed trick from
  # ralph-loop's stop hook.
  local frontmatter
  frontmatter=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$settings_file" 2>/dev/null)
  if [[ -z "$frontmatter" ]]; then
    echo "$default_value"
    return 0
  fi

  # Look for "  <key>: value" under a "hooks:" header. We accept
  # both nested form and flat dot-notation:
  #   hooks:
  #     session_end: false
  # or
  #   hooks.session_end: false
  local value
  value=$(echo "$frontmatter" | grep -E "^[[:space:]]*${key}:[[:space:]]" | head -1 | sed -E "s/^[[:space:]]*${key}:[[:space:]]*//; s/[[:space:]]*$//")
  if [[ -z "$value" ]]; then
    value=$(echo "$frontmatter" | grep -E "^hooks\.${key}:[[:space:]]" | head -1 | sed -E "s/^hooks\.${key}:[[:space:]]*//; s/[[:space:]]*$//")
  fi

  if [[ "$value" == "true" || "$value" == "false" ]]; then
    echo "$value"
  else
    echo "$default_value"
  fi
}

# --------------------------------------------------------------------
# log_to <relative-path> <message> — append a timestamped line to a
# log file under the project's .tesserae/ dir. No-op if the dir
# doesn't exist (won't create it just to log).
# --------------------------------------------------------------------
log_to() {
  local rel_path="$1"
  shift
  local project_root
  project_root=$(resolve_project_root)
  local tdir="${project_root}/.tesserae"
  if [[ ! -d "$tdir" ]]; then
    return 0
  fi
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "${tdir}/${rel_path}" 2>/dev/null || true
}
