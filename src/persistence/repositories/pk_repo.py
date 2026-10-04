import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional
from src.persistence.models import UserPublicKey, UserAgentAssociation

logger = logging.getLogger(__name__)

class PKRepository:
    def __init__(self, db: AsyncSession):
        self.db = db


    async def check_pk(self, pk: str, client_origin: str) -> Optional[UserPublicKey]:
        try:
            logger.info(f"Checking PK: {pk} for client origin: {client_origin}")
            async with self.db.begin():
                # 1. Look up the key and confirm domain authorization
                stmt = (select(UserPublicKey,UserAgentAssociation)
                    .join(UserAgentAssociation, UserAgentAssociation.public_key_id == UserPublicKey.id)
                    .where(
                        UserPublicKey.public_key_body == pk,
                        UserPublicKey.is_active.is_(True),
                        UserAgentAssociation.allowed_domains.contains([client_origin]),
                    )
                )
                result = await self.db.execute(stmt)          # ← await here
                public_key_record = result.scalars().one_or_none()  # ← then unwrap

                return public_key_record
        except Exception as e:
            logger.error(f"Error checking PK: {e}")
            return None


# ── CLI TEST ─────────────────────────────────────────────────────────
# docker exec -it -e PUBLIC_KEY="ed25519_pk_2befacdaecfcce60875c3eff417f8338" -e CLIENT_ORIGIN="localhost" gateway python3 -m src.persistence.repositories.pk_repo

if __name__ == "__main__":
    import asyncio
    import os
    from src.clients.postgres_db import get_async_session  # adjust import to your project

    logging.basicConfig(level=logging.DEBUG)

    pk = os.environ["PUBLIC_KEY"]
    client_origin = os.environ["CLIENT_ORIGIN"]

    async def main():
        async with get_async_session() as db:
            repo = PKRepository(db)
            result = await repo.check_pk(
                pk=pk,
                client_origin=client_origin
            )
            if result:
                print("✅ Found:", result)
            else:
                print("❌ Not found or inactive")

    asyncio.run(main())
        