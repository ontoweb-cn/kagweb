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


class BookReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    page_ids: list[str] = Field(default_factory=list)


class ReadingReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    revision: int = Field(ge=1)
    locators: list[int] = Field(default_factory=list)


class ReadingViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: int | None = Field(default=None, ge=0)
    selection: str | None = None


class TimedMediaViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_seconds: float = Field(ge=0)


MemoryReference = Literal["recent", "profile", "scope", "preferences", "summary"]


class TurnRequest(BaseModel):
    """Validated turn input; ``config`` contains capability options only."""

    model_config = ConfigDict(extra="forbid")

    content: str
    capability: str | None = "chat"
    session_id: str | None = None
    knowledge_bases: list[str] = Field(default_factory=list)
    language: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    history_references: list[str] = Field(default_factory=list)
    book_references: list[BookReference] = Field(default_factory=list)
    reading_references: list[ReadingReference] = Field(default_factory=list)
    memory_references: list[MemoryReference] = Field(default_factory=list)
    attachments: list[OutgoingAttachment] = Field(default_factory=list)

    llm_selection: LLMSelection | None = None
    workspace_mode: str | None = None
    mastery_path_id: str | None = None
    mastery_path_lease_managed: bool = False
    reading_material_id: str | None = None
    reading_material_revision: int | None = Field(default=None, ge=1)
    reading_workspace_id: str | None = None
    reading_viewport: ReadingViewport | None = None
    timed_media_id: str | None = None
    timed_media_viewport: TimedMediaViewport | None = None
    parent_message_id: int | None = None

    # Runtime options are explicit and never passed to a capability schema.
    course_id: str | None = None
    persist_user_message: bool = True
    regenerate: bool = False
    regenerated_from_message_id: int | None = None
    superseded_turn_id: str | None = None
    followup_question_context: dict[str, Any] | None = None
    selection_tutor_context: dict[str, Any] | None = None
    subagent_consult_budget: int | None = Field(default=None, ge=0)
    auto_route: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return an execution payload while preserving omitted-field semantics."""

        return self.model_dump(mode="python", exclude_unset=True)
