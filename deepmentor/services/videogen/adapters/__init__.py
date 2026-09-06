"""Video-generation adapter registry.

Adapters are stateless singletons keyed by the ``adapter`` field on the resolved
config. The async-task adapter covers the submit → poll → download lifecycle
shared by Volcengine Ark Seedance and similar providers; register bespoke
providers by adding new keys here.
"""

from __future__ import annotations

from deepmentor.services.generation_http import GenerationProviderError
from deepmentor.services.videogen.adapters.async_task import AsyncTaskVideogenAdapter
from deepmentor.services.videogen.adapters.dashscope import DashScopeVideogenAdapter
from deepmentor.services.videogen.base import BaseVideogenAdapter

VIDEOGEN_ADAPTERS: dict[str, BaseVideogenAdapter] = {
    "async_task": AsyncTaskVideogenAdapter(),
    "dashscope": DashScopeVideogenAdapter(),
}


def get_videogen_adapter(name: str) -> BaseVideogenAdapter:
    adapter = VIDEOGEN_ADAPTERS.get(name or "async_task")
    if adapter is None:
        raise GenerationProviderError(f"Unsupported videogen adapter: {name!r}")
    return adapter


__all__ = [
    "VIDEOGEN_ADAPTERS",
    "get_videogen_adapter",
    "DashScopeVideogenAdapter",
]
