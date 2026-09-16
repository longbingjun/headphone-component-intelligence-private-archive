FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm AS app

ARG APP_COMMIT_SHA=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PUBLIC_BASE_PATH=/ \
    PUBLIC_IMAGE_BASE=/media \
    SITE_DIR=/app/site \
    IMAGE_CACHE_DIR=/app/runtime/image-cache \
    APP_COMMIT_SHA=${APP_COMMIT_SHA}

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /app

COPY requirements-runtime.txt ./
RUN pip install -r requirements-runtime.txt

COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci

COPY . .
RUN python scripts/verify_text_encoding.py \
    && python scripts/prepare_web_data.py \
    && cd web \
    && npm run build:minio \
    && cd .. \
    && python scripts/verify_static_site.py

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
