#!/usr/bin/env python3
"""Phase 7 reminder scheduler.

Polls Google Calendar for due, not-yet-sent Hermes reminders (created via
household_mcp_server.set_reminder) and pushes each one as a proactive
Telegram message via `hermes send` — no LLM involved, no running
conversation required.

Meant to run as a `hermes cron` job in --no-agent mode (see
project_plan.md Phase 7 for the exact `hermes cron create` invocation).
Deliberately writes nothing to stdout: delivery happens here directly via
subprocess calls to `hermes send`, not via cron's own stdout-forwarding —
avoids depending on unverified assumptions about how that forwarding
behaves when multiple reminders are due in the same tick. Diagnostic
output goes to stderr only.
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from household_mcp_server import _calendar_service  # noqa: E402

HERMES_BIN = "/home/hermes/.local/bin/hermes"
DELIVER_TO = "telegram:8905350819"


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


def _deliver(text: str) -> bool:
    result = subprocess.run(
        [HERMES_BIN, "send", "--to", DELIVER_TO, "--quiet", text],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _log(f"delivery failed (exit {result.returncode}): {result.stderr.strip()}")
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
        text = f"⏰ {summary}"
        _log(f"delivering: {text} (event {event['id']})")
        if _deliver(text):
            _mark_sent(service, event["id"])
        else:
            _log(f"leaving event {event['id']} unmarked — will retry next tick")


if __name__ == "__main__":
    main()
