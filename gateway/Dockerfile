# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Build & Dependencies
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY gateway/requirements.txt .

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Final Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Set internal directory to /app
WORKDIR /app

# Install runtime system dependencies (ffmpeg is required for processing audio tracks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Crucial: Python will now look inside /app for all platform packages
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Copy shared platform infrastructure folders into /app/
COPY dbs_clients/ ./dbs_clients/
COPY pipelines/ ./pipelines/
COPY workflows/ ./workflows/
COPY services/ ./services/

# Copy the core gateway service code into /app/gateway
COPY gateway/ ./gateway/

# Ensure static directory structure exists for assets/debug logs
RUN mkdir -p ./gateway/static

EXPOSE 8080

# Run Uvicorn relative to the /app directory
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]