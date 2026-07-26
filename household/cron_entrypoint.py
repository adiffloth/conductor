#!/usr/bin/env python3
"""Thin entrypoint for `hermes cron`, which requires scripts to be real
(non-symlink, non-traversing) files directly under ~/.hermes/scripts/ — a
symlink pointing outside that directory is rejected at job-creation time
("Script path escapes the scripts directory via traversal").

The actual logic lives in household/reminder_scheduler.py, this repo's real
source of truth (kept outside ~/.hermes so it survives image rebuilds
normally, see project_plan.md Phase 7). This file is a manual one-time copy
into ~/.hermes/scripts/household_reminders.py — not templated/synced
automatically, same category of step as the Google OAuth consent flow.
"""
import runpy

runpy.run_path("/home/hermes/household/reminder_scheduler.py", run_name="__main__")
