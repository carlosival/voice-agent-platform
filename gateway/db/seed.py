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
from gateway.db.models import Base, User, UserPublicKey, VoiceAgent, UserAgentAssociation

# Target your Docker Compose environment configuration
DATABASE_URL = os.getenv(
    "POSTGRES_URL", 
    "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"
)

DOMAIN = os.getenv(
    "DOMAIN", 
    "localhost"
)

fake = Faker()
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Configuration templates for the dynamic mock AI properties
MOCK_LLM = {"model": "llama-3-70b", "temperature": 0.7, "system_prompt": "You are a helpful voice assistant."}
MOCK_TTS = {"voice_id": "en-US-Neural-A", "engine": "cartesia", "speed": 1.0}
MOCK_STT = {"engine": "deepgram", "model": "nova-2", "language": "en"}

# New: valid values matching your model's constraints
TIERS = ["standard"]
REGIONS = ["us-east-1", "us-west-1", "eu-west-1", "eu-central-1", "ap-southeast-1", "global"]

async def seed_fixtures():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            print("🌱 Starting relational database seeding...")

            print("🧼 Cleaning up old environment tables...")
            await session.execute(delete(UserAgentAssociation))
            await session.execute(delete(UserPublicKey))
            await session.execute(delete(User))
            await session.execute(delete(VoiceAgent))

            print("🤖 Injecting System-wide Voice Agents...")
            generic_agents = []
            system_agent_names = ["Support Agent Pro", "Inbound Sales Exec", "Healthcare Scheduler"]
            for name in system_agent_names:
                agent = VoiceAgent(
                    id=uuid.uuid4(),
                    name=name,
                    is_generic=True,
                    llm_config=MOCK_LLM,
                    tts_config=MOCK_TTS,
                    stt_config=MOCK_STT,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(agent)
                generic_agents.append(agent)

            print("👤 Creating user accounts and key assignments...")
            for _ in range(5):
                user = User(
                    id=uuid.uuid4(),
                    email=fake.unique.email(),
                    created_at=datetime.now(timezone.utc)
                )
                session.add(user)

                pub_keys = []
                for _ in range(random.randint(1, 2)):
                    pub_key = UserPublicKey(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        public_key_body=f"ed25519_pk_{fake.unique.sha256()[:32]}",
                        allowed_domains=[fake.domain_name(), f"{DOMAIN}", "localhost", "127.0.0.1", "192.168.1.147"],
                        is_active=True,
                        created_at=datetime.now(timezone.utc)
                    )
                    session.add(pub_key)
                    pub_keys.append(pub_key)

                private_agent = VoiceAgent(
                    id=uuid.uuid4(),
                    name=f"Custom Assistant for {user.email.split('@')[0]}",
                    is_generic=False,
                    llm_config=MOCK_LLM,
                    tts_config=MOCK_TTS,
                    stt_config=MOCK_STT,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(private_agent)

                await session.flush()

                # Private agent association — pick a realistic tier/region pair
                user_tier = random.choice(TIERS)
                user_regions = random.sample(REGIONS, k=random.randint(1, 3))

                assoc_random = UserAgentAssociation(
                    public_key_id=pub_keys[0].id,
                    agent_id=private_agent.id,
                    is_enabled=True,
                    custom_config_override={"business_name": fake.company()},
                    tier=user_tier,
                    region=user_regions,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(assoc_random)


        await session.commit()
        print("✅ Database successfully hydrated with relational fixtures!")


if __name__ == "__main__":
    asyncio.run(seed_fixtures())