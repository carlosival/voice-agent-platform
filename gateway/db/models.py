import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, ForeignKey, Text, DateTime, Boolean, Index, JSON
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class UserAgentAssociation(Base):
    """Junction table enabling Many-to-Many relationships between Users and Agents.
    Also stores subscription status or custom tweaks for generic agents."""
    __tablename__ = "user_agent_associations"
    
    public_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_public_keys.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("voice_agents.id", ondelete="CASCADE"), primary_key=True)
    
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True) # e.g., Set to false if they stop paying
    
    # Optional: If a user wants to override a specific prompt setting on a generic agent
    custom_config_override: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True) # {"business_name": "Acme Corp"}
    
    # Tier tells you wich hardware and compliance the agent you be deployed in
    tier: Mapped[str] = mapped_column(String(50), default="free")

    # Region tells you which part of the world the agent you be deployed in
    region: Mapped[str] = mapped_column(String(50), default="global")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    public_key: Mapped["UserPublicKey"] = relationship(back_populates="agent_associations")
    agent: Mapped["VoiceAgent"] = relationship(back_populates="user_associations")


class User(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    public_keys: Mapped[List["UserPublicKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserPublicKey(Base):
    __tablename__ = "user_public_keys"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    public_key_body: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    allowed_domains: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    user: Mapped["User"] = relationship(back_populates="public_keys")

    agent_associations: Mapped[List["UserAgentAssociation"]] = relationship(back_populates="public_key", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_public_keys_user_id", "user_id"),
        Index("idx_active_public_keys", "public_key_body", postgresql_where=(is_active == True)),
    )


class VoiceAgent(Base):
    __tablename__ = "voice_agents"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # New Flag: Is this a generic system-wide agent template?
    is_generic: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Base configurations for the AI stack
    llm_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    tts_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    stt_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    user_associations: Mapped[List["UserAgentAssociation"]] = relationship(back_populates="agent", cascade="all, delete-orphan")