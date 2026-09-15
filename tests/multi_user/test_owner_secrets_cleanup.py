"""Deleting an account must take its owner-private credentials with it.

The secrets root sits outside every workspace on purpose (the sandbox mounts
workspaces, not this), so nothing else in the codebase ever cleans it up. A
leftover directory means a deleted account's OAuth refresh tokens — and its
linked external-service token — outlive the account and are readable by
whatever account is next given that id.
"""

from __future__ import annotations

from pathlib import Path


def test_deleting_a_user_removes_its_owner_secrets(mu_isolated_root, seed_user) -> None:
    from kagweb.multi_user.identity import delete_user
    from kagweb.multi_user.paths import SYSTEM_ROOT, USER_SECRETS_DIRNAME

    record = seed_user("alice")
    user_id = str(record["id"])
    secrets = SYSTEM_ROOT / USER_SECRETS_DIRNAME / user_id
    (secrets / "private" / "openai-codex").mkdir(parents=True, exist_ok=True)
    (secrets / "private" / "openai-codex" / "credentials.v1.json").write_text("{}")

    assert delete_user("alice") is True
    assert not secrets.exists()


def test_deleting_an_unknown_user_changes_nothing(mu_isolated_root) -> None:
    from kagweb.multi_user.identity import delete_user

    assert delete_user("nobody") is False


def test_the_admin_scope_keeps_its_secrets(mu_isolated_root) -> None:
    """The admin scope is the deployment's own credential home; it has no user
    record to delete, so a purge keyed on it must not fire."""
    from kagweb.multi_user.paths import LOCAL_ADMIN_ID, SYSTEM_ROOT, USER_SECRETS_DIRNAME

    secrets = Path(SYSTEM_ROOT) / USER_SECRETS_DIRNAME / LOCAL_ADMIN_ID
    secrets.mkdir(parents=True, exist_ok=True)

    from kagweb.multi_user.identity import _purge_owner_secrets

    _purge_owner_secrets(LOCAL_ADMIN_ID)
    assert secrets.exists()
