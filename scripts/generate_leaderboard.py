#!/usr/bin/env python3
"""Generate the leaderboard table in README.md from submission files.

Replaces content between <!-- LEADERBOARD:START --> and <!-- LEADERBOARD:END --> markers.

Usage:
    python scripts/generate_leaderboard.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS_DIR = REPO_ROOT / "results" / "submissions"
README_PATH = REPO_ROOT / "README.md"

START_MARKER = "<!-- LEADERBOARD:START -->"
END_MARKER = "<!-- LEADERBOARD:END -->"


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _format_tower_summary(towers: list[dict]) -> str:
    parts = []
    for t in towers:
        upgrades = t.get("upgrades", [0, 0])
        u_str = f"{upgrades[0]}/{upgrades[1]}" if len(upgrades) == 2 else str(upgrades)
        parts.append(f"#{t['id']} {t['name']} ({t['x']},{t['y']}) [{u_str}]")
    return ", ".join(parts)


def _build_leaderboard_lines(submissions: list[dict]) -> list[str]:
    """Build the markdown lines for the leaderboard section."""
    if not submissions:
        return ["", "No submissions yet. Run an agent and submit your results!", ""]

    # Group by model
    by_model: dict[str, list[dict]] = {}
    for s in submissions:
        model = s.get("model", "unknown")
        by_model.setdefault(model, []).append(s)

    # Compute stats per model
    rows = []
    best_runs = []
    for model, runs in by_model.items():
        rounds = [r.get("round_reached", 0) for r in runs]
        tokens = [r.get("tokens_total", 0) for r in runs]
        tower_counts = [len(r.get("towers", [])) for r in runs]

        best_round = max(rounds)
        avg_round = sum(rounds) / len(rounds)
        avg_towers = sum(tower_counts) / len(tower_counts)
        avg_tokens = sum(tokens) / len(tokens)

        rows.append({
            "model": model,
            "runs": len(runs),
            "best_round": best_round,
            "avg_round": avg_round,
            "avg_towers": avg_towers,
            "avg_tokens": avg_tokens,
        })

        best = max(runs, key=lambda r: r.get("round_reached", 0))
        best_runs.append((model, best))

    rows.sort(key=lambda r: r["best_round"], reverse=True)
    best_runs.sort(key=lambda x: x[1].get("round_reached", 0), reverse=True)

    lines = [
        "",
        "## Leaderboard",
        "",
        "| Model | Runs | Best Round | Avg Round | Avg Towers | Avg Tokens |",
        "|-------|------|-----------|-----------|------------|------------|",
    ]

    for r in rows:
        lines.append(
            f"| {r['model']} | {r['runs']} "
            f"| {r['best_round']} "
            f"| {r['avg_round']:.1f} "
            f"| {r['avg_towers']:.0f} "
            f"| {_format_tokens(int(r['avg_tokens']))} |"
        )

    lines.append("")
    lines.append("### Best Runs")
    lines.append("")

    for model, run in best_runs:
        round_reached = run.get("round_reached", 0)
        towers = run.get("towers", [])
        lines.append(f"**{model} — Round {round_reached}**")
        if towers:
            lines.append(f"Towers: {_format_tower_summary(towers)}")
        else:
            lines.append("No tower data available.")
        lines.append("")

    lines.append("### Submit Your Results")
    lines.append("")
    lines.append("1. Run an agent: `python scripts/run_agent.py --model <your-model>`")
    lines.append("2. A submission file is auto-generated in `results/submissions/`")
    lines.append("3. Fork the repo, commit your submission file, and open a PR")
    lines.append("")

    return lines


def generate():
    if not SUBMISSIONS_DIR.exists():
        print("No submissions directory found.", file=sys.stderr)
        return

    submissions = []
    for f in sorted(SUBMISSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            submissions.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Skipping {f.name}: {e}", file=sys.stderr)

    leaderboard_lines = _build_leaderboard_lines(submissions)

    # Read README and replace between markers
    readme = README_PATH.read_text(encoding="utf-8")
    start_idx = readme.find(START_MARKER)
    end_idx = readme.find(END_MARKER)

    if start_idx == -1 or end_idx == -1:
        print(f"Markers not found in {README_PATH}. Add {START_MARKER} and {END_MARKER}.", file=sys.stderr)
        sys.exit(1)

    before = readme[:start_idx + len(START_MARKER)]
    after = readme[end_idx:]
    new_readme = before + "\n".join(leaderboard_lines) + "\n" + after

    README_PATH.write_text(new_readme, encoding="utf-8")
    print(f"README.md updated ({len(submissions)} submissions, {len(set(s.get('model') for s in submissions))} models)", file=sys.stderr)


if __name__ == "__main__":
    generate()
