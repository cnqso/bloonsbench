"""Import Ruffle localStorage SharedObjects for save injection.

Ruffle stores Flash SharedObjects in localStorage as base64-encoded SOL bytes.
Keys are constructed as: {movie_host}/{local_path}/{name}
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page


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
