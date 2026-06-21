# ─────────────────────────────────────────────────────────────────────────────
# FastAPI service — Voice pipeline + Twilio webhooks
# Calls vLLM and Speaches over the internal Docker network.
# No GPU needed here — all inference is delegated.
# ─────────────────────────────────────────────────────────────────────────────

# --- Build Stage ---
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system compilation dependencies if any pip wheels need building
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install dependencies into a localized wheels/cache directory or global site-packages
COPY fastapi/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt


# --- Final Runtime Stage ---
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime system dependencies (ffmpeg is heavy; keeping layers clean)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed python packages from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy internal shared infrastructure modules
COPY dbs_clients/ ./dbs_clients/
COPY pipelines/ ./pipelines/
COPY workflows/ ./workflows/

# Copy the FastAPI core code directly to /app/fastapi
COPY fastapi/ .

# Ensure Python looks at /app root to resolve absolute imports (e.g., import dbs_clients.redis_db)
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

EXPOSE 8081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1"]
