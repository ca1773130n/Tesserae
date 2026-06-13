#!/usr/bin/env bash
# Upload + submit "Clip to Tesserae" to the Chrome Web Store via the API.
#
# Requires (export before running; never commit these):
#   CWS_EXTENSION_ID    item id from the Developer Dashboard (created once via UI)
#   CWS_CLIENT_ID       Google Cloud OAuth client id (Chrome Web Store API enabled)
#   CWS_CLIENT_SECRET   OAuth client secret
#   CWS_REFRESH_TOKEN   OAuth refresh token
#
# Usage: extension/scripts/publish.sh [--no-publish]
#   --no-publish  upload a new draft version but do NOT submit for review
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # extension/
root="$(cd "$here/.." && pwd)"
version="$(node -p "require('$here/manifest.json').version" 2>/dev/null \
  || python3 -c "import json;print(json.load(open('$here/manifest.json'))['version'])")"
zip="$root/dist/tesserae-clip-v$version.zip"

: "${CWS_EXTENSION_ID:?set CWS_EXTENSION_ID}"
: "${CWS_CLIENT_ID:?set CWS_CLIENT_ID}"
: "${CWS_CLIENT_SECRET:?set CWS_CLIENT_SECRET}"
: "${CWS_REFRESH_TOKEN:?set CWS_REFRESH_TOKEN}"

echo "==> Rebuilding $zip"
rm -f "$zip"; mkdir -p "$root/dist"
( cd "$here" && zip -rq "$zip" . -x '*.DS_Store' -x 'store/*' -x 'scripts/*' )

publish_flag="--auto-publish"
[ "${1:-}" = "--no-publish" ] && publish_flag=""

echo "==> Uploading v$version to item $CWS_EXTENSION_ID"
npx --yes chrome-webstore-upload-cli@3 upload \
  --source "$zip" \
  --extension-id "$CWS_EXTENSION_ID" \
  --client-id "$CWS_CLIENT_ID" \
  --client-secret "$CWS_CLIENT_SECRET" \
  --refresh-token "$CWS_REFRESH_TOKEN"

if [ -n "$publish_flag" ]; then
  echo "==> Submitting for review"
  npx --yes chrome-webstore-upload-cli@3 publish \
    --extension-id "$CWS_EXTENSION_ID" \
    --client-id "$CWS_CLIENT_ID" \
    --client-secret "$CWS_CLIENT_SECRET" \
    --refresh-token "$CWS_REFRESH_TOKEN"
  echo "==> Submitted. Track status in the Developer Dashboard."
else
  echo "==> Uploaded as draft (not submitted)."
fi
