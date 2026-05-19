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

# Normalise smart/curly quotes that macOS autocorrect inserts. We
# convert them to the ASCII equivalent BEFORE the strip step so a
# question wrapped in “…” or ‘…’ unwraps the same way as the ASCII
# form.
q="${q//$'“'/\"}"   # left double quotation mark
q="${q//$'”'/\"}"   # right double quotation mark
q="${q//$'‘'/\'}"   # left single quotation mark
q="${q//$'’'/\'}"   # right single quotation mark

# Strip one matching pair of surrounding ASCII quotes (after the
# smart-quote normalisation above so all four forms unwrap).
if [[ "$q" =~ ^\"(.*)\"$ ]]; then
  q="${BASH_REMATCH[1]}"
elif [[ "$q" =~ ^\'(.*)\'$ ]]; then
  q="${BASH_REMATCH[1]}"
fi

# Un-escape backslash-quoted inner quotes (\" → ", \' → '). Without
# this, a user typing /tesserae:ask "say \"hi\" now" would land at
# the CLI with the literal backslashes still in the question text.
q="${q//\\\"/\"}"
q="${q//\\\'/\'}"

if [[ -z "$q" ]]; then
  echo "Error: /tesserae:ask requires a question. Usage: /tesserae:ask \"your question\"" >&2
  exit 2
fi

exec tesserae project ask "$q"
