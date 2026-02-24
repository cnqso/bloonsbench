# BloonsBench

A benchmark for evaluating LLM agents by having them play **Bloons Tower Defense 5**.

The agent sees screenshots of the game, reads cash/lives/round via OCR, and uses tools to place towers, upgrade them, and start rounds. Better models are expected to survive more rounds than worse models.

Everything runs locally — [Ruffle](https://ruffle.rs/) emulates Flash, [Playwright](https://playwright.dev/python/) drives Chromium, and the agent talks to [OpenRouter](https://openrouter.ai/) for LLM inference.

<!-- LEADERBOARD:START -->
## Leaderboard

| Model | Runs | Best Round | Avg Round | Avg Towers | Avg Tokens |
|-------|------|-----------|-----------|------------|------------|
| openai/gpt-5-mini | 3 | 65 | 49.3 | 4 | 2.8M |
| anthropic/claude-sonnet-4.6 | 1 | 59 | 59.0 | 13 | 2.5M |
| openai/gpt-5-nano | 2 | 40 | 25.5 | 3 | 541K |

### Best Runs

**openai/gpt-5-mini — Round 65**
No tower data available.

**anthropic/claude-sonnet-4.6 — Round 59**
Towers: #1 dart_monkey (420.0,145.0) [2/3], #2 dart_monkey (585.0,145.0) [2/3], #3 dart_monkey (150.0,320.0) [2/3], #4 dart_monkey (715.0,240.0) [2/3], #5 dart_monkey (280.0,350.0) [2/3], #6 bomb_tower (500.0,430.0) [3/2], #7 ninja_monkey (350.0,145.0) [4/2], #8 tack_shooter (260.0,235.0) [4/2], #9 bomb_tower (480.0,235.0) [2/4], #10 sniper_monkey (400.0,350.0) [2/2], #11 ice_tower (470.0,145.0) [2/4], #12 ninja_monkey (530.0,145.0) [4/2], #13 super_monkey (100.0,430.0) [2/2]

**openai/gpt-5-nano — Round 40**
Towers: #1 dart_monkey (150.0,320.0) [2/3], #3 dart_monkey (210.0,235.0) [2/3], #4 spike_factory (260.0,150.0) [3/2], #5 dart_monkey (280.0,350.0) [3/2], #6 dart_monkey (430.0,260.0) [4/2]

### Submit Your Results

1. Run an agent: `python scripts/run_agent.py --model <your-model>`
2. A submission file is auto-generated in `results/submissions/`
3. Fork the repo, commit your submission file, and open a PR

<!-- LEADERBOARD:END -->

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Place your BTD5 SWF at `game/btd5.swf` (not distributed, but easily available with a google search).

Set your OpenRouter API key in `.env`:
```
OPENROUTER_API_KEY=sk-or-...
```

## Run

```bash
python scripts/run_agent.py --model openai/gpt-5-nano

# Or, inject your own save file
python scripts/run_agent.py --model openai/gpt-5-nano --saves saves/unlocks_maxed.json
```

Saves are base64-encoded SOL files written to `localStorage` before the Flash VM starts (via deferred Ruffle loading).

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
5. Our harness remembers tower positions and gives upgrade path info to the LLM to bypass computer usage limitations and avoid desyncs.
6. OCR polls for the GO button to detect round completion, and for GAME OVER to detect loss
7. Context distillation kicks in when the conversation gets long, summarizing history to stay within token limits

## Save injection

BTD5 locks most towers behind progression. To benchmark with all towers available, inject a save file:

```bash
python scripts/run_agent.py --model openai/gpt-4o --saves saves/unlocks_maxed.json
```


