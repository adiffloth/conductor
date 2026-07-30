#!/usr/bin/env python3
"""Seed realistic demo data for the household MCP server.

Run manually before a demo to (re)populate calendar events, groceries,
chore-log entries, and inbox emails, relative to "today". Makes
FEATURES_AND_VALIDATION.md's scenarios demonstrable against real,
lived-in-looking data instead of an empty household.

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
    _gmail_service,
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


def _raw_email(from_addr: str, from_name: str, subject: str, body: str, date: datetime) -> str:
    """Base64url-encoded RFC 2822 message for Gmail's messages.insert.

    Deliberately not household_mcp_server.send_email — that sends live via
    SMTP from the household's own address in real time, neither of which
    seed data wants: these need a synthetic "From" (so search/read demos
    have more than one sender to look at) and a backdated Date header (so
    the daily-digest demo has real "yesterday" mail to summarize), and
    insert() stores a message directly in the mailbox without actually
    delivering anything.
    """
    import base64
    from email.message import EmailMessage
    from email.utils import format_datetime

    message = EmailMessage()
    message["From"] = f"{from_name} <{from_addr}>"
    message["To"] = "roseyfamilyconductor@gmail.com"
    message["Subject"] = subject
    message["Date"] = format_datetime(date)
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def seed_emails() -> None:
    service = _gmail_service()
    # (from_addr, from_name, subject, body, day_offset, hour, minute)
    emails = [
        (
            "sam.rosey@example.com",
            "Sam Rosey",
            "Can you pick me up a bit early today?",
            "Practice is ending at 5 instead of 5:30 — any chance someone can grab me then instead?",
            0,
            13,
            12,
        ),
        (
            "buyer.jt@example-marketplace.com",
            "JT (Marketplace)",
            "Interested in your patio table listing",
            "Hi! Is the patio table still available? Would you be open to $60 and I can pick up this weekend?",
            0,
            10,
            40,
        ),
        (
            "office@roosevelt-elementary.example.edu",
            "Roosevelt Elementary",
            "Reminder: school closed Monday for teacher training",
            "This is a reminder that Roosevelt Elementary will be closed this coming Monday for a "
            "scheduled teacher training day. Regular classes resume Tuesday.",
            -1,
            9,
            5,
        ),
        (
            "grandma.rosey@example.com",
            "Grandma",
            "Looking forward to Sunday dinner!",
            "Can't wait to see everyone this Sunday. Should I bring my green bean casserole, or is "
            "someone else already on dessert duty?",
            -1,
            16,
            20,
        ),
        (
            "newsletter@fitclub-example.com",
            "FitClub Weekly",
            "This week's class schedule + a new instructor!",
            "Check out this week's lineup of classes and meet our newest yoga instructor. Book your "
            "spot before it fills up!",
            -1,
            8,
            0,
        ),
        (
            "billing@citypower-example.com",
            "City Power & Utilities",
            "Your latest bill is ready to view",
            "Your most recent utility bill is now available online. Amount due: $142.87, due in 3 weeks.",
            -2,
            7,
            30,
        ),
    ]
    for from_addr, from_name, subject, body, day_offset, hour, minute in emails:
        date = _at(day_offset, hour, minute)
        raw = _raw_email(from_addr, from_name, subject, body, date)
        service.users().messages().insert(
            userId="me",
            body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
            internalDateSource="dateHeader",
        ).execute()
        print(f"  + email: {subject!r} from {from_name} ({day_offset}d)")


if __name__ == "__main__":
    print("Seeding calendar events...")
    seed_calendar()
    print("Seeding groceries...")
    seed_groceries()
    print("Seeding chore log...")
    seed_chores()
    print("Seeding emails...")
    seed_emails()
    print("Done.")
