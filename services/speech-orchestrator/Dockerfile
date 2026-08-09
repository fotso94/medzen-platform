# syntax=docker/dockerfile:1.7
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS builder

ARG SOURCE_COMMIT
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN test -n "$SOURCE_COMMIT"
COPY services/speech-orchestrator/requirements.txt /tmp/requirements.txt
COPY services/speech-orchestrator/requirements.deployed.txt /tmp/requirements.deployed.txt
RUN python -m pip install --no-cache-dir --target /opt/site-packages \
      --requirement /tmp/requirements.deployed.txt \
    && python -c 'import sys; sys.path.insert(0, "/opt/site-packages"); import boto3, botocore, fastapi, multipart, starlette, uvicorn, yaml'

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
      org.opencontainers.image.title="medzen-orchestrator" \
      org.opencontainers.image.source="https://github.com/fotso94/medzen-platform"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/site-packages:/opt/medzen/services/speech-orchestrator \
    MEDZEN_ORCHESTRATOR_MODE=deployed_http_ssm \
    AWS_REGION=eu-central-1 \
    MEDZEN_CLIENT_KEYS_SECRET_ID=medzen/client-api-keys
COPY --from=runtime-rootfs /usr/local /usr/local
COPY --from=runtime-rootfs /lib /lib
COPY --from=runtime-rootfs /usr/lib /usr/lib
COPY --from=runtime-rootfs /etc/ssl/certs /etc/ssl/certs
COPY --from=builder /opt/site-packages /opt/site-packages
COPY services/speech-orchestrator/medzen_speech_orchestrator /opt/medzen/services/speech-orchestrator/medzen_speech_orchestrator
COPY platform/lib /opt/medzen/platform/lib
COPY platform/resilience.yaml /opt/medzen/platform/resilience.yaml
COPY registry/emergency-policies /opt/medzen/registry/emergency-policies
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/python", "-m", "uvicorn", "medzen_speech_orchestrator.streaming_app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
