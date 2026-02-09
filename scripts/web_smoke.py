#!/usr/bin/env python3
"""Smoke test: load BTD5 in Ruffle Web, take screenshots, perform a click."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SWF = REPO_ROOT / "game" / "btd5.swf"

from harness.runtime.local_http import serve_directory
from harness.runtime.ruffle_web_vendor import ensure_ruffle_web


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swf", default=str(DEFAULT_SWF))
    ap.add_argument("--ruffle-tag", default="nightly-2026-02-09")
    ap.add_argument("--out", default="logs/web_smoke")
    args = ap.parse_args()

    run_dir = (REPO_ROOT / args.out).resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    swf_path = Path(args.swf).expanduser().resolve()
    if not swf_path.exists():
        raise SystemExit(f"SWF not found: {swf_path}")

    ruffle = ensure_ruffle_web(REPO_ROOT, tag=args.ruffle_tag)

    www = run_dir / "www"
    www.mkdir(parents=True, exist_ok=True)
    for p in ruffle.dir.iterdir():
        if p.is_file():
            dest = www / p.name
            if not dest.exists():
                dest.symlink_to(p)

    wrapper_src = REPO_ROOT / "harness" / "runtime" / "ruffle_wrapper.html"
    shutil.copy2(wrapper_src, www / "index.html")
    (www / "game.swf").symlink_to(swf_path)

    server = serve_directory(www)
    base = server.base_url
    url = f"{base}/index.html?swf={base}/game.swf"

    action_log = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(run_dir / "chromium-profile"),
            headless=False,
            viewport={"width": 1100, "height": 860},
        )
        page = ctx.new_page()
        action_log.append({"t": time.time(), "op": "goto", "url": url})
        page.goto(url)

        page.wait_for_function("window.__BLOONSBENCH__ && window.__BLOONSBENCH__.player")
        page.wait_for_timeout(1000)

        page.screenshot(path=str(run_dir / "frame_000.png"), full_page=True)
        action_log.append({"t": time.time(), "op": "screenshot", "path": "frame_000.png"})

        box = page.locator("div#container").first.bounding_box()
        if box:
            cx = box["x"] + box["width"] * 0.5
            cy = box["y"] + box["height"] * 0.5
            page.mouse.click(cx, cy)
            action_log.append({"t": time.time(), "op": "click", "x": cx, "y": cy})

        page.wait_for_timeout(1000)
        page.screenshot(path=str(run_dir / "frame_001.png"), full_page=True)
        action_log.append({"t": time.time(), "op": "screenshot", "path": "frame_001.png"})

        ctx.close()

    (run_dir / "actions.jsonl").write_text("\n".join(json.dumps(a) for a in action_log) + "\n")
    server.httpd.shutdown()

    print(f"Run dir: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
