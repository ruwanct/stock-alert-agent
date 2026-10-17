# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A serverless stock alert bot that runs on GitHub Actions (3x daily, Mon-Fri). It fetches stock prices and news, sends them to Claude AI for analysis, then delivers buy/sell signals to Telegram and Discord.

**Cost:** ~$0.50/month (Claude API only). Everything else is free tier.

## Running Locally

The only dependency is `requests`:

```bash
pip install requests
```

Set environment variables, then run the active bot script:

```bash
export FINNHUB_API_KEY=...
export ANTHROPIC_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export DISCORD_WEBHOOK_URL=...   # optional

python stock_alert_bot.py
```

To trigger via GitHub Actions without waiting for the cron schedule: go to the repo's **Actions** tab -> **Stock Alert Bot** -> **Run workflow**.

## Architecture

The pipeline in `stock_alert_bot.py` runs top-to-bottom, once per execution:

1. **`build_watchlist()`** — assembles up to 30 tickers from:
   - `CORE_WATCHLIST` (5 always-on stocks)
   - Top 2 from each sector in `SECTORS` dict
   - News-mentioned tickers from Finnhub general news
   - Big movers (>`BIG_MOVER_THRESHOLD`% change) scanned across all sector stocks

2. **`fetch_quote(symbol)`** — Finnhub `/quote` endpoint per ticker

3. **`aggregate_news()`** — combines three sources, deduplicates by headline prefix:
   - `fetch_finnhub_news()`: general + technology categories
   - `fetch_finnhub_company_news()`: per-ticker news for core 5
   - `fetch_google_news()`: RSS search for ~6 finance queries

4. **`analyze_with_claude()`** — sends prices + news to `claude-sonnet-4-6` with a strict output format template. Returns the formatted alert text.

5. **`send_telegram()` / `send_discord()`** — posts the alert. Discord splits messages >1900 chars into multiple embeds.

## File Versions

- `stock_alert_bot.py` — **active script** (v5), used by the GitHub Actions workflow. Sends to both Telegram and Discord.
- `stock_alert_botv2.py` through `stock_alert_botv5.py` — historical versions kept for reference. `v5` content matches the active `stock_alert_bot.py`.

The workflow file `.github/workflows/stock-alert.yml` always runs `stock_alert_bot.py`.

## Key Configuration

All customization is at the top of `stock_alert_bot.py`:

- `CORE_WATCHLIST` — always-tracked tickers
- `SECTORS` dict — sector universe for scanning and sector picks
- `BIG_MOVER_THRESHOLD` — minimum % change to auto-add a stock (default: 2.0)
- `MAX_DYNAMIC_STOCKS` — max tickers added from news/movers (default: 10)
- Watchlist is capped at 30 total stocks

## Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `FINNHUB_API_KEY` | Stock prices and news |
| `ANTHROPIC_API_KEY` | Claude analysis |
| `TELEGRAM_BOT_TOKEN` | Telegram delivery |
| `TELEGRAM_CHAT_ID` | Telegram recipient |
| `DISCORD_WEBHOOK_URL` | Discord delivery (optional) |

## GitHub Actions Notes

- Scheduled workflows get disabled by GitHub after 60 days of repo inactivity. Re-activate by making any commit.
- Cron times are UTC. Berlin = UTC+1 (winter) / UTC+2 (summer). Current schedule: 06:00, 10:00, 15:00 UTC.
- The job has a 5-minute timeout. Finnhub rate limit is 60 calls/min; the script uses `time.sleep(0.12)` between calls to stay safe.
