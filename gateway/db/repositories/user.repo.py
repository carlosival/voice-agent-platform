import logging
from typing import Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from gateway.db.models import VoiceAgent, UserAgentAssociation

logger = logging.getLogger(__name__)

class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def user_agent_authorized(
        self, public_key_id: str, agent_id: str
    ) -> Optional[UserAgentAssociation]:
        """
        Checks if a specific public key is authorized to use an agent.
        Returns the VoiceAgent and its specific custom configuration overrides if true.
        """
        try:
            # Construct the query checking the junction table via public_key_id
            agent_stmt = (
                select(UserAgentAssociation)
                .where(
                    and_(
                        UserAgentAssociation.agent_id == agent_id,
                        UserAgentAssociation.public_key_id == public_key_id,
                        UserAgentAssociation.is_enabled == True
                    )
                )
            )
            
            # Execute asynchronously and fetch results safely
            result = await self.db.execute(agent_stmt).scalar_one_or_none()
            
            return result

        except Exception as e:
            logger.error(f"Error checking user agent authorization: {e}")
            return None