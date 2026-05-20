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
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# static dir might not exist yet — create it, don't COPY it
RUN mkdir -p ./static

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
