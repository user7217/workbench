FROM node:22-slim AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY schema/ /build/schema/
COPY web/ ./
RUN npm run generate-types && npm run build

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY schema/ ./schema/
COPY agent/ ./agent/
COPY --from=web-builder /build/web/dist ./web/dist

RUN uv pip install --system .

ENV WORKBENCH_STATE_DIR=/data
ENV WORKBENCH_BRANCH=main
ENV WORKBENCH_POLL_SECONDS=600
ENV WORKBENCH_BIND=0.0.0.0
ENV WORKBENCH_PORT=8420

VOLUME ["/data"]
EXPOSE 8420

CMD ["sh", "-c", "uvicorn workbench.server.app:app --host ${WORKBENCH_BIND:-0.0.0.0} --port ${WORKBENCH_PORT:-8420}"]
