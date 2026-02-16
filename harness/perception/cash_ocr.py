"""Read in-game HUD values (cash, lives, round) from a single screenshot via OCR."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Tuple

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

try:
    import easyocr
    import numpy as np

    _HAS_EASYOCR = True
except ImportError:
    _HAS_EASYOCR = False

try:
    import pytesseract

    _HAS_TESSERACT = True
except ImportError:
    _HAS_TESSERACT = False


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
REGION_ROUND = (93, 697, 96, 18)   # screenshot (162,732)→(258,750) – wide enough for "10 of 65"

# OK button detection region and click target (content-relative).
# Screenshot coords (671,406)→(780,468) minus container offset (~69,35).
REGION_OK_BUTTON = (602, 371, 109, 62)
OK_CLICK_TARGET = (590, 373)   # screenshot (659,408)
OK_BRIGHTNESS_THRESHOLD = 0.30  # fraction of bright pixels to trigger detection


class GameStateReader:
    """Reads cash, lives, round, and detects OK dialog from a single screenshot."""

    def __init__(
        self,
        debug_dir: Path | None = None,
        backend: Literal["auto", "easyocr", "tesseract"] = "auto",
        easyocr_gpu: bool = False,
    ):
        self._debug_dir = debug_dir
        self._seq = 0
        self._easyocr_gpu = easyocr_gpu
        self._easy_reader = None
        self._warned_unavailable = False

        requested = (backend or "auto").lower()
        if requested not in {"auto", "easyocr", "tesseract"}:
            raise ValueError(f"Unknown OCR backend: {backend!r}")
        self._requested_backend = requested
        self._resolved_backend = self._resolve_backend()
        logger.info("OCR backend requested=%s resolved=%s", self._requested_backend, self._resolved_backend)

    def _resolve_backend(self) -> str:
        if not _HAS_PIL:
            return "none"
        if self._requested_backend == "easyocr":
            return "easyocr" if _HAS_EASYOCR else "none"
        if self._requested_backend == "tesseract":
            return "tesseract" if _HAS_TESSERACT else "none"
        # auto: prefer EasyOCR first, then Tesseract
        if _HAS_EASYOCR:
            return "easyocr"
        if _HAS_TESSERACT:
            return "tesseract"
        return "none"

    def _get_easyocr_reader(self):
        if self._easy_reader is None:
            self._easy_reader = easyocr.Reader(["en"], gpu=self._easyocr_gpu, verbose=False)
        return self._easy_reader

    def _ocr_round_easyocr(self, crop: "Image.Image") -> tuple[int | None, "Image.Image"]:
        """OCR the round region which shows e.g. '10 of 65' — return just the first number."""
        reader = self._get_easyocr_reader()
        arr = np.array(crop)
        results = reader.readtext(
            arr,
            detail=1,
            paragraph=False,
            allowlist="0123456789of ",
        )
        if not results:
            logger.debug("EasyOCR [round] returned no text")
            return None, crop

        merged = " ".join(str(row[1]) for row in results if len(row) >= 2)
        logger.debug("EasyOCR [round] raw merged: %r", merged)

        # Extract first number (the current round) from "10 of 65"
        m = re.search(r"(\d+)", merged)
        if not m:
            logger.debug("EasyOCR [round] no digits in merged text: %r", merged)
            return None, crop
        return int(m.group(1)), crop

    def _ocr_crop_easyocr(self, crop: "Image.Image", label: str) -> tuple[int | None, "Image.Image"]:
        reader = self._get_easyocr_reader()
        arr = np.array(crop)

        # Raw crop in, modern recognizer handles stylized fonts better than
        # brittle manual thresholding for this HUD.
        results = reader.readtext(
            arr,
            detail=1,
            paragraph=False,
            allowlist="0123456789$,",
        )
        if not results:
            logger.debug("EasyOCR [%s] returned no text", label)
            return None, crop

        best_digits: str | None = None
        best_score = (-1.0, -1)  # (confidence, digit_count)
        for row in results:
            if len(row) < 3:
                continue
            text = str(row[1])
            conf = float(row[2]) if row[2] is not None else 0.0
            digits = re.sub(r"[^0-9]", "", text)
            if not digits:
                continue
            score = (conf, len(digits))
            if score > best_score:
                best_score = score
                best_digits = digits

        if not best_digits:
            merged = "".join(str(row[1]) for row in results if len(row) >= 2)
            merged_digits = re.sub(r"[^0-9]", "", merged)
            if not merged_digits:
                logger.debug("EasyOCR [%s] returned no digits from rows=%r", label, results)
                return None, crop
            best_digits = merged_digits

        return int(best_digits), crop

    def _ocr_round_tesseract(self, crop: "Image.Image") -> tuple[int | None, "Image.Image"]:
        """Tesseract path for round region — parse first number from 'N of M'."""
        gray = crop.convert("L")
        binary = gray.point(lambda p: 0 if p > 160 else 255)
        scaled = binary.resize((binary.width * 4, binary.height * 4), Image.LANCZOS)
        text = pytesseract.image_to_string(
            scaled,
            config="--psm 7",
        )
        logger.debug("Tesseract [round] raw text: %r", text)
        m = re.search(r"(\d+)", text)
        if not m:
            logger.debug("Tesseract [round] no digits in text: %r", text)
            return None, scaled
        return int(m.group(1)), scaled

    def _ocr_crop_tesseract(self, crop: "Image.Image", label: str) -> tuple[int | None, "Image.Image"]:
        gray = crop.convert("L")

        # Legacy fallback pipeline for Tesseract.
        binary = gray.point(lambda p: 0 if p > 160 else 255)  # invert: dark text on white
        scaled = binary.resize((binary.width * 4, binary.height * 4), Image.LANCZOS)

        text = pytesseract.image_to_string(
            scaled,
            config="--psm 7 -c tessedit_char_whitelist=0123456789$,",
        )
        digits = re.sub(r"[^0-9]", "", text)
        if not digits:
            logger.debug("Tesseract [%s] returned no digits from text: %r", label, text)
            return None, scaled
        return int(digits), scaled

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

        guess: int | None = None
        proc = crop
        if label == "round":
            if self._resolved_backend == "easyocr":
                guess, proc = self._ocr_round_easyocr(crop)
            elif self._resolved_backend == "tesseract":
                guess, proc = self._ocr_round_tesseract(crop)
        elif self._resolved_backend == "easyocr":
            guess, proc = self._ocr_crop_easyocr(crop, label)
        elif self._resolved_backend == "tesseract":
            guess, proc = self._ocr_crop_tesseract(crop, label)

        if self._debug_dir is not None:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            guess_tag = str(guess) if guess is not None else "FAIL"
            crop.save(self._debug_dir / f"{label}_raw_{self._seq:04d}_{guess_tag}.png")
            proc.save(self._debug_dir / f"{label}_proc_{self._seq:04d}_{guess_tag}.png")

        return guess

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
        if not _HAS_PIL:
            logger.warning("Pillow not installed — OCR unavailable. Install with: pip install Pillow")
            return GameState(), False
        if self._resolved_backend == "none":
            if not self._warned_unavailable:
                logger.warning(
                    "No OCR backend available (requested=%s). Install easyocr for best results "
                    "or pytesseract+tesseract as fallback.",
                    self._requested_backend,
                )
                self._warned_unavailable = True
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
