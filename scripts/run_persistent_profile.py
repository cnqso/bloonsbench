#!/usr/bin/env python3
"""Launch game with a persistent Chromium profile for manual play/unlocking.

Usage:
  python scripts/run_persistent_profile.py --profile unlocks
"""

from __future__ import annotations

import argparse
from pathlib import Path

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SWF = REPO_ROOT / "game" / "btd5.swf"

from harness.env.config import HarnessConfig
from harness.env.profile_manager import ProfileManager
from harness.env.web_env import BloonsWebEnv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swf", default=str(DEFAULT_SWF))
    ap.add_argument("--profile", default="default")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    pm = ProfileManager(REPO_ROOT)
    persistent = pm.persistent_profile_dir(args.profile)

    cfg = HarnessConfig(
        headless=args.headless,
        persistent_profile_dir=persistent,
        autosave_profile=True,
        block_network=False,  # Allow NK cloud saves during manual play
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)
    run_dir = env.reset()

    env.observe(tag="manual_session")
    print(f"Game launched with persistent profile. Run artifacts: {run_dir}")
    print("Close the browser window when done; profile will be snapshotted.")

    try:
        env.page.wait_for_timeout(10**9)
    except Exception:
        pass
    finally:
        env.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
