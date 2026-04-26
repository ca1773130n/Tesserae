#!/usr/bin/env bash
# LLM-Wiki installer
#
# Quick install:
#   curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash
#
# With options:
#   curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash -s -- --dir ~/.llm-wiki/LLM-Wiki --skip-shell-config

set -euo pipefail

REPO_URL="${LLM_WIKI_REPO_URL:-https://github.com/ca1773130n/LLM-Wiki.git}"
BRANCH="${LLM_WIKI_BRANCH:-main}"
INSTALL_DIR="${LLM_WIKI_INSTALL_DIR:-$HOME/.llm-wiki/LLM-Wiki}"
BIN_DIR="${LLM_WIKI_BIN_DIR:-$HOME/.local/bin}"
USE_VENV=1
UPDATE_SHELL_CONFIG=1

usage() {
  cat <<'USAGE'
LLM-Wiki installer

Usage:
  install.sh [OPTIONS]

Quick install:
  curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

Options:
  --dir PATH             Install/update checkout at PATH (default: ~/.llm-wiki/LLM-Wiki)
  --branch NAME          Git branch to install (default: main)
  --repo URL             Git repository URL (default: https://github.com/ca1773130n/LLM-Wiki.git)
  --bin-dir PATH         Directory for the llm_wiki command wrapper (default: ~/.local/bin)
  --no-venv              Install into the current Python environment instead of .venv
  --skip-shell-config    Do not append PATH setup to shell rc files
  -h, --help             Show this help

After install:
  llm_wiki project init --source-kind Repository --source README.md --source docs --source src
  llm_wiki project compile --changed-only
  llm_wiki project build-site
USAGE
}

log() { printf '\033[1;34m[llm-wiki]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llm-wiki]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[llm-wiki]\033[0m %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dir)
      [ "$#" -ge 2 ] || fail "--dir requires a path"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --branch)
      [ "$#" -ge 2 ] || fail "--branch requires a name"
      BRANCH="$2"
      shift 2
      ;;
    --repo)
      [ "$#" -ge 2 ] || fail "--repo requires a URL"
      REPO_URL="$2"
      shift 2
      ;;
    --bin-dir)
      [ "$#" -ge 2 ] || fail "--bin-dir requires a path"
      BIN_DIR="$2"
      shift 2
      ;;
    --no-venv)
      USE_VENV=0
      shift
      ;;
    --skip-shell-config)
      UPDATE_SHELL_CONFIG=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

need_cmd git
need_cmd python3

mkdir -p "$(dirname "$INSTALL_DIR")" "$BIN_DIR"

if [ -d "$INSTALL_DIR/.git" ]; then
  log "Updating existing checkout at $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only --quiet origin "$BRANCH"
else
  if [ -e "$INSTALL_DIR" ]; then
    fail "$INSTALL_DIR exists but is not a git checkout"
  fi
  log "Cloning $REPO_URL#$BRANCH into $INSTALL_DIR"
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

PYTHON_BIN="python3"
if [ "$USE_VENV" -eq 1 ]; then
  VENV_DIR="$INSTALL_DIR/.venv"
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    log "Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  PYTHON_BIN="$VENV_DIR/bin/python"
fi

log "Installing LLM-Wiki CLI"
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null
"$PYTHON_BIN" -m pip install -e "$INSTALL_DIR"

COMMAND_BIN="$($PYTHON_BIN - <<'PY'
import os, sysconfig
print(os.path.join(sysconfig.get_path('scripts'), 'llm_wiki'))
PY
)"

cat > "$BIN_DIR/llm_wiki" <<EOF
#!/usr/bin/env bash
exec "$COMMAND_BIN" "\$@"
EOF
chmod +x "$BIN_DIR/llm_wiki"

cat > "$BIN_DIR/llm-wiki" <<EOF
#!/usr/bin/env bash
exec "$COMMAND_BIN" "\$@"
EOF
chmod +x "$BIN_DIR/llm-wiki"

if [ "$UPDATE_SHELL_CONFIG" -eq 1 ]; then
  PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$rc" ] && ! grep -Fq "$BIN_DIR" "$rc"; then
      log "Adding $BIN_DIR to PATH in $rc"
      {
        printf '\n# LLM-Wiki CLI\n'
        printf '%s\n' "$PATH_LINE"
      } >> "$rc"
    fi
  done
fi

if ! command -v "$BIN_DIR/llm_wiki" >/dev/null 2>&1; then
  warn "Installed wrapper at $BIN_DIR/llm_wiki, but it is not discoverable via command -v. Add $BIN_DIR to PATH."
fi

log "Installed: $BIN_DIR/llm_wiki"
log "Try: llm_wiki project init --source-kind Repository --source README.md --source docs --source src"
