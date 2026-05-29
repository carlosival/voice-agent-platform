# Production Deployment Guide

This guide ensures a smooth transition of the Voice Agent platform to a production environment.

## 1. Prerequisites
- A virtual machine (VPS) with Docker and Docker Compose installed.
- A public domain name (e.g., `agent.yourdomain.com`).
- Ports 80 and 443 open on your firewall.
- A Google Cloud Service Account JSON key (for TTS).
- Groq API Key (for STT and LLM).

## 2. Setup Environment
1. Copy the production environment template:
   ```bash
   cp .env.production.example .env.prod
   ```
2. Edit `.env.prod` and fill in your secrets and domain details.

## 3. Deployment Steps
To start the production stack:
```bash
docker-compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

## 4. Verification
- **Logs**: Check for errors in the FastAPI container:
  ```bash
  docker logs -f fastapi-prod
  ```
- **HTTPS**: Visit `https://yourdomain.com/health/ping` in your browser.
- **Twilio**: Update your Twilio webhook URL to `https://yourdomain.com/twilio/voice`.

## 5. Security Notes
- **Non-Root**: The production Dockerfile runs as `appuser`.
- **Caddy**: Automatically manages SSL certificates via Let's Encrypt.
- **Passwords**: Ensure `REDIS_PASSWORD` and `POSTGRES_PASSWORD` are strong.
