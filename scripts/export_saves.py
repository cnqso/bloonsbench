#!/usr/bin/env python3
"""Export Ruffle localStorage saves from a persistent profile to JSON.

Usage:
  python scripts/export_saves.py --profile unlocks
  python scripts/export_saves.py --profile unlocks --dump-raw
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SWF = REPO_ROOT / "game" / "btd5.swf"

from harness.env.config import HarnessConfig
from harness.env.profile_manager import ProfileManager
from harness.env.web_env import BloonsWebEnv
from harness.env.save_data import export_saves, dump_all_localstorage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swf", default=str(DEFAULT_SWF))
    ap.add_argument("--profile", default="default")
    ap.add_argument("--output", default=None, help="Output path (default: saves/<profile>.json)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dump-raw", action="store_true", help="Dump ALL localStorage entries")
    args = ap.parse_args()

    output = Path(args.output) if args.output else REPO_ROOT / "saves" / f"{args.profile}.json"

    pm = ProfileManager(REPO_ROOT)
    persistent = pm.persistent_profile_dir(args.profile)

    cfg = HarnessConfig(
        headless=args.headless,
        persistent_profile_dir=persistent,
        autosave_profile=False,
        block_network=True,
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    try:
        run_dir = env.reset()
        print(f"Game loaded. Run dir: {run_dir}")
        env.page.wait_for_timeout(3000)

        if args.dump_raw:
            raw = dump_all_localstorage(env.page)
            print(f"\n=== Raw localStorage: {len(raw)} entries ===")
            if not raw:
                print("  (empty)")
            for key in sorted(raw):
                val = raw[key]
                preview = val[:80] + "..." if len(val) > 80 else val
                print(f"  [{len(val):>6} chars] {key} = {preview}")
            print()

        saves = export_saves(env.page)
        if not saves:
            print("No valid SOL entries found in localStorage.")
            if not args.dump_raw:
                print("Re-run with --dump-raw to see all localStorage contents.")
            return 1

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(saves, indent=2), encoding="utf-8")

        total_bytes = sum(len(v) for v in saves.values())
        print(f"Exported {len(saves)} entries ({total_bytes:,} bytes) to {output}")
        for key in sorted(saves):
            print(f"  {key} ({len(saves[key]):,} bytes)")

        return 0
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
