#!/usr/bin/env python3
"""Autonomous LLM agent for BTD5 via OpenRouter.

Launches the game, then loops forever: observe → think → act.
Uses OpenRouter's OpenAI-compatible API so you can swap models easily.

Usage:
    python scripts/run_agent.py --model anthropic/claude-sonnet-4
    python scripts/run_agent.py --model openai/gpt-4o

Requires OPENROUTER_API_KEY in .env or environment.
Model must support vision + tool/function calling.
"""

from __future__ import annotations

import argparse
import base64
from collections import deque
import io
import json
import os
import re
import sys
import time
import warnings

warnings.filterwarnings("ignore", message=".*pin_memory.*")
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import requests
from PIL import Image
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
from harness.env.config import HarnessConfig
from harness.env.web_env import BloonsWebEnv
from harness.mcp_server import INSTRUCTIONS, TOOL_DEFS, _format_tower_list, _format_status
from scripts.export_run import export_run

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ACTIONS_PER_CYCLE = 25  # safety cap per observation cycle
DISTILL_TOKEN_THRESHOLD = 240_000  # trigger distillation when context exceeds this many tokens
DISTILL_CHECK_INTERVAL_S = 10  # check for distillation need every N seconds
KEEP_IMAGES = 1  # only keep the most recent screenshot
MAX_IMAGE_SIDE = 960
JPEG_QUALITY = 60
GO_POLL_INTERVAL_MS = int(os.getenv("GO_POLL_INTERVAL_MS", "750"))
GO_REGION = (914, 555, 998, 598)  # screenshot-absolute crop for "Go!" button (shifted ~26px up)
OK_REGION = (674, 406, 775, 466)  # screenshot-absolute crop for "OK!" popup button
OK_CLICK = (724, 436)  # screenshot-absolute center of popup button
GAME_OVER_REGION = (353, 162, 645, 216)  # screenshot-absolute crop for "GAME OVER" title (shifted ~27px up)
STREAM_MODE = os.getenv("AGENT_STREAM_MODE", "thinking").strip().lower()  # thinking|content|both
CYCLE_INTERVAL_S = 60  # one planning cycle every minute


# ── .env loading ─────────────────────────────────────────────────

def load_dotenv():
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("\"'"))


# ── Tool format conversion ───────────────────────────────────────

def _sanitize_schema_for_google(schema: dict) -> dict:
    """Sanitize JSON schema for Google AI Studio compatibility.

    Google's schema validator is stricter than OpenAI's:
    - Doesn't handle 'enum' inside properties well
    - May not support all JSON Schema features
    """
    import copy
    schema = copy.deepcopy(schema)

    # Remove 'required' if it's an empty list (some providers reject this)
    if "required" in schema and not schema["required"]:
        del schema["required"]

    # Remove 'enum' from properties and move to description
    if "properties" in schema:
        for prop_name, prop_def in schema["properties"].items():
            if "enum" in prop_def:
                enum_vals = prop_def.pop("enum")
                desc = prop_def.get("description", "")
                if desc and not desc.endswith("."):
                    desc += "."
                prop_def["description"] = f"{desc} Must be one of: {', '.join(map(str, enum_vals))}".strip()

    return schema

def mcp_to_openai_tools(model: str = ""):
    """Convert our MCP TOOL_DEFS to OpenAI function-calling format.

    Args:
        model: Model identifier (e.g. 'google/gemini-...') to apply provider-specific fixes
    """
    is_google = "google" in model.lower() or "gemini" in model.lower()

    tools = []
    for d in TOOL_DEFS:
        schema = d["inputSchema"]
        if is_google:
            schema = _sanitize_schema_for_google(schema)

        tools.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": schema,
            },
        })
    return tools


# ── Tool execution ───────────────────────────────────────────────

def execute_tool(env, name, args, screenshot_hook=None):
    """Run a tool call against the live game.

    Returns (text_result, image_path | None).
    image_path is set only for 'observe' so the caller can inject the screenshot.
    """
    try:
        if name == "observe":
            path = env.observe(tag="agent")
            if screenshot_hook:
                path = screenshot_hook(Path(path), "tool_observe")
            return "Screenshot taken.", str(path)

        if name == "place_tower":
            tid = env.place_tower(args["tower"], float(args["x"]), float(args["y"]))
            return f"Placed {args['tower']} at ({args['x']}, {args['y']}), tower_id={tid}", None

        if name == "upgrade_tower":
            env.upgrade_tower(int(args["tower_id"]), int(args["path"]))
            t = env.get_placed_towers().get(int(args["tower_id"]))
            if t:
                return (
                    f"Upgraded #{args['tower_id']} path {args['path']}"
                    f" → now {t.upgrades[0]}/{t.upgrades[1]}"
                ), None
            return f"Upgraded #{args['tower_id']} path {args['path']}", None

        if name == "sell_tower":
            env.sell_tower(int(args["tower_id"]))
            return f"Sold tower #{args['tower_id']}", None

        if name == "set_target":
            env.set_target(int(args["tower_id"]), args["target"])
            return f"Set #{args['tower_id']} targeting to {args['target']}", None

        if name == "start_round":
            env.start_round()
            return "Round started (fast-forward, waited 7s).", None

        if name == "click":
            env.click_content(float(args["x"]), float(args["y"]))
            return f"Clicked ({args['x']}, {args['y']})", None

        if name == "send_key":
            env.press(args["key"])
            return f"Pressed key: {args['key']}", None

        if name == "wait":
            ms = int(args["ms"])
            env.page.wait_for_timeout(ms)
            return f"Waited {ms}ms", None

        if name == "list_towers":
            return _format_tower_list(), None

        if name == "status":
            return _format_status(env), None

        return f"Error: unknown tool '{name}'", None

    except Exception as e:
        return f"Error: {e}", None


