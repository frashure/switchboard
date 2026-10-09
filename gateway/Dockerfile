# Build context is the repo root (see docker-compose.yaml): the image needs
# web/ (the app), firmware/lvgl_sim (the LVGL prototype, compiled here) and
# gateway/ (the service that serves them). Requires `git submodule update --init` first --
# the LVGL sources are a submodule.

# ---- Stage 1: the web app (Svelte PWA, web/)
FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: compile the LVGL web UI (same toolchain as firmware/lvgl_sim/build.sh)
FROM emscripten/emsdk:6.0.9 AS sim-build
WORKDIR /src
COPY firmware/lvgl_sim/ ./
RUN mkdir -p build && cd build && emcmake cmake .. && emmake make -j"$(nproc)"

# ---- Stage 3: the Gateway
FROM python:3.12-slim
WORKDIR /app

# silero-vad needs torch for CPU inference only; the default PyPI wheel
# bundles CUDA libraries (several GB) the Gateway never uses.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY gateway/pyproject.toml ./
COPY gateway/switchboard ./switchboard
COPY gateway/config ./config
RUN pip install --no-cache-dir .

# Web clients, served by the Gateway (one origin => one HTTPS endpoint is
# enough for the tablet): the PWA at /, the LVGL prototype at /lvgl/, and the
# plain-HTML virtual device at /virtual_device/.
COPY --from=web-build /web/dist/ /app/static/
COPY --from=sim-build /src/build/index.html /app/static/lvgl/index.html
COPY firmware/lvgl_sim/bridge.js firmware/lvgl_sim/pcm-worklet.js /app/static/lvgl/
COPY gateway/client/virtual_device /app/static/virtual_device
ENV SWITCHBOARD_STATIC_DIR=/app/static

EXPOSE 8090
# --ws-ping-interval/--ws-ping-timeout: uvicorn's defaults (20s/20s = 40s
# combined) are shorter than this app's legitimate LLM-turn wait times
# (multi-tool turns can take minutes) -- confirmed via a real disconnect
# that the default closes the connection mid-turn. Heartbeats from the
# Gateway keep the connection alive during long waits.
CMD ["uvicorn", "switchboard.main:app", "--host", "0.0.0.0", "--port", "8090", "--ws-ping-interval", "120", "--ws-ping-timeout", "120"]
