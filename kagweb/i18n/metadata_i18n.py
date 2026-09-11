"""Localized display metadata for built-in tools and capabilities."""

from __future__ import annotations

_CAPABILITY_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "chat": {
        "en": "Default agentic chat with tools, retrieval, memory, and attachments.",
        "zh": "默认智能聊天，支持工具、检索、记忆和附件。",
    },
}


def capability_description_i18n(name: str, fallback: str = "") -> dict[str, str]:
    values = _CAPABILITY_DESCRIPTIONS.get(name)
    if values:
        return dict(values)
    return {"en": fallback, "zh": fallback}


def localized_description(values: dict[str, str], language: str) -> str:
    lang = "zh" if (language or "en").lower().startswith("zh") else "en"
    return values.get(lang) or values.get("en") or values.get("zh") or ""


__all__ = [
    "capability_description_i18n",
    "localized_description",
]
