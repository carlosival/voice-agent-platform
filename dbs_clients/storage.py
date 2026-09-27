import os
import asyncio
import logging

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# Environment-driven config (recommended for Docker)
# For Cloudflare R2, endpoint looks like: https://<account_id>.r2.cloudflarestorage.com
# For MinIO, e.g.: http://minio:9000
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_REGION = os.getenv("S3_REGION", "auto")  # "auto" is what R2 expects

# Create a single boto3 client instance at import time
s3_client = boto3.client(
    service_name="s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
)


async def test_s3_connection_async() -> bool:
    """
    Asynchronous wrapper for testing the S3/R2 connection.
    Uses a thread to avoid blocking the event loop, since boto3 is synchronous.
    """
    try:
        await asyncio.to_thread(s3_client.list_buckets)
        return True
    except (ClientError, EndpointConnectionError) as e:
        logging.error(f"S3 async connection failed: {e}")
        raise


# Example use
if __name__ == "__main__":
    asyncio.run(test_s3_connection_async())