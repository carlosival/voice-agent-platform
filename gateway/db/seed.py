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

async def seed_fixtures():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            print("🌱 Starting relational database seeding...")
            
            # 1. Clear out old tables to make successive local runs deterministic
            print("🧼 Cleaning up old environment tables...")
            await session.execute(delete(UserAgentAssociation))
            await session.execute(delete(UserPublicKey))
            await session.execute(delete(User))
            await session.execute(delete(VoiceAgent))
            
            # 2. Seed System Voice Agents (Generic System-wide templates)
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

            # 3. Seed Users and their dependent items
            print("👤 Creating user accounts and key assignments...")
            for _ in range(5):
                user = User(
                    id=uuid.uuid4(),
                    email=fake.unique.email(),
                    created_at=datetime.now(timezone.utc)
                )
                session.add(user)
                
                # Assign 1 or 2 cryptographic public key bodies to each user
                pub_keys = []
                for _ in range(random.randint(1, 2)):
                    pub_key = UserPublicKey(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        public_key_body=f"ed25519_pk_{fake.unique.sha256()[:32]}",
                        allowed_domains=[fake.domain_name(), f"{DOMAIN}", "localhost", "127.0.0.1"],
                        is_active=True,
                        created_at=datetime.now(timezone.utc)
                    )
                    session.add(pub_key)
                    pub_keys.append(pub_key)
                
                # Give this specific user their own private voice agent template
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
                
                # Flush to database so our relational foreign keys hook up safely
                await session.flush()
                
                # 4. Map the Many-To-Many Subscriptions / Associations
                # Every user gets bound to their private agent
                assoc_private = UserAgentAssociation(
                    public_key_id=pub_keys[0].id,
                    agent_id=private_agent.id,
                    is_enabled=True,
                    custom_config_override={"business_name": fake.company()}
                )
                session.add(assoc_private)
                
                # Every user also links up to one generic global system tool
                chosen_generic = random.choice(generic_agents)
                assoc_generic = UserAgentAssociation(
                    public_key_id=pub_keys[0].id,
                    agent_id=chosen_generic.id,
                    is_enabled=True,
                    custom_config_override=None
                )
                session.add(assoc_generic)

        # Commit all atomic structural mutations to PostgreSQL
        await session.commit()
        print("✅ Database successfully hydrated with relational fixtures!")

if __name__ == "__main__":
    asyncio.run(seed_fixtures())