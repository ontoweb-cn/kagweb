"""Owner-scoped storage for a user's linked Intellect member credential.

One file per KAGWeb account, under the owner-private secrets root
(``data/system/user-secrets/<owner>/private/intellect-agent/``) — the branch of
the tree the sandbox runner does not mount, so a credential that authorizes a
*person* is not readable by that person's own ``exec`` tool. The same rule and
the same layout as the Codex credential store; see
:func:`kagweb.multi_user.paths.get_owner_secrets_dir`.

The record binds itself to the KAGWeb account that created it. The directory it
lives in already says whose it is, but a file restored from a backup, copied
between accounts, or left behind by a deleted-and-recreated user would silently
inherit the wrong owner — so the id is written into the payload and re-checked
on every read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from kagweb.utils.secret_files import ensure_private_directory

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_ASSET_DIRNAME = "intellect-agent"
_CREDENTIAL_FILENAME = "credentials.v1.json"


@dataclass(frozen=True)
class LinkedIdentity:
    """A KAGWeb account's link to an Intellect member."""

    #: The KAGWeb account this link belongs to. Never empty.
    kagweb_user_id: str
    #: The Intellect member the user proved to be (``mem_…``).
    member_id: str
    #: The member token to present to the Gateway. Secret.
    token: str = ""
    team_id: str = ""
    project_id: str = ""
    #: Origin (scheme://host[:port]) of the service this token was minted
    #: against. The token is only ever presented back to that origin: a member
    #: token is a bearer credential for one deployment, and the profile URL is
    #: deployment configuration that can be repointed — so without this binding,
    #: editing the profile would silently ship every linked user's token to the
    #: new host on their next turn.
    service_origin: str = ""
    #: Unix seconds, or ``0.0`` when the issuer set no expiry.
    expires_at: float = 0.0
    linked_at: float = 0.0

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def to_payload(self) -> dict[str, Any]:
        return {"version": _SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, payload: Any) -> LinkedIdentity | None:
        if not isinstance(payload, dict):
            return None
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            return None
        if version != _SCHEMA_VERSION:
            # A future schema is not something to guess at: the credential may
            # have moved to a field this build would ignore, and presenting a
            # half-read token is worse than reporting "not linked".
            logger.warning(
                "intellect identity: ignoring credential file with unsupported version %s",
                version,
            )
            return None

        def text(key: str) -> str:
            value = payload.get(key)
            return str(value) if isinstance(value, (str, int, float)) else ""

        def number(key: str) -> float:
            value = payload.get(key)
            return float(value) if isinstance(value, (int, float)) else 0.0

        user_id = text("kagweb_user_id")
        member_id = text("member_id")
        token = text("token")
        if not user_id or not member_id or not token:
            return None
        return cls(
            kagweb_user_id=user_id,
            member_id=member_id,
            token=token,
            team_id=text("team_id"),
            project_id=text("project_id"),
            service_origin=text("service_origin"),
            expires_at=number("expires_at"),
            linked_at=number("linked_at"),
        )


class IntellectIdentityStore:
    """Read and write one owner's link, atomically and owner-only."""

    def __init__(self, owner_root: Path) -> None:
        self.root = Path(owner_root) / "private" / _ASSET_DIRNAME
        self.credentials_path = self.root / _CREDENTIAL_FILENAME

    # -- read ---------------------------------------------------------------

    def load(self, user_id: str) -> LinkedIdentity | None:
        """This owner's link, or ``None`` when there is no usable one.

        Every unreadable or untrustworthy state collapses to ``None`` — absent
        file, bad JSON, a missing field, a record belonging to another account.
        "Not linked" is the caller's safe branch, and an exception here would
        turn a corrupt leftover file into a broken turn.
        """
        path = self.credentials_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("intellect identity: cannot read credentials: %s", exc)
            return None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("intellect identity: credentials file is not valid JSON")
            return None

        record = LinkedIdentity.from_payload(payload)
        if record is None:
            return None
        if record.kagweb_user_id != str(user_id or ""):
            # The file is in the right directory but names somebody else: a
            # copy, a restore, or a recycled id. Treat as unlinked rather than
            # lend one account's identity to another.
            logger.warning(
                "intellect identity: credential is bound to a different account; ignoring"
            )
            return None
        return record

    # -- write --------------------------------------------------------------

    def save(self, record: LinkedIdentity) -> None:
        ensure_private_directory(self.root)
        payload = record.to_payload()
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.credentials_path.name}.",
            suffix=".tmp",
            dir=self.root,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Set at creation time would be better, but mkstemp fixes 0600
            # already; the chmod is a belt-and-braces re-assert for umask.
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.credentials_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def clear(self) -> bool:
        """Remove the stored link. Returns whether anything was removed."""
        try:
            self.credentials_path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("intellect identity: cannot remove credentials: %s", exc)
            return False


__all__ = ["IntellectIdentityStore", "LinkedIdentity"]
