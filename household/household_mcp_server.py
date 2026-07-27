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

Multi-user scheduling (see project_plan.md "Multi-user support" section):
reads a small, operator-managed registry (~/.hermes/family_members.json,
not committed — see USER_GUIDE.md "Add a family member") to resolve names
mentioned in conversation to email addresses, so events can be scheduled
against everyone's real free/busy and delivered as a native Calendar
invite to their own calendar. Deliberately not a tool a conversation can
write to — see that section for why.
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
FAMILY_MEMBERS_PATH = HERMES_HOME / "family_members.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

GROCERY_LIST_NAME = "Groceries"
CHORES_LIST_NAME = "Chores"

# Guards against a known local-model failure mode: long-lived Telegram/Photon
# sessions carry a "Conversation started: <date>" anchor baked into Hermes's
# cached system prompt (see agent/system_prompt.py upstream), which only gets
# refreshed on specific rebuild paths (new session, context compression) —
# NOT on a gateway/container restart, since a continuing session's prompt is
# restored verbatim from the session DB. A session that spans midnight leaves
# the model believing "today" is whatever date the session started, so a
# relative phrase like "today at 3" silently resolves to the wrong day. This
# checks the model's own output against the real clock and hands back the
# correct current time so it can self-correct in the same turn, rather than
# silently writing a past-dated event that the Phase 7 scheduler then fires
# immediately as "overdue".
def _reject_if_past(start_dt: datetime) -> str | None:
    now = datetime.now(timezone.utc)
    if start_dt > now:
        return None
    return (
        f"'{start_dt.isoformat()}' has already passed — the current date/time "
        f"is {now.isoformat()}. Re-derive the requested time relative to that, "
        "not to any date earlier in this conversation, and call this tool "
        "again with the corrected time."
    )

# Phase 8: cloud escalation. The local oMLX model (Hermes's primary model,
# unchanged) handles everything else; only these tools ever leave the
# household — see project_plan.md Phase 8 for why this is deliberately
# task-triggered rather than a complexity-judging router. OpenAI, not
# Anthropic — the household has free daily token usage on OpenAI's models
# and this project's usage is low-volume enough to stay well inside it.
CLOUD_MODEL = "gpt-5.4"

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


def _normalize_due(value: str) -> str:
    """Google Tasks' `due` field is RFC 3339, but the API only ever reads
    back the date portion — any time-of-day given is discarded. Zero-fill
    a bare date to UTC midnight so the date itself can't shift by a day
    depending on the caller's timezone offset.
    """
    if "T" in value:
        return _with_timezone(value)
    return f"{value}T00:00:00Z"


def _event_summary(e: dict) -> dict:
    return {
        "id": e["id"],
        "summary": e.get("summary", "(no title)"),
        "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
        "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
        "location": e.get("location", ""),
    }


def _load_family_members() -> list[dict]:
    if not FAMILY_MEMBERS_PATH.exists():
        return []
    return json.loads(FAMILY_MEMBERS_PATH.read_text()).get("family_members", [])


def _resolve_emails(names: list[str]) -> tuple[list[str], list[str]]:
    """Resolve family-member names to their registered emails.

    Matching is case-insensitive on the full name only (no fuzzy/partial
    matching) — deliberately strict, since a silent mismatch here means
    either the wrong person gets a calendar invite or a real person's
    availability gets skipped without anyone noticing. Returns
    (resolved_emails, names_that_did_not_match).
    """
    by_name = {m["name"].strip().lower(): m["email"] for m in _load_family_members()}
    emails, unresolved = [], []
    for name in names:
        email = by_name.get(name.strip().lower())
        if email:
            emails.append(email)
        else:
            unresolved.append(name)
    return emails, unresolved


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
def list_family_members() -> str:
    """List the household's registered family members by name. Use this
    before scheduling something on someone's behalf (add_calendar_event's
    attendees, or suggest_meeting_time's people) if you're at all unsure
    how their name is spelled/registered — an unrecognized name is reported
    back rather than silently dropped, but checking first avoids a
    round-trip.
    """
    return json.dumps([{"name": m["name"]} for m in _load_family_members()], ensure_ascii=False)


