import logging
import os
from typing import Dict, List, Optional

from src.clients.infiscal_vault import infisical_client, InfisicalSDKClient, INFISICAL_PROJECT_ID, INFISICAL_ENVIRONMENT

logger = logging.getLogger(__name__)

# Optional mapping: secret key name -> environment variable name to use as fallback.
# If a key is not listed here, the fallback env var has the same name as the secret key.
# Example: {"LLM_API_KEY": "OPENAI_API_KEY"}
KEY_SECRETS_DEFAULT_FALLBACK = {}

# Secrets whose VALUE starts with one of these prefixes are placeholders (e.g. "seed-xxxx")
# and are NOT treated as valid secrets: lookups fall back to os.getenv instead.
# Matching is case-insensitive.
DUMMY_VALUE_PREFIXES = ("seed-",)
 
# The placeholder check only applies in these Infisical environments (compared with
# INFISICAL_ENVIRONMENT, case-insensitive). Everywhere else (staging, prod, ...) a value
# starting with "seed-" is returned as-is, like any other secret.
DUMMY_CHECK_ENVIRONMENTS = ("dev", "development")
 
 
def _is_dummy(value: Optional[str]) -> bool:
    """True if the value is a placeholder rather than a real secret (dev environment only)."""
    if str(INFISICAL_ENVIRONMENT).lower() not in DUMMY_CHECK_ENVIRONMENTS:
        return False
    return isinstance(value, str) and value.lower().startswith(DUMMY_VALUE_PREFIXES)

class SecretNotFoundError(Exception):
    """Raised when secrets are missing in Infisical and have no fallback env var either."""

    def __init__(self, missing_keys: List[str], path: str):
        self.missing_keys = missing_keys
        self.path = path
        super().__init__(
            f"Secret(s) not found in Infisical at '{path}' and no fallback env var set: "
            f"{', '.join(missing_keys)}"
        )


def get_secret(path: str, key_name: str, fallback_env: Optional[str] = None) -> str:
    """
    Fetch a secret from Infisical.

    If Infisical fails (secret missing, network error, auth error, ...), fall back to
    os.getenv. The env var name is resolved in this order:
      1. `fallback_env` argument
      2. KEY_SECRETS_DEFAULT_FALLBACK[key_name]
      3. key_name itself

    Raises the original Infisical exception if the fallback env var is not set either.
    """
    try:
        secret = infisical_client.secrets.get_secret_by_name(
            secret_name=key_name,
            project_id=INFISICAL_PROJECT_ID,
            environment_slug=INFISICAL_ENVIRONMENT,
            secret_path=path,
        )
        return secret.secretValue

    except Exception as e:
        env_name = fallback_env or KEY_SECRETS_DEFAULT_FALLBACK.get(key_name, key_name)
        env_value = os.getenv(env_name)

        if env_value is not None:
            logger.warning(
                "Infisical lookup failed for '%s' at '%s' (%s: %s). Using env var '%s' as fallback.",
                key_name, path, type(e).__name__, e, env_name,
            )
            return env_value

        logger.error(
            "Infisical lookup failed for '%s' at '%s' and env var '%s' is not set.",
            key_name, path, env_name,
        )
        raise


