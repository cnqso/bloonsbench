"""BloonsWebEnv: Ruffle Web + Playwright harness for Bloons TD5."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from harness.env.config import HarnessConfig
from harness.env.network import block_nk_domains
from harness.env.save_data import import_saves_from_file
from harness.env.menu_nav import navigate_to_round, place_tower
from harness.runtime.local_http import LocalServer, serve_directory
from harness.runtime.ruffle_web_vendor import ensure_ruffle_web
from harness.trace.logger import TraceLogger
from harness.env.profile_manager import ProfileManager


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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

    def reset(self, out_root: Optional[Path] = None) -> Path:
        """Start a new run. Returns run_dir."""
        self.close()

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

        # Browser profile
        profile_dir = self.run_dir / "chromium-profile"
        if self.cfg.persistent_profile_dir:
            profile_dir = Path(self.cfg.persistent_profile_dir).expanduser().resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.logger.log("profile_persistent", dir=str(profile_dir))
        elif self.cfg.profile_template_dir:
            shutil.copytree(self.cfg.profile_template_dir, profile_dir, dirs_exist_ok=True)
            self.logger.log("profile_template_copied", src=str(self.cfg.profile_template_dir))

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
            screenshots = navigate_to_round(
                self.page,
                map_name=self.cfg.nav_map_name,
                difficulty=self.cfg.nav_difficulty,
                screenshot_dir=nav_dir,
            )
            self.logger.log("menu_navigation_complete", map=self.cfg.nav_map_name, difficulty=self.cfg.nav_difficulty)

        return self.run_dir

    def observe(self, tag: str = "obs") -> Path:
        assert self.page and self.logger and self.run_dir
        fname = f"{tag}_{int(time.time()*1000)}.png"
        out = self.run_dir / fname
        self.page.screenshot(path=str(out), full_page=True)
        self.logger.log("screenshot", tag=tag, path=fname)
        return out

    def click_content(self, x: float, y: float) -> None:
        """Click at coordinates relative to the content container."""
        assert self.page and self.logger
        box = self.page.locator("div#container").first.bounding_box()
        if not box:
            raise RuntimeError("Container bounding box not available")
        abs_x = box["x"] + x
        abs_y = box["y"] + y
        self.logger.log("click", x=x, y=y, abs_x=abs_x, abs_y=abs_y)
        self.page.mouse.click(abs_x, abs_y)

    def place_tower(self, tower_name: str, x: float, y: float) -> None:
        """Select and place a tower at content-relative (x, y)."""
        assert self.page and self.logger
        self.logger.log("place_tower", tower=tower_name, x=x, y=y)
        place_tower(self.page, tower_name, x, y)

    def press(self, key: str) -> None:
        assert self.page and self.logger
        self.logger.log("press", key=key)
        self.page.keyboard.press(key)

    def start_round(self) -> None:
        assert self.logger
        self.logger.log("start_round")

    def sample_round(self) -> list[Path]:
        """Auto-capture screenshots at configured intervals during a round."""
        assert self.page and self.logger
        frames: list[Path] = []
        t0 = time.time()
        for dt in self.cfg.round_sample_times_s:
            sleep_s = (t0 + dt) - time.time()
            if sleep_s > 0:
                self.page.wait_for_timeout(int(sleep_s * 1000))
            frames.append(self.observe(tag=f"round_{dt:.1f}s"))
        self.logger.log("end_round_sampling")
        return frames

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

        if self.cfg.persistent_profile_dir and self.cfg.autosave_profile:
            try:
                pm = ProfileManager(self.repo_root)
                persistent = Path(self.cfg.persistent_profile_dir).expanduser().resolve()
                snap_name = datetime.now().strftime("%Y%m%d_%H%M%S")
                snap_dir = pm.snapshot_dir(name=persistent.parent.name, snapshot_name=snap_name)
                pm.save_snapshot(persistent, snap_dir, overwrite=False)
                if self.logger:
                    self.logger.log("profile_snapshot_saved", snapshot=str(snap_dir))
            except Exception as e:
                if self.logger:
                    self.logger.log("profile_snapshot_error", error=str(e))

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
