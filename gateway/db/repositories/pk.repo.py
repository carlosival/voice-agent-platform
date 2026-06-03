import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional
from db.models import UserPublicKey

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
                key_record = await self.db.execute(stmt).scalar_one_or_none()

                return key_record
        except Exception as e:
            logger.error(f"Error checking PK: {e}")
            return None
        