# ── Image helpers ────────────────────────────────────────────────

def encode_image(path):
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def make_image_message(image_b64, text, role="user", mime_type="image/png"):
    return {
        "role": role,
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            {"type": "text", "text": text},
        ],
    }


_easy_go_reader = None


def _get_easy_go_reader():
    global _easy_go_reader
    if _easy_go_reader is None:
        _easy_go_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easy_go_reader


def _ocr_go_text(crop: Image.Image) -> tuple[str, Image.Image]:
    """Return (text_guess, processed_image_for_debug)."""
    if _HAS_EASYOCR:
        reader = _get_easy_go_reader()
        arr = np.array(crop.convert("RGB"))
        rows = reader.readtext(arr, detail=1, paragraph=False, allowlist="GoGO! ")
        text = " ".join(str(r[1]) for r in rows if len(r) >= 2).strip()
        return text, crop

    # Fallback: Tesseract on a simple enlarged grayscale crop.
    proc = crop.convert("L").resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    if _HAS_TESSERACT:
        text = pytesseract.image_to_string(
            proc,
            config="--psm 7 -c tessedit_char_whitelist=GoGO! ",
        ).strip()
        return text, proc
    return "", proc


def _ocr_ok_text(crop: Image.Image) -> tuple[str, Image.Image]:
    """Return OCR text for OK button region."""
    if _HAS_EASYOCR:
        reader = _get_easy_go_reader()
        arr = np.array(crop.convert("RGB"))
        rows = reader.readtext(arr, detail=1, paragraph=False, allowlist="OKok! ")
        text = " ".join(str(r[1]) for r in rows if len(r) >= 2).strip()
        return text, crop

    proc = crop.convert("L").resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    if _HAS_TESSERACT:
        text = pytesseract.image_to_string(
            proc,
            config="--psm 7 -c tessedit_char_whitelist=OKok! ",
        ).strip()
        return text, proc
    return "", proc


def _ocr_game_over_text(crop: Image.Image) -> tuple[str, Image.Image]:
    """Return OCR text for GAME OVER title region."""
    if _HAS_EASYOCR:
        reader = _get_easy_go_reader()
        arr = np.array(crop.convert("RGB"))
        rows = reader.readtext(arr, detail=1, paragraph=False, allowlist="GAMEOVERgameover ")
        text = " ".join(str(r[1]) for r in rows if len(r) >= 2).strip()
        return text, crop

    proc = crop.convert("L").resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    if _HAS_TESSERACT:
        text = pytesseract.image_to_string(
            proc,
            config="--psm 6 -c tessedit_char_whitelist=GAMEOVERgameover ",
        ).strip()
        return text, proc
    return "", proc


def _dismiss_ok_popup_if_present(env, shot_path: Path, run_dir: Path, tag: str) -> Path:
    """Run popup OCR on a screenshot; click OK and rescreenshot when detected."""
    ocr_dir = run_dir / "ocr_debug"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(shot_path) as img:
        crop = img.crop(OK_REGION)
        text, proc = _ocr_ok_text(crop)

    cleaned = re.sub(r"\s+", "", text).strip()
    guess_tag = cleaned if cleaned else "FAIL"
    guess_tag = re.sub(r"[^A-Za-z0-9_!.-]", "_", guess_tag)[:40]
    crop.save(ocr_dir / f"ok_raw_{tag}_{guess_tag}.png")
    proc.save(ocr_dir / f"ok_proc_{tag}_{guess_tag}.png")

    if cleaned:
        log_stderr(f"Popup detector: OK text={cleaned!r} — clicking OK")
        env.page.mouse.click(OK_CLICK[0], OK_CLICK[1])
        env.page.wait_for_timeout(300)
        return env.observe(tag=f"{tag}_after_ok")

    return shot_path


def _observe_with_popup_guard(env, run_dir: Path, tag: str) -> Path:
    shot = env.observe(tag=tag)
    return _dismiss_ok_popup_if_present(env, Path(shot), run_dir, tag)


