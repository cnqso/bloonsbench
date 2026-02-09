#!/usr/bin/env python3
"""Demo: pre-round -> round sampling -> pre-round loop."""

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
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    cfg = HarnessConfig(headless=args.headless)
    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    run_dir = env.reset()
    env.observe(tag="preround")
    env.click_content(cfg.content_width * 0.5, cfg.content_height * 0.5)
    env.start_round()
    env.sample_round()
    env.observe(tag="back_to_preround")
    env.close()

    print(f"Run artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
