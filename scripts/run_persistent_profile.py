#!/usr/bin/env python3
"""Launch game with a persistent Chromium profile for manual play/unlocking.

Usage:
  # Start a brand-new local save profile
  python scripts/run_persistent_profile.py --profile my_save --fresh-start

  # Re-open the same profile later to continue progress
  python scripts/run_persistent_profile.py --profile my_save
"""

from __future__ import annotations

import argparse
import shutil
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
    ap.add_argument(
        "--fresh-start",
        action="store_true",
        help="Delete existing browser storage for this profile before launch.",
    )
    ap.add_argument(
        "--seed-saves",
        default=None,
        help="Optional save JSON to inject before game load.",
    )
    ap.add_argument(
        "--allow-cloud-sync",
        action="store_true",
        help="Allow NinjaKiwi network requests (disabled by default for stable local persistence).",
    )
    ap.add_argument("--headless", action="store_true")
    ap.add_argument(
        "--wait",
        type=int,
        default=None,
        help="Auto-close after N seconds (useful for headless checks).",
    )
    args = ap.parse_args()

    pm = ProfileManager(REPO_ROOT)
    persistent = pm.persistent_profile_dir(args.profile)
    if args.fresh_start and persistent.exists():
        shutil.rmtree(persistent)
        persistent.mkdir(parents=True, exist_ok=True)

    save_data_path = None
    if args.seed_saves:
        save_data_path = Path(args.seed_saves).expanduser().resolve()

    cfg = HarnessConfig(
        headless=args.headless,
        persistent_profile_dir=persistent,
        autosave_profile=True,
        block_network=not args.allow_cloud_sync,
        save_data_path=save_data_path,
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)
    run_dir = env.reset()

    env.observe(tag="manual_session")
    print(f"Game launched with persistent profile: {persistent}")
    print(f"Run artifacts: {run_dir}")
    print(f"Cloud sync blocked: {cfg.block_network}")
    print(f"Seed saves injected: {'yes' if save_data_path else 'no'}")
    if args.wait:
        print(f"Auto-closing in {args.wait}s.")
    else:
        print("Close the browser window when done; profile will be snapshotted.")

    try:
        if args.wait:
            env.page.wait_for_timeout(args.wait * 1000)
        else:
            env.page.wait_for_timeout(10**9)
    except Exception:
        pass
    finally:
        env.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