def _wait_for_go_button(env, run_dir: Path, round_started_count: int) -> bool:
    """Poll until GO text is detected, or stop early on GAME OVER detection."""
    if not _HAS_EASYOCR and not _HAS_TESSERACT:
        raise RuntimeError("GO button OCR polling requires easyocr or pytesseract installed")

    poll_idx = 0
    ocr_dir = run_dir / "ocr_debug"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    while True:
        tag = f"go_wait_r{round_started_count:03d}_{poll_idx:04d}"
        shot = _observe_with_popup_guard(env, run_dir, tag)
        with Image.open(shot) as img:
            go_crop = img.crop(GO_REGION)
            go_text, go_proc = _ocr_go_text(go_crop)
            game_over_crop = img.crop(GAME_OVER_REGION)
            game_over_text, game_over_proc = _ocr_game_over_text(game_over_crop)

        go_cleaned = re.sub(r"\s+", "", go_text).strip()
        go_guess_tag = go_cleaned if go_cleaned else "FAIL"
        go_guess_tag = re.sub(r"[^A-Za-z0-9_!.-]", "_", go_guess_tag)[:40]
        go_crop.save(ocr_dir / f"go_raw_r{round_started_count:03d}_{poll_idx:04d}_{go_guess_tag}.png")
        go_proc.save(ocr_dir / f"go_proc_r{round_started_count:03d}_{poll_idx:04d}_{go_guess_tag}.png")

        game_over_cleaned = re.sub(r"[^A-Za-z]", "", game_over_text).upper()
        game_over_guess = game_over_cleaned if game_over_cleaned else "FAIL"
        game_over_guess = re.sub(r"[^A-Za-z0-9_!.-]", "_", game_over_guess)[:40]
        game_over_crop.save(
            ocr_dir / f"game_over_raw_r{round_started_count:03d}_{poll_idx:04d}_{game_over_guess}.png"
        )
        game_over_proc.save(
            ocr_dir / f"game_over_proc_r{round_started_count:03d}_{poll_idx:04d}_{game_over_guess}.png"
        )

        if "GAMEOVER" in game_over_cleaned:
            log_stderr(f"Round end detector: GAME OVER text={game_over_text!r} (poll={poll_idx})")
            return False

        if go_cleaned:
            log_stderr(f"Round complete detector: GO text={go_cleaned!r} (poll={poll_idx})")
            return True

        poll_idx += 1
        # Poll quickly so round-complete detection doesn't lag.
        env.page.wait_for_timeout(GO_POLL_INTERVAL_MS)


# ── API call with retry ─────────────────────────────────────────

# Persistent session — avoids stale connection pool issues by using
# urllib3's built-in retry with backoff on connection/SSL errors.
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

_session = requests.Session()
_retry = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[502, 503, 504],
    raise_on_status=False,
)
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=1))


def _is_transient(exc):
    """Return True for network/SSL/timeout errors that should retry forever."""
    transient_types = (
        requests.exceptions.ConnectionError,
        requests.exceptions.SSLError,
        requests.exceptions.Timeout,
    )
    return isinstance(exc, transient_types)


