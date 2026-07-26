#!/usr/bin/env python3
"""Household calendar MCP server for Hermes Agent.

Exposes get_agenda / add_calendar_event / set_reminder as direct MCP tools,
calling the Google Calendar API in-process. Deliberately bypasses the
bundled google-workspace skill's skill_view + shelled-out-script path: that
route requires an extra LLM round trip to load instructions before the
agent can even attempt a call, and its scripts assume a generic `python`
on PATH with dependencies pre-installed, neither of which holds in this
container (see project_plan.md Phase 6 for the failure this replaces).

Reuses the OAuth token already established via the google-workspace skill's
setup flow (~/.hermes/google_token.json), scoped to Calendar only.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    # Authoritative when running inside the Hermes venv (profile resolution,
    # Docker detection, etc.) — Hermes sets HERMES_HOME to something other
    # than ~/.hermes for spawned tool subprocesses, so the naive env-var
    # fallback below resolves to the wrong directory when it's set at all.
    from hermes_constants import get_hermes_home
except ImportError:

    def get_hermes_home() -> Path:
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"


HERMES_HOME = get_hermes_home()
TOKEN_PATH = HERMES_HOME / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Marker in the event description that the Phase 7 reminder scheduler polls
# for — distinguishes Hermes-created reminders from regular calendar events.
REMINDER_TAG = "[Hermes Reminder]"

mcp = FastMCP("household")


def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        raise RuntimeError(
            "Google Calendar token is invalid — re-run the google-workspace skill's OAuth setup."
        )
    return creds


def _calendar_service():
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=_get_credentials())


def _with_timezone(value: str) -> str:
    if "T" not in value or value.endswith("Z") or "+" in value[10:] or "-" in value[10:]:
        return value
    return value + "Z"


@mcp.tool()
def get_agenda(start: str = "", end: str = "") -> str:
    """Check the household's Google Calendar for scheduled events, appointments,
    or the agenda for a day/week. Use this whenever the user asks what's on
    their calendar/schedule, whether they have any events/appointments coming
    up, or what they're doing on a given day.

    Args:
        start: ISO 8601 start time (e.g. 2026-07-27T00:00:00Z). Defaults to now.
        end: ISO 8601 end time. Defaults to 7 days after start.
    """
    now = datetime.now(timezone.utc)
    time_min = _with_timezone(start) if start else now.isoformat()
    time_max = _with_timezone(end) if end else (now + timedelta(days=7)).isoformat()

    service = _calendar_service()
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = [
        {
            "id": e["id"],
            "summary": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            "location": e.get("location", ""),
        }
        for e in result.get("items", [])
    ]
    return json.dumps(events, ensure_ascii=False)


@mcp.tool()
def add_calendar_event(summary: str, start: str, end: str, location: str = "") -> str:
    """Create a calendar event.

    Args:
        summary: Event title.
        start: ISO 8601 start datetime with timezone (e.g. 2026-07-27T15:00:00Z).
        end: ISO 8601 end datetime with timezone.
        location: Optional location text.
    """
    event = {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    if location:
        event["location"] = location

    service = _calendar_service()
    result = service.events().insert(calendarId="primary", body=event).execute()
    return json.dumps(
        {
            "status": "created",
            "id": result["id"],
            "summary": result.get("summary", ""),
            "htmlLink": result.get("htmlLink", ""),
        }
    )


@mcp.tool()
def set_reminder(summary: str, when: str) -> str:
    """Set a reminder for a specific time.

    Creates a tagged calendar event that the household reminder scheduler
    (a separate process, Phase 7) polls for and delivers as a proactive
    message when it comes due.

    Args:
        summary: What to be reminded about.
        when: ISO 8601 datetime with timezone (e.g. 2026-07-27T15:00:00Z).
    """
    start_dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    end_dt = start_dt + timedelta(minutes=5)
    event = {
        "summary": f"Reminder: {summary}",
        "description": REMINDER_TAG,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }

    service = _calendar_service()
    result = service.events().insert(calendarId="primary", body=event).execute()
    return json.dumps(
        {"status": "reminder set", "id": result["id"], "summary": result.get("summary", ""), "when": when}
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
