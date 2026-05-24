"""Domain and API models for the middleware service."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"


class AgencyTag(StrEnum):
    FIRE = "fire"
    MEDICAL = "medical"
    POLICE = "police"


class Message(BaseModel):
    id: str
    direction: MessageDirection
    text: str
    timestamp: datetime
    delivery_status: DeliveryStatus | None = None
    delivery_error: str | None = None
    delivery_attempts: int | None = None


class Session(BaseModel):
    """Internal session state (keyed by phone in store)."""

    id: str
    phone: str
    status: SessionStatus = SessionStatus.ACTIVE
    last_activity_at: datetime
    messages: list[Message] = Field(default_factory=list)
    agency_tags: list[AgencyTag] = Field(default_factory=list)


# --- Northstar inbound: POST /inbound ---


class InboundWebhookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_number: str = Field(alias="from")
    text: str
    timestamp: datetime


# --- Agent console ---


class SessionSummary(BaseModel):
    """GET /sessions list item."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_number: str = Field(alias="from")
    status: SessionStatus
    preview: str | None = None
    timestamp: datetime | None = None
    last_activity_at: datetime


class PhoneSummary(BaseModel):
    """GET /sessions?group_by_phone=true — one row per caller."""

    model_config = ConfigDict(populate_by_name=True)

    from_number: str = Field(alias="from")
    current_session_id: str
    status: SessionStatus
    preview: str | None = None
    timestamp: datetime | None = None
    last_inbound_at: datetime | None = None
    last_activity_at: datetime
    last_message_direction: MessageDirection | None = None
    agency_tags: list[AgencyTag] = Field(default_factory=list)


class SessionBlock(BaseModel):
    """Prior session for the same phone (read-only context)."""

    id: str
    status: SessionStatus
    started_at: datetime
    expired_at: datetime
    messages: list[Message]
    agency_tags: list[AgencyTag] = Field(default_factory=list)


class SessionDetail(BaseModel):
    """GET /sessions/{id}"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_number: str = Field(alias="from")
    status: SessionStatus
    last_activity_at: datetime
    messages: list[Message]
    previous_sessions: list[SessionBlock] = Field(default_factory=list)
    is_reply_target: bool = False
    agency_tags: list[AgencyTag] = Field(default_factory=list)
    outbound_delivery_failure: str | None = None
    latest_outbound_delivery_status: DeliveryStatus | None = None


class SessionTagsRequest(BaseModel):
    """PATCH /sessions/{id}/tags"""

    tags: list[AgencyTag] = Field(default_factory=list)


class AgentReplyRequest(BaseModel):
    """POST /sessions/{id}/reply"""

    text: str = Field(min_length=1)
    timestamp: datetime | None = None


class AgentReplyResponse(BaseModel):
    success: bool
    error: str | None = None
    delivery_attempts: int = 0
    duplicate: bool = False


class ConsoleConfig(BaseModel):
    session_ttl_seconds: int
    session_expiring_soon_seconds: int


class ErrorResponse(BaseModel):
    code: str
    message: str
