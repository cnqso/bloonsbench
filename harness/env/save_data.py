"""Export/import Ruffle localStorage SharedObjects.

Ruffle stores Flash SharedObjects in localStorage as base64-encoded SOL bytes.
Keys are constructed as: {movie_host}/{local_path}/{name}
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from playwright.sync_api import Page

_SOL_TCSO_MAGIC = b"TCSO"
_SOL_TCSO_OFFSET = 6


def _is_sol_data(b64_value: str) -> bool:
    try:
        raw = base64.b64decode(b64_value)
        return raw[_SOL_TCSO_OFFSET:_SOL_TCSO_OFFSET + 4] == _SOL_TCSO_MAGIC
    except Exception:
        return False


def dump_all_localstorage(page: Page) -> dict[str, str]:
    """Dump ALL localStorage entries (no filtering)."""
    return page.evaluate("""() => {
        const result = {};
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key) result[key] = localStorage.getItem(key);
        }
        return result;
    }""")


def export_saves(page: Page) -> dict[str, str]:
    """Extract all valid SOL entries from localStorage."""
    all_entries = dump_all_localstorage(page)
    return {k: v for k, v in all_entries.items() if _is_sol_data(v)}


def export_saves_to_file(page: Page, path: Path | str) -> dict[str, str]:
    saves = export_saves(page)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saves, indent=2), encoding="utf-8")
    return saves


def import_saves(page: Page, saves: dict[str, str]) -> int:
    """Write save entries into localStorage. Call before game loads (deferred mode)."""
    page.evaluate("""(saves) => {
        for (const [key, value] of Object.entries(saves)) {
            localStorage.setItem(key, value);
        }
    }""", saves)
    return len(saves)


def import_saves_from_file(page: Page, path: Path | str) -> int:
    path = Path(path)
    saves: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return import_saves(page, saves)


def load_saves_file(path: Path | str) -> dict[str, str]:
    """Read a saves JSON file without a page."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
