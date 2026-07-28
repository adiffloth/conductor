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
- **Invoke a more capable cloud model when it's actually warranted** (Phase 8) — deliberately task-triggered, not a complexity-judging router, on the theory that a fixed, testable set of triggers beats the local model guessing whether a question is "hard enough" to escalate:
  - Say "research X" (or a clear synonym — "look into X") and it hands the question to a cloud model with real, live web search, instead of answering from its own training data
  - Ask it to plan out the upcoming week, or summarize the past week — both always run on the cloud model, pulling in real calendar/grocery/chore data first, regardless of how routine the request sounds
  - Everything else — including ordinary Q&A — stays on the local model; this is not automatic for arbitrary "hard" questions

**Calendar**
- Ask what's on the calendar — a specific day, a time range, or "am I free at X?"
- Add a new event
- Change an existing event — move the time, rename it, update the location
- Cancel/remove an event
- Find a specific event by name ("when is Sam's soccer practice?")
- Ask for a good time for something — it'll suggest an open slot based on what's already on the calendar. Name other registered family members who also need to be free ("a slot tomorrow that works for me and Sam") and it checks their real calendars too, not just the household's — see "Multi-user scheduling" below for how a person gets registered
- Set a reminder for a specific time — it'll proactively message you when it comes due, without you having to ask. Defaults to Telegram, but you can ask for it by iMessage instead ("send me an iMessage reminder at 4:30 to call Grandma"). Delivery lands within a couple of seconds of the requested time, not just "sometime in the next 5 minutes" — the scheduler sleeps to the exact moment rather than firing whenever its poll happens to catch it. Reminder-by-phone-call isn't built yet (Phase 10, future)

