"""Download and manage a pinned Ruffle Web (selfhosted) build.

Pinned by GitHub release tag (e.g. nightly-2026-02-09) for reproducibility.
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


@dataclass(frozen=True)
class RuffleWebBuild:
    tag: str
    dir: Path


def _github_release_by_tag(tag: str) -> dict:
    url = f"https://api.github.com/repos/ruffle-rs/ruffle/releases/tags/{tag}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def _find_asset_url(release: dict, suffix: str) -> str:
    for a in release.get("assets", []):
        if a.get("name", "").endswith(suffix):
            return a["browser_download_url"]
    raise RuntimeError(f"No asset ending with {suffix} for release {release.get('tag_name')}")


def ensure_ruffle_web(
    repo_root: Path,
    tag: str = "nightly-2026-02-09",
    force: bool = False,
) -> RuffleWebBuild:
    vendor_dir = repo_root / "vendor" / "ruffle-web" / tag
    marker = vendor_dir / ".ok"

    if vendor_dir.exists() and marker.exists() and not force:
        return RuffleWebBuild(tag=tag, dir=vendor_dir)

    if vendor_dir.exists() and force:
        shutil.rmtree(vendor_dir)

    vendor_dir.mkdir(parents=True, exist_ok=True)

    release = _github_release_by_tag(tag)
    url = _find_asset_url(release, suffix="-web-selfhosted.zip")

    zip_path = vendor_dir / "ruffle-web-selfhosted.zip"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(vendor_dir)
    zip_path.unlink(missing_ok=True)

    if not (vendor_dir / "ruffle.js").exists():
        candidates = list(vendor_dir.rglob("ruffle.js"))
        if len(candidates) == 1:
            inner = candidates[0].parent
            for p in inner.iterdir():
                shutil.move(str(p), str(vendor_dir / p.name))
            try:
                shutil.rmtree(inner)
            except Exception:
                pass

    if not (vendor_dir / "ruffle.js").exists():
        raise RuntimeError(f"Ruffle extraction failed; ruffle.js not found in {vendor_dir}")

    marker.write_text("ok\n")
    return RuffleWebBuild(tag=tag, dir=vendor_dir)
