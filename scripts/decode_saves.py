#!/usr/bin/env python3
"""Decode Bloons save JSON entries into readable JSON.

Supports:
1) Standard export format: {"<localStorageKey>": "<base64 SOL bytes>", ...}
2) Save editor format: {"format":"bloonsbench-save-editor.v1","entries":[...]}

Usage:
  python scripts/decode_saves.py --input saves/my_save.json
  python scripts/decode_saves.py --input saves/default.editor.json --include-disabled
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EDITOR_FORMAT = "bloonsbench-save-editor.v1"
OUTPUT_FORMAT = "bloonsbench-sol-decoded.v1"


@dataclass(frozen=True)
class SaveEntry:
    key: str
    value_b64: str
    enabled: bool = True
    meta: dict[str, Any] | None = None


def _decode_u29(buf: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for idx in range(4):
        if offset >= len(buf):
            raise ValueError("Unexpected end of buffer while reading U29")
        b = buf[offset]
        offset += 1
        if idx < 3:
            value = (value << 7) | (b & 0x7F)
            if not (b & 0x80):
                return value, offset
        else:
            value = (value << 8) | b
            return value, offset
    raise ValueError("Invalid U29 value")


def _decode_amf3_string(buf: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(buf) or buf[offset] != 0x06:
        marker = f"{buf[offset]:#x}" if offset < len(buf) else "EOF"
        raise ValueError(f"Expected AMF3 string marker 0x06, got {marker}")
    u29, pos = _decode_u29(buf, offset + 1)
    if not (u29 & 1):
        raise ValueError("AMF3 string uses reference mode; inline string expected")
    strlen = u29 >> 1
    end = pos + strlen
    if end > len(buf):
        raise ValueError("String length exceeds buffer size")
    return buf[pos:end].decode("utf-8"), end


def _parse_amf3_int_after_key(sol_bytes: bytes, key: bytes) -> int | None:
    idx = sol_bytes.find(key)
    if idx < 0:
        return None
    pos = idx + len(key)
    if pos >= len(sol_bytes) or sol_bytes[pos] != 0x04:
        return None
    value, _ = _decode_u29(sol_bytes, pos + 1)
    if value & 0x10000000:
        value -= 0x20000000
    return value


def _extract_outer_data_string(sol_bytes: bytes) -> str:
    idx = sol_bytes.find(b"data")
    if idx < 0:
        raise ValueError("SOL entry is missing 'data' field")
    data_str, _ = _decode_amf3_string(sol_bytes, idx + 4)
    return data_str


def decode_sol_entry(key: str, value_b64: str) -> dict[str, Any]:
    sol_bytes = base64.b64decode(value_b64)
    data_b64 = _extract_outer_data_string(sol_bytes)
    zlib_payload = base64.b64decode(data_b64)
    inner = zlib.decompress(zlib_payload)
    profile_json, _ = _decode_amf3_string(inner, 0)
    profile = json.loads(profile_json)

    return {
        "entry_key": key,
        "raw_sol": {
            "value_b64": value_b64,
            "bytes_length": len(sol_bytes),
            "sha256": hashlib.sha256(sol_bytes).hexdigest(),
        },
        "outer_ud_fields": {
            "glevel": _parse_amf3_int_after_key(sol_bytes, b"glevel"),
            "gcash": _parse_amf3_int_after_key(sol_bytes, b"gcash"),
            "gxp": _parse_amf3_int_after_key(sol_bytes, b"gxp"),
            "gnum": _parse_amf3_int_after_key(sol_bytes, b"gnum"),
        },
        "data_container": {
            "encoding": "base64(zlib(amf3-string(json)))",
            "compressed_b64_length": len(data_b64),
            "zlib_payload_length": len(zlib_payload),
            "decompressed_length": len(inner),
            "inner_json_length": len(profile_json),
        },
        "profile_json_text": profile_json,
        "profile": profile,
    }


def _load_entries(path: Path, include_disabled: bool) -> tuple[str, list[SaveEntry]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries: list[SaveEntry] = []

    if isinstance(payload, dict) and payload.get("format") == EDITOR_FORMAT:
        for raw in payload.get("entries", []):
            if not isinstance(raw, dict):
                continue
            key = raw.get("key")
            value_b64 = raw.get("value_b64")
            enabled = bool(raw.get("enabled", True))
            if not include_disabled and not enabled:
                continue
            if isinstance(key, str) and isinstance(value_b64, str):
                entries.append(SaveEntry(key=key, value_b64=value_b64, enabled=enabled, meta=raw.get("meta")))
        return "save_editor", entries

    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str):
                entries.append(SaveEntry(key=key, value_b64=value, enabled=True, meta=None))
        return "raw_export", entries

    raise ValueError("Input must be a JSON object (export map or editor format)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to save JSON")
    ap.add_argument("--output", default=None, help="Output path (default: <input>.sol.decoded.json)")
    ap.add_argument(
        "--include-disabled",
        action="store_true",
        help="For editor format: include entries where enabled=false",
    )
    ap.add_argument("--strict", action="store_true", help="Fail on first decode error")
    args = ap.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.with_name(f"{input_path.stem}.sol.decoded.json")
    )

    source_type, entries = _load_entries(input_path, include_disabled=args.include_disabled)
    if not entries:
        raise ValueError("No save entries found in input")

    decoded_entries: list[dict[str, Any]] = []
    failures = 0

    for entry in entries:
        try:
            decoded = decode_sol_entry(entry.key, entry.value_b64)
            decoded["enabled"] = entry.enabled
            if entry.meta is not None:
                decoded["meta"] = entry.meta
            decoded_entries.append(decoded)
        except Exception as exc:
            failures += 1
            try:
                raw_len = len(base64.b64decode(entry.value_b64))
            except Exception:
                raw_len = None
            error_entry = {
                "entry_key": entry.key,
                "enabled": entry.enabled,
                "raw_sol": {
                    "value_b64": entry.value_b64,
                    "bytes_length": raw_len,
                },
                "error": str(exc),
            }
            if entry.meta is not None:
                error_entry["meta"] = entry.meta
            decoded_entries.append(error_entry)
            if args.strict:
                raise

    output = {
        "format": OUTPUT_FORMAT,
        "source": {
            "input_path": str(input_path),
            "input_type": source_type,
            "decoded_entries": len(decoded_entries),
            "decode_failures": failures,
        },
        "entries": decoded_entries,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Decoded {len(decoded_entries)} entries to {output_path}")
    if failures:
        print(f"Warning: {failures} entries failed to decode")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