**Multi-user scheduling** (Phase 8b)
- Create an event that other family members need to attend — name them ("schedule this with Sam and Jamie") and they're added as real Google Calendar attendees, so it shows up on their own personal calendar with a native invite, not just the household's
- Availability checks (`suggest_meeting_time`) account for named family members' real calendars, not just the household's — and say so explicitly when someone's availability *can't* be checked (not registered, or hasn't shared their calendar yet) rather than guessing
- Ask who's registered ("who are the family members?")
- **"For me" resolves automatically over DM** (Telegram or iMessage/Photon), for anyone registered with their channel ID — "am I free tomorrow?" or "add this for me" resolves to that person the same way a named third party would, with no need to say their own name. Only works one-to-one in a direct message, not in a group chat (Hermes's own group-chat sender-tagging already exists and is left as-is). A registered person without a channel ID on file — or someone not registered at all — falls back to today's behavior (household-calendar-only, no personal resolution) rather than guessing.

**Household lists**
- Not just groceries — any named list. "Add oat milk to the grocery list" and "create a Vacation packing list with sunscreen and a swimsuit" work the same way; a new list is created automatically the first time something's added to it
- Ask what's on any list, add to it, remove an item outright (no record kept), or mark an item done while keeping it in that list's history — the last two are different tools on purpose: removing a grocery item you changed your mind about shouldn't look the same as completing a chore
- Chores work as a real to-do list, not just a log — add one ahead of time ("add take out the trash to chores") and mark it done later, or log something as already-done in one shot if it was never on the list to begin with ("log that I mowed the lawn"). Either way it shows up in the weekly summary
- Set a due date on any list item (date only, not a time of day — for something that needs to fire a message at a specific time, that's a reminder, not a due date)
- **Note:** there's still no way to ask it to read back *completed* items on demand outside of the weekly summary; checking history directly means looking at the list in the Google Tasks app

**Email** (Phase 11)
- Ask what's in the inbox, search for something specific, read a full email, send a new one, or reply to one — same conversational shape as Calendar/Tasks, on the household's own dedicated Gmail account
- **Does not ping you on every incoming email** — that's deliberate. Instead:
  - A daily digest each morning summarizing the previous day's mail
  - Watch rules you set up in conversation — "if I get an email from Sam, notify me right away" (by sender) or "if I get an email about the item I have for sale on marketplace, tell me" (by topic, judged by a cloud model against what actually arrives) — either gets you an immediate Telegram notification instead of waiting for the digest
- Ask what watch rules are currently active, or remove one

**Not yet built**
- Texting the household (SMS) isn't live yet — blocked on carrier registration (Phase 4b)
- No way to ask the assistant to read back completed items on any list on demand — chores done this week come through the weekly summary, not a direct query
- Reminder-by-phone-call isn't built — you can only choose Telegram or iMessage for delivery, not "call me" (Phase 10)
- "For me" resolution only works one-to-one over DM (Telegram/Photon), and only for people registered with a `telegram_id`/`phone` — in a group chat, or for anyone without a channel ID on file, phrase requests with the person's actual name instead (Phase 8b)

**Developer/ops tooling (not household-facing)**
- **Benchmark different models/providers on latency and on model intelligence** (`household/ab_test/`, Phase 8 side quest) — replays a fixed conversation script against the live assistant once per model, records per-turn latency (time-to-first-token and total), and grades the two transcripts turn-by-turn with an LLM judge for a win tally, not just a speed number. Used so far to compare the local oMLX model against `gpt-5.4`; the same harness works for any future model/provider swap. See `USER_GUIDE.md`'s "Run another A/B test" for the exact steps — it's a deliberate, one-off diagnostic run (briefly repoints the live primary model), not something that runs continuously.

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

4. **Telegram — a chore as a to-do, then completed**
   "Add take out the trash to chores." Then, later in the same conversation: "I did it." **Verify:** open Google Tasks → Chores list — the item should show as completed with today's date, not just deleted. Separately, "log that I already did the dishes" (never added as pending) should also land as a completed entry in one shot. (Don't ask the agent to recall chore history back on demand — that read path is the weekly summary only, see Capabilities above.)

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

11. **Telegram — set a reminder and ask for it by iMessage instead**
    "Send me an iMessage reminder in a few minutes to call Grandma." **Verify:** the message arrives in **iMessage**, not Telegram, even though it was set from Telegram — confirms delivery channel is a real per-reminder choice, not just wherever it happened to be set from.

12. **Any channel — explicit research request escalates to the cloud model**
    "Can you research the history of Mount Rushmore?" **Verify:** the answer is well-sourced and current (real web search, not just training-data recall) — takes roughly 5–30 seconds since it's a real round-trip to the cloud model, noticeably longer than an ordinary local-model reply. Compare against a plain factual question with no "research" wording (e.g. "what's a good substitute for buttermilk?") — that one should stay fast, confirming the cloud tool only fires on the explicit trigger.

13. **Any channel — weekly planning and weekly summary always use the cloud model**
    "Can you help me plan out my upcoming week?" and, separately, "can you give me a summary of the past week?" **Verify:** both responses reference real seeded calendar/grocery/chore data (not generic advice) and take noticeably longer than an ordinary local reply — these two always escalate regardless of phrasing, per the fixed Phase 8 task list.

14. **Any channel — schedule around a named family member's real calendar**
    Requires at least one other family member actually registered first (see `USER_GUIDE.md` "Add a family member" — a real Google account, calendar shared at free/busy level, entry in `family_members.json`). Ask "can you find a 30-minute slot tomorrow that works for me and \<name\>?" **Verify:** the suggested slots don't overlap that person's real calendar (cross-check directly in their Google Calendar); then "schedule a call with \<name\> at \<one of the suggested times\>" and confirm **they** receive a real Google Calendar invite with an RSVP option, not just an entry on the household calendar. Separately, ask about someone *not* registered ("...that works for me and a made-up name") — **verify:** the assistant says it can't check that person's availability rather than presenting suggestions as if they'd already accounted for them.

15. **Telegram or iMessage DM — "me" resolves without saying your own name**
    Requires a family member registered with a `telegram_id` or `phone` (see `USER_GUIDE.md` "Add a family member"). From *that person's own account*, DM the bot directly (not a group chat) and ask "am I free tomorrow afternoon?" **Verify:** the response reflects that specific person's real calendar, and, revealingly, ask the bot afterward "who did you think was asking?" — it should name them correctly. Then, from a *different*, unregistered phone/Telegram account (or one without a channel ID on file), ask the same question — **verify:** it falls back to today's behavior (household calendar only) rather than guessing or misattributing the request to someone else.

16. **Any channel — read, send, and reply to email**
    "What's in the inbox?" then "read the one from \<sender\>" then "reply and say I'll be there." **Verify:** the reply actually sends (check the real Gmail sent folder) and lands in the same thread the recipient's mail client shows, not as a new, unrelated-looking message. Separately, "send an email to \<address\> with the subject X and tell them Y" should produce a fresh, correctly-subjected email — check that the subject line isn't blank in a real mail client, not just in the tool's own JSON response.

17. **Telegram — set up an email watch rule, then trigger it**
    "If I get an email from \<name or address\>, let me know right away." Have that address send a real test email. **Verify:** an unprompted Telegram notification arrives within a few minutes (the email poller checks every 5 minutes) — don't have to ask, same "no need to check back" property as reminders. Try a topic-based rule too ("notify me about emails regarding \<specific topic\>") with one matching and one clearly unrelated test email sent around the same time — **verify:** only the matching one notifies.

18. **The morning email digest**
    Not demoable on demand (it only fires in a morning window once per day) — instead, verify indirectly: check `~/.hermes/email_notifier_state.json`'s `last_digest_sent_date` advances by one each day, and that the Telegram message it sent actually summarizes real mail from the *previous* calendar day, not the current one.

**Not yet demoable, roadmap-flagged rather than omitted:**
- *Anything over SMS* — blocked on Twilio A2P 10DLC registration (Phase 4b).
