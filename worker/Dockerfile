# ─────────────────────────────────────────────────────────────────────────────
# Audio worker
# Run as a background service
# Listens to Redis queue for new and abandoned offers, delivers answers via Redis Streams
# Run audio processing pipeline WEBRTC
# No GPU needed here — all inference is delegated.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY worker/requirements.txt .

# Upgrading pip first handles large wheel extractions much more efficiently
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the shared folder and the API gateway application
COPY src/clients/ ./src/clients/
COPY src/services/ ./src/services/
COPY src/peer/ ./src/peer/
COPY src/pipelines/ ./src/pipelines/ 
COPY workflows/ ./workflows/ 

# Worker Files
COPY worker/worker.py .
COPY worker/deps_provider.py .


# Updated to match the new workdir path
ENV PYTHONPATH="/app"

CMD ["python", "worker.py"]