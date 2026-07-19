FROM node:22-slim AS node-builder

WORKDIR /portal
COPY portal-webapp/package.json portal-webapp/package-lock.json ./
RUN npm ci
COPY portal-webapp/ ./
RUN npm run build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system hamali && adduser --system --ingroup hamali hamali

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=node-builder /app/node_modules ./node_modules

COPY src ./src
COPY migrations ./migrations
COPY --from=node-builder /portal/dist ./portal-webapp/dist
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/data && chown -R hamali:hamali /app
USER hamali

ENV PORTAL_DIST_DIR=/app/portal-webapp/dist

EXPOSE 8080 8001
CMD ["uvicorn", "hamalivpn.app:app", "--host", "0.0.0.0", "--port", "8080"]
