"""household-identity plugin — DM sender-name resolution (Phase 8b, part 2).

Hermes's own gateway prefixes the sender's display name onto message text
in group/shared chats (``[Name] message``) so the model knows who's
talking, but deliberately not in 1:1 DMs, since those are already 1:1 by
construction (see ``gateway/session.py``'s ``is_shared_multi_user_session``).
For a *household* assistant serving several family members who each DM it
individually, that's exactly the case where the model still needs to know
who "me" refers to — this plugin fills that gap for DMs only, via the
``pre_gateway_dispatch`` hook (fires once per inbound message, before
auth/pairing and agent dispatch; can rewrite the event's text).

Deliberately does NOT touch group-chat prefixing — Hermes's own mechanism
there already works and draws on a different source (the platform's raw
display name) than this plugin's registry name; mixing the two only for
DMs is a deliberate scope decision, see project_plan.md's "Multi-user
support" section for the full reasoning.

Family-member identity comes from ~/.hermes/family_members.json — the same
registry household_mcp_server.py's scheduling tools already read. This
plugin only *reads* it, never writes it (same operator-managed-only
security posture as the rest of Phase 8b — a manipulated DM can't get
itself registered as a new "family member"). Schema:
    {"family_members": [{"name": ..., "email": ..., "telegram_id": ..., "phone": ...}]}
telegram_id/phone are optional per entry. Confirmed via source (not
assumed): Telegram's SessionSource.user_id is the numeric Telegram user
ID; Photon's is the sender's own E.164 phone number — both already exactly
what this project's TELEGRAM_ALLOWED_USERS / PHOTON_ALLOWED_USERS
allowlists use, so no separate ID scheme is needed.
"""
import json
import os
from pathlib import Path
from typing import Optional

try:
    from hermes_constants import get_hermes_home
except ImportError:

    def get_hermes_home() -> Path:
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"


FAMILY_MEMBERS_PATH = get_hermes_home() / "family_members.json"

# platform.value -> the family_members.json field carrying that platform's
# SessionSource.user_id for a given person.
_PLATFORM_ID_FIELD = {
    "telegram": "telegram_id",
    "photon": "phone",
}


def _load_family_members() -> list:
    if not FAMILY_MEMBERS_PATH.exists():
        return []
    try:
        return json.loads(FAMILY_MEMBERS_PATH.read_text()).get("family_members", [])
    except (json.JSONDecodeError, OSError):
        return []


def _resolve_sender_name(platform_value: str, user_id) -> Optional[str]:
    id_field = _PLATFORM_ID_FIELD.get(platform_value)
    if not id_field or not user_id:
        return None
    user_id = str(user_id).strip()
    for member in _load_family_members():
        registered_id = str(member.get(id_field, "")).strip()
        if registered_id and registered_id == user_id:
            return member.get("name")
    return None


def _on_pre_gateway_dispatch(event=None, **_kwargs):
    """Prefix a DM's text with the sender's registered name, if resolvable.

    Never raises — a lookup failure should degrade to "no rewrite" (today's
    behavior), not break message delivery for the whole household.
    """
    try:
        source = getattr(event, "source", None)
        if source is None or getattr(source, "chat_type", None) != "dm":
            return None
        platform = getattr(source, "platform", None)
        platform_value = getattr(platform, "value", None)
        name = _resolve_sender_name(platform_value, getattr(source, "user_id", None))
        if not name:
            return None
        return {"action": "rewrite", "text": f"[{name}] {event.text}"}
    except Exception:
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
