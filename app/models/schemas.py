"""Pydantic data models for API requests, responses, and user credentials."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_USER_MESSAGE_CHARS: int = 1000
MAX_ASSISTANT_MESSAGE_CHARS: int = 3000
MAX_HISTORY_TURNS: int = 10
MAX_TOTAL_HISTORY_CHARS: int = 12000


class AuthenticatedUser(BaseModel):
    """Represents the verified user identity and role extracted from JWT."""

    user_id: str = Field(description="Unique subject / employee ID from the JWT")
    role: str = Field(description="User role controlling document permissions: employee | hr | client | admin | super-admin")
    client_id: str | None = Field(default=None, description="Optional client/customer ID for tenant isolation")
    company_id: str | None = Field(default=None, description="Optional company ID for tenant isolation")


class ChatMessage(BaseModel):
    """Single turn in chat history with role-differentiated length limits."""

    role: Literal["user", "assistant"] = Field(
        description="Message author role: strictly 'user' or 'assistant'",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=MAX_ASSISTANT_MESSAGE_CHARS,
        description="Text content of the message",
    )

    @model_validator(mode="after")
    def validate_role_content_bounds(self) -> ChatMessage:
        """Enforces 1000-char limit for user messages while allowing up to 3000 for assistant."""
        if self.role == "user" and len(self.content) > MAX_USER_MESSAGE_CHARS:
            raise ValueError(
                f"User message exceeds maximum allowed limit of {MAX_USER_MESSAGE_CHARS} characters"
            )
        return self


class ChatRequest(BaseModel):
    """Input payload for POST /chat endpoint with individual and cumulative length bounds."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=MAX_USER_MESSAGE_CHARS,
        description="The question asked by the user",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional server session ID; new session created if omitted",
    )
    temporary: bool = Field(
        default=False,
        description="When true, executes statelessly without persisting session or messages",
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_TURNS,
        description="Prior conversation turns for multi-turn context",
    )

    @model_validator(mode="after")
    def validate_total_history_length(self) -> ChatRequest:
        """Ensures cumulative character count across all history items does not exceed 12000."""
        total_chars = sum(len(m.content) for m in self.history)
        if total_chars > MAX_TOTAL_HISTORY_CHARS:
            raise ValueError(
                f"Total history characters ({total_chars}) exceeds limit of {MAX_TOTAL_HISTORY_CHARS}"
            )
        return self


class HealthResponse(BaseModel):
    """Response payload for GET /health check."""

    status: str = Field(default="ok", description="Overall service status")
    version: str = Field(default="1.0.0", description="Application release version")
    orchestrator: str = Field(default="langgraph", description="Active orchestrator mode")
    llm_provider: str = Field(
        default="mock",
        description="Active LLM provider: mock | groq | openai | gemini",
    )
