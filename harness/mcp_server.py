"""Minimal MCP (Model Context Protocol) stdio server for BloonsBench.

JSON-RPC 2.0 over newline-delimited JSON on stdin/stdout.
Takes an already-initialized BloonsWebEnv — game must be ready before
the server starts accepting commands.

Zero new dependencies beyond the stdlib.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from typing import Any

from harness.env.web_env import BloonsWebEnv
from harness.env.menu_nav import TOWERS, next_upgrade

PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {
    "name": "bloonsbench",
    "version": "0.1.0",
}

INSTRUCTIONS = """\
You are an AUTONOMOUS agent playing Bloons Tower Defense 5 (BTD5) on medium difficulty.
You operate without any human supervision — there is no user to ask questions to or wait for.
Make all decisions yourself. Never output questions, requests for guidance, or "let me know" messages.
Just analyze the game state, decide, and act using tools.

The game is already loaded and navigated to the start of round 1. You have not yet pressed GO.

## Game Objective
Bloons (balloons) travel along a fixed path. You place towers along the path to pop them.
If a bloon reaches the end, you lose lives. Lose all lives and it's game over.
Survive all rounds to win.

## Screen Layout
- The map fills most of the screen (content area: 960x720).
- The tower sidebar is on the RIGHT edge (~x=840-960).
- MONEY, LIVES, and ROUND are read automatically via OCR and shown in status output.
- The GO button is in the BOTTOM-RIGHT area.

## Coordinate System
All coordinates are content-relative within a 960x720 area.
- (0, 0) is the top-left of the game content.
- (960, 720) is the bottom-right.
- The playable map area is roughly x=0-830, y=0-600.
- The sidebar (tower icons) is roughly x=840-960.

## Core Loop
1. Place towers and/or upgrade them while the round is paused.
2. Call start_round to begin. This automatically enables fast-forward and waits 7 seconds.
3. After the wait, observe to see the result.
4. If bloons are still coming, you can wait more and observe again.
5. When the round ends, you'll be back at the pre-round screen. Place/upgrade, then start again.

## Placing Towers
- Use list_towers to see all towers with costs and upgrade paths.
- Use place_tower with a tower name and (x, y) on the map.
- Towers can only be placed on valid terrain (not on the path, water, or other towers).
- IMPORTANT: If you place at an invalid location, the tower will be "stuck to your cursor"
  and the game will wait for a valid click. Use the click tool to place it somewhere valid,
  or send_key with Escape to cancel the placement.
- Each placed tower gets a numeric ID (1, 2, 3...). Use this ID for upgrades, targeting, and selling.
- Use status to see all placed towers, their IDs, and your current cash at any time.

## Suggested Placement Spots (Monkey Lane)
Towers MUST be placed on valid terrain — not on the path, water, or overlapping other towers.
Invalid placements cause the tower to stick to your cursor (use Escape to cancel).
Here are some known-good locations to get you started. This list is NOT exhaustive — there
are many other valid spots on the map. Use observe to visually identify open grass areas.
- (280, 350) — below the center bend
- (150, 320) — left side, near the first curve
- (290, 270) — above the center bend
- Horizontal strip from (200, 235) to (280, 235) — open grass above the path
- Long horizontal strip from (260, 145) to (585, 145) — large open area near the top
- (715, 240) — the crook in the upper-right bend
When these spots are full, look for open grass areas away from the path. Avoid placing
near the edges of the path or too close to existing towers.

## Upgrading Towers
- Each tower has two upgrade paths, each with up to 4 tiers.
- Use status to see current upgrade levels and the name+cost of the NEXT available upgrade.
- Use upgrade_tower with tower_id and path (1 or 2).
- Upgrades cost money. If you can't afford it, the upgrade won't apply.
- The harness tracks upgrade levels internally. Check status to confirm upgrades went through.

## Targeting
- Towers default to "first" targeting (the bloon furthest along the path).
- Use set_target to change: first, last, close, or strong.
- "strong" targets the highest-health bloon in range — good for tough bloons.

## Selling
- Use sell_tower to sell a tower and recover some cash.

## Cash Validation
The harness automatically reads your cash via OCR and blocks purchases you can't afford.
- If you try to place a tower or buy an upgrade without enough money, you'll get an error
  showing the cost and your current cash.
- Use status to see your current cash, tower costs (list_towers), and upgrade costs.
- If OCR fails to read cash, purchases proceed without a cash check — use observe to
  verify the game state if something seems wrong.

