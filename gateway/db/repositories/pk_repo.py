import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional
from gateway.db.models import UserPublicKey

logger = logging.getLogger(__name__)

class PKRepository:
    def __init__(self, db: AsyncSession):
        self.db = db


    async def check_pk(self, pk: str, client_origin: str) -> Optional[UserPublicKey]:
        try:
            async with self.db.begin():
                # 1. Look up the key and confirm domain authorization
                stmt = select(UserPublicKey).where(
                    and_(
                        UserPublicKey.public_key_body == pk,
                        UserPublicKey.is_active == True,
                        UserPublicKey.allowed_domains.any(client_origin)
                    )
                )
                
                result = await self.db.execute(stmt)          # ← await here
                public_key_record = result.scalars().one_or_none()  # ← then unwrap

                return public_key_record
        except Exception as e:
            logger.error(f"Error checking PK: {e}")
            return None


# ── CLI TEST ─────────────────────────────────────────────────────────
# docker exec -it gateway python3 -m gateway.db.repositories.pk_repo

if __name__ == "__main__":
    import asyncio
    import sys
    from dbs_clients import get_async_session  # adjust import to your project

    logging.basicConfig(level=logging.DEBUG)

    # python3 -m gateway.repositories.pk_repository <pk> <client_origin>
    pk = sys.argv[1] if len(sys.argv) > 1 else "ed25519_pk_8810afe1633509d5a54a6ac79f494ebe"
    client_origin = sys.argv[2] if len(sys.argv) > 2 else "localhost"

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
        