import logging, uuid, copy
from typing import Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from src.persistence.models import Agent, UserAgentAssociation, PromptVersion, State, StateFieldAssociations, AgentToolAssociation

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
                            # Agent -> PromptVersion -> State -> fields
                            joinedload(Agent.prompt_version).options(
                                joinedload(PromptVersion.state).options(
                                    selectinload(State.associated_fields).options(
                                        joinedload(StateFieldAssociations.field)
                                    )
                                )
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
           
            '''
            # Refactor All the code below in a services this repo have 2 responsabilities Retrival the agent and preppared the agent configurations.

            agent = result.agent
            pv = agent.prompt_version
            state = pv.state

            base_config = {
                "agent_id": str(agent.id),
                "name": agent.name,
                "strategy": agent.strategy,
                "tier": result.tier,
                "regions": list(result.region),
                "allowed_domains": list(result.allowed_domains),
                "llm_config": agent.llm_config,
                "tts_config": agent.tts_config,
                "stt_config": agent.stt_config,
                "prompt_version": {
                    "id": str(pv.id),
                    "version": pv.version,
                    "content": pv.content,
                    "strategy": pv.strategy,
                    "input_schema": pv.input_schema,
                    "output_schema": pv.output_schema,
                },
                "state_fields": {
                    a.field.name: {
                        "type": a.field.type,
                        "description": a.field.description,
                        "required": a.required,
                        "default_value": a.default_value,
                    }
                    for a in (state.associated_fields if state else [])
                },
                "tools": {
                    t.tool.name: {
                        "id": str(t.tool.id),
                        "name": t.tool.name,
                        "description": t.tool.description,
                        "input_schema": t.tool.input_schema,
                        "output_schema": t.tool.output_schema,
                        "enabled": True,
                        "config": t.config,
                    }
                    for t in agent.agent_tools
                    if t.enabled
                },
            }
            base_config = copy.deepcopy(base_config)  # detach from ORM-owned dicts

            override = {
                k: v
                for k, v in (result.custom_config_override or {}).items()
                if k not in PROTECTED_KEYS
            }

            # Users may only tweak whitelisted attributes of things that already exist
            if "state_fields" in override:
                override["state_fields"] = _filter_keyed_override(
                    override["state_fields"], base_config["state_fields"], OVERRIDABLE_STATE_ATTRS
                )
            if "tools" in override:
                override["tools"] = _filter_keyed_override(
                    override["tools"], base_config["tools"], OVERRIDABLE_TOOL_ATTRS
                )

            # Recursive merge: nested dicts merge, scalars and lists are replaced
            merged = _deep_merge(base_config, override)

            # Drop tools the user disabled; keep the dict shape
            merged["tools"] = {
                name: tool for name, tool in merged["tools"].items() if tool.get("enabled", True)
            }

            logger.info(
                "Loaded agent config",
                extra={"agent_id": str(agent_uuid), "public_key_id": str(public_key_uuid)},
            )
            return merged          
        '''     
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