## Troubleshooting with click and send_key
- The click and send_key tools are escape hatches for anything the specialized tools can't do.
- If the UI is in an unexpected state, try: click on an empty map area, or send_key Escape.
- Observe frequently to verify the game state matches your expectations.
- When in doubt, observe first, then decide.
"""

TOOL_DEFS = [
    {
        "name": "observe",
        "description": "Take a screenshot of the current game state. Returns a base64 PNG image.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "place_tower",
        "description": "Place a tower on the map. Returns a tower ID (integer) used for upgrade/sell/target. Only valid map terrain works — if placement fails, the tower gets stuck to cursor (use click to retry or send_key Escape to cancel). Use list_towers to see names and prices.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tower": {"type": "string", "description": "Tower name (use list_towers to see options)"},
                "x": {"type": "number", "description": "X coordinate on the map (0-830 playable area)"},
                "y": {"type": "number", "description": "Y coordinate on the map (0-600 playable area)"},
            },
            "required": ["tower", "x", "y"],
        },
    },
    {
        "name": "upgrade_tower",
        "description": "Upgrade a placed tower along path 1 or path 2 (max 4 tiers each). Costs money — use status to see the next upgrade name and price. If you can't afford it, nothing happens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tower_id": {"type": "integer", "description": "ID of the placed tower (from place_tower or status)"},
                "path": {"type": "integer", "enum": [1, 2], "description": "Upgrade path (1 or 2)"},
            },
            "required": ["tower_id", "path"],
        },
    },
    {
        "name": "sell_tower",
        "description": "Sell a placed tower for cash back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tower_id": {"type": "integer", "description": "ID of the placed tower"},
            },
            "required": ["tower_id"],
        },
    },
    {
        "name": "set_target",
        "description": "Set targeting mode for a placed tower. Options: first (most forward bloon), last (furthest back), close (nearest to tower), strong (highest health).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tower_id": {"type": "integer", "description": "ID of the placed tower"},
                "target": {"type": "string", "enum": ["first", "last", "close", "strong"], "description": "Targeting mode"},
            },
            "required": ["tower_id", "target"],
        },
    },
    {
        "name": "status",
        "description": "Show all placed towers: IDs, names, positions, upgrade levels, targeting mode, and the name+cost of each tower's next available upgrade.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "start_round",
        "description": "Click GO to start the round with fast-forward enabled. Waits 7 seconds automatically before returning, so the round has time to progress.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click",
        "description": "Raw click at content-relative (x, y). Escape hatch for recovering from stuck UI states (e.g. tower stuck to cursor, unexpected dialog). Coordinates are in the 960x720 content area.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X coordinate (0-960)"},
                "y": {"type": "number", "description": "Y coordinate (0-720)"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "send_key",
        "description": "Press a keyboard key. Useful for Escape (cancel placement / close menus). Playwright key format (e.g. 'Space', 'Escape', 'a').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name (Playwright format)"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for a number of milliseconds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "description": "Milliseconds to wait"},
            },
            "required": ["ms"],
        },
    },
    {
        "name": "list_towers",
        "description": "Return all available tower names with base costs and full upgrade paths (names + costs for all 4 tiers of each path).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def _text_content(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def _image_content(png_path: str) -> list[dict]:
    with open(png_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return [{"type": "image", "data": data, "mimeType": "image/png"}]


def _error_content(msg: str) -> list[dict]:
    return [{"type": "text", "text": f"Error: {msg}"}]


def _format_path(upgrades: tuple) -> str:
    return " > ".join(f"{u.name} ${u.cost}" for u in upgrades)


def _format_tower_list() -> str:
    lines = ["Available towers:"]
    for name, t in TOWERS.items():
        lines.append(f"  {name} (${t.cost})")
        lines.append(f"    Path 1: {_format_path(t.path1)}")
        lines.append(f"    Path 2: {_format_path(t.path2)}")
    return "\n".join(lines)


def _format_status(env: BloonsWebEnv) -> str:
    gs = env.read_game_state()  # cached — no screenshot
    cash_str = f"${gs.cash}" if gs.cash is not None else "unknown"
    lives_str = str(gs.lives) if gs.lives is not None else "unknown"
    round_str = str(gs.round_num) if gs.round_num is not None else "unknown"
    lines = [f"Cash: {cash_str}  |  Lives: {lives_str}  |  Round: {round_str}"]
    towers = env.get_placed_towers()
    if not towers:
        lines.append("No towers placed.")
        return "\n".join(lines)
    lines.append("Placed towers:")
    for tid, t in sorted(towers.items()):
        tdef = TOWERS.get(t.name)
        # Current upgrade names
        p1_cur = tdef.path1[t.upgrades[0] - 1].name if tdef and t.upgrades[0] > 0 else "base"
        p2_cur = tdef.path2[t.upgrades[1] - 1].name if tdef and t.upgrades[1] > 0 else "base"
        lines.append(
            f"  #{tid} {t.name} at ({t.x}, {t.y})"
            f"  [{t.upgrades[0]}/{t.upgrades[1]}]"
            f"  target={t.target}"
            f"  (path1: {p1_cur}, path2: {p2_cur})"
        )
        # Next available upgrades
        nxt1 = next_upgrade(t.name, 1, t.upgrades[0], other_path_level=t.upgrades[1])
        nxt2 = next_upgrade(t.name, 2, t.upgrades[1], other_path_level=t.upgrades[0])
        p1_str = f"{nxt1.name} ${nxt1.cost}" if nxt1 else "LOCKED" if t.upgrades[1] > 2 and t.upgrades[0] < 4 else "MAXED"
        p2_str = f"{nxt2.name} ${nxt2.cost}" if nxt2 else "LOCKED" if t.upgrades[0] > 2 and t.upgrades[1] < 4 else "MAXED"
        lines.append(f"      next: path1 → {p1_str}  |  path2 → {p2_str}")
    return "\n".join(lines)


def handle_tool_call(env: BloonsWebEnv, name: str, args: dict) -> dict:
    """Dispatch a tool call. Returns MCP tool result dict."""
    try:
        if name == "observe":
            path = env.observe(tag="mcp")
            return {"content": _image_content(str(path))}

        elif name == "place_tower":
            tid = env.place_tower(args["tower"], args["x"], args["y"])
            return {"content": _text_content(
                f"Placed {args['tower']} at ({args['x']}, {args['y']}), tower_id={tid}"
            )}

        elif name == "upgrade_tower":
            env.upgrade_tower(args["tower_id"], args["path"])
            tower = env.get_placed_towers()[args["tower_id"]]
            return {"content": _text_content(
                f"Upgraded #{args['tower_id']} {tower.name} path {args['path']}"
                f" → now {tower.upgrades[0]}/{tower.upgrades[1]}"
            )}

        elif name == "sell_tower":
            env.sell_tower(args["tower_id"])
            return {"content": _text_content(f"Sold tower #{args['tower_id']}")}

        elif name == "set_target":
            env.set_target(args["tower_id"], args["target"])
            return {"content": _text_content(
                f"Set #{args['tower_id']} target to {args['target']}"
            )}

        elif name == "status":
            return {"content": _text_content(_format_status(env))}

        elif name == "start_round":
            env.start_round()
            return {"content": _text_content("Round started")}

        elif name == "click":
            env.click_content(args["x"], args["y"])
            return {"content": _text_content(f"Clicked ({args['x']}, {args['y']})")}

        elif name == "send_key":
            env.press(args["key"])
            return {"content": _text_content(f"Pressed {args['key']}")}

        elif name == "wait":
            ms = args["ms"]
            env.page.wait_for_timeout(ms)
            return {"content": _text_content(f"Waited {ms}ms")}

        elif name == "list_towers":
            return {"content": _text_content(_format_tower_list())}

        else:
            return {"content": _error_content(f"Unknown tool: {name}"), "isError": True}

    except Exception as e:
        return {"content": _error_content(str(e)), "isError": True}


def _respond(id: Any, result: dict) -> None:
    """Write a JSON-RPC response to stdout."""
    msg = {"jsonrpc": "2.0", "id": id, "result": result}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _error_response(id: Any, code: int, message: str) -> None:
    msg = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def run_server(env: BloonsWebEnv) -> None:
    """Read JSON-RPC requests from stdin, dispatch, respond on stdout.

    Blocks until stdin closes (or KeyboardInterrupt).
    """
    # Log to stderr so it doesn't interfere with JSON-RPC on stdout
    sys.stderr.write("MCP server ready — reading from stdin\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Bad JSON: {e}\n")
            sys.stderr.flush()
            continue

        req_id = req.get("id")
        method = req.get("method", "")

        # Notifications (no id) — just acknowledge
        if req_id is None:
            continue

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            })

        elif method == "tools/list":
            _respond(req_id, {"tools": TOOL_DEFS})

        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            result = handle_tool_call(env, tool_name, tool_args)
            _respond(req_id, result)

        else:
            _error_response(req_id, -32601, f"Method not found: {method}")

    sys.stderr.write("MCP server shutting down (stdin closed)\n")
    sys.stderr.flush()
