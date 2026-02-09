#!/usr/bin/env python3
"""Launch the game with a click coordinate logger overlay.

Click anywhere — coordinates are displayed on-screen and printed to terminal.
Use these to calibrate menu_nav.py.

Usage:
  python scripts/debug_nav.py
  python scripts/debug_nav.py --wait 10
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


COORD_OVERLAY_JS = """
(() => {
    const CONTAINER_X = %d;
    const CONTAINER_Y = %d;

    // Overlay div for showing coordinates
    const overlay = document.createElement('div');
    overlay.id = 'coord-overlay';
    overlay.style.cssText = `
        position: fixed; top: 10px; right: 10px; z-index: 99999;
        background: rgba(0,0,0,0.85); color: #0f0; font: bold 14px monospace;
        padding: 10px 14px; border-radius: 6px; pointer-events: none;
        min-width: 260px;
    `;
    overlay.innerHTML = 'Click anywhere to log coordinates';
    document.body.appendChild(overlay);

    // Log area
    const log = document.createElement('div');
    log.id = 'coord-log';
    log.style.cssText = `
        position: fixed; bottom: 10px; right: 10px; z-index: 99999;
        background: rgba(0,0,0,0.85); color: #fff; font: 12px monospace;
        padding: 8px 12px; border-radius: 6px; pointer-events: none;
        max-height: 200px; overflow-y: auto; min-width: 300px;
    `;
    document.body.appendChild(log);

    let clickNum = 0;

    document.addEventListener('click', (e) => {
        clickNum++;
        const contentX = Math.round(e.clientX - CONTAINER_X);
        const contentY = Math.round(e.clientY - CONTAINER_Y);

        overlay.innerHTML = `
            Page: (${e.clientX}, ${e.clientY})<br>
            <b>Content: (${contentX}, ${contentY})</b>
        `;

        const entry = document.createElement('div');
        entry.style.borderBottom = '1px solid #333';
        entry.style.padding = '2px 0';
        entry.innerHTML = `#${clickNum}: content=(${contentX}, ${contentY})`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;

        // Expose to Python via window property
        window.__LAST_CLICK__ = {num: clickNum, contentX, contentY, pageX: e.clientX, pageY: e.clientY};
        console.log(`CLICK #${clickNum}: content=(${contentX}, ${contentY}) page=(${e.clientX}, ${e.clientY})`);
    }, true);
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swf", default=str(DEFAULT_SWF))
    ap.add_argument("--saves", default=None)
    ap.add_argument("--wait", type=float, default=8.0, help="Seconds to wait for game load")
    args = ap.parse_args()

    cfg = HarnessConfig(
        headless=False,
        save_data_path=Path(args.saves) if args.saves else None,
        block_network=True,
        auto_navigate_to_round=False,
        startup_wait_s=args.wait,
    )

    env = BloonsWebEnv(repo_root=REPO_ROOT, swf_path=Path(args.swf).expanduser().resolve(), cfg=cfg)

    try:
        run_dir = env.reset()

        box = env.page.locator("div#container").first.bounding_box()
        cx, cy = int(box['x']), int(box['y'])

        print(f"\nContainer offset: x={cx}, y={cy}")
        print(f"Content area: {box['width']:.0f}x{box['height']:.0f}")

        # Inject coordinate overlay
        env.page.evaluate(COORD_OVERLAY_JS % (cx, cy))

        print(f"\nClick through the game menus. Content-relative coordinates")
        print(f"will appear on-screen and in the browser console.")
        print(f"\nClose the browser window when done.\n")

        try:
            env.page.wait_for_timeout(10**9)
        except Exception:
            pass

        return 0
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
