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

    # Reuse a Chromium profile dir across runs so localStorage (game saves)
    # persists.  When set, the browser opens this dir instead of creating
    # an ephemeral per-run profile.
    persistent_profile_dir: Optional[Path] = None

    startup_wait_s: float = 13.0

    # Fixed port for local HTTP server. Pinning ensures localStorage
    # (origin-scoped to 127.0.0.1:PORT) persists across sessions.
    server_port: Optional[int] = 8890

    block_network: bool = True
    # Save injection is opt-in. Set this path explicitly when you want to
    # pre-populate localStorage before loading the game.
    save_data_path: Optional[Path] = None

    auto_navigate_to_round: bool = False
    nav_map_name: str = "monkey_lane"
    nav_difficulty: str = "easy"

    # OCR backend selection:
    # - auto: prefer EasyOCR, fallback to Tesseract
    # - easyocr: require EasyOCR
    # - tesseract: require Tesseract
    ocr_backend: str = "auto"
    ocr_easyocr_gpu: bool = False
