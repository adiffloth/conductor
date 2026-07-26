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
- Ask ordinary questions and hold a normal conversation — answered by a local model running on the household's own hardware, not the cloud
- Ask it to write and run a small script (e.g. a quick calculation or data-formatting task) in a sandboxed environment that can't see or touch anything else on the host computer

**Calendar**
- Ask what's on the calendar — a specific day, a time range, or "am I free at X?"
- Add a new event
- Change an existing event — move the time, rename it, update the location
- Cancel/remove an event
- Find a specific event by name ("when is Sam's soccer practice?")
- Ask for a good time for something — it'll suggest an open slot based on what's already on the calendar
- Set a reminder for a specific time — it'll proactively message you in Telegram when it comes due, without you having to ask (checked every 5 minutes, so expect up to a few minutes of drift, not second-perfect timing)

**Household lists**
- Add something to the grocery list
- Ask what's currently on the grocery list
- Tell it a chore was just done, so there's a record of it — **note: there's currently no way to ask it to read that history back**; checking what's been done means looking at the list directly in the Google Tasks app

**Not yet built**
- Texting the household (SMS) isn't live yet — blocked on carrier registration (Phase 4b)
- No automatic hand-off to a more powerful cloud model for harder questions yet (Phase 8)
- No way to ask the assistant what chores have been done recently — only add new entries
- Reminder delivery currently always goes to Telegram, regardless of which channel the reminder was set from

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

10. **Voice call — set a reminder and wait for it to actually fire**
    Call and say "remind me in a few minutes to check on dinner." **Verify:** the event shows up in Google Calendar immediately (titled "Reminder: ..."); separately, with no further prompting, an unprompted Telegram message arrives once it comes due — the whole point of this one is that *nobody has to check back*, it just shows up (checked every 5 minutes, so allow a few minutes of drift).

**Not yet demoable, roadmap-flagged rather than omitted:**
- *Anything over SMS* — blocked on Twilio A2P 10DLC registration (Phase 4b).
