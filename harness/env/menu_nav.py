"""Menu navigation for BTD5 via coordinate-based click sequences.

All coordinates are relative to the fixed 960x720 content area.
Coordinates are placeholders -- calibrate via screenshot inspection.
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


# Click targets (placeholder coordinates)
PLAY_BUTTON = NavCoord("play", 480, 400)

MAPS = {
    "monkey_lane": NavCoord("monkey_lane", 200, 250),
    "the_rink":    NavCoord("the_rink",    200, 310),
    "park_path":   NavCoord("park_path",   200, 370),
}

DIFFICULTIES = {
    "easy":   NavCoord("easy",   300, 450),
    "medium": NavCoord("medium", 480, 450),
    "hard":   NavCoord("hard",   660, 450),
}

DIALOG_DISMISS = NavCoord("dialog_dismiss", 480, 500)
START_GAME = NavCoord("start_game", 480, 550)


def _click(page: Page, coord: NavCoord, container_box: dict) -> None:
    abs_x = container_box["x"] + coord.x
    abs_y = container_box["y"] + coord.y
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
    step_delay_s: float = 1.5,
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

    return screenshots
