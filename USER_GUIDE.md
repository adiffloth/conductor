# User Guide — Operating This Project

A practical reference for picking this project back up: where things live,
how to start/stop the pieces, and what external accounts it depends on.
Not the build history — see `project_plan.md` for that (chronological,
detailed, includes every decision and gotcha hit along the way). Not the
feature list either — see `FEATURES_AND_VALIDATION.md` for what the
assistant can actually do and how to demo it.

---

## What this is, in one paragraph

A household AI assistant ("Hermes") reachable over Telegram, iMessage, and
phone calls, running mostly on a local LLM on this Mac Studio (via oMLX),
with a small amount of custom code (this repo) giving it Google
Calendar/Tasks/Email access. Everything Hermes-related runs inside one
Docker container; oMLX runs bare-metal on the host; a couple of things
(ngrok, oMLX itself) run on the host outside Docker.

---

## Where things live

**This repo (`conductor/`):**

| Path | What it is |
|---|---|
| `README.md` | Original project brief — the four target scenarios, tech stack as originally envisioned |
| `project_plan.md` | Full build log, phase by phase, every decision and its reasoning, every bug hit and how it was fixed. The source of truth for "why is it built this way" |
| `FEATURES_AND_VALIDATION.md` | What the assistant can do, in plain language, plus demo scenarios to manually validate it |
| `USER_GUIDE.md` | This file |
| `PRODUCT_BRIEF.html` | A designed, one-page HTML brief (capabilities, architecture, principles, example use cases) — for sharing outside the repo, not a working doc. Open directly in a browser, or publish as a Claude Artifact to get a shareable link |
| `Dockerfile`, `docker-compose.yml` | The sandbox container definition |
| `.env` | **All secrets. Not committed (gitignored). Lives only on this machine.** Synced into the container manually (see below) — Docker Compose doesn't read it directly |
| `.gitignore` | Also excludes `.venv/`, `__pycache__/`, `.DS_Store`, `*.pyc` |
| `docker/hermes-config.yaml` | The versioned source of truth for `~/.hermes/config.yaml` — model provider, `terminal.backend`, `plugins`, `platform_toolsets`, `mcp_servers`, per-platform settings. Only reaches a *fresh* mount automatically (see below); edit here and redeploy, don't edit the live one directly |
| `docker/sync-env.py` | Utility script: merges `.env`'s keys into the container's `~/.hermes/.env` without ever putting a secret on a host command line |
| `household/household_mcp_server.py` | **The core custom code.** An MCP server exposing Calendar (7 tools), task lists — generic across groceries/chores/any ad-hoc list, not grocery-specific (8 tools), Email (7 tools: `search_emails`, `read_email`, `send_email`, `reply_to_email`, `add_email_watch_rule`, `list_email_watch_rules`, `remove_email_watch_rule` — Phase 11), multi-user scheduling (`list_family_members` — Phase 8b), and cloud escalation (3 tools: `research_topic`, `plan_upcoming_week`, `summarize_past_week` — Phase 8) directly to Hermes — 30 tools total, no shell-outs, no generic skill-discovery indirection |
| `household/reminder_scheduler.py` | Phase 7: polls Calendar for due reminders, sleeps to the exact requested second (not just "within the poll interval" — Phase 7 update), delivers via `hermes send` (Telegram or iMessage, per-reminder choice), marks them sent |
| `household/cron_entrypoint.py` | A required 3-line wrapper — `hermes cron` only accepts real files directly under `~/.hermes/scripts/`, not symlinks elsewhere. This gets copied there once; the real logic stays in `reminder_scheduler.py` |
| `household/email_notifier.py` | Phase 11: a second `hermes cron` job (`every 5m`) — checks new mail against user-defined watch rules (sender-based, or topic-based via a cloud-model classification call) and pushes a Telegram notification on a match; separately, once a day, summarizes the previous day's mail into a digest. Reuses `reminder_scheduler.py`'s delivery helpers rather than re-implementing them |
| `household/cron_entrypoint_email.py` | Same wrapper pattern as `cron_entrypoint.py`, pointed at `email_notifier.py` |
| `household/seed_demo_data.py` | Populates realistic calendar events / grocery items / chore-log entries, relative to "today". Re-run before a demo to refresh dates |
| `household/ab_test/conversation.json` | Fixed 7-turn script for the local-vs-cloud A/B test (Phase 8 side quest) |
| `household/ab_test/run_ab_test.py` | Replays the script against the live API Server, records per-turn latency + transcript to `household/ab_test/results/<label>.json` |
| `household/ab_test/judge_eval.py` | Grades two transcripts turn-by-turn using an LLM judge (`gpt-5.4`), reports a win tally + avg latency |
| `household/ab_test/results/` | Gitignored — local test artifacts only, not committed |
| `family_members.json` | Multi-user scheduling registry (name → email, + optional telegram_id/phone). Not committed — PII, same treatment as `.env`. See "Add a family member" below |
| `household/plugins/household_identity/` | Hermes plugin (Phase 8b part 2) — resolves a DM sender's registered name via `family_members.json` and prefixes it onto the message text, so "add this for me" resolves like a named third party would. DM-only, deliberately doesn't touch group chats (Hermes's own mechanism already handles those) |

**Inside the container, NOT in this repo** (bind-mounted from
`../conductor-data/hermes` — a sibling directory to this repo, not a
Docker-managed named volume; see `docker-compose.yml`'s comment for why —
persists across container recreation *and* is visible/backed-up-able on
the host filesystem, unlike a named volume):

| Path | What it is |
|---|---|
| `~/.hermes/config.yaml` | Runtime config — model, `terminal.backend`, `platform_toolsets` (which tools each channel can use), `mcp_servers` (our household server registration), `plugins.enabled`. Kept in sync with `docker/hermes-config.yaml` in this repo — edit that file and redeploy, not the live one directly, or the two drift (this drifted badly once already; see project_plan.md's durability-pass note) |
| `~/.hermes/.env` | Secrets, synced from this repo's `.env` (see "Common tasks" below) |
| `~/.hermes/google_token.json` | Google OAuth token (Calendar + Tasks + Gmail `gmail.modify` scopes — Phase 11 added Gmail) |
| `~/.hermes/google_client_secret.json` | Google OAuth client credentials |
| `~/.hermes/family_members.json` | Synced copy of the repo-root `family_members.json` (see "Add a family member" below) |
| `~/.hermes/email_watch_rules.json` | Email notification rules (Phase 11) — unlike `family_members.json`, this one **is** model-writable; created via conversation (`add_email_watch_rule`), not hand-edited |
| `~/.hermes/email_notifier_state.json` | `email_notifier.py`'s own bookkeeping (last poll position, last digest date) — generated on first run, not committed |
| `~/.hermes/scripts/household_reminders.py` | The cron entrypoint wrapper (copy of `household/cron_entrypoint.py`) |
| `~/.hermes/scripts/household_email_notifier.py` | Same, for `household/email_notifier.py` (Phase 11) |
| `~/.hermes/logs/gateway.log` | Platform connect/disconnect, message-level activity — **the first place to look when something doesn't work** |
| `~/.hermes/logs/agent.log` | Per-turn reasoning/tool-call trace — noisier, useful for "why did it do that" |
| `~/.hermes/logs/errors.log` | Just the warnings/errors across everything |

---

## The moving parts, and how to start/stop each

Four things have to be running for the assistant to work end to end. Check
all of them with the snapshot command at the bottom of this section.

### 1. oMLX (bare-metal on the host, not Docker)
The menubar app, already running with `Qwen3.6-35B-A3B-MLX-6bit` loaded
(the active *local* model — `Hermes-4.3-36B-mlx-5Bit` is also downloaded,
queued as a future **local-model-vs-local-model** comparison, see
project_plan.md Decision 2. Not the same thing as the **local-vs-cloud**
A/B harness below — that one compares this local model against OpenAI, not
against another local model). Not something this repo starts/stops — just
needs to be running with the model loaded.
Check: `curl http://127.0.0.1:8000/v1/models`

### 2. The Hermes container
```bash
docker compose up -d          # start (or recreate after Dockerfile/compose changes)
docker compose build          # rebuild the image after a Dockerfile change
docker compose down           # stop and remove the container (volume persists)
```
Container name: `hermes-sandbox`.

### 3. The Hermes gateway (Telegram + Photon + API Server, inside the container)
This is a foreground process — start it detached:
```bash
docker exec -d hermes-sandbox hermes gateway run
docker exec hermes-sandbox hermes gateway status
docker exec hermes-sandbox hermes gateway stop
```
**Restart it (stop, then start again) after any change to:** `config.yaml`,
`.env` secrets, the household MCP server code, plugin code, or the cron
job definition. It does not hot-reload. `hermes cron` jobs are ticked by
this same process — no separate scheduler to manage.

**Shutdown can take longer than it looks.** Full teardown (disconnecting
Telegram/Photon/API Server) can take up to ~20s if a platform's own
notification send is slow — observed concretely with Photon's shutdown
ping round-tripping through its cloud relay. Sending the next start
command before teardown actually finishes leaves a stale `gateway.lock`
pointing at a pid that's already dead, and the new process fails silently.
Confirm the old process actually exited (`grep "Gateway stopped (total" ~/.hermes/logs/gateway.log`,
or poll `/proc/<pid>`) before starting a new one; if a stale lock does show
up, `rm -f ~/.hermes/gateway.lock ~/.hermes/gateway.pid` once the old pid
is confirmed dead, then start normally.

### 4. ngrok (bare-metal on the host, not Docker)
Tunnels the container's API Server port so ElevenLabs (voice) can reach it.
```bash
ngrok http --domain=pumped-prawn-sadly.ngrok-free.app 8642
```
Not needed for Telegram or iMessage (Photon) — both are outbound-initiated
from the container, no public endpoint required. Only Voice needs this.

### Full stack snapshot (paste this to check everything at once)
```bash
docker compose ps
docker exec hermes-sandbox hermes gateway status
docker exec hermes-sandbox hermes cron list
curl -s http://127.0.0.1:4040/api/tunnels | python3 -m json.tool
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
```

---

## External accounts and services

| Service | What it's for | Where the credential lives |
|---|---|---|
| **Telegram** (BotFather) | Bot `@FamilyConductorBot` | `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` |
| **Photon** (photon.codes) | iMessage, free tier | `~/.hermes/.env` in-container: `PHOTON_PROJECT_ID`, `PHOTON_PROJECT_SECRET` (set via `hermes photon` device-login flow, not this repo's `.env`); `.env`: `PHOTON_SIDECAR_TOKEN` (pinned, needed for the reminder scheduler to deliver standalone) |
| **Twilio** | Phone number for Voice; SMS still blocked on A2P 10DLC | `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` |
| **ElevenLabs** | Conversational AI agent (voice), free tier | Configured in their dashboard, not in this repo — Custom LLM Server URL = the ngrok URL + `/v1`, secret named `OPENAI_API_KEY` = `.env`'s `API_SERVER_KEY`. **Unrelated naming coincidence, not the same value:** this is ElevenLabs' own required literal secret name in *their* dashboard, holding our bearer token — nothing to do with the container-level `OPENAI_API_KEY` placeholder discussed under "OpenAI, specifically" below. Two different systems happen to use the same env var name for two different things. |
| **ngrok** | Public tunnel for the Voice/API Server port | Reserved domain `pumped-prawn-sadly.ngrok-free.app`, authtoken already configured in the host's ngrok install (not this repo) |
| **Google Cloud** | Calendar + Tasks APIs | See below |
| **OpenAI** | Cloud escalation (Phase 8) — `research_topic`, `plan_upcoming_week`, `summarize_past_week` | See below |

### Google, specifically

- **Cloud project ID:** `227341610220` (no dedicated name recorded — same project, all three APIs)
- **APIs enabled:** Calendar API, Tasks API, Gmail API — each had to be enabled individually; granting an OAuth scope does **not** imply the API itself is enabled (hit this exact 403 with Tasks, checked proactively for Gmail before it bit us the same way — see project_plan.md Phase 11)
- **OAuth client type:** Desktop app (matters — Google's own official Calendar MCP server requires a *Web application* client instead, a real blocker if that's ever adopted, see project_plan.md Phase 9 research item)
- **Scopes granted, single consent covering all three:** `https://www.googleapis.com/auth/calendar`, `https://www.googleapis.com/auth/tasks`, `https://www.googleapis.com/auth/gmail.modify` — deliberately trimmed down from the bundled google-workspace skill's much broader default (which also requests Gmail *send/modify* at a wider grant, plus Drive, Contacts, Sheets, Docs — `gmail.modify` alone covers read + send + labels, no permanent delete)
- **Whose Google account:** `roseyfamilyconductor@gmail.com` — a **dedicated** account created specifically for this project, not anyone's personal inbox. Data lives in that account's real Calendar (`primary`), two auto-created Tasks lists ("Groceries", "Chores", plus any ad-hoc list created via `create_task_list`/`add_list_item`), and that account's real Gmail inbox (Phase 11)
- **If the token ever needs regenerating:** `google-workspace` skill's `scripts/setup.py --revoke` then `--auth-url` / `--auth-code`, run via `/home/hermes/.hermes/hermes-agent/venv/bin/python3.11` (not the bare `python3.11` — that's a different, dependency-less interpreter). The skill's own `setup.py`/`google_api.py` `SCOPES` lists must match `household_mcp_server.py`'s exactly, or token/credential loading mismatches — both are patched together, see project_plan.md Phase 6/11. Full walkthrough in project_plan.md Phase 6.

### OpenAI, specifically

- **Model used:** `gpt-5.4` — the household has a free daily token allowance on OpenAI, which is why this (not Anthropic) is the cloud-escalation provider. `email_notifier.py` (Phase 11) uses `gpt-5.4-mini` instead (`CLOUD_MODEL_MINI`) for its two calls — topic-rule classification and the daily digest — since those can fire every 5-minute poll tick rather than on an occasional explicit request, and are comparatively easy tasks; `research_topic`/`plan_upcoming_week`/`summarize_past_week` are unaffected.
- **Key lives in `.env` as `OPENAI_CLOUD_API_KEY` — deliberately NOT `OPENAI_API_KEY`.** `docker-compose.yml` already sets `OPENAI_API_KEY=local-omlx-no-key-required` at the container level (a placeholder Hermes's own local-model client needs). Anything reading the plain `OPENAI_API_KEY` env var inside the container gets that placeholder, not a real key — this cost real debugging time in Phase 8. `household_mcp_server.py`'s cloud tools read `OPENAI_CLOUD_API_KEY` explicitly (env first, falling back to reading `~/.hermes/.env` directly if the subprocess didn't inherit it).
- **Called directly via the official SDK's Responses API** (`client.responses.create`, `reasoning: {effort: ...}`) from the household MCP server — not via Hermes's own `model.provider`, which normally stays local/oMLX. See "Run another A/B test" below for the one case where Hermes's primary model *is* temporarily pointed at OpenAI.
- **Effort levels matter for latency:** `research_topic` uses `effort: "medium"` — `"high"` combined with the `web_search` tool can take 5+ minutes (multiple search rounds each add a reasoning pass) and blows past Hermes's MCP tool-call timeout. `plan_upcoming_week`/`summarize_past_week` use `"high"` — no web search, no compounding, tested fast.

---

## Common tasks

**Push a household/ code change into the running container.** `docker cp`
alone is fast for iterating but only patches the *current* container's
writable layer — `household/` is `COPY`'d into the image at build time, not
part of the bind-mounted `~/.hermes`, so a later `docker compose up -d`
(container recreation, not just a restart) silently reverts to whatever's
actually baked into the image. Hit this for real during Phase 11: after a
`docker compose down`/`up -d` for an unrelated change, `household_mcp_server.py`
had reverted to an old snapshot mid-session. For a quick same-session test,
the `docker cp` below is fine; **before the container is ever recreated
again, `docker compose build` first** so the image itself is current:
```bash
# Fast iteration within the current container's lifetime:
docker cp household/household_mcp_server.py hermes-sandbox:/home/hermes/household/household_mcp_server.py
docker exec -u root hermes-sandbox chown hermes:hermes /home/hermes/household/household_mcp_server.py
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run

# Before the container is next recreated, make it durable:
docker compose build
```

**Push a plugin code change** (`household/plugins/household_identity/`, or any future plugin — different deploy path than `household_mcp_server.py`, since Hermes only discovers plugins from specific fixed locations):
```bash
docker cp household/plugins/household_identity hermes-sandbox:/home/hermes/.hermes/plugins/household_identity
docker exec -u root hermes-sandbox chown -R hermes:hermes /home/hermes/.hermes/plugins/household_identity
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run
```
A brand-new plugin also needs enabling once — `hermes plugins list` shows
its status; `hermes plugins enable <name>` (the `name:` field from its
`plugin.yaml`, not the directory name — they can differ) turns it on. Only
needed the first time; edits to an already-enabled plugin just need the
`docker cp` + restart above.

**Re-sync secrets after the container was recreated** (`docker compose up -d` after a Dockerfile change wipes nothing in the volume, but a fresh volume needs this):
```bash
docker cp docker/sync-env.py hermes-sandbox:/tmp/sync-env.py
docker cp .env hermes-sandbox:/tmp/incoming.env
docker exec -u root hermes-sandbox python3.11 /tmp/sync-env.py /tmp/incoming.env /home/hermes/.hermes/.env
docker exec -u root hermes-sandbox chown hermes:hermes /home/hermes/.hermes/.env
docker exec -u root hermes-sandbox rm -f /tmp/incoming.env /tmp/sync-env.py
```

**Test an MCP tool directly, bypassing any channel** (fastest way to check if a bug is in the tool or in the model's usage of it):
```bash
docker exec hermes-sandbox /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 -c "
import asyncio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command='/home/hermes/.hermes/hermes-agent/venv/bin/python3.11',
        args=['/home/hermes/household/household_mcp_server.py'],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool('get_agenda', {})
            print(r.content[0].text)

asyncio.run(main())
"
```

**Test a change without waiting for a real conversation** — a fresh API Server session, bypassing Telegram/Photon/Voice entirely:
```bash
source .env
curl -s http://127.0.0.1:8642/v1/chat/completions -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_SERVER_KEY}" \
  -H "X-Hermes-Session-Id: test-$(date +%s)" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"YOUR QUESTION"}],"stream":false}'
```
Use a **fresh, unique** session ID every time — reusing one (or using
`hermes -z` repeatedly, which continues the last session by default) means
the model can end up citing its own earlier answer from before a bug was
fixed, which looks exactly like the bug is still there when it isn't.

**Refresh demo data before showing this to someone:**
```bash
docker exec hermes-sandbox /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 \
    /home/hermes/household/seed_demo_data.py
```

**Add a family member (multi-user scheduling):**

Two separate steps — one the family member does themselves in their own
Google account, one you do in this repo. Order doesn't matter, but nothing
works until both are done.

> **Pick their `name` carefully and use it consistently everywhere** —
> the calendar-sharing step below, `family_members.json`, and how the
> household actually talks about that person all need to line up. Matching
> is exact (case-insensitive, no fuzzy/nickname matching), and the *same*
> name is what resolves both "schedule this for Sam" (a third party naming
> someone else) and Sam's own "add this for me" (via the DM sender-name
> resolution below) — so if the household sometimes says "Sam" and
> sometimes "Samantha", pick one and use it in the registry and going
> forward, rather than registering both as if they were different people.

*Step 1 — the family member shares free/busy access with the household
account* (their own action, in their own Google Calendar — nothing to
install, no OAuth flow):
1. They go to [Google Calendar](https://calendar.google.com) → Settings
   (gear icon) → click their calendar under "Settings for my calendars" →
   **Share with specific people**.
2. Add the household's Google account email (the same account used for the
   Calendar/Tasks OAuth setup — see "Google, specifically" above) with
   permission **"See only free/busy (hide details)"**. That's deliberately
   the minimum — Hermes doesn't need to see their event titles/locations to
   suggest a meeting time, only when they're busy. (Higher permission
   levels work too but aren't needed for anything this project does.)
3. This step is **only** needed so `suggest_meeting_time` can check their
   availability. Adding them as an event attendee (below) works regardless
   of this — that's a normal Calendar invite, unrelated to sharing.

*Step 2 — register them in `family_members.json`* (your action, this repo):
```bash
# Edit family_members.json at the repo root, e.g.:
# {"family_members": [{"name": "Sam", "email": "sam@gmail.com", "telegram_id": "111111111", "phone": "+15551234567"}]}
#
# name         — what people say in conversation ("schedule this for Sam").
#                See the note above on picking one and sticking with it.
# email        — their Google account (free/busy sharing target + calendar invites).
# telegram_id  — optional. Their own numeric Telegram user ID (get it from
#                @userinfobot, same value TELEGRAM_ALLOWED_USERS uses for
#                that person). Only needed if they DM the bot on Telegram.
# phone        — optional. Their own E.164 number (same value
#                PHOTON_ALLOWED_USERS uses for that person). Only needed if
#                they DM the bot via iMessage/Photon.
#
# telegram_id/phone enable "add this for me"-style DM requests to resolve
# to that person automatically (Phase 8b part 2) — without one, that
# channel's DMs from them still work for everything else, they just won't
# get the "for me" auto-resolution; naming them explicitly ("for Sam")
# always works regardless.

docker cp family_members.json hermes-sandbox:/home/hermes/.hermes/family_members.json
docker exec -u root hermes-sandbox chown hermes:hermes /home/hermes/.hermes/family_members.json
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run
```
The explicit `chown` matters — `docker cp` preserves the *host* file's
ownership, which doesn't match the `hermes` user inside the container, and
the gateway/tools run as `hermes`. Skipping it means the file silently
can't be read (or, worse, an earlier copy silently can't be overwritten).

**Verify "for me" resolution** (if you set `telegram_id`/`phone`): DM the
bot from that person's own account and ask something like "am I free
tomorrow?" or "add a reminder for me." It should resolve to them without
them naming themselves — internally, a plugin (`household/plugins/
household_identity/`, see project_plan.md "Multi-user support" part 2)
prefixes their name onto the message before the model ever sees it, the
same way Hermes's own group-chat mechanism already tags senders — so the
model treats "me" exactly like it'd treat a named third party.

**Verify it worked** — ask (from any channel): *"who are the registered
family members?"* and *"can you find a 30-minute slot tomorrow that works
for me and \<name\>?"* The second one should either return real suggestions
that account for their calendar, or clearly say their availability
couldn't be checked (not shared yet) — never silently ignore them and
suggest a slot as if they don't exist. Full mechanism, including why this
is registry-based rather than something you can just tell the bot in
conversation, is in `project_plan.md`'s "Multi-user support" section.

**Run another A/B test (local vs cloud model, latency + LLM-judged quality):**

This briefly points Hermes's *primary* model at OpenAI, so the real
Telegram/Photon channels answer from `gpt-5.4` instead of the local model
for a few minutes — pick a quiet moment, same as any other gateway
restart. `household/ab_test/run_ab_test.py` only *replays* the fixed
conversation and records results; it does not touch the model config
itself, so swap/restore are separate steps below (`household/ab_test/
swap_primary_model.py`, run inside the container).

```bash
# 1. Baseline run against whatever's currently configured (normally local):
uv run python3 household/ab_test/run_ab_test.py --label local

# 2. Swap the live primary model to OpenAI and restart the gateway:
docker cp household/ab_test/swap_primary_model.py hermes-sandbox:/tmp/swap_primary_model.py
docker exec hermes-sandbox /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 /tmp/swap_primary_model.py to-openai
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run
# wait ~15s for it to fully come up, then optionally sanity-check which model answered:
#   curl the API Server with "what model are you?" (see the "test a change" snippet above)

# 3. Run the cloud arm:
uv run python3 household/ab_test/run_ab_test.py --label cloud

# 4. Restore the local model and restart the gateway again:
docker exec hermes-sandbox /home/hermes/.hermes/hermes-agent/venv/bin/python3.11 /tmp/swap_primary_model.py restore
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run

# 5. Grade the two transcripts and print a report:
uv run python3 household/ab_test/judge_eval.py household/ab_test/results/local.json household/ab_test/results/cloud.json
```

The fixed conversation lives in `household/ab_test/conversation.json` —
edit it to test different questions, but avoid phrasing that would trigger
`research_topic`/`plan_upcoming_week`/`summarize_past_week` (those always
call OpenAI directly regardless of which model is primary, which would
make both arms look identical on those turns). Results and the judge's
per-turn reasoning land in `household/ab_test/results/*.json` (gitignored).
After a real restart, always confirm the gateway actually stabilized to
one process before moving on — a `hermes gateway stop` has been observed
to occasionally leave a stale process behind:
```bash
docker exec hermes-sandbox bash -c 'for p in /proc/[0-9]*; do cat $p/cmdline 2>/dev/null | tr "\0" " "; echo; done | grep "gateway run"'
```
If more than one line comes back, `docker exec hermes-sandbox bash -c "kill -TERM <old_pid>"` (or `-KILL` if that doesn't work) before continuing — two gateways answering at once means duplicate replies on the live channels.

---

## What's not built yet

Don't re-derive this from scratch — `FEATURES_AND_VALIDATION.md`'s "Not yet
built" section is the current, maintained list (SMS, chore-history
read-back, reminder-by-phone-call). `project_plan.md`'s phase list shows
what's next (Phase 4b, 9, 10) and what's deliberately deferred with
reasoning (e.g. the Photon latency investigation, whether to adopt
Google's own Calendar MCP server).
