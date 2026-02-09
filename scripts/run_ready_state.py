#!/usr/bin/env python3
"""Full pipeline: inject saves -> load game -> navigate to round 1.

Usage:
  python scripts/run_ready_state.py --saves saves/unlocks.json
  python scripts/run_ready_state.py --saves saves/unlocks.json --map monkey_lane --difficulty easy
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SWF = REPO_ROOT / "game" / "btd5.swf"

from harness.env.config import HarnessConfig
from harness.env.web_env import BloonsWebEnv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swf", default=str(DEFAULT_SWF))
    ap.add_argument("--saves", required=True)
    ap.add_argument("--map", default="monkey_lane")
    ap.add_argument("--difficulty", default="easy")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    cfg = HarnessConfig(
        headless=args.headless,
        save_data_path=Path(args.saves),
        block_network=True,
        auto_navigate_to_round=True,
        nav_map_name=args.map,
        nav_difficulty=args.difficulty,
        startup_wait_s=3.0,
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    try:
        t0 = time.time()
        run_dir = env.reset()
        elapsed = time.time() - t0

        print(f"Ready state reached in {elapsed:.1f}s")
        print(f"Run dir: {run_dir}")

        path = env.observe(tag="ready_state")
        print(f"Ready state screenshot: {path}")

        if not args.headless:
            print("Game is at round 1. Close browser window to exit.")
            try:
                env.page.wait_for_timeout(10**9)
            except Exception:
                pass

        return 0
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
