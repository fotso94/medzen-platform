# Verification-only init container. The model is fetched from the non-approved
# B6A S3 path; no inference or CUDA library is present in this image.
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_XET=1 \
    AWS_REGION=eu-central-1 \
    MODEL_DESTINATION=/models

RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 medzen \
    && adduser -S -D -H -u 10001 -G medzen medzen

WORKDIR /opt/medzen
RUN pip install --no-cache-dir "pip==26.2"
COPY services/model-loader/requirements.txt ./requirements.txt
# The loader never imports sqlite3. Remove the extension and package so an
# unrelated database parser cannot re-enter the serving supply chain.
RUN pip install -r requirements.txt \
    && python -c 'import boto3, botocore; boto3.client("s3", region_name="eu-central-1")' \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -f /usr/local/lib/python3.12/lib-dynload/_sqlite3*.so \
    && apk del .python-rundeps sqlite-libs \
    && rm -rf /root/.cache
COPY services/model-loader/medzen_model_loader ./medzen_model_loader
# round 11 (Codex): the promotion signing PUBLIC key is baked into the
# image from reviewed source — the trust boundary a bundle author cannot
# rewrite (signing happens only at admission with the KMS private key)
COPY platform/decisions/PROMOTION-SIGNING-PUBLIC-KEY.pem ./PROMOTION-SIGNING-PUBLIC-KEY.pem
# round 14 (Codex finding 2): the sealed-evaluator public key — admission
# verifies the evaluator's signature on sealed-output inference receipts
COPY platform/decisions/SEALED-EVALUATOR-SIGNING-PUBLIC-KEY.pem ./SEALED-EVALUATOR-SIGNING-PUBLIC-KEY.pem

USER 10001:10001
ENTRYPOINT ["python", "-m", "medzen_model_loader"]
