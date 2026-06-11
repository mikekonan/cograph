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
    && python -m pip install 'uv==0.11.13' \
    && cd /app/backend \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt > /dev/null \
    && python -m pip install --require-hashes -r /tmp/requirements.txt

COPY backend /app/backend

RUN python -m pip install --no-deps ./backend \
    && python -m pip uninstall -y uv

# tree-sitter-language-pack v1.6+ bundles NO grammars — get_parser()
# downloads them from GitHub releases on first use, with no timeout. A
# stalled CDN connection froze a prod sync mid-transaction (2026-06-11).
# Bake the grammars cograph parses into the image cache (/root/.cache)
# so runtime never touches the network; `timeout` turns a stalled
# download into a loud build failure. The set of grammars derives from
# backend/app/graph/languages.py — no drift.
RUN timeout 300 python -c "from backend.app.graph.parser import download_missing_grammars, missing_grammars; download_missing_grammars(); left = missing_grammars(); assert not left, f'grammars still missing: {left}'"

EXPOSE 8000
