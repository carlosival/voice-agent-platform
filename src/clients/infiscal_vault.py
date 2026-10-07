import os
import asyncio
import logging

from infisical_sdk import InfisicalSDKClient

logger = logging.getLogger(__name__)

# Environment-driven config (recommended for Docker)
INFISICAL_HOST = os.getenv("VAULT_HOST", "https://app.infisical.com")
INFISICAL_CLIENT_ID = os.getenv("VAULT_MACHINE_IDENTITY_CLIENT_ID")
INFISICAL_CLIENT_SECRET = os.getenv("VAULT_MACHINE_IDENTITY_CLIENT_SECRET")
INFISICAL_PROJECT_ID = os.getenv("VAULT_PROJECT_ID")
INFISICAL_ENVIRONMENT = os.getenv("ENV", "dev").lower()



def _create_infisical_client() -> InfisicalSDKClient:
    """Build and authenticate the client. Fails fast if credentials are missing."""
    if not INFISICAL_CLIENT_ID or not INFISICAL_CLIENT_SECRET:
        raise RuntimeError(
            "INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET must be set."
        )

    client = InfisicalSDKClient(host=INFISICAL_HOST)
    client.auth.universal_auth.login(
        client_id=INFISICAL_CLIENT_ID,
        client_secret=INFISICAL_CLIENT_SECRET,
    )
    return client


# Create a single Infisical client instance at import time
infisical_client: InfisicalSDKClient = _create_infisical_client()


def get_infisical_client() -> InfisicalSDKClient:
    """Drop-in replacement for the old factory; always returns the shared instance."""
    return infisical_client


async def check_infisical_connection() -> bool:
    """
    Asynchronous wrapper for testing the Infisical connection.
    Uses a thread to avoid blocking the event loop, since the SDK is synchronous.
    """
    try:
        await asyncio.to_thread(
            infisical_client.secrets.list_secrets,
            project_id=INFISICAL_PROJECT_ID,
            environment_slug=INFISICAL_ENVIRONMENT,
            secret_path="/",
        )
        logger.info("Connected to Infisical successfully.")
        return True
    except Exception:
        logger.exception("Failed to connect to Infisical")
        raise  # Re-raise so the app fails fast instead of silently continuing


# Example use
# docker exec -it worker-1 python3 src/clients/infiscal_vault.py
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(check_infisical_connection())