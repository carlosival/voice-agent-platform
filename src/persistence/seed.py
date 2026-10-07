import asyncio
import os
import random
import uuid
from datetime import datetime, timezone

from faker import Faker
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

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
            "type": "object",
            "properties": {"customer_name": {"type": "string"}, "start_time": {"type": "string"}},
            "required": ["customer_name", "start_time"],
        },
        "output_schema": {"type": "object", "properties": {"confirmation_id": {"type": "string"}}},
    },
    {
        "name": "send_sms",
        "description": "Send an SMS confirmation to the caller.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "body"],
        },
        "output_schema": None,
    },
    {
        "name": "end_conversation",
        "description": "Create a support ticket in the helpdesk.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
            "required": ["title"],
        },
        "output_schema": None,
    },
    {
        "name": "transfer_to_human",
        "description": "Transfer the call to a human operator.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
        "output_schema": None,
    },
]

# System-wide agents: name, prompt name, tools to attach
SYSTEM_AGENTS = [
    ("Support Agent Pro", "support_agent", ["create_ticket", "transfer_to_human"]),
    ("Inbound Sales Exec", "sales_exec", ["book_appointment", "send_sms"]),
    ("Healthcare Scheduler", "scheduler", ["book_appointment", "send_sms", "transfer_to_human"]),
]


def now():
    return datetime.now(timezone.utc)

def seed_infisical_secrets(
    client: InfisicalSDKClient,
    public_key_id: uuid.UUID,
) -> None:
    secret_path = f"/{public_key_id}"

    for key in INFISICAL_KEYS:
        client.secrets.create_secret(
            secret_name=key,
            secret_value=f"seed-{key.lower()}",
            project_id=INFISICAL_PROJECT_ID,
            environment_slug=ENV,
            secret_path=secret_path,
        )

        print(
            f"🔐 Created Infisical secret: "
            f"{ENV}/{public_key_id}/{key}"
        )


async def seed_fixtures():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            print("🌱 Starting relational database seeding...")

            # Deletion order respects FK constraints (children first).
            # agents -> prompt_versions is ON DELETE RESTRICT, so agents must go
            # before prompt_versions; states/state_field_associations cascade from
            # prompt_versions/fields but we clear them explicitly for clarity.
            print("🧼 Cleaning up old environment tables...")
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

            print("🏷️  Injecting field catalog...")
            fields: dict[str, Field] = {}
            for name, ftype, description in FIELD_FIXTURES:
                field = Field(id=uuid.uuid4(), name=name, type=ftype, description=description)
                session.add(field)
                fields[name] = field
            await session.flush()

            print("📝 Injecting prompts, prompt versions and their state definitions...")
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

                    for field_name,required in v["state_fields"]:
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

            print("🛠️  Injecting tools...")
            tools: dict[str, Tool] = {}
            for t in TOOL_FIXTURES:
                tool = Tool(id=uuid.uuid4(), **t)
                session.add(tool)
                tools[tool.name] = tool
            await session.flush()

            print("🤖 Injecting System-wide Agents...")
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

            print("👤 Creating user accounts and key assignments...")
            for _ in range(5):
                user = User(
                    id=uuid.uuid4(),
                    email=fake.unique.email(),
                    created_at=now(),
                )
                session.add(user)
                await session.flush()

                pub_keys = []
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

                def build_association(public_key, agent):
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

                # Key #1 -> the user's private agent
                session.add(build_association(pub_keys[0], private_agent))

                # Key #2 (if any) -> one of the shared system agents
                if len(pub_keys) > 1:
                    session.add(build_association(pub_keys[1], random.choice(generic_agents)))

            await session.flush()

        print("✅ Database successfully hydrated with relational fixtures!")


if __name__ == "__main__":
    asyncio.run(seed_fixtures())