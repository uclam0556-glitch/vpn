FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system hamali && adduser --system --ingroup hamali hamali

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/data && chown -R hamali:hamali /app
USER hamali

EXPOSE 8080
CMD ["uvicorn", "hamalivpn.app:app", "--host", "0.0.0.0", "--port", "8080"]
