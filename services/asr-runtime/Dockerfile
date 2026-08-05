# B6A internal ASR runtime. Dependency bootstrap is confined to this builder;
# python3.12-venv and its Ubuntu pip wheel package never enter the final image.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc AS asr-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/medzen
RUN python3.12 -m venv /opt/venv \
    && pip install --no-cache-dir "pip==26.2"
COPY services/asr-runtime/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && python -c 'import ctypes; [ctypes.CDLL(n) for n in ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")]' \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /root/.cache

# The zero-shot model is mounted at /models by the model-loader init container;
# no model weights, build tools, venv bootstrap package or installer are baked
# into this final serving stage.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_XET=1 \
    MODEL_DIR=/models \
    MEDZEN_INFERENCE_DEVICE=cuda \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates libsndfile1 python3.12 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home medzen

WORKDIR /opt/medzen
COPY --from=asr-builder /opt/venv /opt/venv
COPY services/asr-runtime/medzen_asr_runtime ./medzen_asr_runtime

USER 10001:10001
EXPOSE 8081
CMD ["uvicorn", "medzen_asr_runtime.app:app", "--host", "0.0.0.0", "--port", "8081", "--no-access-log"]
