# Build-time only -- there is no runtime container here. This just gives
# us a pinned, reproducible Emscripten SDK without installing it on the
# host (docs/design.md Section 6: containerized build, keeps the host
# clean). Output is a static index.html; see build.sh.
FROM emscripten/emsdk:6.0.9
WORKDIR /src
