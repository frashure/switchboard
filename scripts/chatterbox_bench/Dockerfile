# Throwaway benchmark image for Chatterbox-Turbo/Nano (see run.sh). Torch is
# installed first from the wheel index matching the GPU, then chatterbox
# WITHOUT its own dependency resolution: its pyproject pins torch==2.6.0
# (CUDA wheels), which would silently replace a ROCm build. The remaining
# dependencies are listed explicitly below, minus torch/torchaudio/gradio.
FROM python:3.11-slim

# rocm: https://download.pytorch.org/whl/rocm7.2   cuda: .../cu126   cpu: .../cpu
ARG TORCH_INDEX=https://download.pytorch.org/whl/rocm7.2
ARG CHATTERBOX_REF=master

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch torchaudio --index-url ${TORCH_INDEX}

RUN pip install --no-cache-dir \
        "numpy<2" librosa==0.11.0 s3tokenizer "transformers==5.2.0" diffusers==0.29.0 \
        conformer==0.3.2 safetensors==0.5.3 spacy-pkuseg pykakasi==2.3.0 \
        pyloudnorm omegaconf huggingface_hub resemble-perth

RUN pip install --no-cache-dir --no-deps \
        "git+https://github.com/resemble-ai/chatterbox.git@${CHATTERBOX_REF}"

ENV TQDM_DISABLE=1 PYTHONUNBUFFERED=1
WORKDIR /bench
COPY bench.py /bench/bench.py
ENTRYPOINT ["python", "/bench/bench.py"]
