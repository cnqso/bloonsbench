#!/usr/bin/env python3
"""Launch BTD5 and serve it via MCP (stdio) or interactive CLI.

The game launches visible (never headless), injects saves, and navigates
to round start.  Only then does communication open — either as an MCP
JSON-RPC server on stdin/stdout, or as an interactive CLI prompt.

Usage:
    python scripts/run_mcp.py              # MCP mode (for agents)
    python scripts/run_mcp.py --cli        # Interactive CLI
    python scripts/run_mcp.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from harness.env.config import HarnessConfig
from harness.env.web_env import BloonsWebEnv
from harness.mcp_server import run_server, handle_tool_call, _format_tower_list, _format_status


def _launch_game(args: argparse.Namespace) -> BloonsWebEnv:
    """Launch the game, inject saves, navigate to round start."""
    cfg = HarnessConfig(
        headless=False,  # Never headless
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
    sys.stderr.write("Launching game...\n")
    sys.stderr.flush()
    env.reset()
    sys.stderr.write("Game ready — at round start\n")
    sys.stderr.flush()
    return env


def run_cli(env: BloonsWebEnv) -> None:
    """Interactive command loop."""
    HELP = """\
Game is loaded at round 1 start. Money and lives are in the top-left.
Coords are content-relative (960x720). Map area: ~x=0-830, y=0-600.

Commands:
  observe                     Screenshot the current game state
  place <tower> <x> <y>       Place tower, returns ID for upgrade/sell/target
  upgrade <id> <path>         Upgrade tower (path 1 or 2, max 4 tiers each)
  sell <id>                   Sell a placed tower for cash back
  target <id> <mode>          Set targeting: first / last / close / strong
  status                      Show towers, IDs, upgrades, and next upgrade costs
  start                       Start round (fast-forward, 7s wait)
  click <x> <y>              Raw click (escape hatch for stuck UI)
  key <key>                   Press key (e.g. Escape to cancel placement)
  wait <ms>                   Wait milliseconds
  towers                      List all towers with prices + upgrade paths
  help                        Show this help
  quit                        Exit

Tips:
  - Use 'towers' to see all prices. Use 'status' to see next upgrade costs.
  - If a tower gets stuck to cursor (bad coords), click a valid spot or key Escape.
  - 'observe' often to check money, lives, and game state."""

    print(HELP)
    print()

    while True:
        try:
            line = input("bloons> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "observe":
                path = env.observe(tag="cli")
                print(f"Screenshot saved: {path}")
            elif cmd == "place":
                if len(parts) < 4:
                    print("Usage: place <tower> <x> <y>")
                    continue
                result = handle_tool_call(env, "place_tower", {
                    "tower": parts[1], "x": float(parts[2]), "y": float(parts[3])
                })
                print(result["content"][0]["text"])
            elif cmd == "upgrade":
                if len(parts) < 3:
                    print("Usage: upgrade <tower_id> <path>")
                    continue
                result = handle_tool_call(env, "upgrade_tower", {
                    "tower_id": int(parts[1]), "path": int(parts[2])
                })
                print(result["content"][0]["text"])
            elif cmd == "sell":
                if len(parts) < 2:
                    print("Usage: sell <tower_id>")
                    continue
                result = handle_tool_call(env, "sell_tower", {
                    "tower_id": int(parts[1])
                })
                print(result["content"][0]["text"])
            elif cmd == "target":
                if len(parts) < 3:
                    print("Usage: target <tower_id> <first|last|close|strong>")
                    continue
                result = handle_tool_call(env, "set_target", {
                    "tower_id": int(parts[1]), "target": parts[2]
                })
                print(result["content"][0]["text"])
            elif cmd == "status":
                print(_format_status(env))
            elif cmd == "start":
                result = handle_tool_call(env, "start_round", {})
                print(result["content"][0]["text"])
            elif cmd == "click":
                if len(parts) < 3:
                    print("Usage: click <x> <y>")
                    continue
                result = handle_tool_call(env, "click", {
                    "x": float(parts[1]), "y": float(parts[2])
                })
                print(result["content"][0]["text"])
            elif cmd == "key":
                if len(parts) < 2:
                    print("Usage: key <key>")
                    continue
                result = handle_tool_call(env, "send_key", {"key": parts[1]})
                print(result["content"][0]["text"])
            elif cmd == "wait":
                if len(parts) < 2:
                    print("Usage: wait <ms>")
                    continue
                result = handle_tool_call(env, "wait", {"ms": int(parts[1])})
                print(result["content"][0]["text"])
            elif cmd == "towers":
                print(_format_tower_list())
            else:
                print(f"Unknown command: {cmd}. Type 'help' for commands.")
        except Exception as e:
            print(f"Error: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="BloonsBench MCP server / CLI")
    parser.add_argument("--cli", action="store_true", help="Interactive CLI instead of MCP")
    parser.add_argument("--swf", default="game/btd5.swf", help="Path to SWF (relative to repo root)")
    parser.add_argument("--saves", default="saves/unlocks_maxed.json", help="Save file to inject")
    parser.add_argument("--map", default="monkey_lane", help="Map name")
    parser.add_argument("--difficulty", default="easy", help="Difficulty")
    args = parser.parse_args()

    env = _launch_game(args)
    try:
        if args.cli:
            run_cli(env)
        else:
            run_server(env)
    finally:
        env.close()


if __name__ == "__main__":
    main()
