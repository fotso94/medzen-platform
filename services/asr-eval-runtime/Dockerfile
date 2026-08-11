# The builder may contain compilers and installers. The final stage may not.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc AS eval-builder

ARG OMNI_COMMIT=145a12a668aace6c1d0d290128c1225571fc1955
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates cmake git libsndfile1-dev \
        python3.12 python3.12-dev python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build
RUN python3.12 -m venv /opt/venv \
    && pip install --no-cache-dir "pip==26.2"
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 \
        "torch==2.8.0" "torchaudio==2.8.0"
COPY services/asr-eval-runtime/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN git clone --filter=blob:none https://github.com/facebookresearch/omnilingual-asr.git omnilingual-asr \
    && git -C omnilingual-asr checkout --detach "$OMNI_COMMIT" \
    && test "$(git -C omnilingual-asr rev-parse HEAD)" = "$OMNI_COMMIT" \
    && pip install --no-cache-dir --no-deps ./omnilingual-asr \
    && python -c 'import omnilingual_asr; assert omnilingual_asr.__version__ == "0.1.0"' \
    && python -c 'import ctypes; [ctypes.CDLL(n) for n in ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")]' \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /root/.cache /opt/build/omnilingual-asr

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc AS runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_XET=1 \
    FAIRSEQ2_ASSET_DIR=/opt/medzen/assets \
    FAIRSEQ2_CACHE_DIR=/tmp/fairseq2-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/medzen

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates libsndfile1 python3.12 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 medzen \
    && useradd --system --uid 10001 --gid 10001 --no-create-home medzen \
    && mkdir -p /opt/medzen /tmp/fairseq2-cache \
    && chown -R 10001:10001 /tmp/fairseq2-cache

WORKDIR /opt/medzen
COPY --from=eval-builder /opt/venv /opt/venv
COPY services/asr-eval-runtime/medzen_asr_eval ./medzen_asr_eval
COPY services/asr-eval-runtime/assets ./assets

USER 10001:10001
ENTRYPOINT ["python", "-m", "medzen_asr_eval"]
CMD ["qualify"]
