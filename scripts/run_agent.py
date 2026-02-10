#!/usr/bin/env python3
"""Autonomous LLM agent for BTD5 via OpenRouter.

Launches the game, then loops forever: observe → think → act.
Uses OpenRouter's OpenAI-compatible API so you can swap models easily.

Usage:
    python scripts/run_agent.py --model anthropic/claude-sonnet-4
    python scripts/run_agent.py --model openai/gpt-4o --max-rounds 50

Requires OPENROUTER_API_KEY in .env or environment.
Model must support vision + tool/function calling.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import requests
from harness.env.config import HarnessConfig
from harness.env.web_env import BloonsWebEnv
from harness.mcp_server import INSTRUCTIONS, TOOL_DEFS, _format_tower_list, _format_status

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ACTIONS_PER_TICK = 25  # safety cap per observation cycle
DISTILL_THRESHOLD = 80  # trigger distillation when messages exceed this
KEEP_IMAGES = 1  # only keep the most recent screenshot


# ── .env loading ─────────────────────────────────────────────────

def load_dotenv():
    for env_path in [REPO_ROOT / ".env", Path.home() / ".env"]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip("\"'"))


# ── Tool format conversion ───────────────────────────────────────

def mcp_to_openai_tools():
    """Convert our MCP TOOL_DEFS to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["inputSchema"],
            },
        }
        for d in TOOL_DEFS
    ]


# ── Tool execution ───────────────────────────────────────────────

def execute_tool(env, name, args):
    """Run a tool call against the live game.

    Returns (text_result, image_path | None).
    image_path is set only for 'observe' so the caller can inject the screenshot.
    """
    try:
        if name == "observe":
            path = env.observe(tag="agent")
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
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def make_image_message(image_b64, text, role="user"):
    return {
        "role": role,
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            {"type": "text", "text": text},
        ],
    }


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


def call_llm(api_key, model, messages, tools, max_retries=6):
    attempt = 0
    while True:
        try:
            resp = _session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0.3,
                },
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("message", data["error"]))
            return data
        except Exception as e:
            attempt += 1
            if _is_transient(e):
                # Kill the dead connection pool and start fresh
                _session.close()
                _session.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=1))
                wait = min(2 ** attempt, 60)
                log_stderr(f"Network error (attempt {attempt}, retrying forever): {e}. Waiting {wait}s...")
                time.sleep(wait)
            elif attempt < max_retries:
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


def distill_context(messages, api_key, model):
    """Replace old messages with an LLM-generated summary. Always preserves the system prompt."""
    if len(messages) <= DISTILL_THRESHOLD:
        strip_old_images(messages)
        return messages

    log_stderr(f"Distilling context ({len(messages)} messages → summary)...")

    system = messages[0]
    # Keep a recent tail so the model has immediate context after distillation
    recent_count = 10
    old_messages = messages[1:-recent_count]
    recent_messages = messages[-recent_count:]

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
        {"role": "user", "content": f"Here is the game history to summarize:\n\n{history_text}"},
    ]

    try:
        data = call_llm(api_key, model, distill_messages, tools=[], max_retries=2)
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


# ── Main agent loop ──────────────────────────────────────────────

