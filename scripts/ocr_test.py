"""OCR tuning script — run with Ctrl+F5 in your IDE, no args needed.

Loads raw crops from a debug folder, runs the preprocessing pipeline
with tweakable settings, and prints results so you can iterate fast.
"""

import re
from pathlib import Path

import pytesseract
from PIL import Image, ImageFilter

# ══════════════════════════════════════════════════════════════════
# TWEAK THESE
# ══════════════════════════════════════════════════════════════════

DEBUG_DIR = Path("/Users/williamkelly/code/bloonsbench/logs/runs/20260210_134504/ocr_debug")

# Preprocessing
BLUR_RADIUS = 0           # Gaussian blur before threshold (0 = off)
THRESHOLD = 160           # Pixel brightness cutoff (0-255)
INVERT = True             # True = dark text on white (Tesseract preferred)
SCALE_FACTOR = 4          # Upscale multiplier
SCALE_METHOD = Image.LANCZOS  # LANCZOS, BILINEAR, BICUBIC, NEAREST

# Tesseract
PSM = 7                   # Page segmentation mode (7=single line, 8=single word, 13=raw line)
WHITELIST = "0123456789$,"
EXTRA_CONFIG = ""         # e.g. "--oem 1" for LSTM only

# Output
SAVE_PROCESSED = True     # Save processed images next to this script
SAVE_DIR = Path(__file__).parent / "ocr_test_output"

# ══════════════════════════════════════════════════════════════════


def preprocess(img: Image.Image) -> Image.Image:
    gray = img.convert("L")

    if BLUR_RADIUS > 0:
        gray = gray.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))

    if INVERT:
        binary = gray.point(lambda p: 0 if p > THRESHOLD else 255)
    else:
        binary = gray.point(lambda p: 255 if p > THRESHOLD else 0)

    scaled = binary.resize(
        (binary.width * SCALE_FACTOR, binary.height * SCALE_FACTOR),
        SCALE_METHOD,
    )
    return scaled


def ocr(img: Image.Image) -> str:
    config = f"--psm {PSM} -c tessedit_char_whitelist={WHITELIST}"
    if EXTRA_CONFIG:
        config += f" {EXTRA_CONFIG}"
    return pytesseract.image_to_string(img, config=config).strip()


def extract_digits(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)


def main():
    if SAVE_PROCESSED:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)

    labels = ["cash", "lives", "round"]
    results = {label: [] for label in labels}

    # Discover all raw files
    for label in labels:
        raws = sorted(DEBUG_DIR.glob(f"{label}_raw_*.png"))
        if not raws:
            print(f"  No {label}_raw_*.png files found in {DEBUG_DIR}")
            continue

        print(f"\n{'=' * 60}")
        print(f"  {label.upper()} — {len(raws)} samples")
        print(f"{'=' * 60}")
        print(f"  {'#':>4}  {'Raw OCR':<20}  {'Digits':<12}  {'Parsed':<10}")
        print(f"  {'─' * 4}  {'─' * 20}  {'─' * 12}  {'─' * 10}")

        for raw_path in raws:
            seq = raw_path.stem.split("_")[-1]
            img = Image.open(raw_path)
            processed = preprocess(img)

            raw_text = ocr(processed)
            digits = extract_digits(raw_text)
            parsed = int(digits) if digits else "FAIL"

            if SAVE_PROCESSED:
                ocr_tag = digits if digits else "FAIL"
                processed.save(SAVE_DIR / f"{label}_{seq}_ocr{ocr_tag}.png")

            results[label].append((seq, raw_text, digits, parsed))
            print(f"  {seq:>4}  {raw_text:<20}  {digits:<12}  {parsed!s:<10}")

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    for label in labels:
        entries = results[label]
        if not entries:
            continue
        total = len(entries)
        success = sum(1 for _, _, _, p in entries if p != "FAIL")
        print(f"  {label:>6}: {success}/{total} parsed  ({100 * success / total:.0f}%)")

    print(f"\nSettings: blur={BLUR_RADIUS} thresh={THRESHOLD} invert={INVERT} "
          f"scale={SCALE_FACTOR}x psm={PSM}")


if __name__ == "__main__":
    main()
