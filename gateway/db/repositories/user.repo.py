import logging
from typing import Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def user_agent_authorized(
        self, public_key_id: str, agent_id: str
    ) -> Optional[Tuple[VoiceAgent, dict]]:
        """
        Checks if a specific public key is authorized to use an agent.
        Returns the VoiceAgent and its specific custom configuration overrides if true.
        """
        try:
            # Construct the query checking the junction table via public_key_id
            agent_stmt = (
                select(VoiceAgent, UserAgentAssociation.custom_config_override)
                .join(UserAgentAssociation, UserAgentAssociation.agent_id == VoiceAgent.id)
                .where(
                    and_(
                        VoiceAgent.id == agent_id,
                        UserAgentAssociation.public_key_id == public_key_id,
                        UserAgentAssociation.is_enabled == True
                    )
                )
            )
            
            # Execute asynchronously and fetch results safely
            result = await self.db.execute(agent_stmt)
            row = result.one_or_none()
            
            if row:
                # Returns a tuple: (VoiceAgent object, custom_config_override dict)
                return row[0], row[1]
            
            return None

        except Exception as e:
            logger.error(f"Error checking user agent authorization: {e}")
            return None