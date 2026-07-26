#!/usr/bin/env python3
"""Phase 8 side-quest: replay a fixed conversation through Hermes's API
Server and record per-turn latency + full transcript.

This does NOT swap Hermes's primary model itself — that's a deliberately
manual, separate step (edit docker/hermes-config.yaml's `model:` block,
`docker cp` it in, restart the gateway) since it temporarily changes what
answers the live Telegram/Photon channels too. Run this script once per
side of the comparison, with a `--label` naming which primary model was
configured at the time:

    python3 household/ab_test/run_ab_test.py --label local
    # ... swap config to Anthropic, restart gateway ...
    python3 household/ab_test/run_ab_test.py --label cloud

Each run opens one fresh session (shared across all turns in that run, so
multi-turn context carries the way a real conversation would) and writes a
JSON transcript to household/ab_test/results/.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def load_env(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def stream_turn(base_url: str, api_key: str, session_id: str, messages: list) -> tuple[str, float, float]:
    """Send one turn, return (response_text, time_to_first_token, total_time)."""
    body = json.dumps(
        {
            "model": "hermes-agent",
            "messages": messages,
            "stream": True,
        }
    ).encode()

    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": session_id,
        },
    )

    start = time.monotonic()
    first_token_at = None
    chunks = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = event.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                if first_token_at is None:
                    first_token_at = time.monotonic()
                chunks.append(delta)
    end = time.monotonic()

    text = "".join(chunks)
    ttft = (first_token_at - start) if first_token_at else (end - start)
    total = end - start
    return text, ttft, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Name for this run, e.g. 'local' or 'cloud'")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    env = load_env(REPO_ROOT / ".env")
    api_key = env.get("API_SERVER_KEY")
    port = env.get("API_SERVER_PORT", "8642")
    if not api_key:
        raise SystemExit("API_SERVER_KEY not found in .env")

    base_url = f"http://{args.host}:{port}"
    script = json.loads((Path(__file__).parent / "conversation.json").read_text())
    session_id = str(uuid.uuid4())

    print(f"Run '{args.label}' — session {session_id} — {len(script['turns'])} turns against {base_url}")

    messages = []
    turns_out = []
    for i, user_turn in enumerate(script["turns"], 1):
        messages.append({"role": "user", "content": user_turn})
        print(f"  [{i}/{len(script['turns'])}] {user_turn[:70]!r} ...", end="", flush=True)
        try:
            text, ttft, total = stream_turn(base_url, api_key, session_id, messages)
        except urllib.error.URLError as e:
            print(f" FAILED: {e}")
            turns_out.append({"turn": i, "user": user_turn, "error": str(e)})
            break
        messages.append({"role": "assistant", "content": text})
        print(f" {total:.1f}s (first token {ttft:.1f}s)")
        turns_out.append(
            {
                "turn": i,
                "user": user_turn,
                "assistant": text,
                "time_to_first_token_s": round(ttft, 3),
                "total_time_s": round(total, 3),
            }
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.label}.json"
    out_path.write_text(
        json.dumps({"label": args.label, "session_id": session_id, "turns": turns_out}, indent=2, ensure_ascii=False)
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
