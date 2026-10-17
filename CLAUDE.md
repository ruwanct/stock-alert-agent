# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A serverless stock alert bot that runs on GitHub Actions (once daily after the US close, Mon-Fri). It fetches stock prices and news, sends them to Claude AI for analysis, then delivers buy/sell signals to Telegram and Discord. v6 adds persistent memory (signal track record, digests, ticker notes) built with LangChain + LangGraph.

**Cost:** Claude API only (2 calls per run); everything else is free tier.

## Running Locally

```bash
pip install -r requirements-dev.txt   # runtime deps + mongomock for tests
```

Set environment variables, then run:

```bash
export FINNHUB_API_KEY=...
export ANTHROPIC_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export DISCORD_WEBHOOK_URL=...   # optional
export MONGODB_URI=...           # optional; unset = JSON files in memory/

python stock_alert_bot.py
DRY_RUN=1 python stock_alert_bot.py   # run incl. Claude, print output, send nothing, write no memory (JSON or Mongo)
python -m unittest discover -s tests -v   # no network/API needed
```

To trigger via GitHub Actions without waiting for the cron schedule: go to the repo's **Actions** tab -> **Stock Alert Bot** -> **Run workflow**.

## Architecture (v6: LangChain + LangGraph package)

`stock_alert_bot.py` is a thin entry point (validates env, invokes the graph). The logic lives in `bot/`:

- `config.py` — env vars, `CORE_WATCHLIST`, `SECTORS`, thresholds, `MODEL`, memory paths, backend selection (`MONGODB_URI`, `MONGODB_DB`, `MEMORY_BACKEND`), retention constants
- `data.py` — Finnhub quotes/news, Google News RSS, `build_watchlist()`, `aggregate_news()`. `QUOTE_CACHE` holds every quote fetched (incl. the ~55-stock mover scan) so nothing is fetched twice.
- `schemas.py` — Pydantic models (`MarketAlert`, `Signal`, `Mover`, ...) used for structured output
- `chains.py` — `ChatAnthropic` (`claude-sonnet-4-6`), analysis/digest/weekly chains
- `memory.py` — pure memory logic + JSON helpers (signal ledger + scoring, digests, per-ticker `InMemoryStore`)
- `storage.py` — backend interface (`JsonBackend`, `MongoBackend`) and fail-safe `open_memory()`; ticker notes persist via the official `MongoDBStore` (`langgraph-store-mongodb`)
- `scripts/export_memory.py` (Mongo -> `memory/*.json` backup) and `scripts/migrate_json_to_mongo.py` (one-time, idempotent, `--dry-run`)
- `graph.py` — LangGraph `StateGraph`: `load_memory` -> `gather_data` -> `score_past_signals` -> `analyze` -> `validate` (retry once to `analyze`) -> `update_memory` -> `deliver`
- `formatters.py` — Telegram text and Discord embed from a `MarketAlert`
- `delivery.py` — `send_telegram()` / `send_discord()`

Memory backend: `MEMORY_BACKEND=auto` uses MongoDB Atlas (database `stock_alert_bot`; collections `signals`, `digests`, `weekly_digests`, `tickers`) when `MONGODB_URI` is set, else JSON in `memory/*.json`. Digests are never deleted in Mongo (`compressed: true` flag instead). Fail-safe: if Mongo connect/load fails, the run reads the JSON snapshot read-only and skips all memory writes; failed saves only warn; the alert is always delivered. With Mongo the workflow commits a JSON backup only on Fridays (`chore(memory): weekly backup from MongoDB [skip ci]`); with JSON it commits after every run (`if: always()`, `[skip ci]`). Missing/corrupt JSON files must never crash a run (log + start empty). With `DRY_RUN=1` no memory is written. Optional LangSmith tracing via `LANGSMITH_API_KEY` secret.

## File Versions

- `stock_alert_bot.py` + `bot/` — **active (v6)**, run by the workflow.
- Older single-file versions (v2–v5) were removed; use git history (e.g. `git show cf7e076:stock_alert_bot.py` for v5).

## Key Configuration

All customization is in `bot/config.py`:

- `CORE_WATCHLIST`, `SECTORS` — stock universe
- `BIG_MOVER_THRESHOLD` (2.0), `MAX_DYNAMIC_STOCKS` (10), watchlist capped at 30
- `SIGNAL_HORIZON_DAYS` (5), `WIN_THRESHOLD_PCT` (0.5), `MAX_RECENT_DIGESTS` (`max(7, 5 × ALERTS_PER_DAY)`), `MAX_WEEKLY_DIGESTS` (8)
- `PRICE_TOLERANCE_PCT` (1.5) — max distance between a signal's entry price and the live quote

## Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `FINNHUB_API_KEY` | Stock prices and news |
| `ANTHROPIC_API_KEY` | Claude analysis |
| `TELEGRAM_BOT_TOKEN` | Telegram delivery |
| `TELEGRAM_CHAT_ID` | Telegram recipient |
| `DISCORD_WEBHOOK_URL` | Discord delivery (optional) |
| `MONGODB_URI` | MongoDB Atlas memory backend (optional; unset = JSON files) |
| `LANGSMITH_API_KEY` | LangSmith tracing (optional) |

## GitHub Actions Notes

- Scheduled workflows get disabled by GitHub after 60 days of repo inactivity. Re-activate by making any commit.
- Cron times are UTC. Berlin = UTC+1 (winter) / UTC+2 (summer). Current schedule: 21:30 UTC (after the US close).
- `ALERTS_PER_DAY` (repo variable, default 1) must match the number of cron lines; it tunes digest retention and the footer. GitHub can't read cron times from a variable.
- The job has an 8-minute timeout, pip is cached, and runs are serialized via `concurrency` (memory commits). Finnhub rate limit is 60 calls/min; the script uses `time.sleep(0.12)` between calls to stay safe.
