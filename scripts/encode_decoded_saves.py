#!/usr/bin/env python3
"""Re-encode decoded SOL JSON back into save JSON without mutating data.

Usage:
  # Lossless path (decoded file must include raw_sol.value_b64)
  python scripts/encode_decoded_saves.py --input saves/save2.sol.decoded.json --output saves/hacked_save.json

  # Rebuild path for older decoded files (not guaranteed byte-identical)
  python scripts/encode_decoded_saves.py --input saves/legacy.sol.decoded.json --allow-rebuild
"""

from __future__ import annotations

import argparse
import base64
import json
import zlib
from pathlib import Path
from typing import Any

DECODED_FORMAT = "bloonsbench-sol-decoded.v1"


def _encode_u29(value: int) -> bytes:
    if value < 0 or value > 0x1FFFFFFF:
        raise ValueError(f"U29 out of range: {value}")
    if value < 0x80:
        return bytes([value])
    if value < 0x4000:
        return bytes([(value >> 7) | 0x80, value & 0x7F])
    if value < 0x200000:
        return bytes([(value >> 14) | 0x80, ((value >> 7) & 0x7F) | 0x80, value & 0x7F])
    return bytes([
        ((value >> 22) & 0x7F) | 0x80,
        ((value >> 15) & 0x7F) | 0x80,
        ((value >> 8) & 0x7F) | 0x80,
        value & 0xFF,
    ])


def _encode_amf3_int(value: int) -> bytes:
    if value < -0x10000000 or value > 0x0FFFFFFF:
        raise ValueError(f"AMF3 int out of range: {value}")
    if value < 0:
        value += 0x20000000
    return _encode_u29(value)


def _encode_amf3_utf8_vr(text: str, with_marker: bool = False) -> bytes:
    raw = text.encode("utf-8")
    payload = _encode_u29((len(raw) << 1) | 1) + raw
    return (b"\x06" + payload) if with_marker else payload


def _encode_sol_entry(entry_key: str, profile: dict[str, Any], outer_ud_fields: dict[str, Any]) -> str:
    # Rebuild path for legacy decoded files missing raw_sol.value_b64
    profile_json_text = json.dumps(profile, separators=(",", ":"), ensure_ascii=False)
    inner = _encode_amf3_utf8_vr(profile_json_text, with_marker=True)
    data_b64 = base64.b64encode(zlib.compress(inner)).decode("ascii")

    so_name = entry_key.rsplit("/", 1)[-1]
    so_name_bytes = so_name.encode("utf-8")

    glevel = int(outer_ud_fields.get("glevel", 0))
    gcash = int(outer_ud_fields.get("gcash", 0))
    gxp = int(outer_ud_fields.get("gxp", 0))
    gnum = int(outer_ud_fields.get("gnum", 0))

    body = bytearray()
    body.extend(b"TCSO")
    body.extend(b"\x00\x04")
    body.extend(b"\x00\x00\x00\x00")
    body.extend(len(so_name_bytes).to_bytes(2, "big"))
    body.extend(so_name_bytes)
    body.extend(b"\x00\x00\x00\x03")
    body.extend(b"\x05ud\x0a\x0b\x01")

    body.extend(_encode_amf3_utf8_vr("data"))
    body.extend(_encode_amf3_utf8_vr(data_b64, with_marker=True))

    body.extend(_encode_amf3_utf8_vr("glevel"))
    body.extend(b"\x04")
    body.extend(_encode_amf3_int(glevel))

    body.extend(_encode_amf3_utf8_vr("gcash"))
    body.extend(b"\x04")
    body.extend(_encode_amf3_int(gcash))

    body.extend(_encode_amf3_utf8_vr("gxp"))
    body.extend(b"\x04")
    body.extend(_encode_amf3_int(gxp))

    body.extend(_encode_amf3_utf8_vr("gnum"))
    body.extend(b"\x04")
    body.extend(_encode_amf3_int(gnum))

    body.extend(b"\x01\x00")

    raw = bytearray()
    raw.extend(b"\x00\xbf")
    raw.extend(len(body).to_bytes(4, "big"))
    raw.extend(body)
    return base64.b64encode(raw).decode("ascii")


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
        raise ValueError("Expected AMF3 string marker 0x06")
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


def _extract_profile_from_raw(value_b64: str) -> tuple[dict[str, Any], dict[str, int | None]]:
    sol_bytes = base64.b64decode(value_b64)
    idx = sol_bytes.find(b"data")
    if idx < 0:
        raise ValueError("SOL entry is missing 'data' field")
    data_b64, _ = _decode_amf3_string(sol_bytes, idx + 4)
    inner = zlib.decompress(base64.b64decode(data_b64))
    profile_json, _ = _decode_amf3_string(inner, 0)
    profile = json.loads(profile_json)
    outer = {
        "glevel": _parse_amf3_int_after_key(sol_bytes, b"glevel"),
        "gcash": _parse_amf3_int_after_key(sol_bytes, b"gcash"),
        "gxp": _parse_amf3_int_after_key(sol_bytes, b"gxp"),
        "gnum": _parse_amf3_int_after_key(sol_bytes, b"gnum"),
    }
    return profile, outer


