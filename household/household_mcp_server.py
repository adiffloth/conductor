#!/usr/bin/env python3
"""Household MCP server for Hermes Agent — Calendar + Tasks.

Exposes calendar and grocery/chore-list read/write tools directly, calling
the Google Calendar and Tasks APIs in-process. Deliberately bypasses the
bundled google-workspace skill's skill_view + shelled-out-script path: that
route requires an extra LLM round trip to load instructions before the
agent can even attempt a call, and its scripts assume a generic `python`
on PATH with dependencies pre-installed, neither of which holds in this
container (see project_plan.md Phase 6 for the failure this replaces).
Google Tasks isn't covered by that bundled skill at all regardless.

Reuses the OAuth token already established via the google-workspace skill's
setup flow (~/.hermes/google_token.json), scoped to Calendar + Tasks.

Google has since shipped its own official, remote Calendar MCP server
(calendarmcp.googleapis.com, GA'd May 2026) with a broader tool set than
the calendar half of this file. Not adopted yet — see the Phase 9 research
item in project_plan.md before adding more surface area here.
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
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

GROCERY_LIST_NAME = "Groceries"
CHORES_LIST_NAME = "Chores"

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


def _tasks_service():
    from googleapiclient.discovery import build

    return build("tasks", "v1", credentials=_get_credentials())


def _find_or_create_tasklist(service, title: str) -> str:
    """Return the id of the household tasklist with this title, creating it
    the first time it's needed."""
    result = service.tasklists().list(maxResults=100).execute()
    for tl in result.get("items", []):
        if tl["title"] == title:
            return tl["id"]
    created = service.tasklists().insert(body={"title": title}).execute()
    return created["id"]


def _with_timezone(value: str) -> str:
    if "T" not in value or value.endswith("Z") or "+" in value[10:] or "-" in value[10:]:
        return value
    return value + "Z"


def _event_summary(e: dict) -> dict:
    return {
        "id": e["id"],
        "summary": e.get("summary", "(no title)"),
        "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
        "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
        "location": e.get("location", ""),
    }


def _default_window(start: str, end: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    time_min = _with_timezone(start) if start else now.isoformat()
    time_max = _with_timezone(end) if end else (now + timedelta(days=7)).isoformat()
    return time_min, time_max


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
    time_min, time_max = _default_window(start, end)

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

    events = [_event_summary(e) for e in result.get("items", [])]
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


@mcp.tool()
def update_calendar_event(
    event_id: str, summary: str = "", start: str = "", end: str = "", location: str = ""
) -> str:
    """Change an existing calendar event. Only the fields you provide are
    changed — leave the rest blank to keep them as-is. Use get_agenda or
    search_calendar_events first to find the event_id.

    Args:
        event_id: The event's id, from get_agenda/search_calendar_events.
        summary: New title, if changing.
        start: New ISO 8601 start datetime with timezone, if changing.
        end: New ISO 8601 end datetime with timezone, if changing.
        location: New location text, if changing.
    """
    patch = {}
    if summary:
        patch["summary"] = summary
    if start:
        patch["start"] = {"dateTime": start}
    if end:
        patch["end"] = {"dateTime": end}
    if location:
        patch["location"] = location

    service = _calendar_service()
    result = service.events().patch(calendarId="primary", eventId=event_id, body=patch).execute()
    return json.dumps({"status": "updated", **_event_summary(result)}, ensure_ascii=False)


@mcp.tool()
def delete_calendar_event(event_id: str) -> str:
    """Delete a calendar event. Use get_agenda or search_calendar_events
    first to find the event_id — confirm with the user before deleting.

    Args:
        event_id: The event's id, from get_agenda/search_calendar_events.
    """
    service = _calendar_service()
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return json.dumps({"status": "deleted", "id": event_id})


@mcp.tool()
def search_calendar_events(query: str, start: str = "", end: str = "") -> str:
    """Search calendar events by text — matches against title, description,
    and location. Use this instead of get_agenda when the user is looking
    for a specific event by name rather than asking what's on a given day.

    Args:
        query: Text to search for (e.g. "dentist", "soccer practice").
        start: ISO 8601 start of the search window. Defaults to now.
        end: ISO 8601 end of the search window. Defaults to 7 days after start.
    """
    time_min, time_max = _default_window(start, end)

    service = _calendar_service()
    result = (
        service.events()
        .list(
            calendarId="primary",
            q=query,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = [_event_summary(e) for e in result.get("items", [])]
    return json.dumps(events, ensure_ascii=False)


@mcp.tool()
def suggest_meeting_time(duration_minutes: int, start: str = "", end: str = "") -> str:
    """Suggest open time slots of a given length within a time window, based
    on existing calendar busy periods (free/busy check, not working-hours
    aware — a slot at 2am counts as "free" if nothing's scheduled then).

    Args:
        duration_minutes: How long the slot needs to be, in minutes.
        start: ISO 8601 start of the search window. Defaults to now.
        end: ISO 8601 end of the search window. Defaults to 7 days after start.
    """
    time_min, time_max = _default_window(start, end)
    window_start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
    duration = timedelta(minutes=duration_minutes)

    service = _calendar_service()
    result = (
        service.freebusy()
        .query(body={"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]})
        .execute()
    )
    busy = result["calendars"]["primary"]["busy"]
    busy_intervals = sorted(
        (
            datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
        )
        for b in busy
    )

    suggestions = []
    cursor = window_start
    for busy_start, busy_end in busy_intervals:
        if busy_start - cursor >= duration:
            suggestions.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if window_end - cursor >= duration:
        suggestions.append((cursor, window_end))

    return json.dumps(
        [
            {"start": s.isoformat(), "end": (s + duration).isoformat()}
            for s, _ in suggestions[:5]
        ],
        ensure_ascii=False,
    )


@mcp.tool()
def add_grocery_item(item: str) -> str:
    """Add an item to the household grocery list.

    Args:
        item: What to add (e.g. "milk", "organic eggs").
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, GROCERY_LIST_NAME)
    result = service.tasks().insert(tasklist=list_id, body={"title": item}).execute()
    return json.dumps({"status": "added", "id": result["id"], "item": result.get("title", item)})


@mcp.tool()
def list_groceries() -> str:
    """List everything currently on the household grocery list (not yet bought)."""
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, GROCERY_LIST_NAME)
    result = service.tasks().list(tasklist=list_id, showCompleted=False).execute()
    items = [{"id": t["id"], "item": t.get("title", "")} for t in result.get("items", [])]
    return json.dumps(items, ensure_ascii=False)


@mcp.tool()
def log_chore(chore: str, person: str = "") -> str:
    """Record that a household chore was completed. Use this *after* a chore
    is done, to log it — not to schedule or assign an upcoming one.

    Args:
        chore: What was done (e.g. "took out the trash", "vacuumed living room").
        person: Optional — who did it.
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, CHORES_LIST_NAME)
    body = {"title": chore, "status": "completed"}
    if person:
        body["notes"] = f"Done by: {person}"
    result = service.tasks().insert(tasklist=list_id, body=body).execute()
    return json.dumps(
        {
            "status": "logged",
            "id": result["id"],
            "chore": result.get("title", chore),
            "person": person,
            "completed": result.get("status") == "completed",
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
