# syntax=docker/dockerfile:1

# ---- frontend build ----
FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API + worker image ----
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RELEASEWATCH_STATIC_DIR=/app/static
WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/releasewatch ./releasewatch
RUN pip install .

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY --from=frontend /frontend/dist ./static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
