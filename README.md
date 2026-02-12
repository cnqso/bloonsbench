# bloonsbench

Programmatic harness for **Bloons TD5** (.swf) running under [Ruffle](https://ruffle.rs/) + [Playwright](https://playwright.dev/python/).

## What it does

- Runs BTD5 in Ruffle Web inside Chromium, controlled entirely via Playwright
- Portable save system: export/import game state as ~1KB JSON (no 107MB profile copies)
- Blocks NinjaKiwi cloud sync to prevent interference with automation
- Deferred game loading for pre-populating saves before the Flash VM starts
- Coordinate-based menu navigation (placeholder coords, calibrate from screenshots)
- Screenshot capture and JSONL trace logging for every action

## Layout

```
harness/
  env/         config, web environment, save data, network blocking, menu nav
  runtime/     local HTTP server, Ruffle Web vendor, wrapper HTML
  trace/       JSONL trace logger
scripts/       CLI tools for smoke tests, save export/import, ready-state pipeline
game/          place btd5.swf here (not distributed)
saves/         exported save JSON files
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Place your BTD5 SWF at `game/btd5.swf`, then:

```bash
# Smoke test
python scripts/web_smoke.py

# Manual play with persistent profile (for unlocking)
python scripts/run_persistent_profile.py --profile unlocks

# Start a clean new local save profile
python scripts/run_persistent_profile.py --profile my_save --fresh-start

# Export saves from profile
python scripts/export_saves.py --profile unlocks

# Decode save JSON into readable profile fields
python scripts/decode_saves.py --input saves/my_save.json

# Re-encode decoded JSON back into save JSON (lossless roundtrip)
python scripts/encode_decoded_saves.py --input saves/save2.sol.decoded.json --output saves/hacked_save.json

# If you manually edited save2.sol.decoded.json, apply those edits when encoding
python scripts/encode_decoded_saves.py --input saves/save2.sol.decoded.json --output saves/hacked_save.json --apply-edits

# Load that save into a new persistent profile
python scripts/run_persistent_profile.py --profile hacked_save --fresh-start --seed-saves saves/hacked_save.json

# Verify save injection in fresh browser
python scripts/verify_saves.py --saves saves/unlocks.json

# Full pipeline: saves + network block + menu nav
python scripts/run_ready_state.py --saves saves/unlocks.json
```

## How saves work

Ruffle stores Flash SharedObjects in `localStorage` as base64-encoded SOL bytes. The harness:

1. Navigates to the wrapper with `?defer=1` (Ruffle player created, game not loaded)
2. Injects save data into `localStorage` via `page.evaluate()`
3. Calls `window.__BLOONSBENCH__.loadGame()` to start the game with pre-populated saves

The local HTTP server uses a fixed port (8890) so `localStorage` persists across sessions on the same origin.
Save injection is opt-in (`save_data_path` must be set explicitly).
