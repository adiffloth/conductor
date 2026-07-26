#!/usr/bin/env python3
"""Phase 7 reminder scheduler.

Polls Google Calendar for due, not-yet-sent Hermes reminders (created via
household_mcp_server.set_reminder) and pushes each one as a proactive
message — Telegram or iMessage (Photon), per the channel chosen when the
reminder was set — via `hermes send`. No LLM involved, no running
conversation required.

Meant to run as a `hermes cron` job in --no-agent mode (see
project_plan.md Phase 7 for the exact `hermes cron create` invocation).
Deliberately writes nothing to stdout: delivery happens here directly via
subprocess calls to `hermes send`, not via cron's own stdout-forwarding —
avoids depending on unverified assumptions about how that forwarding
behaves when multiple reminders are due in the same tick. Diagnostic
output goes to stderr only.

Photon delivery needs PHOTON_SIDECAR_TOKEN pinned in ~/.hermes/.env (see
that file's comment) — a cron-spawned process like this one never talks to
the live gateway, so it can't discover a randomly-generated token the way
a real conversation turn would.
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from household_mcp_server import _calendar_service, get_hermes_home  # noqa: E402

HERMES_BIN = "/home/hermes/.local/bin/hermes"

DELIVER_TARGETS = {
    "telegram": "telegram:8905350819",
    "photon": "photon",
}
DEFAULT_CHANNEL = "telegram"


def _load_photon_sidecar_token() -> str | None:
    env_path = get_hermes_home() / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.strip().startswith("PHOTON_SIDECAR_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _due_reminders(service) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    result = (
        service.events()
        .list(
            calendarId="primary",
            privateExtendedProperty=["hermesReminder=true", "hermesReminderSent=false"],
            timeMax=now,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def _mark_sent(service, event_id: str) -> None:
    service.events().patch(
        calendarId="primary",
        eventId=event_id,
        body={"extendedProperties": {"private": {"hermesReminder": "true", "hermesReminderSent": "true"}}},
    ).execute()


def _deliver(text: str, channel: str) -> bool:
    target = DELIVER_TARGETS.get(channel, DELIVER_TARGETS[DEFAULT_CHANNEL])
    env = os.environ.copy()
    if channel == "photon":
        token = _load_photon_sidecar_token()
        if not token:
            _log("PHOTON_SIDECAR_TOKEN not set in ~/.hermes/.env — cannot deliver via Photon")
            return False
        env["PHOTON_SIDECAR_TOKEN"] = token

    result = subprocess.run(
        [HERMES_BIN, "send", "--to", target, "--quiet", text],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        _log(f"delivery failed via {channel} (exit {result.returncode}): {result.stderr.strip()}")
        return False
    return True


def main() -> None:
    service = _calendar_service()
    due = _due_reminders(service)
    if not due:
        _log("no due reminders")
        return

    for event in due:
        summary = event.get("summary", "Reminder")
        channel = event.get("extendedProperties", {}).get("private", {}).get(
            "hermesReminderChannel", DEFAULT_CHANNEL
        )
        text = f"⏰ {summary}"
        _log(f"delivering via {channel}: {text} (event {event['id']})")
        if _deliver(text, channel):
            _mark_sent(service, event["id"])
        else:
            _log(f"leaving event {event['id']} unmarked — will retry next tick")


if __name__ == "__main__":
    main()
