#!/usr/bin/env python3
"""Seed realistic demo data for the household MCP server.

Run manually before a demo to (re)populate calendar events, groceries, and
chore-log entries relative to "today". Makes FEATURES_AND_VALIDATION.md's
scenarios demonstrable against real, lived-in-looking data instead of an
empty household.

Not deduped — re-running adds a second copy of everything. Fine for
refreshing dates before a demo (delete the old items first in the Google
apps if that matters), not meant to be idempotent.

Usage (from inside the container):
    /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 \\
        /home/hermes/household/seed_demo_data.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from household_mcp_server import (  # noqa: E402
    CHORES_LIST_NAME,
    GROCERY_LIST_NAME,
    REMINDER_TAG,
    _calendar_service,
    _find_or_create_tasklist,
    _tasks_service,
)


def _at(day_offset: int, hour: int, minute: int) -> datetime:
    """A datetime `day_offset` days from today (UTC midnight), at hour:minute."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=day_offset, hours=hour, minutes=minute)


def seed_calendar() -> None:
    service = _calendar_service()
    events = [
        {"summary": "Team standup", "start": _at(1, 9, 0), "end": _at(1, 9, 30)},
        {
            "summary": "Dentist appointment — Sam",
            "start": _at(2, 14, 0),
            "end": _at(2, 14, 45),
            "location": "Bright Smiles Dental",
        },
        {
            "summary": "Soccer practice — Sam",
            "start": _at(3, 17, 0),
            "end": _at(3, 18, 30),
            "location": "Lincoln Park Fields",
        },
        {
            "summary": "Parent-teacher conference",
            "start": _at(4, 16, 0),
            "end": _at(4, 16, 30),
            "location": "Roosevelt Elementary, Room 12",
        },
        {
            "summary": "Haircut",
            "start": _at(5, 10, 0),
            "end": _at(5, 10, 30),
            "location": "Main St Barbershop",
        },
        {
            "summary": "Birthday party planning",
            "start": _at(6, 11, 0),
            "end": _at(6, 13, 0),
            "location": "Home",
        },
        {
            "summary": "Family dinner at Grandma's",
            "start": _at(7, 18, 0),
            "end": _at(7, 20, 0),
            "location": "Grandma's house",
        },
    ]
    for e in events:
        body = {
            "summary": e["summary"],
            "start": {"dateTime": e["start"].isoformat()},
            "end": {"dateTime": e["end"].isoformat()},
        }
        if "location" in e:
            body["location"] = e["location"]
        result = service.events().insert(calendarId="primary", body=body).execute()
        print(f"  + event: {result.get('summary')} ({e['start'].isoformat()})")

    # One tagged reminder, matching set_reminder's own convention, so the
    # Phase 7 scheduler (once built) has something real to poll for.
    reminder_start = _at(1, 17, 0)
    reminder_end = reminder_start + timedelta(minutes=5)
    result = (
        service.events()
        .insert(
            calendarId="primary",
            body={
                "summary": "Reminder: Pick up dry cleaning",
                "description": REMINDER_TAG,
                "start": {"dateTime": reminder_start.isoformat()},
                "end": {"dateTime": reminder_end.isoformat()},
                "extendedProperties": {
                    "private": {"hermesReminder": "true", "hermesReminderSent": "false"}
                },
            },
        )
        .execute()
    )
    print(f"  + reminder: {result.get('summary')} ({reminder_start.isoformat()})")


def seed_groceries() -> None:
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, GROCERY_LIST_NAME)
    items = [
        "Bread",
        "Bananas",
        "Coffee",
        "Chicken breast",
        "Olive oil",
        "Baby spinach",
        "Orange juice",
        "Pasta",
    ]
    for item in items:
        result = service.tasks().insert(tasklist=list_id, body={"title": item}).execute()
        print(f"  + grocery: {result.get('title')}")


def seed_chores() -> None:
    service = _tasks_service()
    list_id = _find_or_create_tasklist(service, CHORES_LIST_NAME)
    # (title, person, days_ago)
    chores = [
        ("Mowed the lawn", "Sam", 6),
        ("Cleaned the bathroom", "Antoine", 5),
        ("Loaded the dishwasher", "", 3),
        ("Vacuumed living room", "Sam", 2),
        ("Washed the dishes", "Antoine", 1),
    ]
    for title, person, days_ago in chores:
        completed_at = (
            (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        )
        body = {"title": title, "status": "completed", "completed": completed_at}
        if person:
            body["notes"] = f"Done by: {person}"
        result = service.tasks().insert(tasklist=list_id, body=body).execute()
        print(f"  + chore: {result.get('title')} ({days_ago}d ago)")


if __name__ == "__main__":
    print("Seeding calendar events...")
    seed_calendar()
    print("Seeding groceries...")
    seed_groceries()
    print("Seeding chore log...")
    seed_chores()
    print("Done.")
