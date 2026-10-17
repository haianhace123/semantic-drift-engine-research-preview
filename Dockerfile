FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/index.html ./
COPY frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/vite.config.ts ./
COPY frontend/src ./src

RUN npm run build


FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./

RUN python -m pip install -r requirements.lock

RUN playwright install --with-deps chromium

COPY . .

COPY --from=frontend-builder /frontend/dist /app/src/static/app

RUN mkdir -p /app/logs /app/backups /app/data \
    && groupadd --gid 10001 sde \
    && useradd \
        --uid 10001 \
        --gid sde \
        --home-dir /app \
        --no-create-home \
        --shell /usr/sbin/nologin \
        sde \
    && chown -R 10001:10001 /app/logs /app/backups /app/data

USER sde

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]