# Features & Validation

A living, succinct reference: what the assistant can actually do right now,
and a short set of manual scenarios to demo/validate it end-to-end. Not
exhaustive — see `project_plan.md` for the full phase-by-phase build history
and everything still in progress.

Before a demo, (re)run the seed script so scenarios have realistic data to
work against rather than an empty household:

```
docker exec hermes-sandbox /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 \
    /home/hermes/household/seed_demo_data.py
```

---

## Capabilities

**Interaction channels**
- Telegram — bot, DM and group-capable
- iMessage — via Photon (free tier: each family member gets their own assigned number, not one shared household number)
- Voice calls — Twilio number, ElevenLabs Conversational AI (barge-in works)
- SMS — not yet live, blocked on Twilio A2P 10DLC carrier registration (Phase 4b)

**General**
- Conversational Q&A via the local model (oMLX, currently `Qwen3.6-35B-A3B-MLX-6bit`) — no cloud calls for routine questions
- Sandboxed terminal / code execution (no host filesystem access) — README Scenario 4

**Calendar** (`household_mcp_server.py`, 7 tools)
- `get_agenda` — what's scheduled in a time range
- `add_calendar_event` — create an event
- `update_calendar_event` — change an existing event (only the fields you specify)
- `delete_calendar_event` — remove an event
- `search_calendar_events` — find an event by text
- `suggest_meeting_time` — find open slots (free/busy based, not working-hours aware)
- `set_reminder` — create a tagged reminder event — **proactive delivery isn't built yet** (Phase 7); this only creates the calendar entry today, nothing fires when it comes due

**Tasks / household lists** (`household_mcp_server.py`, 3 tools)
- `add_grocery_item`
- `list_groceries`
- `log_chore` — records a chore as already completed (a log, not a pending-task tracker). **No read/list tool for chore history yet** — verify these via the Google Tasks app, not by asking the agent to recall them

**Not yet built**
- Proactive reminder delivery (Phase 7)
- SMS (Phase 4b, blocked on carrier registration)
- Cloud escalation to Anthropic for complex queries (Phase 8)
- Chore history read-back (no `list_chores` tool)

---

## Demo scenarios

Each one names a channel, exercises a real capability, and verifies against
the actual Google app — not just the agent's word for it.

1. **Telegram — check availability, then create an event**
   Ask "am I free Tuesday afternoon?" — should reference the seeded dentist appointment. Then: "add a coffee with Alex on Wednesday at 3pm." **Verify:** the new event appears in [Google Calendar](https://calendar.google.com) with the right time and title.

2. **Voice call — grocery list, add, read back**
   Call the household number. Ask "what's on my grocery list?" Then "add paper towels." Then "can you read that back to confirm?" **Verify:** open [Google Tasks](https://tasks.google.com) and confirm "Paper towels" is on the Groceries list.

3. **iMessage — find and reschedule an event**
   Text "when is Sam's soccer practice?" (search). Then "can you push that to 6pm instead?" (update). **Verify:** the time change is reflected in Google Calendar.

4. **Telegram — log a completed chore**
   "Log that I did the dishes." **Verify:** open Google Tasks → Chores list, confirm a completed "Washed the dishes"-style entry exists with today's date. (Don't ask the agent to recall chore history back — that read path doesn't exist yet, see Capabilities above.)

5. **Voice call — suggest a meeting time**
   "Can you find me a free 30-minute slot this week?" **Verify:** cross-check the suggested time against Google Calendar — it shouldn't overlap any seeded event.

6. **iMessage — delete an event**
   "Cancel the haircut on Friday." (search, then delete). **Verify:** the event is gone from Google Calendar.

7. **Cross-channel state parity**
   Add a grocery item via Telegram ("add butter to the list"). Then, on a call or iMessage, ask what's on the grocery list. **Verify:** butter shows up — same backend, regardless of which channel wrote it.

8. **General Q&A (non-household)**
   Ask any channel something with no calendar/tasks angle at all (e.g. "what's a good substitute for buttermilk?"). **Verify:** confirms the base conversational path still works normally alongside tool availability — tool access doesn't force every answer through a tool call.

9. **Sandboxed execution (README Scenario 4)**
   Ask it to write and run a short script (e.g. "write a script that lists the numbers 1–20 divisible by 3"). **Verify:** it runs and returns real output; separately, `ls ~/Documents`-style prompts should confirm it still can't see any host filesystem — the isolation guarantee from Phase 2, still holding.

**Not yet demoable, roadmap-flagged rather than omitted:**
- *"Text me/call me when it's time for the reminder I set"* — `set_reminder` creates the calendar entry today, but nothing proactively fires yet. Will become a real scenario once Phase 7 (reminder scheduler) ships.
- *Anything over SMS* — blocked on Twilio A2P 10DLC registration (Phase 4b).
