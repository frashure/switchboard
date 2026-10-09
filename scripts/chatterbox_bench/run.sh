#!/usr/bin/env bash
# Build and run the Chatterbox benchmark in a container.
#
#   run.sh rocm  turbo            # AMD GPU (e.g. the R9700 server)
#   run.sh cuda  turbo            # NVIDIA GPU (needs nvidia-container-toolkit)
#   run.sh cpu   nano             # no GPU (slow; mostly to check the setup)
#   run.sh rocm  turbo --ref /refs/my_voice.wav --reps 3   (put my_voice.wav next to this script)
#
# Output WAVs and results.json land in ./out; model weights are cached in
# ~/.cache/huggingface. Set HF_TOKEN if Hugging Face asks for one.
set -euo pipefail
cd "$(dirname "$0")"

GPU="${1:-rocm}"; MODEL="${2:-turbo}"; shift $(( $# >= 2 ? 2 : $# )) || true

case "$GPU" in
  rocm) INDEX="https://download.pytorch.org/whl/rocm7.2"
        RUN_FLAGS=(--device=/dev/kfd --device=/dev/dri --group-add video --group-add render
                   --security-opt seccomp=unconfined --ipc=host) ;;
  cuda) INDEX="https://download.pytorch.org/whl/cu126"
        RUN_FLAGS=(--gpus all) ;;
  cpu)  INDEX="https://download.pytorch.org/whl/cpu"
        RUN_FLAGS=() ;;
  *) echo "usage: $0 {rocm|cuda|cpu} {turbo|nano} [bench.py args]" >&2; exit 2 ;;
esac

IMAGE="chatterbox-bench:$GPU"
docker build -t "$IMAGE" --build-arg TORCH_INDEX="$INDEX" .

mkdir -p out "$HOME/.cache/huggingface"
EXTRA_ENV=()
[ -n "${HF_TOKEN:-}" ] && EXTRA_ENV+=(-e HF_TOKEN)
[ -n "${HSA_OVERRIDE_GFX_VERSION:-}" ] && EXTRA_ENV+=(-e HSA_OVERRIDE_GFX_VERSION)

DEVICE=$([ "$GPU" = cpu ] && echo cpu || echo cuda)   # ROCm torch also calls it "cuda"
docker run --rm "${RUN_FLAGS[@]}" "${EXTRA_ENV[@]}" \
  -v "$PWD/out:/out" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -v "$PWD/../../gateway/switchboard/text.py:/bench/switchboard_text.py:ro" \
  -v "$PWD:/refs:ro" \
  "$IMAGE" --model "$MODEL" --device "$DEVICE" --out /out "$@"
