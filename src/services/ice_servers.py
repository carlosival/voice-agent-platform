import os
import logging
import httpx
from async_lru import alru_cache

logger = logging.getLogger(__name__)


CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    # We log a warning instead of raising RuntimeError at module level to allow app to start
    # but the endpoint will fail gracefully.
    logger.warning("Missing Cloudflare Environment Variables! ICE server generation will fail.")


async def fetch_cloudflare_ice_servers():
    cloudflare_url = f"https://rtc.live.cloudflare.com/v1/turn/keys/{CLOUDFLARE_ACCOUNT_ID}/credentials/generate-ice-servers"
    
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Request credentials that expire in 10 minutes (600 seconds)
    payload = {
        "ttl": 600 
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(cloudflare_url, headers=headers, json=payload)
            response.raise_for_status()  # raises httpx.HTTPStatusError on 4xx/5xx
            return response.json()

        except httpx.HTTPStatusError as exc:
            logger.error(f"Cloudflare API error {exc.response.status_code}: {exc.response.text}")
            raise

        except httpx.RequestError as exc:
            logger.error(f"HTTP request to Cloudflare failed: {exc}")
            raise


# ── CLI test ──────────────────────────────────────────────────────────────────
# Uses this command to test:
# docker exec -it -e CLOUDFLARE_ACCOUNT_ID="<your-account-id>" -e CLOUDFLARE_API_TOKEN="<your-api-token>" fastapi python3 services/ice_servers.py

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    )

    async def main():
        logger.info(f"Account ID: {CLOUDFLARE_ACCOUNT_ID or 'NOT SET'}")
        logger.info(f"API Token:  {'SET' if CLOUDFLARE_API_TOKEN else 'NOT SET'}")

        try:
            result = await fetch_cloudflare_ice_servers()
            print(json.dumps(result, indent=2))

            for server in result.get("iceServers", []):
                urls = server.get("urls", [])
                has_creds = "username" in server
                logger.info(f"urls={urls} credentials={'yes' if has_creds else 'no'}")

        except Exception as e:
            logger.error(f"Failed: {type(e).__name__} — {e}")

    asyncio.run(main())