"""Menu navigation and tower placement for BTD5.

All coordinates are content-relative (960x720 area), calibrated via debug_nav.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page


@dataclass(frozen=True)
class NavCoord:
    name: str
    x: float
    y: float


# ── Menu navigation ──────────────────────────────────────────────
PLAY_BUTTON = NavCoord("play", 828, 533)

MAPS = {
    "monkey_lane": NavCoord("monkey_lane", 331, 276),
}

DIFFICULTIES = {
    "easy":   NavCoord("easy", 210, 268),
}

DIALOG_DISMISS = NavCoord("dialog_dismiss", 491, 526)
START_GAME = NavCoord("start_game", 458, 261)
CLOSE_PREMIUM = NavCoord("close_premium", 728, 91)

# ── Tower selection ──────────────────────────────────────────────
TOWER_PAGE_1 = NavCoord("tower_page_1", 884, 100)
TOWER_PAGE_2 = NavCoord("tower_page_2", 887, 466)


@dataclass(frozen=True)
class TowerDef:
    name: str
    page: int        # 1 or 2
    icon: NavCoord   # click target on the sidebar


TOWERS = {
    "dart_monkey":  TowerDef("dart_monkey",  1, NavCoord("dart_monkey_icon", 859, 144)),
    "tack_shooter": TowerDef("tack_shooter", 1, NavCoord("tack_shooter_icon", 918, 148)),
}


def _click(page: Page, coord: NavCoord, container_box: dict) -> None:
    abs_x = container_box["x"] + coord.x
    abs_y = container_box["y"] + coord.y
    # Show red dot at click location
    try:
        page.evaluate(f"window.__BLOONSBENCH__?.showDot?.({abs_x}, {abs_y})")
    except Exception:
        pass
    page.mouse.click(abs_x, abs_y)


def _get_container_box(page: Page) -> dict:
    box = page.locator("div#container").first.bounding_box()
    if not box:
        raise RuntimeError("Container bounding box not available")
    return box


def navigate_to_round(
    page: Page,
    map_name: str = "monkey_lane",
    difficulty: str = "easy",
    screenshot_dir: Optional[Path] = None,
    step_delay_s: float = 2.0,
) -> list[Path]:
    """Navigate from main menu to round 1 start.

    Returns list of screenshot paths (empty if screenshot_dir is None).
    """
    if map_name not in MAPS:
        raise ValueError(f"Unknown map: {map_name!r}. Known: {list(MAPS)}")
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"Unknown difficulty: {difficulty!r}. Known: {list(DIFFICULTIES)}")

    box = _get_container_box(page)
    screenshots: list[Path] = []
    step_idx = 0

    def _step(name: str, coord: NavCoord) -> None:
        nonlocal step_idx
        _click(page, coord, box)
        page.wait_for_timeout(int(step_delay_s * 1000))
        if screenshot_dir:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = screenshot_dir / f"nav_{step_idx:02d}_{name}.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(path)
        step_idx += 1

    _step("dismiss_dialog", DIALOG_DISMISS)
    _step("play", PLAY_BUTTON)
    _step(f"map_{map_name}", MAPS[map_name])
    _step(f"diff_{difficulty}", DIFFICULTIES[difficulty])
    _step("start", START_GAME)
    _step("close_premium", CLOSE_PREMIUM)

    return screenshots


def place_tower(
    page: Page,
    tower_name: str,
    x: float,
    y: float,
    click_delay_s: float = 0.5,
) -> None:
    """Select a tower from the sidebar and place it at (x, y) on the map.

    Coordinates are content-relative (960x720).
    """
    if tower_name not in TOWERS:
        raise ValueError(f"Unknown tower: {tower_name!r}. Known: {list(TOWERS)}")

    tower = TOWERS[tower_name]
    box = _get_container_box(page)

    # Click the correct page tab
    page_btn = TOWER_PAGE_1 if tower.page == 1 else TOWER_PAGE_2
    _click(page, page_btn, box)
    page.wait_for_timeout(int(click_delay_s * 1000))

    # Click the tower icon
    _click(page, tower.icon, box)
    page.wait_for_timeout(int(click_delay_s * 1000))

    # Click the map to place it
    _click(page, NavCoord(f"place_{tower_name}", x, y), box)
    page.wait_for_timeout(int(click_delay_s * 1000))
