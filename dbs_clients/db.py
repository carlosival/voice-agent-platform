

import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from contextlib import asynccontextmanager

db_user = os.getenv("POSTGRES_USER")
db_pass = os.getenv("POSTGRES_PASSWORD")
db_host = os.getenv("POSTGRES_HOST")
db_port = os.getenv("POSTGRES_PORT")
db = os.getenv("POSTGRES_DB")

DATABASE_URL=f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db}"

# Async engine with pooling and thread-safe setup
async_engine = create_async_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

# Session factory
AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,  # Keeps objects usable after commit
    autoflush=False,
    autocommit=False,
    class_=AsyncSession
)

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session