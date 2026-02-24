from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TraceLogger:
    run_dir: Path

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.run_dir / "trace.jsonl", "a", encoding="utf-8")

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            pass

    def log(self, op: str, **fields: Any) -> None:
        evt = {"t": time.time(), "op": op, **fields}
        self._fp.write(json.dumps(evt) + "\n")
        self._fp.flush()
