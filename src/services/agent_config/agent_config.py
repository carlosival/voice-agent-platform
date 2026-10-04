
from src.persistence.repositories.user_repo import UserRepository
from .builder import build_agent_config
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class AgentConfigService:
    def __init__(self, repo: Optional[UserRepository] = None):
        self.repo = repo

    def set_repo(self, repo:UserRepository):
        self.repo = repo

    async def get_config(self, public_key_id, agent_id) -> Optional[dict]:
        assoc = await self.repo.user_agent_config(public_key_id, agent_id)
        if assoc is None:
            return None
        config = build_agent_config(assoc)
        logger.info(
            "Loaded agent config",
            extra={"agent_id": str(agent_id), "hash": config["meta"]["config_hash"]},
        )
        return config


# ── CLI TEST ─────────────────────────────────────────────────────────
# docker exec -it -e PUBLIC_KEY_ID="34782ee3-5e46-4084-9be6-731115005279" -e AGENT_ID="61b20893-f694-4f5b-afda-59a4bf712df6" gateway python3 -m src.services.agent_config.agent_config

if __name__ == "__main__":
    from sqlalchemy import text
    from src.clients.postgres_db import get_async_session
    from .builder import validate_agent_config, AgentConfigError
    import logging, os, sys, asyncio, json


    logging.basicConfig(level=logging.INFO)
    # Keep SQL noise out of the output; set to INFO to see queries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    public_key_id = os.environ["PUBLIC_KEY_ID"]
    agent_id = os.environ["AGENT_ID"]

    async def main() -> int:
        async with get_async_session() as db:
            # Which database is the app really using?
            row = (await db.execute(text(
                "SELECT current_database(), current_schema(), "
                "inet_server_addr(), inet_server_port()"))).one()
            print(f"🔌 Connected: db={row[0]} schema={row[1]} server={row[2]}:{row[3]}")

            service = AgentConfigService(UserRepository(db))
            try:
                cfg = await service.get_config(public_key_id, agent_id)
            except AgentConfigError as e:
                print(f"❌ Config cannot be built: {e}")
                return 1

        if cfg is None:
            print("❌ Not found / disabled / not authorized for this key+agent pair")
            return 1

        print(json.dumps(cfg, indent=2, default=str))
        print("-" * 60)

        problems = validate_agent_config(cfg)
        if problems:
            print("❌ Schema problems:")
            for p in problems:
                print(f"   - {p}")
            return 1

        print("✅ Config matches the schema")
        print(f"   agent      : {cfg['meta']['name']} ({cfg['meta']['agent_id']})")
        print(f"   hash       : {cfg['meta']['config_hash']}")
        print(f"   tools      : {list(cfg['tools']) or '(none)'}")
        print(f"   state vals : {cfg['state']}")
        print(f"   json size  : {len(json.dumps(cfg))} bytes")
        return 0

    sys.exit(asyncio.run(main()))