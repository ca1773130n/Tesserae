#!/usr/bin/env bash
# Wrapper for /tesserae:ask that strips one matching pair of surrounding
# quotes from $ARGUMENTS before forwarding the question to the CLI.
#
# Why this exists: Claude Code's $ARGUMENTS interpolation passes the
# literal user-typed text including any surrounding quotes. For
# `/tesserae:ask "what did we decide?"`, $ARGUMENTS becomes the
# 8-character string `"what did we decide?"` (with quotes), not the
# 7-word question. `tesserae project ask "$ARGUMENTS"` would then call
# the CLI with a *quoted-string* question — confusing the parser.
#
# We strip ONE matching pair of double or single quotes if present and
# pass the inner text on. Multi-word questions without surrounding
# quotes are forwarded verbatim (also correct).

set -euo pipefail

q="${1-}"
if [[ "$q" =~ ^\"(.*)\"$ ]]; then
  q="${BASH_REMATCH[1]}"
elif [[ "$q" =~ ^\'(.*)\'$ ]]; then
  q="${BASH_REMATCH[1]}"
fi

if [[ -z "$q" ]]; then
  echo "Error: /tesserae:ask requires a question. Usage: /tesserae:ask \"your question\"" >&2
  exit 2
fi

exec tesserae project ask "$q"
