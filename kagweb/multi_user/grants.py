"""Logical resource grants for non-admin users."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .identity import get_user_by_id
from .paths import SYSTEM_ROOT, ensure_system_dirs

GRANTS_DIR = SYSTEM_ROOT / "grants"


def empty_grant(user_id: str) -> dict[str, Any]:
    return {
        "version": 2,
        "user_id": user_id,
        "models": {"llm": []},
        # Tool whitelists share the partner-config semantics for built-ins:
        # ``enabled_tools=None`` means "default" (every tool in the pool),
        # ``[]`` means none, a list is an explicit whitelist. MCP tools can
        # proxy host-side capabilities, so non-admin runtime access treats
        # ``mcp_tools=None`` as deny-by-default until an admin grants explicit
        # names. ``cli_apps`` is the same posture for installed CLI apps, keyed
        # by app id: each one is third-party code executing in the sandbox, so
        # an absent grant is no access rather than all of them.
        # ``exec_enabled`` is a tri-state override on top of the
        # deployment exec policy: ``None`` follows the policy, ``False`` always
        # denies, ``True`` is only honored where the sandbox can actually
        # isolate users (SYSTEM isolation).
        "enabled_tools": None,
        "mcp_tools": None,
        "cli_apps": None,
        "exec_enabled": None,
        # Whether the user may drive turns through the configured agent
        # backend. Same tri-state as ``exec_enabled``: ``None`` follows the
        # deployment (an admin who configured a backend means users may talk to
        # it — the point of the feature), ``False`` suspends that user, ``True``
        # is equivalent to ``None`` here since there is no stricter deployment
        # policy to override. A multi-user deployment must be usable out of the
        # box; requiring an explicit per-user grant would have made it not.
        "agent_loop": None,
        # Whether the user may start an agent backend that spawns a LOCAL
        # process (the CLI and ACP families). This is not a model grant: the
        # child runs with the server's privileges, so allowing it is handing
        # the user code execution on this host. It is therefore opt-in —
        # ``True`` allows, and both ``None`` (the default) and ``False`` deny.
        # Fail-closed on purpose, and deliberately the opposite direction from
        # ``agent_loop`` above: an existing deployment has no such key, so it
        # tightens rather than loosens on upgrade. The HTTP family starts
        # nothing locally and stays covered by ``agent_loop``.
        "agent_loop_cli": None,
    }


def _normalize_tool_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def grant_path(user_id: str) -> Path:
    ensure_system_dirs()
    return GRANTS_DIR / f"{user_id}.json"


def normalize_grant(user_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce any stored/submitted grant payload into the v2 shape.

    v1 grants normalize losslessly for everything that was ever enforced:
    ``models.embedding`` / ``models.search`` / ``spaces`` had no runtime
    consumers and are dropped; absent v2 fields default to unrestricted.
    """
    base = empty_grant(user_id)
    if not isinstance(payload, dict):
        return base
    base["user_id"] = user_id
    models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
    items = models.get("llm") if isinstance(models, dict) else []
    if not isinstance(items, list):
        items = []
    base["models"]["llm"] = [dict(item) for item in items if isinstance(item, dict)]
    for key in ("enabled_tools", "mcp_tools", "cli_apps"):
        base[key] = _normalize_tool_list(payload.get(key))
    exec_enabled = payload.get("exec_enabled")
    base["exec_enabled"] = bool(exec_enabled) if isinstance(exec_enabled, bool) else None
    agent_loop = payload.get("agent_loop")
    base["agent_loop"] = bool(agent_loop) if isinstance(agent_loop, bool) else None
    agent_loop_cli = payload.get("agent_loop_cli")
    base["agent_loop_cli"] = bool(agent_loop_cli) if isinstance(agent_loop_cli, bool) else None
    return base


def load_grant(user_id: str) -> dict[str, Any]:
    path = grant_path(user_id)
    if not path.exists():
        return empty_grant(user_id)
    try:
        return normalize_grant(user_id, json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return empty_grant(user_id)


def save_grant(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise ValueError(f"Unknown user id: {user_id}")
    _username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise ValueError("Admin users use the main workspace and cannot receive assignments.")
    grant = normalize_grant(user_id, payload)
    validate_grant(grant)
    path = grant_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(grant, indent=2, ensure_ascii=False), encoding="utf-8")
    return grant


def validate_grant(grant: dict[str, Any]) -> None:
    """Reject accidental secret/path material in grants.

    Grants carry logical ids only. Runtime resolution happens server-side.
    """
    forbidden = {"api_key", "secret", "password", "token", "path", "base_url"}

    def walk(value: Any, trail: str = "grant") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in forbidden or lowered.endswith("_key"):
                    raise ValueError(f"Grants must not contain secret/path field: {trail}.{key}")
                walk(child, f"{trail}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{trail}[{index}]")

    walk(grant)


def public_grant(user_id: str) -> dict[str, Any]:
    return deepcopy(load_grant(user_id))
