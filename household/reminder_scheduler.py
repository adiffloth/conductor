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

Delivery timing: this only *starts* once per POLL_INTERVAL_MINUTES (the
cron job's "every 5m" schedule, jobs.json), but doesn't just fire whatever
happens to already be due at that instant — the query looks ahead by a
full interval, and anything found still in the future gets a precise
time.sleep() before delivery rather than going out immediately. Without
this, a reminder set for 2:45 was landing anywhere up to ~5 minutes late
(2:49 observed) depending on where 2:45 fell relative to the poll tick,
not because delivery itself was slow. A run that sleeps close to the full
interval can overlap the next scheduled tick; the cron scheduler already
skips a tick that fires while the previous one is still running rather
than running both at once, and nothing is lost by that — the skipped
tick's window is still covered by the *next* tick's own lookahead, since
there's no lower bound on how overdue a not-yet-sent reminder can be.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from household_mcp_server import _calendar_service, get_hermes_home  # noqa: E402

HERMES_BIN = "/home/hermes/.local/bin/hermes"

DELIVER_TARGETS = {
    "telegram": "telegram:8905350819",
    "photon": "photon",
}
DEFAULT_CHANNEL = "telegram"

# Must match (or exceed) the cron job's own schedule interval (jobs.json:
# "every 5m") — the query window below has to be at least this wide, or a
# reminder due between two ticks could be missed by both.
POLL_INTERVAL_MINUTES = 5


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


def _due_or_upcoming_reminders(service) -> list[dict]:
    """Not-yet-sent reminders due now, or due within the next poll interval.

    timeMax looks a full POLL_INTERVAL_MINUTES ahead rather than just up to
    now — main() sleeps off the remainder for anything still in the future
    so delivery lands on the requested minute instead of at the mercy of
    where "now" happened to fall relative to the poll tick. No lower bound
    on how overdue something can be (hermesReminderSent=false already
    excludes delivered ones), so a reminder that fell in a skipped tick's
    window is still picked up here, just later than intended rather than
    lost.
    """
    now = datetime.now(timezone.utc)
    time_max = (now + timedelta(minutes=POLL_INTERVAL_MINUTES)).isoformat()
    result = (
        service.events()
        .list(
            calendarId="primary",
            privateExtendedProperty=["hermesReminder=true", "hermesReminderSent=false"],
            timeMax=time_max,
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
    due = _due_or_upcoming_reminders(service)
    if not due:
        _log("no due reminders")
        return

    for event in due:
        start_str = event.get("start", {}).get("dateTime", "")
        if start_str:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            wait_seconds = (start_dt - datetime.now(timezone.utc)).total_seconds()
            if wait_seconds > 0:
                _log(f"sleeping {wait_seconds:.0f}s so event {event['id']} delivers at {start_str}")
                time.sleep(wait_seconds)

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
