#!/usr/bin/env python3
"""Merge KEY=value pairs from a source .env into a target .env in place.

Replaces existing (including commented-out) KEY= lines, appends any keys
not already present, and preserves everything else in the target file
untouched. Used to push secrets from the repo's root .env into the
container's ~/.hermes/.env without ever putting a secret value on a
command line or in shell history.
"""
import re
import sys


def load_env(path):
    values = {}
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key, value = key.strip(), value.strip()
            if value:
                values[key] = value
    return values


def merge(source_path, target_path):
    updates = load_env(source_path)
    if not updates:
        return

    with open(target_path) as f:
        lines = f.readlines()

    handled = set()
    for i, line in enumerate(lines):
        m = re.match(r"^#?\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
        if not m or m.group(1) not in updates:
            continue
        key, rest = m.group(1), m.group(2)
        comment = "  #" + rest.split("#", 1)[1] if "#" in rest else ""
        lines[i] = f"{key}={updates[key]}{comment}\n"
        handled.add(key)

    for key, value in updates.items():
        if key not in handled:
            lines.append(f"{key}={value}\n")

    with open(target_path, "w") as f:
        f.writelines(lines)


if __name__ == "__main__":
    merge(sys.argv[1], sys.argv[2])
