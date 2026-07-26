FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache

RUN pip install --no-cache-dir uv==0.11.3

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY evaluation_sets ./evaluation_sets
COPY scripts ./scripts

RUN addgroup --system alert-sage \
    && adduser --system --ingroup alert-sage alert-sage \
    && chown -R alert-sage:alert-sage /app

USER alert-sage

EXPOSE 8000 9101

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
