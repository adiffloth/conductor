#!/usr/bin/env python3
"""Phase 8 side-quest: grade the two A/B transcripts turn-by-turn with
Claude as an LLM judge, and report latency + quality side by side.

Usage:
    python3 household/ab_test/judge_eval.py \
        household/ab_test/results/local.json \
        household/ab_test/results/cloud.json
"""
import argparse
import json
import os
from pathlib import Path

import openai

REPO_ROOT = Path(__file__).resolve().parents[2]
JUDGE_MODEL = "gpt-5.4"

RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["a", "b", "tie"]},
        "correctness_notes": {"type": "string"},
        "helpfulness_notes": {"type": "string"},
        "tone_notes": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["winner", "correctness_notes", "helpfulness_notes", "tone_notes", "summary"],
    "additionalProperties": False,
}


def load_env(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def judge_pair(client: openai.OpenAI, user_turn: str, response_a: str, response_b: str) -> dict:
    prompt = (
        "You are judging two candidate assistant responses to the same household-assistant "
        "conversation turn, from two different backing models (labels 'a' and 'b' are anonymized "
        "on purpose — do not guess which is which). Judge only this turn in isolation.\n\n"
        f"User turn:\n{user_turn}\n\n"
        f"Response A:\n{response_a}\n\n"
        f"Response B:\n{response_b}\n\n"
        "Score on three dimensions: correctness (is it factually/logically right, and did it use "
        "tools correctly if applicable), helpfulness (does it actually address what was asked, "
        "right level of detail), and tone (natural, appropriate for a household assistant chatting "
        "with a family member). Pick an overall winner — 'a', 'b', or 'tie' if genuinely comparable."
    )
    response = client.responses.create(
        model=JUDGE_MODEL,
        input=prompt,
        reasoning={"effort": "medium"},
        text={"format": {"type": "json_schema", "name": "verdict", "schema": RUBRIC_SCHEMA, "strict": True}},
    )
    return json.loads(response.output_text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_a", type=Path)
    parser.add_argument("result_b", type=Path)
    args = parser.parse_args()

    env = load_env(REPO_ROOT / ".env")
    if env.get("OPENAI_CLOUD_API_KEY"):
        os.environ.setdefault("OPENAI_CLOUD_API_KEY", env["OPENAI_CLOUD_API_KEY"])

    run_a = json.loads(args.result_a.read_text())
    run_b = json.loads(args.result_b.read_text())
    client = openai.OpenAI(api_key=os.environ["OPENAI_CLOUD_API_KEY"])

    print(f"{'#':>3}  {run_a['label']:>10} latency   {run_b['label']:>10} latency   winner")
    print("-" * 60)

    tally = {"a": 0, "b": 0, "tie": 0}
    verdicts = []
    for turn_a, turn_b in zip(run_a["turns"], run_b["turns"]):
        if "error" in turn_a or "error" in turn_b:
            print(f"{turn_a['turn']:>3}  (skipped — a turn errored)")
            continue
        verdict = judge_pair(client, turn_a["user"], turn_a["assistant"], turn_b["assistant"])
        tally[verdict["winner"]] += 1
        verdicts.append({"turn": turn_a["turn"], "user": turn_a["user"], **verdict})
        print(
            f"{turn_a['turn']:>3}  {turn_a['total_time_s']:>9.1f}s  {turn_b['total_time_s']:>9.1f}s   "
            f"{verdict['winner']} — {verdict['summary'][:60]}"
        )

    print("-" * 60)
    print(f"Wins — {run_a['label']}: {tally['a']}, {run_b['label']}: {tally['b']}, tie: {tally['tie']}")

    avg_a = sum(t["total_time_s"] for t in run_a["turns"] if "total_time_s" in t) / len(run_a["turns"])
    avg_b = sum(t["total_time_s"] for t in run_b["turns"] if "total_time_s" in t) / len(run_b["turns"])
    print(f"Avg latency — {run_a['label']}: {avg_a:.1f}s, {run_b['label']}: {avg_b:.1f}s")

    out_path = Path(__file__).resolve().parent / "results" / "judge_report.json"
    out_path.write_text(
        json.dumps(
            {
                "tally": tally,
                "avg_latency_s": {run_a["label"]: avg_a, run_b["label"]: avg_b},
                "verdicts": verdicts,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