@mcp.tool()
def add_calendar_event(
    summary: str, start: str, end: str, location: str = "", attendees: list[str] | None = None
) -> str:
    """Create a calendar event.

    Args:
        summary: Event title.
        start: ISO 8601 start datetime with timezone (e.g. 2026-07-27T15:00:00Z).
        end: ISO 8601 end datetime with timezone.
        location: Optional location text.
        attendees: Names of registered family members who need to attend,
            besides whoever is asking. Each gets a real Google Calendar
            invite sent to their registered email — this is how the event
            ends up on their own personal calendar too, not just the
            household's. A name that isn't registered is reported back in
            the result rather than silently skipped — tell the user.
    """
    start_dt = datetime.fromisoformat(_with_timezone(start).replace("Z", "+00:00"))
    past_error = _reject_if_past(start_dt)
    if past_error:
        return json.dumps({"status": "error", "error": past_error})

    event = {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    if location:
        event["location"] = location

    emails, unresolved = _resolve_emails(attendees or [])
    if emails:
        event["attendees"] = [{"email": e} for e in emails]

    service = _calendar_service()
    result = (
        service.events()
        .insert(calendarId="primary", body=event, sendUpdates="all" if emails else "none")
        .execute()
    )
    response = {
        "status": "created",
        "id": result["id"],
        "summary": result.get("summary", ""),
        "htmlLink": result.get("htmlLink", ""),
    }
    if unresolved:
        response["unresolved_attendee_names"] = unresolved
    return json.dumps(response, ensure_ascii=False)


REMINDER_CHANNELS = {"telegram", "photon"}


@mcp.tool()
def set_reminder(summary: str, when: str, channel: str = "telegram") -> str:
    """Set a reminder for a specific time.

    Creates a tagged calendar event that the household reminder scheduler
    (a separate process, Phase 7) polls for and delivers as a proactive
    message when it comes due.

    Args:
        summary: What to be reminded about.
        when: ISO 8601 datetime with timezone (e.g. 2026-07-27T15:00:00Z).
        channel: Where to deliver it — "telegram" (default) or "photon"
            (iMessage). Use "photon" when the user specifically asks for an
            iMessage/text reminder rather than Telegram. Voice/phone-call
            delivery isn't supported yet — if asked, say so rather than
            silently falling back to another channel.
    """
    if channel not in REMINDER_CHANNELS:
        return json.dumps(
            {
                "status": "error",
                "error": f"Unknown channel '{channel}'. Supported: {sorted(REMINDER_CHANNELS)}.",
            }
        )

    start_dt = datetime.fromisoformat(_with_timezone(when).replace("Z", "+00:00"))
    past_error = _reject_if_past(start_dt)
    if past_error:
        return json.dumps({"status": "error", "error": past_error})

    end_dt = start_dt + timedelta(minutes=5)
    event = {
        "summary": f"Reminder: {summary}",
        "description": REMINDER_TAG,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
        # extendedProperties.private is invisible in the UI (unlike the
        # description tag above, which is just for a human glancing at the
        # event) — the Phase 7 reminder scheduler filters on these via the
        # Calendar API's privateExtendedProperty query param, and flips
        # hermesReminderSent after delivering so it isn't sent twice.
        "extendedProperties": {
            "private": {
                "hermesReminder": "true",
                "hermesReminderSent": "false",
                "hermesReminderChannel": channel,
            }
        },
    }

    service = _calendar_service()
    result = service.events().insert(calendarId="primary", body=event).execute()
    return json.dumps(
        {
            "status": "reminder set",
            "id": result["id"],
            "summary": result.get("summary", ""),
            "when": when,
            "channel": channel,
        }
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
def suggest_meeting_time(
    duration_minutes: int, people: list[str] | None = None, start: str = "", end: str = ""
) -> str:
    """Suggest open time slots of a given length within a time window, based
    on existing calendar busy periods (free/busy check, not working-hours
    aware — a slot at 2am counts as "free" if nothing's scheduled then).

    Args:
        duration_minutes: How long the slot needs to be, in minutes.
        people: Names of registered family members who also need to be free
            for this, besides the household calendar. Use
            list_family_members first if you're unsure a name is
            registered. A name that isn't registered, or whose calendar
            hasn't been shared with the household yet, is reported back
            separately rather than silently treated as free — tell the
            user rather than assuming the suggested slots account for them.
        start: ISO 8601 start of the search window. Defaults to now.
        end: ISO 8601 end of the search window. Defaults to 7 days after start.
    """
    time_min, time_max = _default_window(start, end)
    window_start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
    duration = timedelta(minutes=duration_minutes)

    emails, unresolved = _resolve_emails(people or [])
    calendar_ids = ["primary"] + emails

    service = _calendar_service()
    result = (
        service.freebusy()
        .query(body={"timeMin": time_min, "timeMax": time_max, "items": [{"id": cid} for cid in calendar_ids]})
        .execute()
    )
    calendars = result.get("calendars", {})

    # A calendar the household account can't see (not shared, or shared
    # without even free/busy visibility) comes back with an "errors" entry
    # instead of a "busy" list — checked explicitly so an unshared calendar
    # is reported, not silently treated as wide open.
    unavailable = [cid for cid, data in calendars.items() if data.get("errors")]

    busy_intervals = sorted(
        (
            datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
        )
        for cid, data in calendars.items()
        if not data.get("errors")
        for b in data.get("busy", [])
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
        {
            "suggestions": [
                {"start": s.isoformat(), "end": (s + duration).isoformat()}
                for s, _ in suggestions[:5]
            ],
            "unresolved_names": unresolved,
            "could_not_check_availability_for": unavailable,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def list_task_lists() -> str:
    """List the names of every household task list that exists — the
    grocery list, the chores list, and any ad-hoc list a family member has
    created (e.g. "Vacation packing"). Use this before adding to a new list
    by name (add_list_item creates one automatically on first use) to check
    whether a similarly-named list already exists — avoids ending up with
    both "Vacation packing" and "Packing for vacation" as separate lists
    from a small wording difference.
    """
    service = _tasks_service()
    result = service.tasklists().list(maxResults=100).execute()
    return json.dumps([tl["title"] for tl in result.get("items", [])], ensure_ascii=False)


@mcp.tool()
def add_list_item(item: str, list_name: str = GROCERY_LIST_NAME, due: str = "") -> str:
    """Add an item to a household task list. Defaults to the grocery list —
    pass list_name for any other list (e.g. "Chores", "Vacation packing").
    Adding to the chores list creates a pending to-do — use
    complete_list_item on it once it's done. Creates the list automatically
    on first use if it doesn't exist yet; use list_task_lists first if
    you're unsure whether a similarly-named list already exists.

    Args:
        item: What to add (e.g. "milk", "organic eggs", "take out the trash").
        list_name: Which list to add to. Defaults to the grocery list.
        due: Optional due date (e.g. "2026-07-31"). Google Tasks only
            tracks the date, not a time of day — for something that should
            fire a message at a specific time, use set_reminder instead.
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, list_name)
    body = {"title": item}
    if due:
        body["due"] = _normalize_due(due)
    result = service.tasks().insert(tasklist=list_id, body=body).execute()
    response = {"status": "added", "id": result["id"], "item": result.get("title", item), "list": list_name}
    if result.get("due"):
        response["due"] = result["due"]
    return json.dumps(response)


@mcp.tool()
def set_due_date(item_id: str, due: str, list_name: str = GROCERY_LIST_NAME) -> str:
    """Set or change the due date on an existing task-list item. Google
    Tasks only tracks the date, not a time of day — for something that
    should fire a message at a specific time, use set_reminder instead.
    Use list_items first to find the item's id.

    Args:
        item_id: The item's id, from list_items.
        due: New due date (e.g. "2026-07-31").
        list_name: Which list the item is on. Defaults to the grocery list.
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, list_name)
    result = service.tasks().patch(
        tasklist=list_id, task=item_id, body={"due": _normalize_due(due)}
    ).execute()
    return json.dumps({"status": "updated", "id": item_id, "list": list_name, "due": result.get("due", "")})


@mcp.tool()
def remove_list_item(item_id: str, list_name: str = GROCERY_LIST_NAME) -> str:
    """Delete an item from a household task list outright, with no record
    left behind. Use this to clear an item that's no longer wanted (a
    grocery item you changed your mind about, a chore that turned out not
    to be needed) — use complete_list_item instead if the item was done
    and should stay in that list's history (chores in particular, since
    summarize_past_week reads completed chores, not deleted ones). Use
    list_items first to find the item's id — matching is by id, not by
    name, so a duplicate (e.g. "oat milk" listed twice) can be removed
    once while leaving the other in place. Defaults to the grocery list —
    pass list_name for any other list.

    Args:
        item_id: The item's id, from list_items.
        list_name: Which list to remove from. Defaults to the grocery list.
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, list_name)
    service.tasks().delete(tasklist=list_id, task=item_id).execute()
    return json.dumps({"status": "removed", "id": item_id, "list": list_name})


@mcp.tool()
def complete_list_item(item_id: str, list_name: str = GROCERY_LIST_NAME, notes: str = "") -> str:
    """Mark an item on a household task list as done, keeping it in that
    list's history — use this for a pending chore (added via add_list_item)
    that's now finished, since summarize_past_week reads completed chores
    for its weekly recap and a deleted item wouldn't show up there. Use
    remove_list_item instead if the item should just disappear with no
    record. Use list_items first to find the item's id.

    Args:
        item_id: The item's id, from list_items.
        list_name: Which list the item is on. Defaults to the grocery list.
        notes: Optional note to attach (e.g. "Done by: Sam").
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, list_name)
    body = {"status": "completed"}
    if notes:
        body["notes"] = notes
    result = service.tasks().patch(tasklist=list_id, task=item_id, body=body).execute()
    return json.dumps(
        {"status": "completed", "id": item_id, "list": list_name, "item": result.get("title", "")}
    )


@mcp.tool()
def list_items(list_name: str = GROCERY_LIST_NAME) -> str:
    """List everything currently on a household task list (not yet
    completed/bought). Defaults to the grocery list — pass list_name for
    any other list. Use list_task_lists first if you're unsure a list by
    this name exists.

    Args:
        list_name: Which list to read. Defaults to the grocery list.
    """
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, list_name)
    result = service.tasks().list(tasklist=list_id, showCompleted=False).execute()
    items = []
    for t in result.get("items", []):
        entry = {"id": t["id"], "item": t.get("title", "")}
        if t.get("due"):
            entry["due"] = t["due"]
        items.append(entry)
    return json.dumps(items, ensure_ascii=False)


@mcp.tool()
def create_task_list(list_name: str) -> str:
    """Create a new, empty household task list by name (e.g. "Home
    Projects"). Not required before add_list_item, which creates a list
    automatically on first use — use this only when the user wants an
    empty list started ahead of any items being added to it. Idempotent by
    name: if a list with this exact title already exists, returns it
    rather than creating a duplicate (Google Tasks does not enforce unique
    titles, so a second insert with the same title would otherwise silently
    create a second, separate list). Use list_task_lists first if you're
    unsure whether a similarly-worded list already exists.

    Args:
        list_name: The new list's title (e.g. "Home Projects").
    """
    service = _tasks_service()
    result = service.tasklists().list(maxResults=100).execute()
    for tl in result.get("items", []):
        if tl["title"] == list_name:
            return json.dumps({"status": "already exists", "id": tl["id"], "list": list_name})
    created = service.tasklists().insert(body={"title": list_name}).execute()
    return json.dumps({"status": "created", "id": created["id"], "list": list_name})


@mcp.tool()
def delete_task_list(list_name: str) -> str:
    """Delete an entire household task list, including every item on it.
    This is permanent and cannot be undone — confirm with the user before
    calling it. Not the same as remove_list_item, which deletes one item
    while leaving the list itself in place. Use list_task_lists first to
    confirm the exact name.

    Args:
        list_name: The list's exact title, from list_task_lists.
    """
    service = _tasks_service()
    result = service.tasklists().list(maxResults=100).execute()
    for tl in result.get("items", []):
        if tl["title"] == list_name:
            service.tasklists().delete(tasklist=tl["id"]).execute()
            return json.dumps({"status": "deleted", "list": list_name})
    return json.dumps({"status": "error", "error": f"No task list named '{list_name}' found."})


def _get_openai_cloud_api_key() -> str:
    """Read the cloud-escalation API key. Checks the environment first, but
    falls back to reading ~/.hermes/.env directly — relying on this MCP
    subprocess consistently inheriting the gateway process's environment
    turned out to be unreliable in practice (some spawns had it, some
    didn't, traced via project_plan.md Phase 8's live testing), so this
    reads the same file Hermes itself loads it from, deterministically,
    every call.
    """
    key = os.environ.get("OPENAI_CLOUD_API_KEY")
    if key:
        return key
    for line in (HERMES_HOME / ".env").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("OPENAI_CLOUD_API_KEY="):
            return stripped.partition("=")[2].strip()
    raise RuntimeError("OPENAI_CLOUD_API_KEY not found in environment or ~/.hermes/.env")


def _call_cloud(prompt: str, *, tools: list | None = None, effort: str = "high") -> str:
    """Call the cloud model directly for a request explicitly routed to the
    cloud tier (see project_plan.md Phase 8) — the local oMLX model never
    sees this prompt or its answer. Returns the response text, or a JSON
    error envelope if the request came back empty.

    effort defaults to "high" — fine for a single reasoning pass (the
    plan/summarize tools below). Live testing found "high" effort combined
    with the web_search tool (multiple search rounds, each adding more
    reasoning) can take 5+ minutes and blow past Hermes's MCP tool-call
    timeout; research_topic passes "medium" to stay responsive.
    """
    import openai

    # Deliberately not the plain OPENAI_API_KEY env var — docker-compose.yml
    # already claims that name as a placeholder for Hermes's own local-model
    # OpenAI-compatible client. See the .env comment next to
    # OPENAI_CLOUD_API_KEY for how this was found.
    client = openai.OpenAI(api_key=_get_openai_cloud_api_key())
    kwargs = dict(
        model=CLOUD_MODEL,
        input=prompt,
        reasoning={"effort": effort},
    )
    if tools:
        kwargs["tools"] = tools
    response = client.responses.create(**kwargs)

    return response.output_text or json.dumps(
        {"status": "error", "error": "The cloud model returned no text response."}
    )


@mcp.tool()
def research_topic(topic: str) -> str:
    """Research something using a more capable cloud model with live web
    search. Use this whenever the user explicitly asks you to "research"
    something (e.g. "research the history of Mount Rushmore", "can you
    research X", "look into Y for me") — that phrasing is this household's
    signal to escalate past your own local knowledge. Do not use this for
    routine household questions (calendar, groceries, chores) or ordinary
    conversation — only when research/deep-lookup is explicitly requested.

    Args:
        topic: What to research, in the user's own words.
    """
    return _call_cloud(
        f"Research the following and give a clear, well-sourced answer:\n\n{topic}",
        tools=[{"type": "web_search"}],
        effort="medium",
    )


@mcp.tool()
def plan_upcoming_week() -> str:
    """Produce a plan for the coming week using a more capable cloud model.
    Always call this tool — instead of answering directly — whenever the
    user asks for help planning the week ahead, a weekly game plan, or a
    similar "what should this week look like" request. This is one of a
    small, fixed set of household tasks that always escalate to the cloud
    model regardless of how routine the request sounds.
    """
    agenda = get_agenda()
    groceries = list_items(GROCERY_LIST_NAME)
    prompt = (
        "You are helping a household plan the coming week. Given the "
        "calendar agenda and grocery list below, write a short, practical "
        "plan for the week: flag any scheduling conflicts or tight days, "
        "and suggest anything worth prepping ahead of time.\n\n"
        f"Calendar (next 7 days):\n{agenda}\n\n"
        f"Grocery list:\n{groceries}"
    )
    return _call_cloud(prompt)


@mcp.tool()
def summarize_past_week() -> str:
    """Summarize the past week using a more capable cloud model. Always
    call this tool — instead of answering directly — whenever the user
    asks for a recap or summary of the past week (what happened, chores
    done, etc). This is one of a small, fixed set of household tasks that
    always escalate to the cloud model regardless of how routine the
    request sounds.
    """
    now = datetime.now(timezone.utc)
    agenda = get_agenda(start=(now - timedelta(days=7)).isoformat(), end=now.isoformat())

    service = _tasks_service()
    chores_list_id = _find_or_create_tasklist(service, CHORES_LIST_NAME)
    chores = service.tasks().list(tasklist=chores_list_id, showCompleted=True, showHidden=True).execute()
    completed = [t.get("title", "") for t in chores.get("items", []) if t.get("status") == "completed"]

    prompt = (
        "You are summarizing the past week for a household. Given the "
        "calendar events and completed chores below, write a short, "
        "friendly recap of the week.\n\n"
        f"Calendar (past 7 days):\n{agenda}\n\n"
        f"Chores completed:\n{json.dumps(completed, ensure_ascii=False)}"
    )
    return _call_cloud(prompt)


if __name__ == "__main__":
    mcp.run(transport="stdio")
