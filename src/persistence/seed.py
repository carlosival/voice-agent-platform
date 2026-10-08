import asyncio, logging, os, random, uuid
from datetime import datetime, timezone

from faker import Faker
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.clients.infiscal_vault import infisical_client, InfisicalSDKClient, INFISICAL_PROJECT_ID

# Import your declarative models
from src.persistence.models import (
    Base,
    User,
    UserPublicKey,
    Agent,
    UserAgentAssociation,
    Tool,
    AgentToolAssociation,
    Prompt,
    PromptVersion,
    State,
    StateFieldAssociations,
    Field,
)

logger = logging.getLogger(__name__)


ENV = "dev"

# Target your Docker Compose environment configuration
DATABASE_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres",
)

DOMAIN = os.getenv("DOMAIN", "localhost")

fake = Faker()
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Configuration templates for the dynamic mock AI properties
MOCK_LLM = {"model": "llama-3.3-70b-versatile", "engine": "groq", "temperature": 0}
MOCK_TTS = {"voice_id": "sharvard", "language": "es","engine": "speaches", "speed": 1.0, "model":"speaches-ai/piper-es_ES-sharvard-medium"}
MOCK_STT = {"engine": "groq", "model": "whisper-large-v3-turbo", "language": "es"}

# Valid values matching your model's constraints
TIERS = ["standard"]
REGIONS = ["us-east-1", "us-west-1", "eu-west-1", "eu-central-1", "ap-southeast-1", "global"]
STRATEGIES = ["conversational"]

# Reusable field catalog. (name, type, description)
FIELD_FIXTURES = [
    ("business_name", "string", "Name of the business the agent represents."),
    ("caller_name", "string", "Name of the person calling."),
    ("callback_number", "string", "Phone number to reach the caller back on."),
    ("intent", "string", "Detected purpose of the call."),
    ("issue_summary", "string", "Short summary of the caller's issue."),
    ("ticket_id", "string", "Helpdesk ticket identifier, once created."),
    ("qualified", "boolean", "Whether the lead was qualified for a demo."),
    ("appointment_time", "string", "Confirmed appointment date/time."),
]

