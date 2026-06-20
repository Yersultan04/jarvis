from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Pointer to a chunk used as evidence in the final answer."""

    chunk_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    quote: str = Field(..., max_length=400, description="Short verbatim snippet that justifies the answer")


class ToolCall(BaseModel):
    kind: Literal["tool"] = "tool"
    thought: str = Field(..., description="Why this tool is being called now")
    tool: str = Field(..., description="Tool name from the registry")
    args: dict[str, Any] = Field(default_factory=dict)


class FinalAnswer(BaseModel):
    kind: Literal["final"] = "final"
    thought: str = Field(..., description="Reasoning summary")
    answer: str = Field(..., description="Final answer to the user's question")
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


class Escalation(BaseModel):
    kind: Literal["escalate"] = "escalate"
    thought: str
    reason: str = Field(..., description="What was tried, what's missing, why a human is needed")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


NextStep = ToolCall | FinalAnswer | Escalation


class NextStepEnvelope(BaseModel):
    """Discriminated union wrapper. LLM emits exactly one of: tool / final / escalate."""

    step: ToolCall | FinalAnswer | Escalation = Field(..., discriminator="kind")


class PoolEntry(BaseModel):
    """Lightweight view of a chunk for prompt context."""

    chunk_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    heading: str | None = None
    text: str
    score: float = 0.0


class AgentRunStartRequest(BaseModel):
    query: str = Field(..., min_length=1)
    max_steps: int | None = Field(default=None, ge=1, le=80)
    session_id: uuid.UUID | None = None


class AgentRunOut(BaseModel):
    id: uuid.UUID
    rag_id: uuid.UUID
    status: str
    query: str
    answer: str | None
    citations: list[Citation] = []
    error: str | None = None
    steps_used: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
