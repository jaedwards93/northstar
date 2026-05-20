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


class Message(BaseModel):
    id: str
    direction: MessageDirection
    text: str
    timestamp: datetime


class Session(BaseModel):
    """Internal session state (keyed by phone in store)."""

    id: str
    phone: str
    status: SessionStatus = SessionStatus.ACTIVE
    last_activity_at: datetime
    messages: list[Message] = Field(default_factory=list)


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


class SessionDetail(BaseModel):
    """GET /sessions/{id}"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_number: str = Field(alias="from")
    status: SessionStatus
    last_activity_at: datetime
    messages: list[Message]


class AgentReplyRequest(BaseModel):
    """POST /sessions/{id}/reply"""

    text: str = Field(min_length=1)


class AgentReplyResponse(BaseModel):
    success: bool
    error: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
