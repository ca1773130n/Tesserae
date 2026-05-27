---
description: List and install named sync profile presets (work, personal, oss, minimal, compliance)
---

Browse and install pre-built sync profile presets. Presets configure which sections and targets are synced for common workflows.

Usage: /sync-preset [list|install] [OPTIONS]

Actions:
- list: Show all available presets with descriptions (default)
- install PRESET: Install a preset as a named profile

Options:
- --name NAME: Save the preset under a custom profile name
- --no-overwrite: Don't overwrite if a profile with the same name exists

Examples:
- /sync-preset                     List all presets
- /sync-preset install work        Install the "work" preset
- /sync-preset install oss --name my-oss  Install "oss" preset as "my-oss"

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_preset.py [user-provided arguments]
