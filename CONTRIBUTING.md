# Contributing to BloonsBench

## Submitting Results

The main way to contribute is by running an agent and submitting your results.

1. Run an agent against any model available on OpenRouter:
   ```bash
   python scripts/run_agent.py --model <your-model>
   ```
2. A submission file is auto-generated in `results/submissions/` when the run finishes.
3. Fork this repo, commit your submission file, and open a PR.

## Updating the Leaderboard

After adding new submissions, regenerate the leaderboard table in the README:

```bash
python scripts/generate_leaderboard.py
```

This reads all files in `results/submissions/` and updates the leaderboard block in `README.md`.

## Code Contributions

I am a chill guy and will merge pretty much anything as long as it doesn't break benchmark continuity.

## Development Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Place your BTD5 SWF at `game/btd5.swf` and set `OPENROUTER_API_KEY` in `.env`.