def _write_failed_request_dump(
    payload: dict,
    error: str,
    dump_dir: Path | None = None,
    response_status: int | None = None,
    response_body: str | None = None,
) -> Path | None:
    if dump_dir is None:
        return None
    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out = dump_dir / f"openrouter_error_{ts}.json"

        # Truncate response_body if it's huge (prevents infinite loops or huge files)
        MAX_RESPONSE_BODY_SIZE = 50000  # 50KB should be plenty for error messages
        if response_body and len(response_body) > MAX_RESPONSE_BODY_SIZE:
            response_body = (
                response_body[:MAX_RESPONSE_BODY_SIZE]
                + f"\n\n... [TRUNCATED: response was {len(response_body)} bytes]"
            )

        # Strip request payload images to avoid giant dumps
        safe_payload = payload.copy()
        if "messages" in safe_payload:
            safe_messages = []
            for msg in safe_payload["messages"]:
                if isinstance(msg.get("content"), list):
                    msg = msg.copy()
                    msg["content"] = [
                        c if c.get("type") != "image_url" else {"type": "text", "text": "[image]"}
                        for c in msg["content"]
                    ]
                safe_messages.append(msg)
            safe_payload["messages"] = safe_messages

        out.write_text(
            json.dumps(
                {
                    "error": error,
                    "response_status": response_status,
                    "response_body": response_body,
                    "request_payload": safe_payload,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return out
    except Exception as e:
        # If dump writing fails, log to stderr but don't crash
        try:
            log_stderr(f"Warning: Failed to write error dump: {e}")
        except Exception:
            pass
        return None


def _iter_sse_data(resp):
    """Yield complete SSE data payloads, including multi-line data events."""
    data_lines = []
    for raw_line in resp.iter_lines(chunk_size=1, decode_unicode=True):
        if raw_line is None:
            continue
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            data_lines.append(data)
    if data_lines:
        yield "\n".join(data_lines)


def _extract_text_fragments(value):
    """Flatten provider-specific text containers into plain string fragments."""
    fragments = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        for item in value:
            fragments.extend(_extract_text_fragments(item))
        return fragments
    if isinstance(value, dict):
        # Most providers emit text here.
        text = value.get("text")
        if isinstance(text, str):
            fragments.append(text)
        # Some providers wrap text blocks under these keys.
        for key in ("content", "summary", "output"):
            if key in value:
                fragments.extend(_extract_text_fragments(value[key]))
        return fragments
    return fragments


def _merge_tool_call_delta(accumulated_tool_calls, tc_delta):
    """Merge a single streamed tool-call delta into the accumulated structure."""
    idx = tc_delta.get("index", 0)
    if not isinstance(idx, int) or idx < 0:
        idx = 0
    while len(accumulated_tool_calls) <= idx:
        accumulated_tool_calls.append({
            "id": None,
            "type": "function",
            "function": {"name": "", "arguments": ""}
        })

    tc = accumulated_tool_calls[idx]
    if "id" in tc_delta and tc_delta["id"]:
        tc["id"] = tc_delta["id"]
    if tc_delta.get("type"):
        tc["type"] = tc_delta["type"]

    fn_delta = tc_delta.get("function")
    if isinstance(fn_delta, dict):
        if isinstance(fn_delta.get("name"), str):
            tc["function"]["name"] += fn_delta["name"]
        if isinstance(fn_delta.get("arguments"), str):
            tc["function"]["arguments"] += fn_delta["arguments"]


def _parse_streaming_response(resp):
    """Parse OpenRouter SSE streaming response and reconstruct OpenAI-style output.

    Streams both assistant content and model reasoning/thinking text to stderr.
    """
    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_reasoning_details = []
    accumulated_tool_calls = []
    finish_reason = None
    usage = {}
    printed_any = False
    seen_reasoning = False
    deferred_content_fragments = []
    recent_fragments = deque(maxlen=8)

    def stream_fragment(fragment):
        nonlocal printed_any
        if not fragment:
            return
        normalized = fragment.strip()
        if len(normalized) >= 32 and normalized in recent_fragments:
            return
        if not printed_any:
            sys.stderr.write("[Agent] ")
            printed_any = True
        sys.stderr.write(fragment)
        sys.stderr.flush()
        if len(normalized) >= 32:
            recent_fragments.append(normalized)

    for data_str in _iter_sse_data(resp):
        if data_str == "[DONE]":
            break

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if "error" in chunk:
            err = chunk["error"]
            if isinstance(err, dict):
                msg = err.get("message") or json.dumps(err)
            else:
                msg = str(err)
            raise RuntimeError(f"Stream error: {msg}")

        if "usage" in chunk and isinstance(chunk["usage"], dict):
            usage = chunk["usage"]

        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue

            # Stream reasoning/thinking first, then normal assistant content.
            reasoning_fragments = []
            if "reasoning" in delta:
                reasoning_fragments.extend(_extract_text_fragments(delta["reasoning"]))
            if "reasoning_details" in delta and isinstance(delta["reasoning_details"], list):
                accumulated_reasoning_details.extend(delta["reasoning_details"])
                for detail in delta["reasoning_details"]:
                    if isinstance(detail, dict) and detail.get("type") == "reasoning.encrypted":
                        continue
                    reasoning_fragments.extend(_extract_text_fragments(detail))
            if "thinking" in delta:
                reasoning_fragments.extend(_extract_text_fragments(delta["thinking"]))

            for fragment in reasoning_fragments:
                seen_reasoning = True
                stream_fragment(fragment)
                accumulated_reasoning += fragment

            content_fragments = _extract_text_fragments(delta.get("content"))
            for fragment in content_fragments:
                accumulated_content += fragment
                if STREAM_MODE == "both":
                    stream_fragment(fragment)
                elif STREAM_MODE == "content":
                    stream_fragment(fragment)
                else:
                    # In thinking mode, only surface content if reasoning is absent.
                    if not seen_reasoning:
                        deferred_content_fragments.append(fragment)

            # Accumulate tool calls.
            if isinstance(delta.get("tool_calls"), list):
                for tc_delta in delta["tool_calls"]:
                    if isinstance(tc_delta, dict):
                        _merge_tool_call_delta(accumulated_tool_calls, tc_delta)

            if "finish_reason" in choice and choice["finish_reason"]:
                finish_reason = choice["finish_reason"]

    if STREAM_MODE == "thinking" and not seen_reasoning:
        for fragment in deferred_content_fragments:
            stream_fragment(fragment)

    if printed_any:
        sys.stderr.write("\n")
        sys.stderr.flush()

    message = {}
    if accumulated_content:
        message["content"] = accumulated_content
    encrypted_reasoning = [
        d
        for d in accumulated_reasoning_details
        if isinstance(d, dict) and d.get("type") == "reasoning.encrypted"
    ]
    if encrypted_reasoning:
        message["reasoning_details"] = encrypted_reasoning
    if accumulated_tool_calls:
        message["tool_calls"] = accumulated_tool_calls

    return {
        "choices": [{
            "message": message,
            "finish_reason": finish_reason or "stop"
        }],
        "usage": usage
    }

def call_llm(
    api_key,
    model,
    messages,
    tools,
    reasoning_effort="low",
    max_retries=6,
    error_dump_dir: Path | None = None,
    stream=True,
    tool_choice="auto",
):
    attempt = 0
    while True:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.3,
            "reasoning": {"effort": reasoning_effort, "exclude": False},
            "stream": stream,
        }
        if tools:
            payload["tool_choice"] = tool_choice
        if stream:
            payload["stream_options"] = {"include_usage": True}
        response_status = None
        response_body = None
        try:
            resp = _session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=180,
                stream=stream,
            )
            response_status = resp.status_code

            if resp.status_code >= 400:
                response_body = resp.text
                detail = response_body
                try:
                    body = resp.json()
                    if isinstance(body, dict):
                        if "error" in body and isinstance(body["error"], dict):
                            detail = body["error"].get("message") or json.dumps(body)
                        else:
                            detail = json.dumps(body)
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {resp.status_code}: {detail}")

            # Handle streaming vs non-streaming response
            if stream:
                data = _parse_streaming_response(resp)
            else:
                response_body = resp.text
                data = resp.json()
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", data["error"]))

            return data
        except Exception as e:
            attempt += 1
            dump_path = _write_failed_request_dump(
                payload=payload,
                error=str(e),
                dump_dir=error_dump_dir,
                response_status=response_status,
                response_body=response_body,
            )
            if dump_path:
                log_stderr(f"OpenRouter error request dump: {dump_path}")

            # Classify error type for retry logic
            if _is_transient(e):
                # Network/SSL/timeout errors: retry forever
                _session.close()
                _session.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=1))
                wait = min(2 ** attempt, 60)
                log_stderr(f"Network error (attempt {attempt}, retrying forever): {e}. Waiting {wait}s...")
                time.sleep(wait)
            elif isinstance(e, RuntimeError) and "HTTP 429" in str(e):
                # Rate limit: retry with exponential backoff
                wait = min(2 ** attempt, 30)
                log_stderr(f"Rate limit (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                if attempt >= max_retries:
                    raise
                time.sleep(wait)
            elif isinstance(e, RuntimeError) and "HTTP 400" in str(e):
                # HTTP 400 from provider: retry a few times (provider may be flaky)
                max_400_retries = 3
                if attempt <= max_400_retries:
                    wait = 5 + (attempt * 2)  # 5s, 7s, 9s
                    log_stderr(f"Provider error HTTP 400 (attempt {attempt}/{max_400_retries}): {e}")
                    log_stderr(f"  Model: {model}")
                    log_stderr(f"  Retrying in {wait}s (provider may be temporarily unstable)...")
                    time.sleep(wait)
                else:
                    log_stderr(f"Provider error HTTP 400 persisted after {max_400_retries} retries. Giving up.")
                    raise
            elif isinstance(e, RuntimeError) and "HTTP 4" in str(e):
                # Other 4xx errors (401, 403, 404, etc.): don't retry
                log_stderr(f"Client error {e} - not retrying")
                raise
            elif attempt < max_retries:
                # Other API errors: retry with exponential backoff
                wait = min(2 ** attempt, 30)
                log_stderr(f"API error (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# ── Context management ───────────────────────────────────────────

def strip_old_images(messages):
    """Replace image content with placeholder in all but the last KEEP_IMAGES observations."""
    image_indices = []
    for i, m in enumerate(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            if any(c.get("type") == "image_url" for c in m["content"]):
                image_indices.append(i)

    for i in image_indices[:-KEEP_IMAGES]:
        messages[i]["content"] = [
            c if c.get("type") != "image_url" else {"type": "text", "text": "[screenshot]"}
            for c in messages[i]["content"]
        ]


def _safe_recent_messages(messages, limit=6):
    """Keep only recent non-tool-call messages to avoid broken tool-call chains."""
    kept = []
    for m in reversed(messages):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        # Skip assistant tool-call wrappers; they require matching tool messages.
        if role == "assistant" and m.get("tool_calls"):
            continue
        kept.append(m)
        if len(kept) >= limit:
            break
    kept.reverse()
    return kept


DISTILL_PROMPT = """\
You are a context distillation assistant. Summarize the game history below into a concise
briefing for the next phase of play. Include:

1. **Current round** (approximate) and overall progress
2. **Towers placed**: IDs, names, positions, upgrade levels, targeting — only towers still alive
3. **Strategy so far**: what worked, what failed, any bloon leaks or close calls
4. **Money situation**: last known cash, spending patterns
5. **Key lessons**: placement mistakes, rounds that were hard, anything to avoid repeating
6. **Recommended next steps**: what to buy/upgrade next based on the trajectory

Be concise but complete. This summary replaces the full history — anything you omit is lost."""

RULES_REMINDER = """\
## Key Rules Reminder
- You are AUTONOMOUS. No human is watching. Make all decisions yourself. Never ask questions.
- Cash, lives, and round are shown in every status update — trust them.
- The harness blocks purchases you can't afford. If you get an error, DO NOT retry the same action.
  Instead: start_round to earn more cash, or pick a cheaper upgrade/tower.
- Only one upgrade path per tower can go past tier 2 (e.g. 4/2 is fine, 3/3 is not).
- After placing/upgrading, call start_round to progress. Rounds earn you money.
- Use observe to check the visual game state if unsure what's happening.
"""


def distill_context(messages, token_count, api_key, model, reasoning_effort="low", error_dump_dir: Path | None = None):
    """Replace old messages with an LLM-generated summary when context gets large.

    Args:
        token_count: Approximate token count of the current context

    Returns the distilled messages if threshold exceeded, otherwise strips old images and returns original.
    """
    if token_count < DISTILL_TOKEN_THRESHOLD:
        strip_old_images(messages)
        return messages

    log_stderr(f"Distilling context (~{token_count} tokens, {len(messages)} messages → summary)...")

    system = messages[0]
    # Keep a recent tail of only safe non-tool messages.
    recent_count = 10
    old_messages = messages[1:-recent_count]
    recent_messages = _safe_recent_messages(messages[-recent_count:], limit=6)

    # Build a text-only version of old messages for the distillation call
    old_text_parts = []
    for m in old_messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Flatten multimodal content, skip images
            parts = []
            for c in content:
                if c.get("type") == "text":
                    parts.append(c["text"])
                elif c.get("type") == "image_url":
                    parts.append("[screenshot]")
            content = " ".join(parts)
        if content:
            old_text_parts.append(f"[{role}] {content[:500]}")

    history_text = "\n".join(old_text_parts)

    # Ask the model to distill
    distill_messages = [
        {"role": "system", "content": DISTILL_PROMPT},
        {"role": "user", "content": f"*TOKEN DISTILLATION*: Token limit reached: please provide a summary of the gameplay history so far to maintain continuity. History: \n\n{history_text}"},
    ]

    try:
        data = call_llm(
            api_key,
            model,
            distill_messages,
            tools=[],
            reasoning_effort=reasoning_effort,
            max_retries=2,
            error_dump_dir=error_dump_dir,
            stream=False,  # Don't stream distillation summaries
        )
        summary = data["choices"][0]["message"].get("content", "")
        if not summary:
            raise RuntimeError("Empty distillation response")
        log_stderr(f"Distillation complete ({len(summary)} chars):\n{summary}")
    except Exception as e:
        log_stderr(f"Distillation failed: {e}. Falling back to hard trim.")
        summary = (
            "[Context distillation failed. Earlier history lost. "
            "Use status and observe to check current game state.]"
        )

    result = [
        system,
        {"role": "user", "content": f"## Game History Summary\n\n{summary}\n\n{RULES_REMINDER}"},
        {"role": "assistant", "content": "Understood. I have the context and rules. Continuing play — I will not retry failed actions."},
        *recent_messages,
    ]
    strip_old_images(result)
    return result


# ── Logging ──────────────────────────────────────────────────────

def log_stderr(msg):
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def _prompt_continue_after_token_limit(tokens_total: int, token_limit: int) -> bool:
    """Pause execution and ask the user whether to continue after hitting token limit."""
    if not sys.stdin or not sys.stdin.isatty():
        log_stderr(
            f"Token limit reached ({tokens_total} > {token_limit}) in non-interactive mode; stopping."
        )
        return False

    while True:
        sys.stderr.write(
            f"[{time.strftime('%H:%M:%S')}] Token limit reached "
            f"({tokens_total} > {token_limit}). Continue run? [y/N]: "
        )
        sys.stderr.flush()
        answer = sys.stdin.readline()
        if not answer:
            return False
        choice = answer.strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"", "n", "no"}:
            return False
        log_stderr("Please answer 'y' or 'n'.")


# ── Main agent loop ──────────────────────────────────────────────

def run_agent(env, api_key, model, log_path, reasoning_effort="low", error_dump_dir: Path | None = None):
    tools = mcp_to_openai_tools(model=model)
    start_time = time.time()

    system_prompt = (
        INSTRUCTIONS
        + "\n## Available Towers & Upgrade Paths\n"
        + _format_tower_list()
        + "\n\n## Your Directive\n"
        "You are fully autonomous. There is no human player — you make every decision.\n"
        "Never ask questions, seek confirmation, or say 'let me know'. Just act.\n"
        "Play the game. Survive as many rounds as possible.\n"
        "Each turn you see a screenshot and your tower status (cash, lives, round).\n"
        "Plan your actions, then execute them with tools.\n"
        "After placing/upgrading towers, call start_round to begin.\n"
        "After each round completes, observe the result and adapt your strategy.\n"
        "Upgrades are often more cost-effective than new towers.\n"
        "Your cash is shown in the status — trust it and spend wisely.\n"
    )

    messages = [{"role": "system", "content": system_prompt}]
    total_tokens = {"prompt": 0, "completion": 0}
    current_context_tokens = 0  # Track current conversation context size (not cumulative)
    tracked_round = 1
    rounds_started = 0
    cycle_count = 0
    idle_cycles = 0  # cycles where model returned no tool calls
    game_over = False

    # Loop detection: track state to detect when we're stuck
    last_state = None  # (round, cash, tower_count)
    stuck_cycles = 0
    MAX_STUCK_CYCLES = 5  # Stop if stuck in same state for this many cycles
    TOKEN_LIMIT_STEP = 5_000_000  # Increase token budget in 5M steps when user continues
    MAX_RESPONSE_TOKENS = 100_000  # Stop if a single response exceeds this
    cycles_this_round = 0
    MAX_CYCLES_PER_ROUND = 20  # Stop if we spend too many cycles on one round
    token_limit = TOKEN_LIMIT_STEP

    log_file = open(log_path, "a") if log_path else None
    stop_reason = "unknown"
    last_distill_check = time.time()

    def log_action(entry):
        if log_file:
            entry["ts"] = time.time()
            log_file.write(json.dumps(entry) + "\n")
            log_file.flush()

    try:
        while True:
            cycle_started_at = time.monotonic()
            cycle_count += 1

            # ── Observe ──
            screenshot_path = _observe_with_popup_guard(env, env.run_dir, "agent")
            status_text = _format_status(env)
            b64, mime = encode_image(str(screenshot_path))

            prompt_text = (
                f"Round {tracked_round}\n\n"
                f"{status_text}\n\n"
                f"Make your move using tools. Call start_round when ready to begin the next round."
            )
            if idle_cycles >= 3:
                prompt_text = (
                    f"Round {tracked_round}\n\n"
                    f"{status_text}\n\n"
                    f"⚠️  ERROR: You did not use any tools.\n"
                    f"You MUST call a tool now. DO NOT output text.\n"
                    f"Example: start_round, place_tower, upgrade_tower, status"
                )
                idle_cycles = 0

            messages.append(make_image_message(b64, prompt_text, mime_type=mime))

            # Parse game state from status for the cycle status line
            gs = env.read_game_state()
            cash_display = f"${gs.cash}" if gs.cash is not None else "?"
            lives_display = str(gs.lives) if gs.lives is not None else "?"
            elapsed_minutes = int((time.time() - start_time) // 60)

            log_stderr(
                f"Round {tracked_round}"
                f" | Cash: {cash_display} | Lives: {lives_display}"
                f" | Towers: {len(env.get_placed_towers())}"
                f" | Tokens: {sum(total_tokens.values())}"
                f" | Elapsed: {elapsed_minutes}m"
            )

            # ── Safety checks: detect loops and token limits ──
            current_state = (tracked_round, gs.cash, len(env.get_placed_towers()))
            if current_state == last_state:
                stuck_cycles += 1
                if stuck_cycles >= MAX_STUCK_CYCLES:
                    log_stderr(
                        f"Loop detected: stuck in same state for {stuck_cycles} cycles "
                        f"(Round {tracked_round}, Cash {cash_display}, {len(env.get_placed_towers())} towers). "
                        "Stopping to prevent infinite loop."
                    )
                    stop_reason = "loop_detected"
                    break
            else:
                stuck_cycles = 0
                last_state = current_state

            tokens_total = sum(total_tokens.values())
            if tokens_total > token_limit:
                should_continue = _prompt_continue_after_token_limit(tokens_total, token_limit)
                if should_continue:
                    token_limit += TOKEN_LIMIT_STEP
                    log_stderr(
                        f"Continuing run. New token limit: {token_limit}. "
                        "Attempting immediate context distillation to reduce prompt size."
                    )
                    messages = distill_context(
                        messages,
                        token_count=max(current_context_tokens, DISTILL_TOKEN_THRESHOLD + 1),
                        api_key=api_key,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        error_dump_dir=error_dump_dir,
                    )
                else:
                    log_stderr(f"Token limit exceeded ({tokens_total} > {token_limit}). Stopping.")
                    stop_reason = "token_limit"
                    break

            cycles_this_round += 1
            if cycles_this_round > MAX_CYCLES_PER_ROUND:
                log_stderr(
                    f"Spending too long on round {tracked_round} ({cycles_this_round} cycles). "
                    "Possible infinite loop. Stopping."
                )
                stop_reason = "cycle_limit"
                break

            # ── Inner action loop ──
            actions_this_cycle = 0
            did_act = False
            round_started_this_cycle = False
            should_stop = False

            while actions_this_cycle < MAX_ACTIONS_PER_CYCLE and not should_stop:
                data = call_llm(
                    api_key,
                    model,
                    messages,
                    tools,
                    reasoning_effort=reasoning_effort,
                    error_dump_dir=error_dump_dir,
                    tool_choice="required",
                )

                # Track usage
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens["prompt"] += prompt_tokens
                total_tokens["completion"] += completion_tokens
                current_context_tokens = prompt_tokens  # Current context size

                # Check for runaway responses
                response_tokens = prompt_tokens + completion_tokens
                if response_tokens > MAX_RESPONSE_TOKENS:
                    log_stderr(
                        f"Single response exceeded token limit ({response_tokens} > {MAX_RESPONSE_TOKENS}). "
                        "Model may be stuck in reasoning loop. Stopping."
                    )
                    stop_reason = "response_token_limit"
                    should_stop = True
                    break

                choice = data["choices"][0]
                msg = choice["message"]

                # Build assistant message for history
                assistant_msg = {"role": "assistant", "content": msg.get("content") or None}
                if msg.get("tool_calls"):
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                if msg.get("reasoning_details"):
                    assistant_msg["reasoning_details"] = msg["reasoning_details"]

                # Don't keep large no-tool rambles in history.
                if not msg.get("tool_calls") and assistant_msg.get("content"):
                    if len(assistant_msg["content"]) > 240:
                        assistant_msg["content"] = assistant_msg["content"][:240] + " …"
                messages.append(assistant_msg)

                # Content already streamed in real-time during call_llm
                # No need to log again here

                # No tool calls → end of cycle
                if not msg.get("tool_calls"):
                    if not did_act:
                        idle_cycles += 1
                    # Detect if model is outputting huge text dumps instead of tool calls
                    content_len = len(msg.get("content") or "")
                    if content_len > 1000 and not did_act:
                        log_stderr(f"⚠️  Model output {content_len} chars without tool calls. Likely confused/looping.")
                    break

                idle_cycles = 0
                did_act = True
                inject_screenshot = False
                inject_path = None

                for tc in msg["tool_calls"]:
                    fn = tc["function"]
                    name = fn["name"]
                    try:
                        args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    # Strict pre-action guard: always clear popup state before every action.
                    if env.run_dir is None:
                        raise RuntimeError("run_dir missing before pre-action popup guard")
                    _observe_with_popup_guard(env, env.run_dir, f"pre_action_c{cycle_count:04d}_{actions_this_cycle:03d}")

                    result_text, img_path = execute_tool(
                        env,
                        name,
                        args,
                        screenshot_hook=lambda p, t: _dismiss_ok_popup_if_present(env, p, env.run_dir, t),
                    )
                    actions_this_cycle += 1

                    log_stderr(f"  tool: {name}({json.dumps(args)}) → {result_text}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })

                    log_action({
                        "cycle": cycle_count,
                        "round": tracked_round,
                        "tool": name,
                        "args": args,
                        "result": result_text,
                    })

                    if name == "start_round":
                        rounds_started += 1
                        round_started_this_cycle = True

                    if img_path:
                        inject_screenshot = True
                        inject_path = img_path

                    if round_started_this_cycle:
                        break

                if round_started_this_cycle:
                    break

                # If model called observe, inject the image so it can see it
                if inject_screenshot and inject_path:
                    b64, mime = encode_image(inject_path)
                    messages.append(make_image_message(
                        b64, "Here is the screenshot you requested. Continue.", mime_type=mime
                    ))

            # Check if inner loop hit a stop condition
            if should_stop:
                break

            # On start_round, wait until Go button returns before next LLM turn.
            if round_started_this_cycle:
                log_stderr("Round started by agent; waiting for GO button to return...")
                if env.run_dir is None:
                    raise RuntimeError("run_dir missing while waiting for GO button")
                round_completed = _wait_for_go_button(env, env.run_dir, rounds_started)
                if not round_completed:
                    log_stderr("Detected GAME OVER while waiting for round completion. Stopping agent run.")
                    game_over = True
                    break
                tracked_round += 1
                cycles_this_round = 0  # Reset counter for new round

            # ── Distill context if needed (periodic check based on time + tokens) ──
            now = time.time()
            if now - last_distill_check >= DISTILL_CHECK_INTERVAL_S:
                last_distill_check = now
                # Use current context size (prompt tokens), not cumulative total
                messages = distill_context(
                    messages,
                    token_count=current_context_tokens,
                    api_key=api_key,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    error_dump_dir=error_dump_dir,
                )

            log_action({
                "cycle": cycle_count,
                "round": tracked_round,
                "event": "cycle_end",
                "actions": actions_this_cycle,
                "message_count": len(messages),
                "tokens_total": sum(total_tokens.values()),
            })

            # Keep cadence stable for planning-only cycles.
            # If we just started/completed a round, continue immediately.
            if not round_started_this_cycle:
                cycle_elapsed = time.monotonic() - cycle_started_at
                if cycle_elapsed < CYCLE_INTERVAL_S:
                    time.sleep(CYCLE_INTERVAL_S - cycle_elapsed)

    except KeyboardInterrupt:
        log_stderr("Stopped by user.")
        stop_reason = "user_interrupt"
    except Exception as e:
        log_stderr(f"Fatal error: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        stop_reason = f"error: {e}"
    else:
        # Don't clobber explicit stop reasons set inside the loop.
        if stop_reason == "unknown":
            if game_over:
                stop_reason = "game_over"
            else:
                stop_reason = "completed"
    finally:
        elapsed_s = time.time() - start_time
        log_stderr(
            f"\nDone: {cycle_count} cycles, {rounds_started} rounds started,"
            f" {sum(total_tokens.values())} tokens used"
        )
        if log_file:
            log_file.close()

        # Write results summary
        placed = env.get_placed_towers()
        towers_data = [
            {"id": tid, "name": t.name, "x": t.x, "y": t.y,
             "upgrades": t.upgrades, "target": t.target}
            for tid, t in sorted(placed.items())
        ]
        results = {
            "model": model,
            "round_reached": tracked_round,
            "rounds_started": rounds_started,
            "cycles": cycle_count,
            "elapsed_s": round(elapsed_s, 1),
            "total_messages": len(messages),
            "tokens_prompt": total_tokens["prompt"],
            "tokens_completion": total_tokens["completion"],
            "tokens_total": total_tokens["prompt"] + total_tokens["completion"],
            "stop_reason": stop_reason,
            "timestamp": datetime.now().isoformat(),
            "towers": towers_data,
        }
        if env.run_dir:
            results_path = env.run_dir / "results.json"
            results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            log_stderr(f"Results saved: {results_path}")

            # Auto-export submission for leaderboard
            try:
                sub_path = export_run(env.run_dir)
                if sub_path:
                    log_stderr(f"Submission exported: {sub_path}")
            except Exception as e:
                log_stderr(f"Auto-export failed (non-fatal): {e}")

        log_stderr(json.dumps(results, indent=2))


# ── Entry point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run an LLM agent playing BTD5 via OpenRouter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python scripts/run_agent.py --model anthropic/claude-sonnet-4\n"
               "  python scripts/run_agent.py --model openai/gpt-4o\n",
    )
    parser.add_argument("--model", required=True, help="OpenRouter model ID")
    parser.add_argument("--max-rounds", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--swf", default="game/btd5.swf")
    parser.add_argument(
        "--profile",
        default="profiles/persistent/default/chromium-profile",
        help="Persistent Chromium profile dir (relative to repo root)",
    )
    parser.add_argument("--saves", default="saves/default.json", help="Save JSON to inject before load (default: saves/default.json)")
    parser.add_argument("--map", default="monkey_lane")
    parser.add_argument("--difficulty", default="easy")
    parser.add_argument(
        "--reasoning",
        default="low",
        choices=["low", "medium", "high"],
        help="Reasoning effort passed to the model (default: low)",
    )
    args = parser.parse_args()
    if args.max_rounds is not None:
        log_stderr("Warning: --max-rounds is deprecated and ignored. Runs are now unbounded.")

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Error: OPENROUTER_API_KEY not found. Set it in .env or your environment.")

    # Launch game
    cfg = HarnessConfig(
        headless=False,
        persistent_profile_dir=REPO_ROOT / args.profile,
        auto_navigate_to_round=True,
        nav_map_name=args.map,
        nav_difficulty=args.difficulty,
        save_data_path=REPO_ROOT / args.saves,
        block_network=True,
    )
    env = BloonsWebEnv(
        repo_root=REPO_ROOT,
        swf_path=(REPO_ROOT / args.swf).resolve(),
        cfg=cfg,
    )

    log_stderr(f"Launching game (model: {args.model})...")
    run_dir = env.reset()
    log_stderr(f"Game ready. Logs: {run_dir}")

    log_path = run_dir / "agent_log.jsonl"

    try:
        run_agent(
            env,
            api_key,
            args.model,
            log_path,
            reasoning_effort=args.reasoning,
            error_dump_dir=run_dir,
        )
    finally:
        env.close()


if __name__ == "__main__":
    main()
