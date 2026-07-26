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
Calendar/Tasks access. Everything Hermes-related runs inside one Docker
container; oMLX runs bare-metal on the host; a couple of things (ngrok,
oMLX itself) run on the host outside Docker.

---

## Where things live

**This repo (`conductor/`):**

| Path | What it is |
|---|---|
| `README.md` | Original project brief — the four target scenarios, tech stack as originally envisioned |
| `project_plan.md` | Full build log, phase by phase, every decision and its reasoning, every bug hit and how it was fixed. The source of truth for "why is it built this way" |
| `FEATURES_AND_VALIDATION.md` | What the assistant can do, in plain language, plus demo scenarios to manually validate it |
| `USER_GUIDE.md` | This file |
| `Dockerfile`, `docker-compose.yml` | The sandbox container definition |
| `.env` | **All secrets. Not committed (gitignored). Lives only on this machine.** Synced into the container manually (see below) — Docker Compose doesn't read it directly |
| `.gitignore` | Also excludes `.venv/`, `__pycache__/`, `.DS_Store`, `*.pyc` |
| `docker/hermes-config.yaml` | Hermes's runtime config, baked into the image at build time (model provider, terminal backend) |
| `docker/sync-env.py` | Utility script: merges `.env`'s keys into the container's `~/.hermes/.env` without ever putting a secret on a host command line |
| `household/household_mcp_server.py` | **The core custom code.** An MCP server exposing Calendar (7 tools), Tasks/grocery/chore (3 tools), and cloud escalation (3 tools: `research_topic`, `plan_upcoming_week`, `summarize_past_week` — Phase 8) directly to Hermes — no shell-outs, no generic skill-discovery indirection |
| `household/reminder_scheduler.py` | Phase 7: polls Calendar for due reminders, delivers via `hermes send` (Telegram or iMessage, per-reminder choice), marks them sent |
| `household/cron_entrypoint.py` | A required 3-line wrapper — `hermes cron` only accepts real files directly under `~/.hermes/scripts/`, not symlinks elsewhere. This gets copied there once; the real logic stays in `reminder_scheduler.py` |
| `household/seed_demo_data.py` | Populates realistic calendar events / grocery items / chore-log entries, relative to "today". Re-run before a demo to refresh dates |
| `household/ab_test/conversation.json` | Fixed 7-turn script for the local-vs-cloud A/B test (Phase 8 side quest) |
| `household/ab_test/run_ab_test.py` | Replays the script against the live API Server, records per-turn latency + transcript to `household/ab_test/results/<label>.json` |
| `household/ab_test/judge_eval.py` | Grades two transcripts turn-by-turn using an LLM judge (`gpt-5.4`), reports a win tally + avg latency |
| `household/ab_test/results/` | Gitignored — local test artifacts only, not committed |
| `family_members.json` | Multi-user scheduling registry (name → email). Not committed — PII, same treatment as `.env`. See "Add a family member" below |

**Inside the container, NOT in this repo** (lives in the `hermes_data` Docker
volume, persists across container recreation but not across a volume wipe):

| Path | What it is |
|---|---|
| `~/.hermes/config.yaml` | Runtime config — model, `terminal.backend`, `platform_toolsets` (which tools each channel can use), `mcp_servers` (our household server registration), `plugins.enabled` (Photon) |
| `~/.hermes/.env` | Secrets, synced from this repo's `.env` (see "Common tasks" below) |
| `~/.hermes/google_token.json` | Google OAuth token (Calendar + Tasks scopes) |
| `~/.hermes/google_client_secret.json` | Google OAuth client credentials |
| `~/.hermes/family_members.json` | Synced copy of the repo-root `family_members.json` (see "Add a family member" below) |
| `~/.hermes/scripts/household_reminders.py` | The cron entrypoint wrapper (copy of `household/cron_entrypoint.py`) |
| `~/.hermes/logs/gateway.log` | Platform connect/disconnect, message-level activity — **the first place to look when something doesn't work** |
| `~/.hermes/logs/agent.log` | Per-turn reasoning/tool-call trace — noisier, useful for "why did it do that" |
| `~/.hermes/logs/errors.log` | Just the warnings/errors across everything |

---

## The moving parts, and how to start/stop each

Four things have to be running for the assistant to work end to end. Check
all of them with the snapshot command at the bottom of this section.

