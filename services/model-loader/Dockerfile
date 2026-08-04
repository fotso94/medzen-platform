# Runtime code only. The model is fetched from the non-approved B6A S3 path.
FROM python:3.12-slim-trixie@sha256:cab2dbf575e971934a81e4622f5aba17aa7929719bd7e31033a3a83b97fd0464

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_XET=1 \
    AWS_REGION=eu-central-1 \
    MODEL_DESTINATION=/models \
    LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home medzen

WORKDIR /opt/medzen
RUN pip install --no-cache-dir "pip==26.2"
COPY services/model-loader/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && python -c 'import ctypes; [ctypes.CDLL(n) for n in ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")]' \
    && python -m pip uninstall -y pip setuptools wheel \
    && apt-get purge -y --allow-remove-essential perl-base \
    && rm -rf /var/lib/apt/lists/* /root/.cache
COPY services/model-loader/medzen_model_loader ./medzen_model_loader

USER 10001:10001
ENTRYPOINT ["python", "-m", "medzen_model_loader"]
