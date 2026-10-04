import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    String, ForeignKey, Text, DateTime, Boolean, Index, Integer, text, func, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship 

class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Internal runtime execution strategy.
    # This should not necessarily be exposed to end users.
    strategy: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'conversational'"),
    )
    
    # Base configurations for the AI stack
    llm_config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tts_config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    stt_config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    
    # Exact prompt version currently used by the agent.
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    
    # Relationships
 
    agent_tools: Mapped[list["AgentToolAssociation"]] = relationship(
    "AgentToolAssociation",
    cascade="all, delete-orphan",
    )

    prompt_version: Mapped["PromptVersion"] = relationship("PromptVersion")

    
    user_associations: Mapped[List["UserAgentAssociation"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class UserAgentAssociation(Base):
    """Junction table enabling Many-to-Many relationships between Users and Agents.
    Also stores subscription status or custom tweaks for generic agents."""
    __tablename__ = "user_agent_associations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    
    public_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_public_keys.id", ondelete="CASCADE")
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True) # e.g., Set to false if they stop working
    
    ''' 
        Optional: If a user wants to override a specific value like { statrategy: new_val, llm_config:{new overrride} prompt_version:{content: newcontent}, etc} 
        Maybe could be other values like initial_greeting, etc for voice agents
    '''
    custom_config_override: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, server_default=text("'{}'::jsonb")) # {"business_name": "Acme Corp"}
    
    # Single value — clear billing/compliance boundary
    tier: Mapped[str] = mapped_column(String(50), nullable=False, default="free")

    # Any ACL related with the agent and goes here domain, resource access etc.
    
    region: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, default=["global"])

    # Multi-value — real deployment topology
    allowed_domains: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    public_key: Mapped["UserPublicKey"] = relationship(back_populates="agent_associations")
    
    agent: Mapped["Agent"] = relationship(back_populates="user_associations")


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
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    user: Mapped["User"] = relationship(back_populates="public_keys")

    agent_associations: Mapped["UserAgentAssociation"] = relationship(back_populates="public_key")

    __table_args__ = (
        Index("idx_public_keys_user_id", "user_id"),
        Index("idx_active_public_keys", "public_key_body", postgresql_where=(is_active == True)),
    )




class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)

    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'{}'::jsonb"))



class AgentToolAssociation(Base):
    __tablename__ = "agent_tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )

    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )


    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    tool: Mapped["Tool"] = relationship("Tool")




class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    versions: Mapped[list["PromptVersion"]] = relationship(
        "PromptVersion",
        back_populates="prompt",
        cascade="all, delete-orphan",
        order_by="PromptVersion.version",
    )

class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Structured Output Type or simple string 
    output_schema: Mapped[dict] = mapped_column(JSONB,nullable=False,default=dict,)

    # Type of prompt (zero shot, CoT, ReAct, etc)
    strategy: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'conversational'"),
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    prompt: Mapped["Prompt"] = relationship(
        "Prompt",
        back_populates="versions",
    )


    state: Mapped["State | None"] = relationship(
    "State",
    back_populates="prompt_version",
    uselist=False,
    cascade="all, delete-orphan",
    )


class State(Base):
        __tablename__ = "states"

        id: Mapped[uuid.UUID] = mapped_column(
            UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
        )

        prompt_version_id: Mapped[uuid.UUID] = mapped_column(
            UUID(as_uuid=True),
            ForeignKey("prompt_versions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        )

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )

        prompt_version: Mapped["PromptVersion"] = relationship(
            "PromptVersion",
            back_populates="state",
        )

        associated_fields: Mapped[list["StateFieldAssociations"]] = relationship(
            "StateFieldAssociations",
            back_populates="state",
            cascade="all, delete-orphan",
            
        )


class StateFieldAssociations(Base):
    __tablename__ = "state_fields_associations"

    __table_args__ = (
        UniqueConstraint(
            "state_id",
            "field_id",
            name="uq_state_definition_field",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("states.id", ondelete="CASCADE"),
        nullable=False,
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fields.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Configuration of this field within this particular definition
    required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    
    state: Mapped["State"] = relationship(
        "State",
        back_populates="associated_fields",
    )

    field: Mapped["Field"] = relationship("Field",)


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    

