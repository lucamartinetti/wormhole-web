FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Copy source
COPY src/ src/

# Install project
RUN uv sync --no-dev

EXPOSE 8080 4002

ENTRYPOINT ["uv", "run", "wormhole-web"]
CMD ["--port", "8080", "--transit-port", "4002"]
