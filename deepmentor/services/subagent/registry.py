"""Backend registry — the single place that knows which subagents exist.

Add a new subagent by writing a :class:`SubagentBackend` and listing it here;
the capability, API and UI all discover it through these helpers. Local-CLI
backends (Claude Code, Codex, Antigravity CLI, Kimi CLI, opencode,
MiMo Code, Hermes Agent, OpenClaw, DeepSeek Harness) and
the in-process partner backend live in the same registry but are told apart by
``local_cli`` — only CLIs are detected on the machine and offered in the
connect-CLI modal.
"""

from __future__ import annotations

import asyncio

from deepmentor.services.subagent.antigravity import AntigravityBackend
from deepmentor.services.subagent.base import SubagentBackend
from deepmentor.services.subagent.claude_code import ClaudeCodeBackend
from deepmentor.services.subagent.codex import CodexBackend
from deepmentor.services.subagent.deepseek_harness import DeepSeekHarnessBackend
from deepmentor.services.subagent.hermes import HermesBackend
from deepmentor.services.subagent.kimi import KimiBackend
from deepmentor.services.subagent.openclaw import OpenClawBackend
from deepmentor.services.subagent.opencode_family import MimoBackend, OpencodeBackend
from deepmentor.services.subagent.partner import PartnerBackend
from deepmentor.services.subagent.types import DetectResult

_BACKENDS: dict[str, SubagentBackend] = {
    backend.kind: backend
    for backend in (
        ClaudeCodeBackend(),
        CodexBackend(),
        AntigravityBackend(),
        KimiBackend(),
        OpencodeBackend(),
        MimoBackend(),
        HermesBackend(),
        OpenClawBackend(),
        DeepSeekHarnessBackend(),
        PartnerBackend(),
    )
}


def list_backend_kinds() -> list[str]:
    """Every connectable backend kind (CLIs + partner)."""
    return list(_BACKENDS.keys())


def get_backend(kind: str) -> SubagentBackend | None:
    return _BACKENDS.get(str(kind or "").strip())


def _cli_backends() -> list[SubagentBackend]:
    return [b for b in _BACKENDS.values() if getattr(b, "local_cli", True)]


async def detect_all() -> list[DetectResult]:
    """Probe each local-CLI backend for installability on this machine.

    Non-CLI backends (the partner backend) are skipped — they aren't installed,
    they're connected from their own list — so this only ever returns the CLIs
    the connect-CLI modal offers.
    """
    cli = _cli_backends()
    results = await asyncio.gather(
        *(backend.detect() for backend in cli),
        return_exceptions=True,
    )
    detections: list[DetectResult] = []
    for backend, result in zip(cli, results, strict=True):
        if isinstance(result, DetectResult):
            detections.append(result)
        else:
            detections.append(
                DetectResult(
                    kind=backend.kind,
                    display_name=backend.display_name,
                    available=False,
                    detail=str(result),
                )
            )
    return detections


__all__ = ["list_backend_kinds", "get_backend", "detect_all"]
