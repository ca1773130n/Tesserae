#!/usr/bin/env bash
# Friendly stub for /tesserae:setup. The Tesserae setup wizard is
# interactive (uses Python's input()), and Claude Code slash commands
# run in a non-interactive shell with no stdin attached, so the
# wizard's first prompt hits EOF immediately. Print instructions
# pointing the user at a real terminal.
#
# Lives as a script (not inline in the slash command body) because
# the message text needs to contain literal backticks for
# `.tesserae/` and `/tesserae:*`, and inline backticks would close
# the outer bash block in the slash-command's `!`-prefix syntax.

cat <<EOF

  The Tesserae setup wizard is interactive and can't run inside a Claude
  Code slash command (no interactive stdin → EOF on first prompt).

  Run this in a real terminal instead:

    cd ${PWD}
    tesserae init

  Once .tesserae/ exists in this project, every other /tesserae:* command
  works inline from your Claude Code session.

EOF
