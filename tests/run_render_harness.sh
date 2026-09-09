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
FRONT="/tmp/hermes-loadout-tests"

mkdir -p "$FRONT/staging/node_modules/@hermes/plugin-sdk"
if [ ! -d "$FRONT/node_modules/react" ] || [ ! -d "$FRONT/node_modules/react-test-renderer" ]; then
  cd "$FRONT"
  [ -f package.json ] || echo '{"name":"hermes-loadout-render-harness","private":true,"type":"module"}' > package.json
  npm install --no-fund --no-audit --silent react@18.3.1 react-dom@18.3.1 react-test-renderer@18.3.1
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
ln -sfn "$FRONT/node_modules/react-test-renderer" "$FRONT/staging/node_modules/react-test-renderer"

# fresh plugin copy each run (like a hot reload)
cp "$REPO/desktop/plugin.js" "$FRONT/staging/plugin.js"
cp "$HERE/render_harness_body.mjs" "$FRONT/render_harness_body.mjs"
cp "$HERE/workspace_harness_body.mjs" "$FRONT/workspace_harness_body.mjs"
cp "$HERE/mutation_harness_body.mjs" "$FRONT/mutation_harness_body.mjs"
cp "$HERE/client_library_harness_body.mjs" "$FRONT/client_library_harness_body.mjs"
cp "$HERE/loadout_harness_body.mjs" "$FRONT/loadout_harness_body.mjs"
# committed render fixtures (screenshot-shaped 105-skill state) ride along
rm -rf "$FRONT/fixtures"
cp -R "$HERE/fixtures" "$FRONT/fixtures"

cd "$FRONT"
PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" node render_harness_body.mjs
PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" node workspace_harness_body.mjs

PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" node mutation_harness_body.mjs

PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" node client_library_harness_body.mjs

PLUGIN_SRC="$FRONT/staging/plugin.js" STAGING_PLUGIN="$FRONT/staging/plugin.js" node loadout_harness_body.mjs
