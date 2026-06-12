# ─────────────────────────────────────────────────────────────────────────────
# FastAPI service — Voice pipeline + Twilio webhooks
# Calls vLLM and Speaches over the internal Docker network.
# No GPU needed here — all inference is delegated.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY fastapi/requirements.txt .

# Upgrading pip first handles large wheel extractions much more efficiently
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the shared folder and the API gateway application
COPY dbs_clients/ ./dbs_clients/
COPY fastapi/ ./fastapi/ 

# static dir might not exist yet — create it, don't COPY it
RUN mkdir -p ./static

# Updated to match the new workdir path
ENV PYTHONPATH="/app"

EXPOSE 8081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
