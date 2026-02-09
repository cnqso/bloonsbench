#!/usr/bin/env python3
"""Verify save injection: launch fresh browser with injected saves, take screenshots.

Usage:
  python scripts/verify_saves.py --saves saves/unlocks.json
"""

from __future__ import annotations

import argparse
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
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--wait", type=int, default=10, help="Seconds to wait for visual inspection")
    args = ap.parse_args()

    cfg = HarnessConfig(
        headless=args.headless,
        save_data_path=Path(args.saves),
        block_network=True,
        startup_wait_s=3.0,
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    try:
        run_dir = env.reset()
        print(f"Game loaded with injected saves. Run dir: {run_dir}")

        for i in range(3):
            env.page.wait_for_timeout(2000)
            path = env.observe(tag=f"verify_{i}")
            print(f"  Screenshot: {path}")

        if not args.headless:
            print(f"Waiting {args.wait}s for visual inspection...")
            env.page.wait_for_timeout(args.wait * 1000)

        print("Done.")
        return 0
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
