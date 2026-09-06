from __future__ import annotations

from typing import Any

import pytest

from deepmentor.partners.bus.queue import MessageBus
from deepmentor.partners.channels.discord import DiscordChannel, DiscordConfig
from deepmentor.partners.channels.email import EmailChannel, EmailConfig
from deepmentor.partners.channels.mattermost import MattermostChannel, MattermostConfig
from deepmentor.partners.channels.slack import SlackChannel, SlackConfig
from deepmentor.partners.channels.telegram import TelegramChannel, TelegramConfig
from deepmentor.partners.channels.zulip import ZulipChannel, ZulipConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_cls", "config"),
    [
        (DiscordChannel, DiscordConfig(enabled=True, allow_from=["*"])),
        (MattermostChannel, MattermostConfig(enabled=True, allow_from=["*"])),
        (SlackChannel, SlackConfig(enabled=True, allow_from=["*"])),
        (TelegramChannel, TelegramConfig(enabled=True, allow_from=["*"])),
        (ZulipChannel, ZulipConfig(enabled=True, allow_from=["*"])),
    ],
)
async def test_missing_credentials_are_visible_in_webui_status(
    channel_cls: type[Any], config: Any
) -> None:
    channel = channel_cls(config, MessageBus())

    await channel.start()

    assert channel.is_running is False
    assert channel.setup_state == {
        "status": "action_required",
        "message": (
            "Required fields are missing. Complete the channel configuration and save again."
        ),
    }


@pytest.mark.asyncio
async def test_email_consent_requirement_is_visible_in_webui_status() -> None:
    channel = EmailChannel(
        EmailConfig(enabled=True, allow_from=["*"], consent_granted=False),
        MessageBus(),
    )

    await channel.start()

    assert channel.setup_state == {
        "status": "action_required",
        "message": "Explicit consent is required before email polling can start.",
    }
