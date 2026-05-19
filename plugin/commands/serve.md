---
description: Serve the compiled Tesserae site locally for browsing.
argument-hint: "[--host HOST] [--port PORT]"
allowed-tools:
  - "Bash(tesserae project serve:*)"
---

Start the dev HTTP server against `.tesserae/site/`. Default port 8765. Pass `--host 0.0.0.0` to expose on the LAN.

The server runs in the foreground; stop it with Ctrl-C in the underlying terminal.

!`tesserae project serve $ARGUMENTS`
