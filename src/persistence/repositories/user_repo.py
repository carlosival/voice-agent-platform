import logging, uuid, copy
from typing import Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from src.persistence.models import Agent, UserAgentAssociation, PromptVersion, State, AgentToolAssociation

logger = logging.getLogger(__name__)


# Keys a per-user override must never be able to change
PROTECTED_KEYS = {"agent_id", "name", "tier", "regions", "allowed_domains"}
OVERRIDABLE_STATE_ATTRS = {"default_value", "required"}
OVERRIDABLE_TOOL_ATTRS = {"enabled", "config"}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

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
            Single joined query: validates authorization AND fetches the full agent
            config with user-level overrides applied.

            Returns the merged config dict, or None if not found / not authorized / disabled.
            This is the config every worker's Deps Provider uses at runtime.
            """
            try:
                public_key_uuid = uuid.UUID(str(public_key_id))
                agent_uuid = uuid.UUID(str(agent_id))
            except ValueError:
                return None
            
            stmt = (
                    select(UserAgentAssociation)
                    .where(
                        UserAgentAssociation.public_key_id == public_key_id,
                        UserAgentAssociation.agent_id == agent_id,
                        UserAgentAssociation.is_enabled.is_(True),
                    )
                    .options(
                        joinedload(UserAgentAssociation.agent).options(
                            # Agent -> PromptVersion -> State 
                            joinedload(Agent.prompt_version).options(
                                joinedload(PromptVersion.state)
                            ),
                            # Agent -> tools
                            selectinload(Agent.agent_tools).options(
                                joinedload(AgentToolAssociation.tool)
                            ),
                        )
                    )
                    )


            query_result = await self.db.execute(stmt)
            result = query_result.scalars().one_or_none()
            
            if result is None or result.agent is None:
                return None

            return result
                
        except Exception as e:
            logger.error(f"Error checking user agent authorization: {e}")
            return None

# ── CLI TEST ─────────────────────────────────────────────────────────
# docker exec -it -e PUBLIC_KEY_ID="1596842f-d9d5-472d-a504-b1c9fed5a34e" -e AGENT_ID="79e023fe-9a79-4f8c-bd55-3a9603c1af1b" gateway python3 -m src.persistence.repositories.user_repo 

if __name__ == "__main__":
    import asyncio
    import os
    from src.clients.postgres_db import get_async_session

    logging.basicConfig(level=logging.DEBUG)

    # python3 -m gateway.db.repositories.user_repo <pk> <client_origin>
    public_key_id = os.environ["PUBLIC_KEY_ID"]
    agent_id = os.environ["AGENT_ID"]

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

            result_2 = await repo.user_agent_config(
                public_key_id=public_key_id,
                agent_id=agent_id 
            )


            if result:
                print("✅ Found:", result_2)
            else:
                print("❌ Not found or inactive")

    asyncio.run(main())