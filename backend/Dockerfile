FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/uv.lock /app/backend/

RUN python -m pip install --upgrade pip \
    && python -m pip install uv \
    && cd /app/backend \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt > /dev/null \
    && python -m pip install --require-hashes -r /tmp/requirements.txt

COPY backend /app/backend

RUN python -m pip install --no-deps ./backend \
    && python -m pip uninstall -y uv

EXPOSE 8000
