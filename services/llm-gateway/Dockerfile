# syntax=docker/dockerfile:1.7
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS builder

ARG SOURCE_COMMIT
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN test -n "$SOURCE_COMMIT"
COPY services/llm-gateway/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --target /opt/site-packages \
      --requirement /tmp/requirements.txt \
    && python -c 'import sys; sys.path.insert(0, "/opt/site-packages"); import fastapi, starlette, uvicorn, yaml'

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
      org.opencontainers.image.title="medzen-llm-gateway" \
      org.opencontainers.image.source="https://github.com/fotso94/medzen-platform"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/site-packages:/opt/medzen/services/llm-gateway \
    MEDZEN_LLM_PROVIDER=fake
COPY --from=runtime-rootfs /usr/local /usr/local
COPY --from=runtime-rootfs /lib /lib
COPY --from=runtime-rootfs /usr/lib /usr/lib
COPY --from=runtime-rootfs /etc/ssl/certs /etc/ssl/certs
COPY --from=builder /opt/site-packages /opt/site-packages
COPY services/llm-gateway/medzen_llm_gateway /opt/medzen/services/llm-gateway/medzen_llm_gateway
COPY platform/lib /opt/medzen/platform/lib
COPY platform/resilience.yaml /opt/medzen/platform/resilience.yaml
COPY registry/languages /opt/medzen/registry/languages
COPY registry/llm-policies /opt/medzen/registry/llm-policies
USER 10001:10001
EXPOSE 8082
ENTRYPOINT ["/usr/local/bin/python", "-m", "uvicorn", "medzen_llm_gateway.app:app", "--host", "0.0.0.0", "--port", "8082", "--no-access-log"]
