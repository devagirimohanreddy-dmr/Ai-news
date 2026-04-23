# =============================================================================
# Multi-stage Dockerfile for AI News Aggregator Bot
# Base: Python 3.12 slim
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — install build tools and Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Stage 2: Runtime — lean image with only what we need
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# System dependencies required at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the project source
COPY . .

# Install the project itself (editable-style, deps already present)
RUN pip install --no-cache-dir .

# Install Playwright Chromium browser and its OS-level dependencies
RUN playwright install chromium --with-deps

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
