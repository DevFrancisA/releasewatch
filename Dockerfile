# syntax=docker/dockerfile:1

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/releasewatch ./releasewatch
RUN pip install .

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
