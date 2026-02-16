"""BloonsWebEnv: Ruffle Web + Playwright harness for Bloons TD5."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from harness.env.config import HarnessConfig
from harness.env.network import block_nk_domains
from harness.env.save_data import import_saves_from_file
from harness.env.menu_nav import (
    TOWERS, GO_BUTTON, DESELECT_SPOT, NavCoord,
    navigate_to_round, place_tower, validate_placement, next_upgrade,
    select_tower_at, click_upgrade, click_sell, click_target, deselect,
    _get_container_box, _click,
)
from harness.perception.cash_ocr import GameStateReader, GameState, OK_CLICK_TARGET
from harness.runtime.local_http import LocalServer, serve_directory
from harness.runtime.ruffle_web_vendor import ensure_ruffle_web
from harness.trace.logger import TraceLogger


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class PlacedTower:
    """Tracks a tower that has been placed on the map."""
    id: int
    name: str
    x: float
    y: float
    upgrades: list  # [path1_level, path2_level]
    target: str = "first"  # first, last, close, strong


@dataclass
class BloonsWebEnv:
    repo_root: Path
    swf_path: Path
    cfg: HarnessConfig

    run_dir: Optional[Path] = None
    logger: Optional[TraceLogger] = None
    server: Optional[LocalServer] = None
    _pw = None
    ctx: Optional[BrowserContext] = None
    page: Optional[Page] = None

    # Tower tracking
    _next_tower_id: int = 1
    _placed_towers: Dict[int, PlacedTower] = field(default_factory=dict)
    _state_reader: Optional[GameStateReader] = None
    _last_game_state: GameState = field(default_factory=GameState)

    def reset(self, out_root: Optional[Path] = None) -> Path:
        """Start a new run. Returns run_dir."""
        self.close()

        # Reset tower tracking
        self._next_tower_id = 1
        self._placed_towers = {}
        self._last_game_state = GameState()

        out_root = (out_root or (self.repo_root / "logs" / "runs")).resolve()
        self.run_dir = out_root / _ts()
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.logger = TraceLogger(self.run_dir)
        self.logger.log("reset", swf=str(self.swf_path), ruffle_tag=self.cfg.ruffle_tag)

        ruffle = ensure_ruffle_web(self.repo_root, tag=self.cfg.ruffle_tag)

        # Stage www directory with symlinked Ruffle assets
        www = self.run_dir / "www"
        www.mkdir(parents=True, exist_ok=True)
        for p in ruffle.dir.iterdir():
            if p.is_file():
                dest = www / p.name
                if not dest.exists():
                    dest.symlink_to(p)

        # Copy wrapper and apply content dimensions
        wrapper_src = self.repo_root / "harness" / "runtime" / "ruffle_wrapper.html"
        wrapper_text = wrapper_src.read_text(encoding="utf-8")
        wrapper_text = wrapper_text.replace("960px", f"{self.cfg.content_width}px").replace("720px", f"{self.cfg.content_height}px")
        (www / "index.html").write_text(wrapper_text, encoding="utf-8")

        game_link = www / "game.swf"
        if not game_link.exists():
            game_link.symlink_to(self.swf_path)

        self.server = serve_directory(www, port=self.cfg.server_port)
        base = self.server.base_url

        use_deferred = self.cfg.save_data_path is not None
        url = f"{base}/index.html?swf={base}/game.swf"
        if use_deferred:
            url += "&defer=1"

        # Browser profile — reuse persistent dir when configured,
        # otherwise create an ephemeral per-run profile.
        profile_dir = self.run_dir / "chromium-profile"
        if self.cfg.persistent_profile_dir:
            profile_dir = Path(self.cfg.persistent_profile_dir).expanduser().resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.logger.log("profile_persistent", dir=str(profile_dir))

        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self.cfg.headless,
            viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
        )
        self.page = self.ctx.new_page()

        if self.cfg.block_network:
            block_nk_domains(self.page)
            self.logger.log("network_blocked")

        self.logger.log("goto", url=url)
        self.page.goto(url)
        self.page.wait_for_function("window.__BLOONSBENCH__ && window.__BLOONSBENCH__.player")

        # Inject save data before game loads (deferred mode)
        if use_deferred:
            save_path = Path(self.cfg.save_data_path).expanduser().resolve()
            n = import_saves_from_file(self.page, save_path)
            self.logger.log("saves_injected", path=str(save_path), count=n)
            self.page.evaluate("window.__BLOONSBENCH__.loadGame()")
            self.logger.log("deferred_load_triggered")

        self.page.wait_for_timeout(int(self.cfg.startup_wait_s * 1000))
        self.observe(tag="startup")

        if self.cfg.auto_navigate_to_round:
            nav_dir = self.run_dir / "nav_screenshots"
            navigate_to_round(
                self.page,
                map_name=self.cfg.nav_map_name,
                difficulty=self.cfg.nav_difficulty,
                screenshot_dir=nav_dir,
            )
            self.logger.log("menu_navigation_complete", map=self.cfg.nav_map_name, difficulty=self.cfg.nav_difficulty)

        return self.run_dir

    # ── Screenshot + OCR core ────────────────────────────────────────

    def _capture_screenshot(self, path: str | Path) -> None:
        """Capture viewport screenshot with options that reduce headful jitter."""
        assert self.page
        self.page.screenshot(
            path=str(path),
            animations="disabled",
            caret="hide",
            scale="css",
        )

    def _get_reader(self) -> GameStateReader:
        if self._state_reader is None:
            debug_dir = self.run_dir / "ocr_debug" if self.run_dir else None
            self._state_reader = GameStateReader(
                debug_dir=debug_dir,
                backend=self.cfg.ocr_backend,
                easyocr_gpu=self.cfg.ocr_easyocr_gpu,
            )
        return self._state_reader

    def _update_state(self, screenshot_path: str | Path | None = None) -> None:
        """Take a screenshot (or reuse one), run OCR + OK detection, cache result.

        If screenshot_path is provided, uses that image (no new screenshot).
        Otherwise takes a temp screenshot, processes it, and deletes it.
        """
        assert self.page
        box = _get_container_box(self.page)
        reader = self._get_reader()

        cleanup = False
        if screenshot_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            screenshot_path = tmp.name
            tmp.close()
            self._capture_screenshot(screenshot_path)
            cleanup = True

        state, ok_detected = reader.update(str(screenshot_path), box)

        if ok_detected:
            cx, cy = OK_CLICK_TARGET
            _click(self.page, NavCoord("dismiss_ok", cx, cy), box)
            self.page.wait_for_timeout(300)
            if self.logger:
                self.logger.log("auto_dismiss_ok")
            # Retake + re-read after dismissal
            if cleanup:
                Path(screenshot_path).unlink(missing_ok=True)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            screenshot_path = tmp.name
            tmp.close()
            self._capture_screenshot(screenshot_path)
            cleanup = True
            state, _ = reader.update(str(screenshot_path), box)

        if cleanup:
            Path(screenshot_path).unlink(missing_ok=True)

        self._last_game_state = state

    def observe(self, tag: str = "obs") -> Path:
        """Take a screenshot, run OCR + OK detection from it, return path."""
        assert self.page and self.logger and self.run_dir
        fname = f"{tag}_{int(time.time()*1000)}.png"
        out = self.run_dir / fname
        self._capture_screenshot(out)

        # Run OCR + OK detection on this same screenshot (no extra screenshot)
        box = _get_container_box(self.page)
        state, ok_detected = self._get_reader().update(str(out), box)

        if ok_detected:
            cx, cy = OK_CLICK_TARGET
            _click(self.page, NavCoord("dismiss_ok", cx, cy), box)
            self.page.wait_for_timeout(300)
            if self.logger:
                self.logger.log("auto_dismiss_ok")
            # Retake since dialog changed the screen
            self._capture_screenshot(out)
            state, _ = self._get_reader().update(str(out), box)

        self._last_game_state = state
        self.logger.log("screenshot", tag=tag, path=fname)
        return out

    def read_game_state(self) -> GameState:
        """Return the cached game state from the last screenshot."""
        return self._last_game_state

    def read_cash(self) -> int | None:
        """Return cached cash from the last screenshot."""
        return self._last_game_state.cash

    # ── Actions (each followed by _update_state) ─────────────────────

    def click_content(self, x: float, y: float) -> None:
        """Click at coordinates relative to the content container."""
        assert self.page and self.logger
        box = self.page.locator("div#container").first.bounding_box()
        if not box:
            raise RuntimeError("Container bounding box not available")
        abs_x = box["x"] + x
        abs_y = box["y"] + y
        self.logger.log("click", x=x, y=y, abs_x=abs_x, abs_y=abs_y)
        self.page.evaluate(f"window.__BLOONSBENCH__?.showDot({abs_x}, {abs_y})")
        self.page.mouse.click(abs_x, abs_y)
        self._update_state()

    def place_tower(self, tower_name: str, x: float, y: float) -> int:
        """Select and place a tower at content-relative (x, y).

        Returns the tower ID for later reference (upgrade/sell).
        Raises ValueError if the placement is invalid (off-zone or too close).
        """
        assert self.page and self.logger
        if tower_name not in TOWERS:
            raise ValueError(f"Unknown tower: {tower_name!r}. Known: {list(TOWERS)}")
        ok, reason = validate_placement(x, y, self._placed_towers, tower_name)
        if not ok:
            raise ValueError(reason)
        tower_def = TOWERS[tower_name]
        cash = self.read_cash()
        if cash is not None and cash < tower_def.cost:
            raise ValueError(
                f"Cannot afford {tower_name} (${tower_def.cost}), current cash: ${cash}. "
                "Use 'status' to check cash, or sell a tower first."
            )
        # Fresh OCR to get authoritative cash before placement
        self._update_state()
        cash_before = self._last_game_state.cash

        tid = self._next_tower_id
        self._next_tower_id += 1
        self.logger.log("place_tower", tower=tower_name, x=x, y=y, tower_id=tid)
        place_tower(self.page, tower_name, x, y)
        self._placed_towers[tid] = PlacedTower(
            id=tid, name=tower_name, x=x, y=y, upgrades=[0, 0],
        )
        self._update_state()
        cash_after = self._last_game_state.cash

        if cash_before == cash_after:
            # Cash didn't change at all — placement almost certainly failed.
            # (Both None, or both the same number.)
            del self._placed_towers[tid]
            self._next_tower_id -= 1
            # Cancel any dangling placement cursor
            box = _get_container_box(self.page)
            _click(self.page, DESELECT_SPOT, box)
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)
            self.logger.log("place_tower_failed", tower=tower_name, x=x, y=y,
                            cash_before=cash_before, cash_after=cash_after)
            raise ValueError(
                f"Placement of {tower_name} at ({x}, {y}) appears to have failed "
                f"(cash unchanged: {cash_before} → {cash_after}). "
                "The spot may be invalid. Try different coordinates."
            )
        return tid

    def upgrade_tower(self, tower_id: int, path: int) -> None:
        """Upgrade a placed tower. path is 1 or 2."""
        assert self.page and self.logger
        if tower_id not in self._placed_towers:
            raise ValueError(f"No tower with id {tower_id}. Placed: {list(self._placed_towers)}")
        tower = self._placed_towers[tower_id]
        other_path = 2 if path == 1 else 1
        nxt = next_upgrade(tower.name, path, tower.upgrades[path - 1],
                           other_path_level=tower.upgrades[other_path - 1])
        if nxt is None:
            raise ValueError(
                f"Tower #{tower_id} {tower.name} path {path} is locked or maxed "
                f"(current: {tower.upgrades[0]}/{tower.upgrades[1]}). "
                "Only one path can go past tier 2."
            )
        cash = self.read_cash()
        if cash is not None and cash < nxt.cost:
            raise ValueError(
                f"Cannot afford {nxt.name} (${nxt.cost}), current cash: ${cash}. "
                "Use 'status' to check cash, or sell a tower first."
            )
        # Fresh OCR to get authoritative cash before upgrade
        self._update_state()
        cash_before = self._last_game_state.cash

        self.logger.log("upgrade_tower", tower_id=tower_id, path=path,
                        name=tower.name, before=list(tower.upgrades))
        select_tower_at(self.page, tower.x, tower.y)
        click_upgrade(self.page, path)
        tower.upgrades[path - 1] += 1
        deselect(self.page)
        self._update_state()
        cash_after = self._last_game_state.cash

        if cash_before == cash_after:
            # Cash didn't change at all — upgrade almost certainly failed.
            tower.upgrades[path - 1] -= 1
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)
            self.logger.log("upgrade_failed", tower_id=tower_id, path=path,
                            cash_before=cash_before, cash_after=cash_after)
            raise ValueError(
                f"Upgrade of tower #{tower_id} ({tower.name}) path {path} appears to have failed "
                f"(cash unchanged: {cash_before} → {cash_after}). "
                "The upgrade may not be available or affordable."
            )
        self.logger.log("upgrade_complete", tower_id=tower_id,
                        after=list(tower.upgrades))

    def sell_tower(self, tower_id: int) -> None:
        """Sell a placed tower."""
        assert self.page and self.logger
        if tower_id not in self._placed_towers:
            raise ValueError(f"No tower with id {tower_id}. Placed: {list(self._placed_towers)}")
        tower = self._placed_towers[tower_id]
        self.logger.log("sell_tower", tower_id=tower_id, name=tower.name)
        select_tower_at(self.page, tower.x, tower.y)
        click_sell(self.page)
        del self._placed_towers[tower_id]
        self._update_state()

    def set_target(self, tower_id: int, target: str) -> None:
        """Set targeting mode for a placed tower (first/last/close/strong)."""
        assert self.page and self.logger
        if tower_id not in self._placed_towers:
            raise ValueError(f"No tower with id {tower_id}. Placed: {list(self._placed_towers)}")
        tower = self._placed_towers[tower_id]
        self.logger.log("set_target", tower_id=tower_id, target=target, name=tower.name)
        select_tower_at(self.page, tower.x, tower.y)
        click_target(self.page, target)
        tower.target = target.lower()
        deselect(self.page)
        self._update_state()

    def get_placed_towers(self) -> Dict[int, PlacedTower]:
        """Return the current placed towers dict."""
        return dict(self._placed_towers)

    def press(self, key: str) -> None:
        assert self.page and self.logger
        self.logger.log("press", key=key)
        self.page.keyboard.press(key)
        self._update_state()

    def start_round(self) -> None:
        """Click GO twice (short delay) to start round on fast-forward, then wait 7s."""
        assert self.page and self.logger
        self.logger.log("start_round")
        box = _get_container_box(self.page)
        _click(self.page, GO_BUTTON, box)
        self.page.wait_for_timeout(300)
        _click(self.page, GO_BUTTON, box)
        self.page.wait_for_timeout(7000)
        self._update_state()

    def close(self) -> None:
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        if self.server:
            try:
                self.server.httpd.shutdown()
            except Exception:
                pass
        if self.logger:
            try:
                self.logger.close()
            except Exception:
                pass

        self.ctx = None
        self.page = None
        self._pw = None
        self.server = None
        self.logger = None
