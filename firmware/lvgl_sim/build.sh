#!/usr/bin/env bash
# Builds the LVGL-in-browser UI prototype entirely inside a container --
# no Emscripten SDK install on the host. Output: build/index.html
# (WASM inlined via SINGLE_FILE, so no separate .wasm/.js to serve).
#
# Usage:
#   firmware/lvgl_sim/build.sh
#   python3 -m http.server 8002 --directory firmware/lvgl_sim/build
#   open http://localhost:8002/index.html?gateway=localhost:8090
set -euo pipefail
cd "$(dirname "$0")"

docker build -t switchboard-lvgl-sim-builder .

docker run --rm \
  -v "$(pwd)":/src \
  -w /src \
  -u "$(id -u):$(id -g)" \
  switchboard-lvgl-sim-builder \
  bash -c "mkdir -p build && cd build && emcmake cmake .. && emmake make -j\$(nproc)"

# bridge.js/pcm-worklet.js are loaded by relative <script src> from the
# built index.html, so they need to sit alongside it to be served.
cp bridge.js pcm-worklet.js build/
