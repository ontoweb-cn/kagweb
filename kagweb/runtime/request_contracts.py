"""Public request contracts and config validators for built-in capabilities."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from kagweb.runtime.capability_catalog import EmptyConfig


class ChatRequestConfig(EmptyConfig):
    pass


def _clean_public_config(raw_config: dict[str, Any] | None) -> dict[str, Any]:
    if raw_config is None:
        return {}
    if not isinstance(raw_config, dict):
        raise ValueError("Capability config must be an object.")
    return dict(raw_config)


def _validate_model(
    model_type: type[BaseModel],
    raw_config: dict[str, Any] | None,
    *,
    label: str,
) -> BaseModel:
    cleaned = _clean_public_config(raw_config)
    try:
        return model_type.model_validate(cleaned)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(f"Invalid {label} config: {details}") from exc


def validate_chat_request_config(raw_config: dict[str, Any] | None) -> ChatRequestConfig:
    return _validate_model(ChatRequestConfig, raw_config, label="chat")


def build_request_schema(model_type: type[BaseModel]) -> dict[str, Any]:
    return model_type.model_json_schema(mode="validation")


CAPABILITY_CONFIG_VALIDATORS: dict[str, Callable[[dict[str, Any] | None], Any]] = {
    "chat": validate_chat_request_config,
}

CAPABILITY_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "chat": ChatRequestConfig,
}


def _model_validator(
    model_type: type[BaseModel],
    capability_name: str,
) -> Callable[[dict[str, Any] | None], BaseModel]:
    def validate(raw_config: dict[str, Any] | None) -> BaseModel:
        return _validate_model(
            model_type,
            raw_config,
            label=capability_name.replace("_", " "),
        )

    return validate


# Every built-in has an explicit validator, including no-options capabilities.
for _capability_name, _model_type in CAPABILITY_CONFIG_MODELS.items():
    CAPABILITY_CONFIG_VALIDATORS.setdefault(
        _capability_name,
        _model_validator(_model_type, _capability_name),
    )

CAPABILITY_REQUEST_SCHEMAS: dict[str, dict[str, Any]] = {
    name: build_request_schema(model_type) for name, model_type in CAPABILITY_CONFIG_MODELS.items()
}


def validate_capability_config(
    capability: str, raw_config: dict[str, Any] | None
) -> dict[str, Any]:
    validator = CAPABILITY_CONFIG_VALIDATORS.get(capability)
    if validator is None:
        return _clean_public_config(raw_config)
    model = validator(raw_config)
    if isinstance(model, BaseModel):
        return model.model_dump(exclude_none=True)
    return _clean_public_config(raw_config)


def get_capability_request_schema(capability: str) -> dict[str, Any]:
    return dict(CAPABILITY_REQUEST_SCHEMAS.get(capability, {}))


__all__ = [
    "CAPABILITY_CONFIG_VALIDATORS",
    "CAPABILITY_CONFIG_MODELS",
    "CAPABILITY_REQUEST_SCHEMAS",
    "ChatRequestConfig",
    "build_request_schema",
    "get_capability_request_schema",
    "validate_capability_config",
    "validate_chat_request_config",
]
