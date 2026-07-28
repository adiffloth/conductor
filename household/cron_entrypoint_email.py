#!/usr/bin/env python3
"""Thin entrypoint for `hermes cron`, mirroring household/cron_entrypoint.py
exactly (see that file for why this indirection exists — `hermes cron`
requires scripts to be real files directly under ~/.hermes/scripts/; a
symlink pointing outside that directory is rejected at job-creation time).

The actual logic lives in household/email_notifier.py, this repo's real
source of truth. This file is a manual one-time copy into
~/.hermes/scripts/household_email_notifier.py — not templated/synced
automatically, same category of step as the Google OAuth consent flow.
"""
import runpy

runpy.run_path("/home/hermes/household/email_notifier.py", run_name="__main__")
