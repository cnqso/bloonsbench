"""Menu navigation and tower placement for BTD5.

All coordinates are content-relative (960x720 area), calibrated via debug_nav.py.
All prices are base costs (medium difficulty).
NOTE: The "easy" difficulty coord actually selects medium in-game.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import Page


@dataclass(frozen=True)
class NavCoord:
    name: str
    x: float
    y: float


@dataclass(frozen=True)
class UpgradeTier:
    name: str
    cost: int


# ── Menu navigation ──────────────────────────────────────────────
PLAY_BUTTON = NavCoord("play", 828, 533)

MAPS = {
    "monkey_lane": NavCoord("monkey_lane", 331, 276),
}

DIFFICULTIES = {
    "easy":   NavCoord("easy", 210, 268),  # actually selects medium in-game
}

DISMISS_OFFLINE = NavCoord("dismiss_offline", 434, 13)
DIALOG_DISMISS = NavCoord("dialog_dismiss", 491, 526)
START_GAME = NavCoord("start_game", 458, 261)
CLOSE_PREMIUM = NavCoord("close_premium", 728, 91)
GO_BUTTON = NavCoord("go_button", 900, 600)
DESELECT_SPOT = NavCoord("deselect", 84, 32)  # windmill — safe neutral click

# ── Tower selection ──────────────────────────────────────────────
TOWER_PAGE_1 = NavCoord("tower_page_1", 884, 100)
TOWER_PAGE_2 = NavCoord("tower_page_2", 885, 469)

_U = UpgradeTier  # shorthand


@dataclass(frozen=True)
class TowerDef:
    name: str
    page: int        # 1 or 2
    icon: NavCoord   # click target on the sidebar
    cost: int        # base cost
    path1: Tuple[UpgradeTier, ...]  # up to 4 tiers
    path2: Tuple[UpgradeTier, ...]


# Grid layout: 2 columns (x≈858, x≈918), rows spaced ~54px starting at y≈146
_LX, _RX = 858, 918
_ROW_Y = [146, 200, 254, 308, 362, 416]

TOWERS = {
    # ── Page 1 ──────────────────────────────────────────────────
    "dart_monkey": TowerDef(
        "dart_monkey", 1, NavCoord("dart_monkey", _LX, _ROW_Y[0]), cost=200,
        path1=(_U("Long Range Darts", 90), _U("Enhanced Eyesight", 120),
               _U("Spike-O-Pult", 500), _U("Juggernaut", 1500)),
        path2=(_U("Sharp Shots", 140), _U("Razor Sharp Shots", 170),
               _U("Triple Darts", 330), _U("Super Monkey Fan Club", 8000)),
    ),
    "tack_shooter": TowerDef(
        "tack_shooter", 1, NavCoord("tack_shooter", _RX, _ROW_Y[0]), cost=360,
        path1=(_U("Faster Shooting", 210), _U("Even Faster Shooting", 300),
               _U("Tack Sprayer", 500), _U("Ring of Fire", 2500)),
        path2=(_U("Extra Range Tacks", 100), _U("Super Range Tacks", 225),
               _U("Blade Shooter", 680), _U("Blade Maelstrom", 2700)),
    ),
    "sniper_monkey": TowerDef(
        "sniper_monkey", 1, NavCoord("sniper_monkey", _LX, _ROW_Y[1]), cost=350,
        path1=(_U("Full Metal Jacket", 350), _U("Point Five Oh", 2200),
               _U("Deadly Precision", 4000), _U("Cripple MOAB", 12500)),
        path2=(_U("Faster Firing", 400), _U("Night Vision Goggles", 300),
               _U("Semi-Automatic Rifle", 3500), _U("Supply Drop", 12000)),
    ),
    "boomerang_thrower": TowerDef(
        "boomerang_thrower", 1, NavCoord("boomerang_thrower", _RX, _ROW_Y[1]), cost=400,
        path1=(_U("Multi-Target", 250), _U("Glaive Thrower", 280),
               _U("Glaive Riccochet", 1500), _U("Glaive Lord", 9000)),
        path2=(_U("Sonic Boom", 100), _U("Red Hot 'Rangs", 150),
               _U("Bionic Boomer", 1600), _U("Turbo Charge", 3000)),
    ),
    "ninja_monkey": TowerDef(
        "ninja_monkey", 1, NavCoord("ninja_monkey", _LX, _ROW_Y[2]), cost=500,
        path1=(_U("Ninja Discipline", 300), _U("Sharp Shurikens", 350),
               _U("Double Shot", 850), _U("Bloonjitsu", 2750)),
        path2=(_U("Seeking Shuriken", 250), _U("Distraction", 350),
               _U("Flash Bomb", 2750), _U("Sabotage Supply Lines", 2800)),
    ),
    "bomb_tower": TowerDef(
        "bomb_tower", 1, NavCoord("bomb_tower", _RX, _ROW_Y[2]), cost=650,
        path1=(_U("Longer Range", 200), _U("Frag Bombs", 300),
               _U("Cluster Bombs", 800), _U("Bloon Impact", 4000)),
        path2=(_U("Bigger Bombs", 400), _U("Missile Launcher", 400),
               _U("MOAB Mauler", 900), _U("MOAB Assassin", 3200)),
    ),
    "ice_tower": TowerDef(
        "ice_tower", 1, NavCoord("ice_tower", _LX, _ROW_Y[3]), cost=300,
        path1=(_U("Enhanced Freeze", 225), _U("Snap Freeze", 400),
               _U("Arctic Wind", 6500), _U("Viral Frost", 6000)),
        path2=(_U("Permafrost", 100), _U("Deep Freeze", 350),
               _U("Ice Shards", 2000), _U("Absolute Zero", 2000)),
    ),
    "glue_gunner": TowerDef(
        "glue_gunner", 1, NavCoord("glue_gunner", _RX, _ROW_Y[3]), cost=270,
        path1=(_U("Glue Soak", 200), _U("Corrosive Glue", 300),
               _U("Bloon Dissolver", 3300), _U("Bloon Liquefier", 12500)),
        path2=(_U("Stickier Glue", 120), _U("Glue Splatter", 2200),
               _U("Glue Hose", 3250), _U("Glue Striker", 5500)),
    ),
    "monkey_buccaneer": TowerDef(
        "monkey_buccaneer", 1, NavCoord("monkey_buccaneer", _LX, _ROW_Y[4]), cost=500,
        path1=(_U("Faster Shooting", 400), _U("Longer Cannons", 180),
               _U("Destroyer", 2200), _U("Aircraft Carrier", 15000)),
        path2=(_U("Grape Shot", 500), _U("Crow's Nest", 250),
               _U("Cannon Ship", 1200), _U("Monkey Pirates", 4500)),
    ),
    "monkey_ace": TowerDef(
        "monkey_ace", 1, NavCoord("monkey_ace", _RX, _ROW_Y[4]), cost=900,
        path1=(_U("Rapid Fire", 700), _U("Sharper Darts", 500),
               _U("Neva-Miss Targeting", 2200), _U("Spectre", 18000)),
        path2=(_U("Pineapple Express", 200), _U("Spy Plane", 350),
               _U("Operation: Dart Storm", 3000), _U("Ground Zero", 14000)),
    ),
    "super_monkey": TowerDef(
        "super_monkey", 1, NavCoord("super_monkey", _LX, _ROW_Y[5]), cost=3500,
        path1=(_U("Laser Blasts", 3500), _U("Plasma Blasts", 5000),
               _U("Sun God", 16500), _U("Temple of the Monkey God", 100000)),
        path2=(_U("Super Range", 1000), _U("Epic Range", 1500),
               _U("Robo Monkey", 9000), _U("Technological Terror", 25000)),
    ),
    "monkey_apprentice": TowerDef(
        "monkey_apprentice", 1, NavCoord("monkey_apprentice", _RX, _ROW_Y[5]), cost=550,
        path1=(_U("Intense Magic", 300), _U("Lightning Bolt", 1200),
               _U("Summon Whirlwind", 2000), _U("Tempest Tornado", 8000)),
        path2=(_U("Fireball", 300), _U("Monkey Sense", 300),
               _U("Dragon's Breath", 4200), _U("Summon Phoenix", 5000)),
    ),
    # ── Page 2 ──────────────────────────────────────────────────
    "monkey_village": TowerDef(
        "monkey_village", 2, NavCoord("monkey_village", 859, 314), cost=1600,
        path1=(_U("Monkey Beacon", 500), _U("Jungle Drums", 1500),
               _U("Monkey Town", 10000), _U("High Energy Beacon", 12000)),
        path2=(_U("Monkey Fort", 900), _U("Radar Scanner", 2000),
               _U("Monkey Intelligence Bureau", 4300), _U("MIB Call to Arms", 24000)),
    ),
    "banana_farm": TowerDef(
        "banana_farm", 2, NavCoord("banana_farm", 915, 312), cost=1000,
        path1=(_U("More Bananas", 300), _U("Banana Plantation", 1400),
               _U("Banana Republic", 3200), _U("Banana Research Facility", 14000)),
        path2=(_U("Long Life Bananas", 500), _U("Valuable Bananas", 4000),
               _U("Monkey Bank", 4200), _U("Banana Investments Advisory", 5500)),
    ),
    "mortar_tower": TowerDef(
        "mortar_tower", 2, NavCoord("mortar_tower", 860, 370), cost=750,
        path1=(_U("Increased Accuracy", 200), _U("Bigger Blast", 600),
               _U("Bloon Buster", 800), _U("The Big One", 10000)),
        path2=(_U("Rapid Reload", 250), _U("Burny Stuff", 500),
               _U("Signal Flare", 500), _U("Artillery Battery", 9000)),
    ),
    "dartling_gun": TowerDef(
        "dartling_gun", 2, NavCoord("dartling_gun", 920, 370), cost=950,
        path1=(_U("Focused Firing", 250), _U("Faster Barrel Spin", 1200),
               _U("Laser Cannon", 6000), _U("Ray of Doom", 55000)),
        path2=(_U("Powerful Darts", 600), _U("Depleted Bloontonium Darts", 1000),
               _U("Hydra Rocket Pods", 7000), _U("Bloon Area Denial System", 20000)),
    ),
    "spike_factory": TowerDef(
        "spike_factory", 2, NavCoord("spike_factory", 858, 427), cost=750,
        path1=(_U("Bigger Stacks", 700), _U("White Hot Spikes", 900),
               _U("Spiked Ball Factory", 2400), _U("Spiked Mines", 14000)),
        path2=(_U("Faster Production", 800), _U("Even Faster Production", 1250),
               _U("MOAB-SHREDR Spikes", 3000), _U("Spike Storm", 6500)),
    ),
    # "exploding_pineapple" at (914, 520) — disabled
}

# ── Upgrade / sell UI ────────────────────────────────────────────
UPGRADE_PATH_1 = NavCoord("upgrade_path_1", 558, 676)
UPGRADE_PATH_2 = NavCoord("upgrade_path_2", 785, 676)
SELL_BUTTON = NavCoord("sell", 346, 654)

TARGETS = {
    "first":  NavCoord("target_first",  150, 700),
    "last":   NavCoord("target_last",   215, 700),
    "close":  NavCoord("target_close",  280, 700),
    "strong": NavCoord("target_strong", 350, 700),
}

# ── Placement validation (Monkey Lane) ──────────────────────────
# Each zone is (x1, y1, x2, y2) — valid rectangles for tower placement.
MONKEY_LANE_ZONES: list[Tuple[int, int, int, int]] = [
    (198, 215, 508, 256),   # Horizontal strip below top path
    (245, 126, 619, 167),   # Top strip
    (575, 126, 615, 435),   # Vertical strip right side
    (379, 396, 606, 435),   # Horizontal strip mid-right
    (380, 227, 504, 427),   # Large center block
    (574, 219, 731, 264),   # Right bend area
    ( 30, 308, 179, 349),   # Left side near first curve
    (272, 312, 317, 612),   # Vertical strip center-bottom
    ( 30, 414, 173, 612),   # Bottom-left area
]

TOWER_EXCLUSION_RADIUS = 40


def next_upgrade(
    tower_name: str,
    path: int,
    current_level: int,
    other_path_level: int = 0,
) -> Optional[UpgradeTier]:
    """Return the next UpgradeTier for a path, or None if maxed/locked.

    BTD5 specialization rule: only one path can exceed tier 2.
    If the other path is already at 3+, this path is locked at 2.
    """
    tdef = TOWERS.get(tower_name)
    if not tdef:
        return None
    # Specialization: can't upgrade past 2 if the other path is already 3+
    if current_level + 1 > 2 and other_path_level > 2:
        return None
    upgrades = tdef.path1 if path == 1 else tdef.path2
    if current_level < len(upgrades):
        return upgrades[current_level]
    return None


def validate_placement(
    x: float,
    y: float,
    placed_towers: dict,
    tower_name: str,
) -> Tuple[bool, str]:
    """Check whether (x, y) is a valid placement on Monkey Lane.

    Returns (ok, reason).  reason is "" on success.
    """
    import math

    # 1. Must be inside at least one valid zone
    in_zone = any(
        x1 <= x <= x2 and y1 <= y <= y2
        for x1, y1, x2, y2 in MONKEY_LANE_ZONES
    )
    if not in_zone:
        return False, (
            f"({x}, {y}) is not in any valid placement zone for Monkey Lane. "
            "Use 'status' to see valid zones and already-placed towers."
        )

    # 2. Must be far enough from every existing tower
    for t in placed_towers.values():
        dx = x - t.x
        dy = y - t.y
        dist = math.hypot(dx, dy)
        if dist < TOWER_EXCLUSION_RADIUS:
            return False, (
                f"({x}, {y}) is too close to tower #{t.id} '{t.name}' at "
                f"({t.x}, {t.y}) — distance {dist:.0f}px < {TOWER_EXCLUSION_RADIUS}px minimum. "
                "Use 'status' to see already-placed towers."
            )

    return True, ""


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

    _step("dismiss_offline", DISMISS_OFFLINE)
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

    # Deselect so the info panel closes
    _click(page, DESELECT_SPOT, box)
    page.wait_for_timeout(int(click_delay_s * 1000))


def select_tower_at(page: Page, x: float, y: float, click_delay_s: float = 0.3) -> None:
    """Click a placed tower on the map to open its info panel."""
    box = _get_container_box(page)
    _click(page, NavCoord("select_tower", x, y), box)
    page.wait_for_timeout(int(click_delay_s * 1000))


def click_upgrade(page: Page, path: int, click_delay_s: float = 0.3) -> None:
    """Click an upgrade button. path is 1 or 2."""
    if path not in (1, 2):
        raise ValueError(f"path must be 1 or 2, got {path}")
    box = _get_container_box(page)
    coord = UPGRADE_PATH_1 if path == 1 else UPGRADE_PATH_2
    _click(page, coord, box)
    page.wait_for_timeout(int(click_delay_s * 1000))


def click_sell(page: Page, click_delay_s: float = 0.3) -> None:
    """Click the sell button (tower must already be selected)."""
    box = _get_container_box(page)
    _click(page, SELL_BUTTON, box)
    page.wait_for_timeout(int(click_delay_s * 1000))


def click_target(page: Page, target: str, click_delay_s: float = 0.3) -> None:
    """Click a targeting mode button (tower must already be selected)."""
    target = target.lower()
    if target not in TARGETS:
        raise ValueError(f"Unknown target: {target!r}. Options: {list(TARGETS)}")
    box = _get_container_box(page)
    _click(page, TARGETS[target], box)
    page.wait_for_timeout(int(click_delay_s * 1000))


def deselect(page: Page) -> None:
    """Click a neutral spot to close the tower info panel."""
    box = _get_container_box(page)
    _click(page, DESELECT_SPOT, box)
    page.wait_for_timeout(200)