### 1. oMLX (bare-metal on the host, not Docker)
The menubar app, already running with `Qwen3.6-35B-A3B-MLX-6bit` loaded
(the active model — `Hermes-4.3-36B-mlx-5Bit` is also downloaded, queued as
a future A/B comparison, see project_plan.md Decision 2). Not something
this repo starts/stops — just needs to be running with the model loaded.
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
`.env` secrets, the household MCP server code, or the cron job definition.
It does not hot-reload. `hermes cron` jobs are ticked by this same process
— no separate scheduler to manage.

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
| **ElevenLabs** | Conversational AI agent (voice), free tier | Configured in their dashboard, not in this repo — Custom LLM Server URL = the ngrok URL + `/v1`, secret named `OPENAI_API_KEY` = `.env`'s `API_SERVER_KEY` |
| **ngrok** | Public tunnel for the Voice/API Server port | Reserved domain `pumped-prawn-sadly.ngrok-free.app`, authtoken already configured in the host's ngrok install (not this repo) |
| **Google Cloud** | Calendar + Tasks APIs | See below |
| **OpenAI** | Cloud escalation (Phase 8) — `research_topic`, `plan_upcoming_week`, `summarize_past_week` | See below |

### Google, specifically

- **Cloud project ID:** `227341610220` (no dedicated name recorded — same project, both APIs)
- **APIs enabled:** Calendar API, Tasks API — both had to be enabled individually; granting an OAuth scope does **not** imply the API itself is enabled (hit this exact 403 with Tasks)
- **OAuth client type:** Desktop app (matters — Google's own official Calendar MCP server requires a *Web application* client instead, a real blocker if that's ever adopted, see project_plan.md Phase 9 research item)
- **Scopes granted, single consent covering both:** `https://www.googleapis.com/auth/calendar`, `https://www.googleapis.com/auth/tasks` — deliberately trimmed down from the bundled google-workspace skill's much broader default (which also requests Gmail send/modify, Drive, Contacts, Sheets, Docs)
- **Whose Google account:** the household's own personal Gmail account — data lives in that account's real Calendar (`primary`) and two auto-created Tasks lists ("Groceries", "Chores")
- **If the token ever needs regenerating:** `google-workspace` skill's `scripts/setup.py --revoke` then `--auth-url` / `--auth-code`, run via `/home/hermes/.hermes/hermes-agent/venv/bin/python3.11` (not the bare `python3.11` — that's a different, dependency-less interpreter). Full walkthrough in project_plan.md Phase 6.

### OpenAI, specifically

- **Model used:** `gpt-5.4` — the household has a free daily token allowance on OpenAI, which is why this (not Anthropic) is the cloud-escalation provider.
- **Key lives in `.env` as `OPENAI_CLOUD_API_KEY` — deliberately NOT `OPENAI_API_KEY`.** `docker-compose.yml` already sets `OPENAI_API_KEY=local-omlx-no-key-required` at the container level (a placeholder Hermes's own local-model client needs). Anything reading the plain `OPENAI_API_KEY` env var inside the container gets that placeholder, not a real key — this cost real debugging time in Phase 8. `household_mcp_server.py`'s cloud tools read `OPENAI_CLOUD_API_KEY` explicitly (env first, falling back to reading `~/.hermes/.env` directly if the subprocess didn't inherit it).
- **Called directly via the official SDK's Responses API** (`client.responses.create`, `reasoning: {effort: ...}`) from the household MCP server — not via Hermes's own `model.provider`, which normally stays local/oMLX. See "Run another A/B test" below for the one case where Hermes's primary model *is* temporarily pointed at OpenAI.
- **Effort levels matter for latency:** `research_topic` uses `effort: "medium"` — `"high"` combined with the `web_search` tool can take 5+ minutes (multiple search rounds each add a reasoning pass) and blows past Hermes's MCP tool-call timeout. `plan_upcoming_week`/`summarize_past_week` use `"high"` — no web search, no compounding, tested fast.

---

## Common tasks

**Push a household/ code change into the running container** (no rebuild needed for household/ files):
```bash
docker cp household/household_mcp_server.py hermes-sandbox:/home/hermes/household/household_mcp_server.py
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run
```

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
# {"family_members": [{"name": "Sam", "email": "sam@gmail.com"}]}
# "name" is what people will say in conversation ("schedule this for Sam")
# — match matters exactly (case-insensitive, no fuzzy matching, so "Sam"
# and "Samantha" are different entries if that's how the household talks).

docker cp family_members.json hermes-sandbox:/home/hermes/.hermes/family_members.json
docker exec -u root hermes-sandbox chown hermes:hermes /home/hermes/.hermes/family_members.json
docker exec hermes-sandbox hermes gateway stop
docker exec -d hermes-sandbox hermes gateway run
```
The explicit `chown` matters — `docker cp` preserves the *host* file's
ownership, which doesn't match the `hermes` user inside the container, and
the gateway/tools run as `hermes`. Skipping it means the file silently
can't be read (or, worse, an earlier copy silently can't be overwritten).

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
