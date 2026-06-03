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
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Final Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Set internal directory to /app
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Crucial: Python will now look inside /app for your packages
ENV PYTHONPATH="/app"



# Copy the folders into /app/dbs_clients and /app/gateway
COPY dbs_clients/ ./dbs_clients/
COPY gateway/ ./gateway/

EXPOSE 8000

# Run Uvicorn relative to the /app directory
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]