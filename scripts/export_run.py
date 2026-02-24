#!/usr/bin/env python3
"""Export a run directory as a submission file for the leaderboard.

Usage:
    python scripts/export_run.py logs/runs/20260218_123556
    python scripts/export_run.py --latest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS_DIR = REPO_ROOT / "results" / "submissions"

MIN_ROUND = 2


def _strip_base64(obj):
    """Recursively strip base64 image data from trace entries."""
    if isinstance(obj, dict):
        return {
            k: "[base64_stripped]" if isinstance(v, str) and len(v) > 500 and re.match(r"^[A-Za-z0-9+/=]+$", v[:200]) else _strip_base64(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_base64(item) for item in obj]
    return obj


def _model_slug(model: str) -> str:
    """Convert model ID like 'anthropic/claude-sonnet-4' to 'claude-sonnet-4'."""
    return model.rsplit("/", 1)[-1]


def export_run(run_dir: Path) -> Path | None:
    """Export a run directory to a submission file.

    Returns the path to the created submission file, or None if the run
    doesn't meet quality criteria.
    """
    results_path = run_dir / "results.json"
    if not results_path.exists():
        print(f"No results.json in {run_dir}", file=sys.stderr)
        return None

    results = json.loads(results_path.read_text(encoding="utf-8"))

    # Quality gate
    round_reached = results.get("round_reached", 0)
    stop_reason = results.get("stop_reason", "")
    if round_reached < MIN_ROUND:
        print(f"Skipping: round_reached={round_reached} < {MIN_ROUND}", file=sys.stderr)
        return None
    if stop_reason.startswith("error"):
        print(f"Skipping: stop_reason={stop_reason}", file=sys.stderr)
        return None

    # Load and strip trace
    trace = []
    log_path = run_dir / "agent_log.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                trace.append(_strip_base64(entry))
            except json.JSONDecodeError:
                continue

    # Build submission
    submission = {**results, "trace": trace}

    # Generate filename
    slug = _model_slug(results.get("model", "unknown"))
    round_str = f"{round_reached}r"
    # Use timestamp from results, falling back to run dir name
    ts = results.get("timestamp", "")
    if ts:
        # "2026-02-18T13:19:37.485344" -> "20260218_131937"
        dt_part = ts.split(".")[0]  # drop fractional seconds
        ts_clean = dt_part.replace("-", "").replace("T", "_").replace(":", "")
    else:
        ts_clean = run_dir.name

    filename = f"{slug}_{round_str}_{ts_clean}.json"
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SUBMISSIONS_DIR / filename
    out_path.write_text(json.dumps(submission, indent=2), encoding="utf-8")
    print(f"Submission written: {out_path}", file=sys.stderr)
    return out_path


def _find_latest_run() -> Path | None:
    runs_dir = REPO_ROOT / "logs" / "runs"
    if not runs_dir.exists():
        return None
    dirs = sorted(runs_dir.iterdir())
    for d in reversed(dirs):
        if (d / "results.json").exists():
            return d
    return None


def main():
    parser = argparse.ArgumentParser(description="Export a run as a leaderboard submission")
    parser.add_argument("run_dir", nargs="?", help="Path to the run directory")
    parser.add_argument("--latest", action="store_true", help="Export the most recent run")
    args = parser.parse_args()

    if args.latest:
        run_dir = _find_latest_run()
        if run_dir is None:
            sys.exit("No runs found with results.json")
    elif args.run_dir:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = REPO_ROOT / run_dir
    else:
        parser.print_help()
        sys.exit(1)

    if not run_dir.exists():
        sys.exit(f"Run directory not found: {run_dir}")

    result = export_run(run_dir)
    if result is None:
        sys.exit(1)
    print(result)


if __name__ == "__main__":
    main()
