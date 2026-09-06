#!/usr/bin/env bash
# Render harness runner — simulates a staging install of desktop/plugin.js
# (plugin copy + resolvable node_modules with a stubbed @hermes/plugin-sdk and
# real react/react-dom), then renders the pane with react-dom/server and
# asserts the professional-bar behaviors. See tests/render_harness_body.mjs.
#
# Prereqs: node, network for `npm install react react-dom` (cached in /tmp).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
FRONT="/tmp/skt-front"

mkdir -p "$FRONT/staging/node_modules/@hermes/plugin-sdk"
if [ ! -d "$FRONT/node_modules/react" ]; then
  cd "$FRONT"
  [ -f package.json ] || echo '{"name":"skills-toggle-render-harness","private":true,"type":"module"}' > package.json
  npm install --no-fund --no-audit --silent react@18 react-dom@18
fi

# staging node_modules: stub + real react
cp "$HERE/sdk_stub.mjs" "$FRONT/staging/node_modules/@hermes/plugin-sdk/index.js"
cat > "$FRONT/staging/node_modules/@hermes/plugin-sdk/package.json" <<'JSON'
{
  "name": "@hermes/plugin-sdk",
  "version": "0.0.0-stub",
  "type": "module",
  "main": "index.js",
  "exports": { ".": "./index.js" }
}
JSON
ln -sfn "$FRONT/node_modules/react" "$FRONT/staging/node_modules/react"
ln -sfn "$FRONT/node_modules/react-dom" "$FRONT/staging/node_modules/react-dom"

# fresh plugin copy each run (like a hot reload)
cp "$REPO/desktop/plugin.js" "$FRONT/staging/plugin.js"
cp "$HERE/render_harness_body.mjs" "$FRONT/render_harness_body.mjs"

cd "$FRONT"
PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" exec node render_harness_body.mjs
