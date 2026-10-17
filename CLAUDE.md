# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A serverless stock alert bot that runs on GitHub Actions (3x daily, Mon-Fri). It fetches stock prices and news, sends them to Claude AI for analysis, then delivers buy/sell signals to Telegram and Discord. v6 adds persistent memory (signal track record, digests, ticker notes) built with LangChain + LangGraph.

**Cost:** ~$1/month (Claude API only). Everything else is free tier.

## Running Locally

```bash
pip install -r requirements.txt
```

Set environment variables, then run:

```bash
export FINNHUB_API_KEY=...
export ANTHROPIC_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export DISCORD_WEBHOOK_URL=...   # optional

python stock_alert_bot.py
DRY_RUN=1 python stock_alert_bot.py   # run incl. Claude, print output, send nothing, write no memory
python -m unittest discover -s tests -v   # no network/API needed
```

To trigger via GitHub Actions without waiting for the cron schedule: go to the repo's **Actions** tab -> **Stock Alert Bot** -> **Run workflow**.

## Architecture (v6: LangChain + LangGraph package)

`stock_alert_bot.py` is a thin entry point (validates env, invokes the graph). The logic lives in `bot/`:

- `config.py` — env vars, `CORE_WATCHLIST`, `SECTORS`, thresholds, `MODEL`, memory paths, retention constants
- `data.py` — Finnhub quotes/news, Google News RSS, `build_watchlist()`, `aggregate_news()`. `QUOTE_CACHE` holds every quote fetched (incl. the ~55-stock mover scan) so nothing is fetched twice.
- `schemas.py` — Pydantic models (`MarketAlert`, `Signal`, `Mover`, ...) used for structured output
- `chains.py` — `ChatAnthropic` (`claude-sonnet-4-6`), analysis/digest/weekly chains
- `memory.py` — JSON memory (signal ledger + scoring, digests, per-ticker `InMemoryStore`)
- `graph.py` — LangGraph `StateGraph`: `load_memory` -> `gather_data` -> `score_past_signals` -> `analyze` -> `validate` (retry once to `analyze`) -> `update_memory` -> `deliver`
- `formatters.py` — Telegram text and Discord embed from a `MarketAlert`
- `delivery.py` — `send_telegram()` / `send_discord()`

Memory lives in `memory/*.json` and is committed back to `main` by the workflow (`if: always()` step, `[skip ci]`). Missing/corrupt files must never crash a run (log + start empty). With `DRY_RUN=1` no memory is written. Optional LangSmith tracing via `LANGSMITH_API_KEY` secret.

## File Versions

- `stock_alert_bot.py` + `bot/` — **active (v6)**, run by the workflow.
- Older single-file versions (v2–v5) were removed; use git history (e.g. `git show cf7e076:stock_alert_bot.py` for v5).

## Key Configuration

All customization is in `bot/config.py`:

- `CORE_WATCHLIST`, `SECTORS` — stock universe
- `BIG_MOVER_THRESHOLD` (2.0), `MAX_DYNAMIC_STOCKS` (10), watchlist capped at 30
- `SIGNAL_HORIZON_DAYS` (5), `WIN_THRESHOLD_PCT` (0.5), `MAX_RECENT_DIGESTS` (15), `MAX_WEEKLY_DIGESTS` (8)
- `PRICE_TOLERANCE_PCT` (1.5) — max distance between a signal's entry price and the live quote

## Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `FINNHUB_API_KEY` | Stock prices and news |
| `ANTHROPIC_API_KEY` | Claude analysis |
| `TELEGRAM_BOT_TOKEN` | Telegram delivery |
| `TELEGRAM_CHAT_ID` | Telegram recipient |
| `DISCORD_WEBHOOK_URL` | Discord delivery (optional) |
| `LANGSMITH_API_KEY` | LangSmith tracing (optional) |

## GitHub Actions Notes

- Scheduled workflows get disabled by GitHub after 60 days of repo inactivity. Re-activate by making any commit.
- Cron times are UTC. Berlin = UTC+1 (winter) / UTC+2 (summer). Current schedule: 06:00, 10:00, 15:00 UTC.
- The job has an 8-minute timeout, pip is cached, and runs are serialized via `concurrency` (memory commits). Finnhub rate limit is 60 calls/min; the script uses `time.sleep(0.12)` between calls to stay safe.