def get_secrets(
    path: str,
    key_names: List[str],
    fallbacks: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Fetch several secrets at once. Returns the values in the same order as `key_names`.

    Uses a SINGLE list_secrets call (one request for the whole path) and picks the requested
    keys from the result. Any key that is not present there, or all of them if the list call
    itself fails, falls back to os.getenv. The env var name is resolved in this order:
      1. `fallbacks[key]` (dict: key_name -> env var name)
      2. KEY_SECRETS_DEFAULT_FALLBACK[key]
      3. the key name itself

    If any key is found in neither place, raises SecretNotFoundError listing ALL the
    missing keys, so you can fix them in one go.
    """
    fallbacks = fallbacks or {}

    # One request for the whole path; if it fails, every key goes through the env fallback
    try:
        found = {s["key"]: s["value"] for s in list_secrets(path, include_values=True) if s["value"]}
    except Exception as e:
        logger.warning(
            "Infisical list failed at '%s' (%s: %s). Trying env var fallbacks for all keys.",
            path, type(e).__name__, e,
        )
        found = {}

    values: List[str] = []
    missing: List[str] = []

    for key in key_names:
        if key in found:
            values.append(found[key])
            continue

        env_name = fallbacks.get(key) or KEY_SECRETS_DEFAULT_FALLBACK.get(key, key)
        env_value = os.getenv(env_name)

        if env_value is not None:
            logger.warning("Secret '%s' not in Infisical at '%s'. Using env var '%s'.", key, path, env_name)
            values.append(env_value)
        else:
            missing.append(key)

    if missing:
        logger.error("Missing secrets at '%s' with no fallback: %s", path, missing)
        raise SecretNotFoundError(missing, path)

    return values


def set_secret(path: str, key_name: str, value: str, comment: Optional[str] = None) -> str:
    """
    Create or update a secret in Infisical. Returns the secret value that was stored.

    Tries to update first; if the secret does not exist yet, creates it.
    """
    try:
        infisical_client.secrets.update_secret_by_name(
            current_secret_name=key_name,
            project_id=INFISICAL_PROJECT_ID,
            environment_slug=INFISICAL_ENVIRONMENT,
            secret_path=path,
            secret_value=value,
        )
        logger.info("Secret '%s' updated at '%s'.", key_name, path)

    except Exception as update_error:
        logger.info(
            "Update failed for '%s' (%s); trying to create it.",
            key_name, type(update_error).__name__,
        )
        infisical_client.secrets.create_secret_by_name(
            secret_name=key_name,
            project_id=INFISICAL_PROJECT_ID,
            environment_slug=INFISICAL_ENVIRONMENT,
            secret_path=path,
            secret_value=value,
            secret_comment=comment,
        )
        logger.info("Secret '%s' created at '%s'.", key_name, path)

    return value


def list_secrets(path: str, include_values: bool = False) -> List:
    """
    List the secrets stored at `path`.

    By default returns only the secret names (list[str]), so values never leak by accident.
    With include_values=True returns a list of {"key": ..., "value": ...} dicts.
    """
    response = infisical_client.secrets.list_secrets(
        project_id=INFISICAL_PROJECT_ID,
        environment_slug=INFISICAL_ENVIRONMENT,
        secret_path=path,
        view_secret_value=include_values,
    )

    secrets = [
        s for s in response.secrets
        if not (exclude_dummy and _is_dummy(s.secretValue))
    ]
 
    if include_values:
        return [{"key": s.secretKey, "value": s.secretValue} for s in secrets]
    return [s.secretKey for s in secrets]


# ─── Test ─────────────────────────────────────────────────────────────────────
# Uses tis command to test:
# docker exec -it worker-1 python3 src/services/vault/infiscal.py


if __name__ == "__main__":

    test_path = "/09b890dc-d224-4362-acfe-5ae9431b6b11"
    test_key = "LLM_API_KEY"

    # 1. get_secret (with env fallback)
    try:
        value = get_secret(test_path, test_key)
        print("[OK] Secret retrieved successfully.")
        # Value is not printed in full to avoid leaking it into Docker logs
        print(f"     Value length: {len(value)} characters")
        masked = value[:2] + "*" * max(len(value) - 4, 0) + value[-2:] if len(value) > 4 else "****"
        print(f"     Masked value: {masked}")
    except Exception as e:
        print(f"[ERROR] Could not retrieve secret: {type(e).__name__}: {e}")

    # 1b. get_secrets (several keys, one of them intentionally missing)
    try:
        values = get_secrets(test_path, [test_key, "THIS_KEY_DOES_NOT_EXIST"])
        print(f"[OK] Retrieved {len(values)} secret(s).")
    except SecretNotFoundError as e:
        print(f"[OK] Expected error raised for missing keys: {e.missing_keys}")
    except Exception as e:
        print(f"[ERROR] Unexpected failure in get_secrets: {type(e).__name__}: {e}")

    # 2. list_secrets (names only)
    try:
        names = list_secrets(test_path)
        print(f"[OK] Found {len(names)} secret(s): {names}")
    except Exception as e:
        print(f"[ERROR] Could not list secrets: {type(e).__name__}: {e}")

    # 3. set_secret (uses a throwaway key so real secrets are never touched)
    try:
        set_secret(test_path, "TEST_TEMP_SECRET", "temp-value-123", comment="infiscal.py self-test")
        print("[OK] set_secret succeeded for TEST_TEMP_SECRET.")
    except Exception as e:
        print(f"[ERROR] Could not set secret: {type(e).__name__}: {e}")

    print("=" * 50)
    print("Test completed.")
    print("=" * 50)