# Prompt fixtures: name -> list of versions.
# Each version also declares which state fields it tracks: (field_name, required)
PROMPT_FIXTURES = {
    "support_agent": [
        {
            "content": "You are a support agent for {{business_name}}. Resolve the caller's issue or open a ticket.",
            "input_schema": {"type": "object", "properties": {"business_name": {"type": "string"}}},
            "output_schema": {},
            "state_fields": [
                ("business_name", True),
                ("issue_summary", False),
            ],
        },
        {
            "content": (
                "You are a concise, friendly support agent for {{business_name}}. "
                "Diagnose the issue step by step, open a ticket if unresolved, and offer a human handoff."
            ),
            "input_schema": {"type": "object", "properties": {"business_name": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
            "state_fields": [
                ("business_name", True),
                ("issue_summary", False),
                ("ticket_id", False),
            ],
        },
    ],
    "sales_exec": [
        {
            "content": "You are an inbound sales executive for {{business_name}}. Qualify the lead and book a demo.",
            "input_schema": {"type": "object", "properties": {"business_name": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"qualified": {"type": "boolean"}}},
            "state_fields": [
                ("business_name", True),
                ("qualified", False),
                ("appointment_time", False),
            ],
        },
    ],
    "scheduler": [
        {
            "content": "You schedule appointments for {{business_name}}. Confirm name, phone number and preferred time.",
            "input_schema": {"type": "object", "properties": {"business_name": {"type": "string"}}},
            "output_schema": {},
            "state_fields": [
                ("business_name", True),
                ("caller_name", True),
                ("callback_number", True),
                ("appointment_time", False),
            ],
        },
    ],
    "custom_assistant": [
        {
            "content": "You are a personal voice assistant for {{business_name}}.",
            "input_schema": {"type": "object", "properties": {"business_name": {"type": "string"}}},
            "output_schema": {},
            "state_fields": [
                ("business_name", True),
                ("intent", False),
            ],
        },
    ],
}

# Tool fixtures
TOOL_FIXTURES = [
    {
        "name": "book_appointment",
        "description": "Book an appointment in the calendar.",
        "input_schema": {
            "customer_name": {"type": "string", "description": "Full name of the customer."},
            "start_time": {"type": "string", "description": "Appointment start, ISO 8601 date-time."},
        },
        "output_schema": "string",  # returns the confirmation id
    },
    {
        "name": "send_sms",
        "description": "Send an SMS confirmation to the caller.",
        "input_schema": {
            "to": {"type": "string", "description": "Destination phone number in E.164 format."},
            "body": {"type": "string", "description": "Text of the message."},
        },
        "output_schema": "string",
    },
    {
        "name": "end_conversation",
        "description": "End the call once the caller's issue is resolved or they ask to hang up.",
        "input_schema": {
            "reason": {
                "type": "string",
                "description": "Why the call is ending.",
                "nullable": True,
            },
        },
        "output_schema": "string",
    },
    {
        "name": "transfer_to_human",
        "description": "Transfer the call to a human operator.",
        "input_schema": {
            "reason": {
                "type": "string",
                "description": "Why the caller needs a human.",
                "nullable": True,
            },
        },
        "output_schema": "string",
    },
]

# System-wide agents: name, prompt name, tools to attach
SYSTEM_AGENTS = [
    ("Support Agent Pro", "support_agent", ["end_conversation", "transfer_to_human"]),
    ("Inbound Sales Exec", "sales_exec", ["book_appointment", "send_sms","end_conversation"]),
    ("Healthcare Scheduler", "scheduler", ["book_appointment", "send_sms", "transfer_to_human","end_conversation"]),
]


INFISICAL_KEYS = [
    "LLM_API_KEY",
    "STT_API_KEY",
    "TTS_API_KEY",
]

def now():
    return datetime.now(timezone.utc)

def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False

# --------------------------------------------------------------------------- #
# Infisical helpers (the SDK is synchronous, so these run via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def purge_infisical_folders(client: InfisicalSDKClient) -> int:
    """
    Delete every UUID-named folder under "/" in this environment, along with the
    secrets inside. Mirrors the DB cleanup so old public-key folders don't pile up.
    Non-UUID folders are left alone.
    """
    response = client.folders.list_folders(
        project_id=INFISICAL_PROJECT_ID,
        environment_slug=ENV,
        path="/",
    )
 
    deleted = 0
    for folder in response.folders:
        if not _is_uuid(folder.name):
            continue
        client.folders.delete_folder(
            folder_id_or_name=folder.name,
            environment_slug=ENV,
            project_id=INFISICAL_PROJECT_ID,
            path="/",
        )
        deleted += 1
 
    return deleted
 
 
def seed_infisical_secrets(client: InfisicalSDKClient, public_key_id: uuid.UUID) -> None:
    folder_name = str(public_key_id)
    secret_path = f"/{folder_name}"
 
    # 1. Ensure the folder exists (creating a secret does not create folders)
    try:
        client.folders.create_folder(
            name=folder_name,
            environment_slug=ENV,
            project_id=INFISICAL_PROJECT_ID,
            path="/",
        )
        logger.info("Created folder %s in %s", secret_path, ENV)
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise
        logger.info("Folder %s already exists, reusing it", secret_path)
 
    # 2. Create the secrets inside it
    for key in INFISICAL_KEYS:
        try:
            client.secrets.create_secret_by_name(
                secret_name=key,
                secret_value=f"seed-{key.lower()}",
                project_id=INFISICAL_PROJECT_ID,
                environment_slug=ENV,
                secret_path=secret_path,
            )
            logger.info(f"🔐 Created Infisical secret: {ENV}{secret_path}/{key}")
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
            logger.info("Secret %s already exists in %s, skipping", key, secret_path)

def seed_infisical_secrets(
    client: InfisicalSDKClient,
    public_key_id: uuid.UUID,
) -> None:
    folder_name = str(public_key_id)
    secret_path = f"/{folder_name}"

    # 1. Ensure the folder exists (create_secret won't make it)
    try:
        client.folders.create_folder(
            name=folder_name,
            environment_slug=ENV,
            path="/",
            project_id=INFISICAL_PROJECT_ID,
        )
        logger.info("Created folder %s in %s", secret_path, ENV)
    except Exception as e:
        # Folder may already exist on a re-run; anything else should surface
        if "already exists" not in str(e).lower():
            raise
        logger.info("Folder %s already exists, reusing it", secret_path)

    # 2. Create the secrets inside it
    for key in INFISICAL_KEYS:
        try:
            client.secrets.create_secret_by_name(
                secret_name=key,
                secret_value=f"seed-{key.lower()}",
                project_id=INFISICAL_PROJECT_ID,
                environment_slug=ENV,
                secret_path=secret_path,
            )
            logger.info("Created secret %s%s/%s", ENV, secret_path, key)
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
            logger.info("Secret %s already exists, skipping", key)

def build_association(public_key: UserPublicKey, agent: Agent) -> UserAgentAssociation:
    return UserAgentAssociation(
        id=uuid.uuid4(),
        public_key_id=public_key.id,
        agent_id=agent.id,
        is_enabled=True,
        custom_config_override={"business_name": fake.company()},
        tier=random.choice(TIERS),
        region=random.sample(REGIONS, k=random.randint(1, 3)),
        allowed_domains=[
            fake.domain_name(),
            DOMAIN,
            "localhost",
            "127.0.0.1",
            "192.168.1.147",
        ],
        created_at=now(),
    )


async def seed_fixtures():
    # Collected inside the transaction, used after it commits
    key_ids: list[uuid.UUID] = []
 
    logger.info("🧼 Cleaning up old Infisical folders...")
    purged = await asyncio.to_thread(purge_infisical_folders, infisical_client)
    logger.info(f"🗑️  Removed {purged} old Infisical folder(s)")
 
    async with AsyncSessionLocal() as session:
        async with session.begin():
            logger.info("🌱 Starting relational database seeding...")
 
            # Deletion order respects FK constraints (children first).
            # agents -> prompt_versions is ON DELETE RESTRICT, so agents must go
            # before prompt_versions; states/state_field_associations cascade from
            # prompt_versions/fields but we clear them explicitly for clarity.
            logger.info("🧼 Cleaning up old environment tables...")
            await session.execute(delete(UserAgentAssociation))
            await session.execute(delete(AgentToolAssociation))
            await session.execute(delete(Agent))
            await session.execute(delete(StateFieldAssociations))
            await session.execute(delete(State))
            await session.execute(delete(PromptVersion))
            await session.execute(delete(Prompt))
            await session.execute(delete(Tool))
            await session.execute(delete(Field))
            await session.execute(delete(UserPublicKey))
            await session.execute(delete(User))
 
            logger.info("🏷️  Injecting field catalog...")
            fields: dict[str, Field] = {}
            for name, ftype, description in FIELD_FIXTURES:
                field = Field(id=uuid.uuid4(), name=name, type=ftype, description=description)
                session.add(field)
                fields[name] = field
            await session.flush()
 
            logger.info("📝 Injecting prompts, prompt versions and their state definitions...")
            latest_version: dict[str, PromptVersion] = {}
            for prompt_name, versions in PROMPT_FIXTURES.items():
                prompt = Prompt(id=uuid.uuid4(), name=prompt_name)
                session.add(prompt)
                await session.flush()
 
                for i, v in enumerate(versions, start=1):
                    pv = PromptVersion(
                        id=uuid.uuid4(),
                        prompt_id=prompt.id,
                        version=i,
                        content=v["content"],
                        input_schema=v["input_schema"],
                        output_schema=v["output_schema"],
                    )
                    session.add(pv)
                    await session.flush()
 
                    # Each prompt version gets exactly one State (uselist=False, unique FK).
                    state = State(id=uuid.uuid4(), prompt_version_id=pv.id)
                    session.add(state)
                    await session.flush()
 
                    for field_name, required in v["state_fields"]:
                        session.add(
                            StateFieldAssociations(
                                id=uuid.uuid4(),
                                state_id=state.id,
                                field_id=fields[field_name].id,
                                required=required,
                            )
                        )
 
                    latest_version[prompt_name] = pv  # last one wins = newest version
                await session.flush()
 
            logger.info("🛠️  Injecting tools...")
            tools: dict[str, Tool] = {}
            for t in TOOL_FIXTURES:
                tool = Tool(id=uuid.uuid4(), **t)
                session.add(tool)
                tools[tool.name] = tool
            await session.flush()
 
            logger.info("🤖 Injecting System-wide Agents...")
            generic_agents = []
            for name, prompt_name, tool_names in SYSTEM_AGENTS:
                agent = Agent(
                    id=uuid.uuid4(),
                    name=name,
                    strategy=random.choice(STRATEGIES),
                    llm_config=MOCK_LLM,
                    tts_config=MOCK_TTS,
                    stt_config=MOCK_STT,
                    prompt_version_id=latest_version[prompt_name].id,
                    created_at=now(),
                )
                session.add(agent)
                await session.flush()
 
                for tool_name in tool_names:
                    session.add(
                        AgentToolAssociation(
                            id=uuid.uuid4(),
                            agent_id=agent.id,
                            tool_id=tools[tool_name].id,
                            enabled=True,
                            config={},
                        )
                    )
                generic_agents.append(agent)
            await session.flush()
 
            logger.info("👤 Creating user accounts and key assignments...")
            for _ in range(5):
                user = User(
                    id=uuid.uuid4(),
                    email=fake.unique.email(),
                    created_at=now(),
                )
                session.add(user)
                await session.flush()
 
                pub_keys: list[UserPublicKey] = []
                for _ in range(random.randint(1, 2)):
                    pub_key = UserPublicKey(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        public_key_body=f"ed25519_pk_{fake.unique.sha256()[:32]}",
                        is_active=True,
                        created_at=now(),
                    )
                    session.add(pub_key)
                    pub_keys.append(pub_key)
                    key_ids.append(pub_key.id)  # Infisical is seeded after commit
 
                # Flush so the public keys exist before the associations reference them
                await session.flush()
 
                private_agent = Agent(
                    id=uuid.uuid4(),
                    name=f"Custom Assistant for {user.email.split('@')[0]}",
                    strategy=random.choice(STRATEGIES),
                    llm_config=MOCK_LLM,
                    tts_config=MOCK_TTS,
                    stt_config=MOCK_STT,
                    prompt_version_id=latest_version["custom_assistant"].id,
                    created_at=now(),
                )
                session.add(private_agent)
                await session.flush()
 
                # Key #1 -> the user's private agent
                session.add(build_association(pub_keys[0], private_agent))
 
                # Key #2 (if any) -> one of the shared system agents
                if len(pub_keys) > 1:
                    session.add(build_association(pub_keys[1], random.choice(generic_agents)))
 
            await session.flush()
        # <- transaction committed here
 
    logger.info("✅ Database successfully hydrated with relational fixtures!")
 
    # Only touch Infisical once the DB commit succeeded, so a DB rollback
    # never leaves orphaned folders behind.
    logger.info(f"🔐 Seeding Infisical secrets for {len(key_ids)} public key(s)...")
    for key_id in key_ids:
        await asyncio.to_thread(seed_infisical_secrets, infisical_client, key_id)
 
    logger.info("✅ Infisical folders and secrets seeded!")




if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(seed_fixtures())