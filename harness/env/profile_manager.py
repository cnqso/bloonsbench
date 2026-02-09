from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProfileManager:
    repo_root: Path

    def profiles_root(self) -> Path:
        return (self.repo_root / "profiles").resolve()

    def persistent_profile_dir(self, name: str = "default") -> Path:
        p = self.profiles_root() / "persistent" / name / "chromium-profile"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def snapshot_dir(self, name: str, snapshot_name: str) -> Path:
        p = self.profiles_root() / "snapshots" / name / snapshot_name / "chromium-profile"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save_snapshot(self, persistent_dir: Path, snapshot_dir: Path, overwrite: bool = False) -> None:
        if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
            if not overwrite:
                raise FileExistsError(f"Snapshot dir not empty: {snapshot_dir}")
            shutil.rmtree(snapshot_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(persistent_dir, snapshot_dir, dirs_exist_ok=True)
