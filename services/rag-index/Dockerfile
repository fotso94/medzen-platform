# syntax=docker/dockerfile:1.7
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS builder

ARG SOURCE_COMMIT
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN test -n "$SOURCE_COMMIT"
COPY services/rag-index/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --target /opt/site-packages \
      --requirement /tmp/requirements.txt \
    && python -c 'import sys; sys.path.insert(0, "/opt/site-packages"); import fastapi, starlette, uvicorn'

FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS runtime-rootfs
RUN rm -rf \
      /lib/apk \
      /sbin/apk \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.12/ensurepip \
      /usr/local/lib/python3.12/site-packages \
      /var/cache/apk

FROM scratch AS runtime
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT \
      org.opencontainers.image.title="medzen-rag-index" \
      org.opencontainers.image.source="https://github.com/fotso94/medzen-platform"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/site-packages:/opt/medzen/services/rag-index \
    RAG_INDEX_ROOT=/opt/medzen/platform/testdata/rag-index \
    RAG_INDEX_ALIAS=current
COPY --from=runtime-rootfs /usr/local /usr/local
COPY --from=runtime-rootfs /lib /lib
COPY --from=runtime-rootfs /usr/lib /usr/lib
COPY --from=runtime-rootfs /etc/ssl/certs /etc/ssl/certs
COPY --from=builder /opt/site-packages /opt/site-packages
COPY services/rag-index/medzen_rag_index /opt/medzen/services/rag-index/medzen_rag_index
COPY platform/testdata/rag-index /opt/medzen/platform/testdata/rag-index
COPY platform/testdata/rag-index-dev /opt/medzen/platform/testdata/rag-index-dev
USER 10001:10001
EXPOSE 8083
ENTRYPOINT ["/usr/local/bin/python", "-m", "uvicorn", "medzen_rag_index.app:app", "--host", "0.0.0.0", "--port", "8083", "--no-access-log"]
