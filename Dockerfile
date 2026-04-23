# =============================================================================
# Dockerfile for AI News Aggregator Bot
# =============================================================================

FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files and install dependencies
COPY pyproject.toml ./
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Install Playwright Chromium browser and its OS-level dependencies
RUN playwright install chromium --with-deps

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
