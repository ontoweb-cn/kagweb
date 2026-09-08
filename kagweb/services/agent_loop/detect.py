"""Local agent-loop detection — DeepMentor's probe pattern, KAGWeb-scoped.

Two probe families, matching the backend families:

* **CLI** — a ``shutil.which`` PATH probe (Windows PATHEXT-safe, so npm
  ``.cmd``/``.bat`` shims are found). Fast and side-effect free, which is
  why it runs on settings-page load rather than behind a button; the
  definitive command *execution* check stays on the explicit Test action.
* **HTTP** — a short-timeout reachability GET against a configured service
  URL. Any HTTP response (even 404) proves the service is up; the consult
  contract has no health endpoint, and POSTing the real turn endpoint would
  run a real agent turn. Local URLs (loopback hosts) are labeled ``local``
  so the UI can mark "本地 / 远端" and the auto-primary rule can prefer
  local Intellect deployments.

Preset-level CLI probes answer "is this agent CLI installed on this
machine" for the settings preset picker; profile-level probes answer "is
*my* configured loop reachable" for each saved profile card.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import shutil
from typing import Any
from urllib.parse import urlsplit

import httpx

#: Reachability probe budget — short enough not to stall a settings-page
#: load, long enough for a waking local service.
_HTTP_PROBE_TIMEOUT_SECONDS = 2.5

_LOCAL_HOSTNAMES = frozenset({"localhost", "::1", "0.0.0.0"})


@dataclass
class DetectResult:
    """One probe outcome; ``key`` is a preset name or a profile id."""

    key: str
    label: str
    family: str  # "cli" | "http"
    local: bool
    available: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_local_url(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host in _LOCAL_HOSTNAMES or host.startswith("127.")


def detect_cli(key: str, label: str, command: str) -> DetectResult:
    """PATH-probe one CLI command (no subprocess, no version call)."""
    command = str(command or "").strip()
    if not command:
        return DetectResult(key, label, "cli", True, False, "no command configured")
    resolved = shutil.which(command)
    return DetectResult(
        key,
        label,
        "cli",
        True,
        resolved is not None,
        resolved or f"'{command}' not found on PATH",
    )


async def detect_http(key: str, label: str, url: str) -> DetectResult:
    """Reachability-probe one service URL. TLS errors still count as
    reachable — the probe only asks "is something answering", never
    transfers agent data."""
    url = str(url or "").strip()
    local = is_local_url(url)
    if not url:
        return DetectResult(key, label, "http", local, False, "no URL configured")
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_PROBE_TIMEOUT_SECONDS,
            verify=False,  # probe only — no credentials, no body
            follow_redirects=True,
            # Direct connection: a system proxy answering on the machine
            # would turn "unreachable" into a proxy error page.
            trust_env=False,
        ) as client:
            response = await client.get(url)
        return DetectResult(key, label, "http", local, True, f"HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001 — any probe failure is a result
        return DetectResult(key, label, "http", local, False, str(exc)[:200])


async def detect_agent_loops(block: dict[str, Any] | None = None) -> list[DetectResult]:
    """Probe every preset CLI plus every configured profile.

    Returns preset-level CLI results (keyed by preset name, for the picker)
    followed by profile-level results (keyed by profile id, for the cards).
    """
    from .builtin import PRESETS
    from .settings import get_agent_loop_settings

    settings = block if block is not None else get_agent_loop_settings()
    profiles = [item for item in (settings.get("profiles") or []) if isinstance(item, dict)]

    cli_results: list[DetectResult] = []
    profile_coros = []

    for preset in PRESETS.values():
        if preset.family != "cli" or not preset.command:
            continue
        cli_results.append(detect_cli(preset.name, preset.name, preset.command))

    for profile in profiles:
        preset = PRESETS.get(str(profile.get("preset") or ""))
        label = str(profile.get("name") or profile.get("preset") or "profile")
        if preset is not None and preset.family == "cli":
            command = str(profile.get("command") or "").strip()
            if command:
                profile_coros.append(
                    asyncio.to_thread(detect_cli, str(profile.get("id")), label, command)
                )
        elif str(profile.get("url") or "").strip():
            profile_coros.append(
                detect_http(str(profile.get("id")), label, str(profile.get("url")))
            )

    probed = await asyncio.gather(*profile_coros) if profile_coros else []
    return [*cli_results, *probed]


__all__ = [
    "DetectResult",
    "detect_agent_loops",
    "detect_cli",
    "detect_http",
    "is_local_url",
]
