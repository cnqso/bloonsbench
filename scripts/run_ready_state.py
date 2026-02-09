#!/usr/bin/env python3
"""Full pipeline: inject saves -> load game -> navigate to round 1.

Usage:
  python scripts/run_ready_state.py
  python scripts/run_ready_state.py --map monkey_lane --difficulty easy
  python scripts/run_ready_state.py --saves saves/custom.json
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
    ap.add_argument("--saves", default=None, help="Save file (default: saves/default.json)")
    ap.add_argument("--map", default="monkey_lane")
    ap.add_argument("--difficulty", default="easy")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    cfg_kwargs = dict(
        headless=args.headless,
        block_network=False,
        auto_navigate_to_round=True,
        nav_map_name=args.map,
        nav_difficulty=args.difficulty,
    )
    if args.saves:
        cfg_kwargs["save_data_path"] = Path(args.saves)
    cfg = HarnessConfig(**cfg_kwargs)

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    try:
        t0 = time.time()
        run_dir = env.reset()
        elapsed = time.time() - t0

        print(f"Ready state reached in {elapsed:.1f}s")
        print(f"Run dir: {run_dir}")

        path = env.observe(tag="ready_state")
        print(f"Ready state screenshot: {path}")

        # Test: place a dart monkey after 3 seconds
        env.page.wait_for_timeout(3000)
        env.place_tower("dart_monkey", 400, 350)
        env.page.wait_for_timeout(1000)
        path = env.observe(tag="after_placement")
        print(f"After placement screenshot: {path}")

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
