"""Validated turn input shared by CLI, WebSocket, and SDK entry points."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field



class OutgoingAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    url: str | None = None
    base64: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    id: str | None = None
    extracted_text: str | None = None

class LLMSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    model_id: str


class TurnRequest(BaseModel):
    """Validated turn input; ``config`` contains capability options only."""

    model_config = ConfigDict(extra="forbid")

    content: str
    capability: str | None = "chat"
    session_id: str | None = None
    tools: list[str] | None = None
    language: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    history_references: list[str] = Field(default_factory=list)
    partner_group_references: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[OutgoingAttachment] = Field(default_factory=list)

    persona: str | None = None
    llm_selection: LLMSelection | None = None
    workspace_mode: str | None = None
    parent_message_id: int | None = None

    persist_user_message: bool = True
    regenerate: bool = False
    regenerated_from_message_id: int | None = None
    superseded_turn_id: str | None = None
    selection_tutor_context: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return an execution payload while preserving omitted-field semantics."""

        return self.model_dump(mode="python", exclude_unset=True)
