#!/usr/bin/env bash
# Build and run the Chatterbox benchmark in a container.
#
#   run.sh rocm  turbo            # AMD GPU (e.g. the R9700 server)
#   run.sh cuda  turbo            # NVIDIA GPU (needs nvidia-container-toolkit)
#   run.sh cpu   nano             # no GPU (slow; mostly to check the setup)
#   run.sh rocm  turbo --ref /refs/my_voice.wav --reps 3   (put my_voice.wav next to this script)
#   run.sh rocm  turbo --workers 2     # also test 2 parallel model instances
#   run.sh rocm  turbo --bucket 32     # bound S3Gen shapes + pre-tune them (fixes the cold-shape cost)
#
# Output WAVs and results.json land in ./out; model weights are cached in
# ~/.cache/huggingface. Set HF_TOKEN if Hugging Face asks for one.
# Needs bash (arrays, pipefail); re-run under it if started with `sh run.sh`.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
cd "$(dirname "$0")"

GPU="${1:-rocm}"; MODEL="${2:-turbo}"; shift $(( $# >= 2 ? 2 : $# )) || true

case "$GPU" in
  rocm) INDEX="https://download.pytorch.org/whl/rocm7.2"
        # Numeric host GIDs, not names: --group-add resolves names inside the
        # container, whose /etc/group (python:slim) has no "render" group.
        RUN_FLAGS=(--device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined --ipc=host)
        for grp in video render; do
          gid="$(getent group "$grp" | cut -d: -f3 || true)"
          [ -n "$gid" ] && RUN_FLAGS+=(--group-add "$gid")
        done ;;
  cuda) INDEX="https://download.pytorch.org/whl/cu126"
        RUN_FLAGS=(--gpus all) ;;
  cpu)  INDEX="https://download.pytorch.org/whl/cpu"
        RUN_FLAGS=() ;;
  *) echo "usage: $0 {rocm|cuda|cpu} {turbo|nano} [bench.py args]" >&2; exit 2 ;;
esac

IMAGE="chatterbox-bench:$GPU"
docker build -t "$IMAGE" --build-arg TORCH_INDEX="$INDEX" .

mkdir -p out "$HOME/.cache/huggingface" "$HOME/.cache/chatterbox-bench/miopen-config" "$HOME/.cache/chatterbox-bench/miopen-cache"
# MIOpen (ROCm's conv library) compiles/tunes kernels per tensor shape; keep
# its caches across runs, or every run pays that cost again. Its warnings are
# also extremely chatty, so only show errors (override with MIOPEN_LOG_LEVEL).
EXTRA_ENV=(-e "MIOPEN_LOG_LEVEL=${MIOPEN_LOG_LEVEL:-2}")
for var in $(compgen -e | grep '^MIOPEN_' | grep -v '^MIOPEN_LOG_LEVEL$' || true); do EXTRA_ENV+=(-e "$var"); done
[ -n "${HF_TOKEN:-}" ] && EXTRA_ENV+=(-e HF_TOKEN)
[ -n "${HSA_OVERRIDE_GFX_VERSION:-}" ] && EXTRA_ENV+=(-e HSA_OVERRIDE_GFX_VERSION)

DEVICE=$([ "$GPU" = cpu ] && echo cpu || echo cuda)   # ROCm torch also calls it "cuda"
docker run --rm "${RUN_FLAGS[@]}" "${EXTRA_ENV[@]}" \
  -v "$PWD/out:/out" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -v "$HOME/.cache/chatterbox-bench/miopen-config:/root/.config/miopen" \
  -v "$HOME/.cache/chatterbox-bench/miopen-cache:/root/.cache/miopen" \
  -v "$PWD/../../gateway/switchboard/text.py:/bench/switchboard_text.py:ro" \
  -v "$PWD:/refs:ro" \
  "$IMAGE" --model "$MODEL" --device "$DEVICE" --out /out "$@"
