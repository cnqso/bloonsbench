# BloonsBench

A benchmark for evaluating LLM agents by having them play **Bloons Tower Defense 5**.

The agent sees screenshots of the game, reads cash/lives/round via OCR, and uses tools to place towers, upgrade them, and start rounds. The score is simply: how many rounds can it survive?

Everything runs locally — [Ruffle](https://ruffle.rs/) emulates Flash, [Playwright](https://playwright.dev/python/) drives Chromium, and the agent talks to [OpenRouter](https://openrouter.ai/) for LLM inference.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Place your BTD5 SWF at `game/btd5.swf` (not distributed).

Set your API key in `.env`:
```
OPENROUTER_API_KEY=sk-or-...
```

## Run

```bash
# Run an autonomous LLM agent
python scripts/run_agent.py --model openai/gpt-4o

# With a specific map and difficulty
python scripts/run_agent.py --model anthropic/claude-sonnet-4 --map monkey_lane --difficulty easy

# Stop after N rounds
python scripts/run_agent.py --model openai/gpt-4o --max-rounds 50

# Inject pre-made saves (to skip tower unlocking)
python scripts/run_agent.py --model openai/gpt-4o --saves saves/unlocks_maxed.json
```

### Interactive CLI

Play manually or test the harness:

```bash
python scripts/run_mcp.py --cli
```

### MCP server

For external agents that speak [MCP](https://modelcontextprotocol.io/) (JSON-RPC over stdio):

```bash
python scripts/run_mcp.py
```

## Agent tools

| Tool | Description |
|------|-------------|
| `observe` | Screenshot the current game state |
| `place_tower` | Place a tower at (x, y) on the map |
| `upgrade_tower` | Upgrade a tower along path 1 or 2 |
| `sell_tower` | Sell a tower for cash back |
| `set_target` | Set targeting: first / last / close / strong |
| `start_round` | Start the next round (fast-forward, 7s wait) |
| `status` | Show placed towers, cash, lives, round |
| `list_towers` | List all towers with costs and upgrade paths |
| `click` | Raw click at (x, y) — escape hatch for stuck UI |
| `send_key` | Press a key (e.g. Escape to cancel placement) |
| `wait` | Wait N milliseconds |

## Architecture

```
harness/
  env/         Game environment, config, menu navigation, save injection
  runtime/     Local HTTP server, Ruffle vendor, wrapper HTML
  perception/  OCR for reading cash/lives/round from screenshots
  trace/       JSONL action logging
scripts/
  run_agent.py   Autonomous LLM agent (OpenRouter)
  run_mcp.py     MCP server or interactive CLI
game/            Place btd5.swf here (not distributed)
saves/           Pre-made save files for tower unlocks
```

## How it works

1. A local HTTP server (port 8890) serves the Ruffle wrapper + SWF
2. Playwright launches Chromium, navigates to the game, and auto-clicks through menus to round 1
3. The agent loop: screenshot → OCR for game state → LLM decides actions → execute tools → repeat
4. Between rounds, the agent places/upgrades towers; then calls `start_round` to begin
5. OCR polls for the GO button to detect round completion, and for GAME OVER to detect loss
6. Context distillation kicks in when the conversation gets long, summarizing history to stay within token limits

## Save injection

BTD5 locks most towers behind progression. To benchmark with all towers available, inject a save file:

```bash
python scripts/run_agent.py --model openai/gpt-4o --saves saves/unlocks_maxed.json
```

Saves are base64-encoded SOL files written to `localStorage` before the Flash VM starts (via deferred Ruffle loading).