def run_agent(env, api_key, model, log_path, max_rounds):
    tools = mcp_to_openai_tools()

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
    round_num = 0
    tick = 0
    idle_ticks = 0  # ticks where model returned no tool calls

    log_file = open(log_path, "a") if log_path else None

    def log_action(entry):
        if log_file:
            entry["ts"] = time.time()
            log_file.write(json.dumps(entry) + "\n")
            log_file.flush()

    try:
        while max_rounds <= 0 or round_num < max_rounds:
            tick += 1

            # ── Observe ──
            screenshot_path = env.observe(tag="agent")
            status_text = _format_status(env)
            b64 = encode_image(str(screenshot_path))

            prompt_text = f"Current status:\n{status_text}\n\nDecide your next actions."
            if idle_ticks >= 3:
                prompt_text += "\n\nPlease use the available tools to take action."
                idle_ticks = 0

            messages.append(make_image_message(b64, prompt_text))

            # Parse game state from status for the tick header
            gs = env.read_game_state()
            if gs.round_num is not None:
                round_num = gs.round_num
            cash_display = f"${gs.cash}" if gs.cash is not None else "?"
            lives_display = str(gs.lives) if gs.lives is not None else "?"

            log_stderr(
                f"{'=' * 50}\n"
                f"[{time.strftime('%H:%M:%S')}] Tick {tick} | Round {round_num}"
                f" | Cash: {cash_display} | Lives: {lives_display}"
                f" | Towers: {len(env.get_placed_towers())}"
                f" | Tokens: {sum(total_tokens.values())}\n"
                f"{'=' * 50}"
            )

            # ── Inner action loop ──
            actions_this_tick = 0
            did_act = False

            while actions_this_tick < MAX_ACTIONS_PER_TICK:
                data = call_llm(api_key, model, messages, tools)

                # Track usage
                usage = data.get("usage", {})
                total_tokens["prompt"] += usage.get("prompt_tokens", 0)
                total_tokens["completion"] += usage.get("completion_tokens", 0)

                choice = data["choices"][0]
                msg = choice["message"]

                # Build assistant message for history
                assistant_msg = {"role": "assistant", "content": msg.get("content") or None}
                if msg.get("tool_calls"):
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                messages.append(assistant_msg)

                if msg.get("content"):
                    log_stderr(f"Agent: {msg['content'][:300]}")

                # No tool calls → end of tick
                if not msg.get("tool_calls"):
                    if not did_act:
                        idle_ticks += 1
                    break

                idle_ticks = 0
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

                    result_text, img_path = execute_tool(env, name, args)
                    actions_this_tick += 1

                    log_stderr(f"  tool: {name}({json.dumps(args)}) → {result_text}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })

                    log_action({
                        "tick": tick,
                        "round": round_num,
                        "tool": name,
                        "args": args,
                        "result": result_text,
                    })

                    if name == "start_round":
                        round_num += 1

                    if img_path:
                        inject_screenshot = True
                        inject_path = img_path

                # If model called observe, inject the image so it can see it
                if inject_screenshot and inject_path:
                    b64 = encode_image(inject_path)
                    messages.append(make_image_message(
                        b64, "Here is the screenshot you requested. Continue."
                    ))

            # ── Distill context if needed ──
            messages = distill_context(messages, api_key, model)

            log_action({
                "tick": tick,
                "round": round_num,
                "event": "tick_end",
                "actions": actions_this_tick,
                "message_count": len(messages),
                "tokens_total": sum(total_tokens.values()),
            })

    except KeyboardInterrupt:
        log_stderr("Stopped by user.")
    except Exception as e:
        log_stderr(f"Fatal error: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
    finally:
        log_stderr(
            f"\nDone: {tick} ticks, ~{round_num} rounds started,"
            f" {sum(total_tokens.values())} tokens used"
        )
        if log_file:
            log_file.close()


# ── Entry point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run an LLM agent playing BTD5 via OpenRouter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python scripts/run_agent.py --model anthropic/claude-sonnet-4\n"
               "  python scripts/run_agent.py --model openai/gpt-4o --max-rounds 20\n",
    )
    parser.add_argument("--model", required=True, help="OpenRouter model ID")
    parser.add_argument("--max-rounds", type=int, default=0, help="Stop after N rounds (0 = infinite)")
    parser.add_argument("--swf", default="game/btd5.swf")
    parser.add_argument("--saves", default="saves/unlocks_maxed.json")
    parser.add_argument("--map", default="monkey_lane")
    parser.add_argument("--difficulty", default="easy")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Error: OPENROUTER_API_KEY not found. Set it in .env or your environment.")

    # Launch game
    cfg = HarnessConfig(
        headless=False,
        auto_navigate_to_round=True,
        nav_map_name=args.map,
        nav_difficulty=args.difficulty,
        save_data_path=Path(args.saves) if args.saves else None,
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
        run_agent(env, api_key, args.model, log_path, args.max_rounds)
    finally:
        env.close()


if __name__ == "__main__":
    main()