def _norm_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load_decoded_entries(path: Path, include_disabled: bool) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(payload, dict) and payload.get("format") == DECODED_FORMAT:
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("Decoded file has invalid 'entries' format")
        out: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if not include_disabled and entry.get("enabled") is False:
                continue
            if "entry_key" in entry:
                out.append(entry)
        return out

    if isinstance(payload, dict) and "entry_key" in payload:
        if payload.get("enabled") is False and not include_disabled:
            return []
        return [payload]

    raise ValueError("Input must be a decoded save JSON file")


def _extract_value_b64(entry: dict[str, Any], allow_rebuild: bool, apply_edits: bool) -> tuple[str, str]:
    raw_sol = entry.get("raw_sol")
    if isinstance(raw_sol, dict):
        value_b64 = raw_sol.get("value_b64")
        if isinstance(value_b64, str) and value_b64:
            if apply_edits and isinstance(entry.get("profile"), dict):
                outer = entry.get("outer_ud_fields")
                if not isinstance(outer, dict):
                    outer = {}
                rebuilt = _encode_sol_entry(str(entry["entry_key"]), entry["profile"], outer)
                return rebuilt, "rebuilt_from_edits"

            # Guardrail: if decoded data was edited, do not silently ignore it.
            if isinstance(entry.get("profile"), dict):
                try:
                    raw_profile, raw_outer = _extract_profile_from_raw(value_b64)
                    if _norm_json(raw_profile) != _norm_json(entry["profile"]):
                        raise ValueError(
                            "Entry profile differs from raw SOL payload. "
                            "Use --apply-edits to rebuild from edited decoded JSON."
                        )
                    outer = entry.get("outer_ud_fields")
                    if isinstance(outer, dict):
                        outer_cmp = {
                            "glevel": outer.get("glevel"),
                            "gcash": outer.get("gcash"),
                            "gxp": outer.get("gxp"),
                            "gnum": outer.get("gnum"),
                        }
                        if _norm_json(raw_outer) != _norm_json(outer_cmp):
                            raise ValueError(
                                "Entry outer_ud_fields differ from raw SOL payload. "
                                "Use --apply-edits to rebuild from edited decoded JSON."
                            )
                except ValueError:
                    raise
                except Exception:
                    # If we cannot parse raw bytes for comparison, preserve lossless path.
                    pass
            return value_b64, "lossless"

    value_b64 = entry.get("value_b64")
    if isinstance(value_b64, str) and value_b64:
        return value_b64, "lossless"

    if not allow_rebuild:
        raise ValueError(
            "Entry is missing raw_sol.value_b64; cannot perform lossless encode. "
            "Re-decode with scripts/decode_saves.py or use --allow-rebuild."
        )

    entry_key = entry.get("entry_key")
    profile = entry.get("profile")
    if not isinstance(entry_key, str) or not isinstance(profile, dict):
        raise ValueError("Legacy rebuild requires entry_key and profile fields")
    outer = entry.get("outer_ud_fields")
    if not isinstance(outer, dict):
        outer = {}
    rebuilt = _encode_sol_entry(entry_key, profile, outer)
    return rebuilt, "rebuilt"


def _default_output_path(input_path: Path) -> Path:
    name = input_path.name
    suffix = ".sol.decoded.json"
    if name.endswith(suffix):
        base = name[: -len(suffix)]
    else:
        base = input_path.stem
    return input_path.with_name(f"{base}.reencoded.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Decoded JSON path")
    ap.add_argument("--output", default=None, help="Output save JSON path")
    ap.add_argument("--include-disabled", action="store_true", help="Include disabled entries")
    ap.add_argument(
        "--allow-rebuild",
        action="store_true",
        help="Allow non-lossless fallback rebuild for legacy decoded files missing raw bytes",
    )
    ap.add_argument(
        "--apply-edits",
        action="store_true",
        help="Rebuild entries from edited decoded profile/outer fields instead of embedded raw SOL bytes",
    )
    args = ap.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_output_path(input_path)
    )

    entries = _load_decoded_entries(input_path, include_disabled=args.include_disabled)
    if not entries:
        raise ValueError("No entries found in decoded input")

    save_map: dict[str, str] = {}
    rebuild_count = 0
    for entry in entries:
        key = entry.get("entry_key")
        if not isinstance(key, str):
            raise ValueError("Decoded entry missing valid entry_key")
        value_b64, mode = _extract_value_b64(
            entry,
            allow_rebuild=args.allow_rebuild,
            apply_edits=args.apply_edits,
        )
        if mode in ("rebuilt", "rebuilt_from_edits"):
            rebuild_count += 1
        save_map[key] = value_b64

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(save_map, indent=2), encoding="utf-8")
    print(f"Wrote save JSON: {output_path}")
    print(f"Entries: {len(save_map)}")
    if rebuild_count:
        print(f"Warning: rebuilt {rebuild_count} entries (not guaranteed byte-identical)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
