"""Stable application-layer facade for KAGWeb entry points."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from typing import Any, AsyncIterator

from .container import get_application_container
from .contracts import TurnRequest


@dataclass(slots=True)
class CapabilityAvailability:
    """Availability result for optional capabilities."""

    name: str
    available: bool
    install_hint: str = ""


class KAGWebApp:
    """Facade around runtime, session, and capability contracts."""

    def __init__(self) -> None:
        self.container = get_application_container()
        self.turns = self.container.turns
        self.capabilities = self.container.capability_registry

    def resolve_capability(self, value: str | None) -> str:
        requested = str(value or "chat").strip() or "chat"
        manifests = self.capabilities.get_manifests()
        for manifest in manifests:
            if manifest["name"] == requested:
                return requested
            aliases = {str(alias).strip() for alias in manifest.get("cli_aliases", [])}
            if requested in aliases:
                return str(manifest["name"])
        available = ", ".join(sorted(manifest["name"] for manifest in manifests))
        raise ValueError(f"Unknown capability `{requested}`. Available: {available}")

    def get_capability_contracts(self) -> list[dict[str, Any]]:
        contracts = []
        for manifest in self.capabilities.get_manifests():
            contracts.append(
                {
                    **manifest,
                    "availability": self.get_capability_availability(manifest["name"]).__dict__,
                }
            )
        return contracts

    def get_capability_contract(self, value: str) -> dict[str, Any]:
        resolved = self.resolve_capability(value)
        for manifest in self.capabilities.get_manifests():
            if manifest["name"] == resolved:
                return {
                    **manifest,
                    "availability": self.get_capability_availability(resolved).__dict__,
                }
        raise ValueError(f"Capability not found: {resolved}")

    def get_capability_availability(self, capability: str) -> CapabilityAvailability:
        resolved = self.resolve_capability(capability)
        if resolved == "math_animator":
            available = importlib.util.find_spec("manim") is not None
            return CapabilityAvailability(
                name=resolved,
                available=available,
                install_hint=(
                    ""
                    if available
                    else "Install with `pip install -e '.[math-animator]'` "
                    "or `pip install -r requirements/math-animator.txt`."
                ),
            )
        return CapabilityAvailability(name=resolved, available=True)

    async def start_turn(
        self, request: TurnRequest | dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if isinstance(request, dict):
            request = TurnRequest(**request)
        await self.container.start()
        resolved_capability = self.resolve_capability(request.capability)
        return await self.turns.start_turn(
            {
                **request.to_payload(),
                "capability": resolved_capability,
            }
        )

    async def stream_turn(self, turn_id: str, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        await self.container.start()
        async for item in self.turns.subscribe_turn(turn_id, after_seq=after_seq):
            yield item

    async def cancel_turn(self, turn_id: str) -> bool:
        await self.container.start()
        return await self.turns.cancel_turn(turn_id)

    async def submit_user_reply(
        self,
        turn_id: str,
        text: str | None = None,
        *,
        answers: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Deliver the user's reply to a turn paused on ``ask_user``."""
        await self.container.start()
        return await self.turns.submit_user_reply(turn_id, text=text, answers=answers)

    async def regenerate_last_turn(
        self,
        session_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await self.container.start()
        return await self.turns.regenerate_last_turn(session_id, overrides=overrides)

    async def list_sessions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        await self.container.start()
        return await self.turns.list_sessions(limit=limit, offset=offset)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        await self.container.start()
        return await self.turns.get_session(session_id)

    async def export_session_trace(
        self,
        session_id: str,
        fmt: str = "dsl",
        *,
        stable: bool = False,
        normalize_ids: bool = False,
        include_text: bool = True,
    ) -> str | None:
        """Export a session's reasoning trace (module 18): ``dsl`` → pretty
        JSON, ``mermaid`` → flowchart source. ``None`` when the session does
        not exist. Same serializer the CLI and the REST endpoint share."""
        from kagweb.services.session.dsl_export import build_session_dsl, dsl_to_mermaid

        if fmt not in ("dsl", "mermaid"):
            raise ValueError(f"Unsupported trace format: {fmt} (supported: dsl, mermaid)")
        session = await self.get_session(session_id)
        if session is None:
            return None
        doc = build_session_dsl(
            session.get("messages", []),
            stable=stable,
            normalize_ids=normalize_ids,
            include_text=include_text,
            session_id=session_id,
        )
        if fmt == "mermaid":
            return dsl_to_mermaid(doc)
        return json.dumps(doc, ensure_ascii=False, indent=2)

    async def rename_session(self, session_id: str, title: str) -> bool:
        await self.container.start()
        return await self.turns.rename_session(session_id, title)

    async def delete_session(self, session_id: str) -> bool:
        await self.container.start()
        return await self.turns.delete_session(session_id)

    async def get_active_turn(self, session_id: str) -> dict[str, Any] | None:
        await self.container.start()
        return await self.turns.check_active_turn(session_id)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
