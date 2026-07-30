#!/usr/bin/env python3
"""Email notifier — daily digest + user-defined watch rules.

Companion to reminder_scheduler.py, same shape: a `hermes cron` job in
--no-agent mode (see project_plan.md for the household-reminders precedent
this mirrors), reusing that file's `_deliver`/`_log` Telegram-push helpers
directly rather than re-implementing subprocess/token handling a second
time.

Two independent checks each tick, five minutes apart (see project_plan.md
for why the cron job must be created as "every 5m", not "5m" — a bare
duration is a one-shot job, not recurring):

  1. Watch-rule matching: mail received since the last poll (a small
     overlap window + short-lived seen-ids set guard the poll boundary,
     same care as reminder_scheduler.py's lookahead fix) against
     user-defined rules (household_mcp_server.add_email_watch_rule).
     Sender rules are a plain address comparison, no LLM. Topic rules need
     a real judgment call, so this tick's new mail plus the active topic
     rules get batched into a single cloud call — CLOUD_MODEL_MINI, not the
     CLOUD_MODEL the other cloud tools use, since this can fire every five
     minutes rather than on an occasional explicit user request.
  2. Daily digest: once per calendar day, in a morning window (wide enough
     to tolerate a missed/delayed tick), summarizes the previous day's mail
     via the same cloud path and pushes it — independent of watch rules,
     since its purpose is exactly the lower-priority mail the rules don't
     catch.

State (last poll position, recently-notified ids, last digest date) lives
in ~/.hermes/email_notifier_state.json — not committed, created on first
run.
"""
import json
import sys
from datetime import timedelta
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from household_mcp_server import (  # noqa: E402
    CLOUD_MODEL_MINI,
    _call_cloud,
    _gmail_service,
    _headers_dict,
    _load_email_watch_rules,
    get_hermes_home,
)
from reminder_scheduler import _deliver, _log  # noqa: E402

try:
    from hermes_time import now as _hermes_now
except ImportError:
    from datetime import datetime as _datetime, timezone as _timezone

    def _hermes_now():
        return _datetime.now(_timezone.utc)


STATE_PATH = get_hermes_home() / "email_notifier_state.json"
POLL_OVERLAP_SECONDS = 90
MAX_RECENT_IDS = 200
DIGEST_WINDOW_START_HOUR = 7
DIGEST_WINDOW_END_HOUR = 8
DEFAULT_NOTIFY_CHANNEL = "telegram"

# A message that fails classification or delivery gets retried this many
# ticks (~5 min apart) before we give up and stop checking it — bounds
# retry pileup rather than retrying a genuinely dead message forever. See
# _check_watch_rules: without this, a transient failure was silently and
# permanently dropping the notification (a real email about an item for
# sale never notified — traced to a one-off topic-classification miss,
# with no way to tell after the fact whether it was that or a delivery
# failure, since neither path retried or was distinguishable in the
# original code).
MAX_RETRY_ATTEMPTS = 3


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _message_summary(service, message_id: str) -> dict:
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject"])
        .execute()
    )
    headers = _headers_dict(msg.get("payload", {}))
    return {
        "id": message_id,
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "snippet": msg.get("snippet", ""),
    }


def _classify_topic_matches(messages: list[dict], topic_rules: list[dict]) -> tuple[dict, bool]:
    """Ask the cloud model which of this tick's new messages match which
    topic-based watch rules. Returns ({message_id: [rule_id, ...]}, ok).

    ``ok`` is False on any API/parsing problem — deliberately NOT treated
    as "no matches" here. That distinction used to be collapsed (a failure
    silently produced the same empty dict as a genuine no-match verdict),
    which meant a transient hiccup permanently dropped a message with no
    retry, indistinguishable after the fact from the classifier correctly
    saying "no". The caller now retries on ``ok=False`` instead of treating
    silence as a real answer.
    """
    if not messages or not topic_rules:
        return {}, True
    topics_desc = "\n".join(f"- id={r['id']}: {r['topic']}" for r in topic_rules)
    emails_desc = "\n".join(
        f"- id={m['id']}: from {m['from']}, subject: {m['subject']}, preview: {m['snippet']}"
        for m in messages
    )
    prompt = (
        "A household member wants to be notified about emails matching "
        "specific topics they described. Given the topics and the new "
        "emails below, decide which emails (if any) match which topics.\n\n"
        f"Topics:\n{topics_desc}\n\n"
        f"New emails:\n{emails_desc}\n\n"
        "Respond with ONLY a JSON object mapping each matching email's id "
        'to a list of matching topic ids, e.g. {"<email_id>": ["<topic_id>"]}. '
        "Omit emails with no match entirely. No other text, no markdown fences."
    )
    try:
        raw = _call_cloud(prompt, model=CLOUD_MODEL_MINI, effort="low")
        return json.loads(raw), True
    except Exception as exc:
        _log(f"topic classification failed (will retry): {exc}")
        return {}, False


