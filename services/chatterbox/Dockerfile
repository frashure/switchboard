# Chatterbox-Turbo TTS service. Torch is installed first from the wheel index
# for the GPU, then chatterbox WITHOUT its dependency resolution: its
# pyproject pins torch==2.6.0 (CUDA wheels), which would silently replace a
# ROCm build. Same dependency list as scripts/chatterbox_bench/Dockerfile.
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
        pyloudnorm omegaconf huggingface_hub resemble-perth \
        "fastapi>=0.115" "uvicorn>=0.32"

RUN pip install --no-cache-dir --no-deps \
        "git+https://github.com/resemble-ai/chatterbox.git@${CHATTERBOX_REF}"

ENV TQDM_DISABLE=1 PYTHONUNBUFFERED=1 MIOPEN_LOG_LEVEL=2
WORKDIR /app
COPY server.py /app/server.py
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
