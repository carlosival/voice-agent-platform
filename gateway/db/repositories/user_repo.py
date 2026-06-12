import logging
from typing import Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
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
            query_result = await self.db.execute(agent_stmt)
            result = query_result.scalars().first()
            
            return result

        except Exception as e:
            logger.error(f"Error checking user agent authorization: {e}")
            return None

    async def user_agent_config(
        self, public_key_id: str, agent_id: str
    ) -> Optional[UserAgentAssociation]:
        try:
            """
            Single joined query: validates authorization AND fetches the full
            agent config with any user-level overrides applied.

            Returns a merged config dict, or None if unauthorized / not found.
            """
            stmt = (
                select(UserAgentAssociation)
                .where(
                    UserAgentAssociation.public_key_id == public_key_id,
                    UserAgentAssociation.agent_id == agent_id,
                    UserAgentAssociation.is_enabled == True,
                )
                .options(joinedload(UserAgentAssociation.agent))  # single JOIN,
            )
            query_result = await self.db.execute(stmt)
            result = query_result.scalars().one_or_none()
            
            if result is None or result.agent is None:
                return None

            agent: VoiceAgent = result.agent

            # Base config assembled from the VoiceAgent row
            base_config = {
                "agent_id":   str(agent.id),
                "name":       agent.name,
                "is_generic": agent.is_generic,
                "tier":       result.tier,
                "regions":    result.region,
                "llm_config": dict(agent.llm_config),
                "tts_config": dict(agent.tts_config),
                "stt_config": dict(agent.stt_config),
            }

            # Deep-merge the per-user override on top of the base config.
            # Overrides are shallow-merged into each sub-config key they target;
            # unknown top-level keys are stored at the root for flexibility.
            override: dict = result.custom_config_override or {}
            for key, value in override.items():
                if key in ("llm_config", "tts_config", "stt_config") and isinstance(value, dict):
                    base_config[key] = {**base_config[key], **value}  # shallow merge per sub-config
                else:
                    base_config[key] = value  # direct override for scalar / custom keys
        
            logger.info(f"User agent config: {base_config}")
            return base_config

        except Exception as e:
            logger.error(f"Error checking user agent authorization: {e}")
            return None

# ── CLI TEST ─────────────────────────────────────────────────────────
# docker exec -it gateway python3 -m gateway.db.repositories.user_repo 2f6081d3-caba-48d3-95e0-c87d0ea5cc6a d179992c-51aa-4693-8b82-894e9b5f95de

if __name__ == "__main__":
    import asyncio
    import sys
    from dbs_clients import get_async_session  # adjust import to your project

    logging.basicConfig(level=logging.DEBUG)

    # python3 -m gateway.db.repositories.user_repo <pk> <client_origin>
    public_key_id = sys.argv[1] if len(sys.argv) > 1 else "123e4567-e89b-12d3-a456-426614174000"
    agent_id = sys.argv[2] if len(sys.argv) > 2 else "123e4567-e89b-12d3-a456-426614174000"

    async def main():
        async with get_async_session() as db:
            repo = UserRepository(db)
            result = await repo.user_agent_authorized(
                public_key_id=public_key_id,
                agent_id=agent_id
            )
            if result:
                print("✅ Found:", result)
            else:
                print("❌ Not found or inactive")

    asyncio.run(main())