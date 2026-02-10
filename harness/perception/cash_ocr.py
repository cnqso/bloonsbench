"""Read in-game HUD values (cash, lives, round) from a single screenshot via Tesseract OCR."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


@dataclass
class GameState:
    """Snapshot of HUD values read via OCR. None means OCR failed for that field."""
    cash: int | None = None
    lives: int | None = None
    round_num: int | None = None


# Content-relative crop regions (x, y, w, h).
# Derived from screenshot-absolute coords minus container offset (~69, ~35).
REGION_CASH  = (855, 30, 71, 19)   # screenshot (924,65)→(995,84)
REGION_LIVES = (855, 58, 33, 19)   # screenshot (924,93)→(957,112)
REGION_ROUND = (93, 697, 24, 18)   # screenshot (162,732)→(186,750)

# OK button detection region and click target (content-relative).
# Screenshot coords (671,406)→(780,468) minus container offset (~69,35).
REGION_OK_BUTTON = (602, 371, 109, 62)
OK_CLICK_TARGET = (590, 373)   # screenshot (659,408)
OK_BRIGHTNESS_THRESHOLD = 0.30  # fraction of bright pixels to trigger detection


class GameStateReader:
    """Reads cash, lives, round, and detects OK dialog from a single screenshot."""

    def __init__(self, debug_dir: Path | None = None):
        self._debug_dir = debug_dir
        self._seq = 0

    def _ocr_crop(
        self,
        full_img: "Image.Image",
        cx: float,
        cy: float,
        region: Tuple[int, int, int, int],
        label: str,
    ) -> int | None:
        """Crop a region from a full screenshot, preprocess, OCR, return int or None."""
        rx, ry, rw, rh = region
        crop = full_img.crop((cx + rx, cy + ry, cx + rx + rw, cy + ry + rh))
        gray = crop.convert("L")

        # Strict threshold — no blur, high cutoff to kill shadows between digits
        binary = gray.point(lambda p: 0 if p > 160 else 255)  # invert: dark text on white

        # Scale up with smooth interpolation
        scaled = binary.resize((binary.width * 4, binary.height * 4), Image.LANCZOS)

        if self._debug_dir is not None:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            crop.save(self._debug_dir / f"{label}_raw_{self._seq:04d}.png")
            scaled.save(self._debug_dir / f"{label}_proc_{self._seq:04d}.png")

        text = pytesseract.image_to_string(
            scaled,
            config="--psm 7 -c tessedit_char_whitelist=0123456789$,",
        )

        digits = re.sub(r"[^0-9]", "", text)
        if not digits:
            logger.debug("OCR [%s] returned no digits from text: %r", label, text)
            return None
        return int(digits)

    def _detect_ok(
        self,
        full_img: "Image.Image",
        cx: float,
        cy: float,
    ) -> bool:
        """Check if the OK button is visible via pixel brightness."""
        rx, ry, rw, rh = REGION_OK_BUTTON
        crop = full_img.crop((cx + rx, cy + ry, cx + rx + rw, cy + ry + rh))
        gray = crop.convert("L")
        pixels = list(gray.getdata())
        bright = sum(1 for p in pixels if p > 200)
        ratio = bright / len(pixels) if pixels else 0
        if ratio >= OK_BRIGHTNESS_THRESHOLD:
            logger.debug("OK button detected (bright ratio: %.2f)", ratio)
            return True
        return False

    def update(
        self,
        screenshot_path: str | Path,
        container_box: dict,
    ) -> Tuple[GameState, bool]:
        """Read all HUD values + detect OK button from a single screenshot file.

        Returns (game_state, ok_button_visible).
        """
        if not _HAS_DEPS:
            logger.warning(
                "pytesseract or Pillow not installed — OCR unavailable. "
                "Install with: pip install pytesseract Pillow  "
                "and brew install tesseract"
            )
            return GameState(), False

        img = Image.open(screenshot_path)
        cx, cy = container_box["x"], container_box["y"]

        state = GameState()
        for field, region, label in [
            ("cash", REGION_CASH, "cash"),
            ("lives", REGION_LIVES, "lives"),
            ("round_num", REGION_ROUND, "round"),
        ]:
            try:
                setattr(state, field, self._ocr_crop(img, cx, cy, region, label))
            except Exception as e:
                logger.warning("OCR [%s] failed: %s", label, e)

        ok_visible = False
        try:
            ok_visible = self._detect_ok(img, cx, cy)
        except Exception as e:
            logger.warning("OK button detection failed: %s", e)

        self._seq += 1
        return state, ok_visible
