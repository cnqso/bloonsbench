from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class HarnessConfig:
    content_width: int = 960
    content_height: int = 720

    viewport_width: int = 1100
    viewport_height: int = 860

    headless: bool = False
    ruffle_tag: str = "nightly-2026-02-09"

    persistent_profile_dir: Optional[Path] = None
    profile_template_dir: Optional[Path] = None
    autosave_profile: bool = True

    round_sample_times_s: tuple[float, ...] = (0.5, 2.0, 5.0)
    startup_wait_s: float = 13.0

    # Fixed port for local HTTP server. Pinning ensures localStorage
    # (origin-scoped to 127.0.0.1:PORT) persists across sessions.
    server_port: Optional[int] = 8890

    block_network: bool = True
    save_data_path: Optional[Path] = Path(__file__).resolve().parents[2] / "saves" / "unlocks_maxed.json"

    auto_navigate_to_round: bool = False
    nav_map_name: str = "monkey_lane"
    nav_difficulty: str = "easy"
