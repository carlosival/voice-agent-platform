import os
from infisical_sdk import InfisicalSDKClient

ENV = os.getenv("ENV","DEV").lower()
host = os.getenv("VAULT_HOST")
project_id = os.getenv("VAULT_PROJECT_ID")
machine_identity_client_id = os.getenv("VAULT_MACHINE_IDENTITY_CLIENT_ID")
machine_identity_client_secret = os.getenv("VAULT_MACHINE_IDENTITY_CLIENT_SECRET")

client = InfisicalSDKClient(host)

client.auth.universal_auth.login(
  machine_identity_client_id,
  machine_identity_client_secret
)



def get_secret(path:str, key_name:str) -> str:
    
    name = client.secrets.get_secret_by_name(
      secret_name=key_name,
      project_id=project_id,
      environment_slug=ENV,
      secret_path=path
    )

    return name.secretValue

# ─── Test ─────────────────────────────────────────────────────────────────────
# Uses tis command to test:
# docker exec -it -e ENV -e VAULT_HOST -e VAULT_PROJECT_ID -e VAULT_MACHINE_IDENTITY_CLIENT_ID -e VAULT_MACHINE_IDENTITY_CLIENT_SECRET worker-1 python3 services/infiscal.py


if __name__ == "__main__":
    print("=" * 50)
    print("Infisical SDK - Connection Test")
    print("=" * 50)

    # 1. Check required environment variables
    required_vars = {
        "VAULT_HOST": host,
        "VAULT_PROJECT_ID": project_id,
        "VAULT_MACHINE_IDENTITY_CLIENT_ID": machine_identity_client_id,
        "VAULT_MACHINE_IDENTITY_CLIENT_SECRET": machine_identity_client_secret,
    }

    missing = [k for k, v in required_vars.items() if not v]
    if missing:
        print("Missing keys")

    print(f"[OK] ENV: {ENV}")
    print(f"[OK] VAULT_HOST: {host}")
    print(f"[OK] VAULT_PROJECT_ID: {project_id}")
    print(f"[OK] CLIENT_ID: {machine_identity_client_id[:8]}...")
    print("-" * 50)

    test_path = "/"
    test_key = "LLM_MODEL"
    try:
        value = get_secret(test_path, test_key)
        print("[OK] Secret retrieved successfully.")
        # Value is not printed in full to avoid leaking it into Docker logs
        print(f"     Value length: {len(value)} characters")
        masked = value[:2] + "*" * max(len(value) - 4, 0) + value[-2:] if len(value) > 4 else "****"
        print(f"     Masked value: {masked}")
    except Exception as e:
        print(f"[ERROR] Could not retrieve secret: {type(e).__name__}: {e}")
        

    print("=" * 50)
    print("Test completed successfully.")
    print("=" * 50)