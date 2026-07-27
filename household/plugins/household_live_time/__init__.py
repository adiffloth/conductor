"""household-live-time plugin — per-turn live clock injection.

Works around a still-open hermes-agent limitation (NousResearch/hermes-agent
#10421, dup of #58813/#27742/#28290/#53061): the system prompt's date anchor
("Conversation started: <date>") is written once and cached for the life of
a session, and a *continuing* session's cached prompt is restored verbatim
from the session DB on every turn — including across a full gateway/
container restart (confirmed against ``conversation_loop.py``'s
``_restore_or_build_system_prompt``: a session with history reuses the
stored prompt whenever the runtime signature still matches, and a restart
alone doesn't change that). A session that spans midnight — the normal case
for a long-lived Telegram/Photon DM — leaves the model believing "today" is
whatever date the session started, so any relative phrase ("today",
"tomorrow", "in 5 days") it resolves from that stale anchor silently drifts
by however many days have actually passed. This is what produced a reminder
dated two days in the past in this project's 2026-07-27 incident.

The upstream fix (PR #32942, "inject turn-level current time context") is
still open/unmerged. This plugin implements the same idea locally via the
documented ``pre_llm_call`` hook (confirmed valid and already exercised by
two of Hermes's own bundled plugins — ``plugins/observability/langfuse`` and
``plugins/observability/nemo_relay``): inject the real current time into
*this turn's user-message context only*. Per ``hermes_cli/plugins.py``'s
``invoke_hook`` docstring, that context is always injected into the user
message, never the cached system prompt, and is never persisted to the
session DB — so it costs no prompt-cache hits and, unlike the stale date
anchor, needs no restart to stay correct turn-to-turn (a restart is only
needed once, to load this plugin's code into a running gateway at all).

Complementary to, not a replacement for, household_mcp_server.py's
``_reject_if_past`` guard on ``set_reminder``/``add_calendar_event``: that
guard only catches a resolved date landing in the *past*. A model that
overshoots into the *future* ("in 5 days" landing 2 days later than
intended) produces a technically-valid future timestamp that guard can't
catch — this plugin fixes the underlying cause (the model's date belief)
rather than one symptom of it.
"""
from typing import Any, Optional


def _on_pre_llm_call(**_kwargs: Any) -> Optional[dict]:
    """Return the real current time as ephemeral user-message context.

    Never raises — a formatting/import failure should degrade to "no
    context injected" (pre-plugin behavior), not break the turn.
    """
    try:
        from hermes_time import now as _hermes_now

        now = _hermes_now()
        human = now.strftime("%A, %B %d, %Y, %I:%M %p %Z").strip()
        return {
            "context": (
                f"[Current date/time: {human} ({now.isoformat(timespec='minutes')})] "
                "Resolve any relative date or time in the user's message "
                "(\"today\", \"tomorrow\", \"in N days\", \"this afternoon\", "
                "etc.) against this, not against any date mentioned earlier "
                "in the conversation."
            )
        }
    except Exception:
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