def _check_watch_rules(service, state: dict) -> None:
    now = _hermes_now()
    last_poll_epoch = state.get("last_poll_epoch")
    if last_poll_epoch is None:
        # First run — nothing to backfill against, and we don't want to
        # dump the entire mailbox history as "new" on the very first tick.
        state["last_poll_epoch"] = now.timestamp()
        _log("email_notifier first run — establishing baseline, no backfill")
        return

    after_epoch = int(last_poll_epoch) - POLL_OVERLAP_SECONDS
    result = (
        service.users()
        .messages()
        .list(userId="me", q=f"in:inbox after:{after_epoch}", maxResults=50)
        .execute()
    )
    candidate_ids = [m["id"] for m in result.get("messages", [])]
    state["last_poll_epoch"] = now.timestamp()

    recent_notified = set(state.get("recent_notified_ids", []))
    retry_attempts: dict = dict(state.get("pending_retry", {}))

    # This tick's evaluation set: genuinely new mail from the time window,
    # plus anything still owed a retry from a previous tick's classification
    # or delivery failure. The time-window query alone won't re-surface a
    # retry candidate once more than POLL_OVERLAP_SECONDS has passed, so
    # without explicitly re-including it here a transient failure would
    # silently and permanently drop the message — which is exactly what
    # happened to a real "item for sale" email before this fix.
    new_ids = [mid for mid in candidate_ids if mid not in recent_notified]
    to_check = list(dict.fromkeys(new_ids + list(retry_attempts.keys())))
    if not to_check:
        _log("no new mail since last poll")
        return

    messages = [_message_summary(service, mid) for mid in to_check]
    rules = _load_email_watch_rules()
    sender_rules = [r for r in rules if r.get("kind") == "sender"]
    topic_rules = [r for r in rules if r.get("kind") == "topic"]

    topic_matches, classification_ok = _classify_topic_matches(messages, topic_rules)

    next_retry: dict = {}
    for msg in messages:
        matched_descriptions = []
        sender_addr = parseaddr(msg["from"])[1].lower()
        for rule in sender_rules:
            if rule.get("sender_email", "").lower() == sender_addr:
                matched_descriptions.append(f"from {rule['sender_email']}")

        # Sender matches are deterministic and don't depend on the cloud
        # call, so they're evaluated (and delivered) regardless of whether
        # topic classification succeeded this tick.
        topic_pending = False
        if classification_ok:
            for rule_id in topic_matches.get(msg["id"], []):
                rule = next((r for r in topic_rules if r["id"] == rule_id), None)
                if rule:
                    matched_descriptions.append(f"about '{rule['topic']}'")
        elif topic_rules:
            topic_pending = True

        delivery_failed = False
        if matched_descriptions:
            text = (
                f"📧 New email matching your watch rule ({', '.join(matched_descriptions)}):\n"
                f"From: {msg['from']}\nSubject: {msg['subject']}\n\n{msg['snippet']}"
            )
            if _deliver(text, DEFAULT_NOTIFY_CHANNEL):
                _log(f"notified: {text[:80]}...")
            else:
                delivery_failed = True
                _log(f"delivery failed for {msg['id']} (will retry)")

        if topic_pending or delivery_failed:
            attempts = retry_attempts.get(msg["id"], 0) + 1
            if attempts <= MAX_RETRY_ATTEMPTS:
                next_retry[msg["id"]] = attempts
                continue
            _log(f"giving up on {msg['id']} after {attempts} failed attempt(s)")

        recent_notified.add(msg["id"])

    state["pending_retry"] = next_retry
    state["recent_notified_ids"] = list(recent_notified)[-MAX_RECENT_IDS:]


def _check_daily_digest(service, state: dict) -> None:
    now = _hermes_now()
    if not (DIGEST_WINDOW_START_HOUR <= now.hour < DIGEST_WINDOW_END_HOUR):
        return
    today_str = now.date().isoformat()
    if state.get("last_digest_sent_date") == today_str:
        return

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    after_epoch = int(yesterday_start.timestamp())
    before_epoch = int(today_start.timestamp())

    result = (
        service.users()
        .messages()
        .list(userId="me", q=f"in:inbox after:{after_epoch} before:{before_epoch}", maxResults=100)
        .execute()
    )
    ids = [m["id"] for m in result.get("messages", [])]
    # Set the gate before sending — a delivery failure shouldn't retry the
    # digest all day; it'll just be covered by tomorrow's digest instead.
    state["last_digest_sent_date"] = today_str

    if not ids:
        _log("daily digest: no mail yesterday")
        _deliver("📧 Daily email digest: no new mail yesterday.", DEFAULT_NOTIFY_CHANNEL)
        return

    messages = [_message_summary(service, mid) for mid in ids]
    listing = "\n".join(f"- From {m['from']}: {m['subject']} — {m['snippet']}" for m in messages)
    prompt = (
        "Summarize yesterday's household email inbox in a short, friendly "
        "digest — group related items, call out anything that looks like "
        "it needs action, and skip anything obviously promotional.\n\n"
        f"Yesterday's emails:\n{listing}"
    )
    summary = _call_cloud(prompt, model=CLOUD_MODEL_MINI, effort="low")
    _log(f"daily digest: summarized {len(ids)} message(s)")
    _deliver(f"📧 Daily email digest:\n\n{summary}", DEFAULT_NOTIFY_CHANNEL)


def main() -> None:
    service = _gmail_service()
    state = _load_state()
    _check_watch_rules(service, state)
    _check_daily_digest(service, state)
    _save_state(state)


if __name__ == "__main__":
    main()
