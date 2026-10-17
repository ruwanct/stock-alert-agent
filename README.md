# 🤖 Stock Alert Telegram Bot (GitHub Actions — 100% Free)

![Tests](https://github.com/ruwanct/predictor/actions/workflows/tests.yml/badge.svg)

AI-powered stock alerts delivered to your Telegram and Discord, running entirely on GitHub Actions for **$0/month** (except ~$1 for Claude API). v6 adds **memory across runs** built with LangChain + LangGraph.

## How It Works

```
GitHub Actions (cron: daily Mon-Fri after the US close)
    │
    ├── Finnhub API → stock prices + news (free)
    │
    ├── LangGraph + Claude → analysis with memory of past calls (~$1/mo)
    │
    ├── MongoDB Atlas (or memory/ JSON) → signal track record + digests, weekly JSON backup
    │
    ├── Telegram Bot → sends alert to your chat (free)
    │
    └── Discord Webhook → sends alert to your server (free, optional)
```

No servers. No hosting. No sign-ups beyond what you already have.

---

## Setup (10 minutes)

### Step 1: Get your API keys (5 min)

| What | Where | Cost |
|------|-------|------|
| Finnhub API key | [finnhub.io/register](https://finnhub.io/register) | Free |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) | ~$1/mo |
| Telegram Bot token | Message [@BotFather](https://t.me/BotFather) → send `/newbot` | Free |
| Telegram Chat ID | Message [@userinfobot](https://t.me/userinfobot) → send `/start` | Free |
| Discord Webhook URL | Server Settings → Integrations → Webhooks → New Webhook | Free |

⚠️ **Important:** After creating your Telegram bot, open it and send any message (e.g., "hi"). Bots can't message you until you message them first!

### Step 2: Create a GitHub repo (2 min)

1. Go to [github.com/new](https://github.com/new)
2. Name it `stock-alert-bot` (or anything you like)
3. Make it **Private** (your secrets are in settings, but still good practice)
4. Click **Create repository**

### Step 3: Upload the files (2 min)

Upload these files to your repo keeping the folder structure:

```
your-repo/
├── .github/
│   └── workflows/
│       └── stock-alert.yml    ← GitHub Actions workflow
├── stock_alert_bot.py         ← Entry point
├── requirements.txt           ← Pinned dependencies
├── bot/                       ← LangChain + LangGraph package
├── memory/                    ← JSON memory (local/dev backend, weekly backup from MongoDB)
├── scripts/                   ← export_memory.py, migrate_json_to_mongo.py
├── tests/                     ← Unit tests
└── README.md                  ← This file
```

**Easiest way:** fork or clone this repo, or push it with `git push`. The bot is now a multi-file package, so uploading files one by one through the web UI is error-prone.

### Step 4: Add your secrets (2 min)

1. In your repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **"New repository secret"** and add each one:

| Secret Name | Value |
|------------|-------|
| `FINNHUB_API_KEY` | Your Finnhub API key |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (starts with `sk-ant-`) |
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your numeric chat ID |
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL (optional) |
| `MONGODB_URI` | MongoDB Atlas connection string (optional, see [MongoDB Atlas backend](#mongodb-atlas-backend)) |
| `LANGSMITH_API_KEY` | LangSmith tracing key (optional) |

### Step 5: Test it! (1 min)

1. Go to **Actions** tab in your repo
2. Click **"Stock Alert Bot"** in the left sidebar
3. Click **"Run workflow"** → **"Run workflow"**
4. Wait ~30 seconds
5. Check your Telegram — you should see an alert! 🎉

---

## Schedule

The bot runs automatically once a day, Monday through Friday, after the US market closes:

| Berlin Time | UTC | Alert |
|------------|-----|-------|
| 23:30 (summer) / 22:30 (winter) | 21:30 | Daily close summary |

Running after the close means every quote is a closing price, so signals are opened and scored on the same basis.

*Note: GitHub Actions cron can have delays of 5-15 minutes. This is normal.*

---

## Customizing

### Change your watchlist
Edit `bot/config.py`:
```python
CORE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
```

### Change alert schedule
Edit `.github/workflows/stock-alert.yml`:
```yaml
schedule:
  - cron: '30 21 * * 1-5'  # Change this time (UTC); add lines for more runs
```

If you add more cron lines, also set the repository variable `ALERTS_PER_DAY` (Settings → Secrets and variables → Actions → **Variables**) to the number of runs per day. GitHub can't read cron times from a variable, so the schedule itself stays in the YAML; `ALERTS_PER_DAY` makes the bot keep the right amount of digest history (`max(7, 5 × runs)`) and show the right footer. Claude cost scales with the number of runs.

### Add more stocks
Add symbols to `CORE_WATCHLIST` or the `SECTORS` dict in `bot/config.py`. Finnhub supports all major US stocks.

---

## 🧠 Memory & LangChain architecture

v6 remembers what it said. Each run scores its earlier signals against live prices and feeds the track record, recent digests and per-ticker notes back into Claude, so alerts can say things like *"NVDA BUY @ $182 Mon → +3.8% ✅"* or *"defense rally, day 4"*.

The pipeline is a LangGraph `StateGraph`:

```
 START
   │
   ▼
 load_memory ──► gather_data ──► score_past_signals ──► analyze ◄─────────┐
 (JSON/MongoDB)  (watchlist,      (live price for open    (ChatAnthropic   │ errors and
                 prices, news)    BUY/SELL, close >5d)    structured out) │ retries < 1
                                                              │           │
                                                              ▼           │
                                                          validate ───────┘
                                                              │ ok / out of retries
                                                              ▼
                                                        update_memory ──► deliver ──► END
                                                        (ledger, digest,   (Telegram text +
                                                         ticker store,      Discord embed)
                                                         save to backend)
```

Mermaid, generated with `build_graph().get_graph().draw_mermaid()`:

```mermaid
graph TD;
	__start__([__start__])
	__start__ --> load_memory;
	load_memory --> gather_data;
	gather_data --> score_past_signals;
	score_past_signals --> analyze;
	analyze --> validate;
	validate -.->|errors, retry once| analyze;
	validate -.->|ok| update_memory;
	update_memory --> deliver;
	deliver --> __end__([__end__]);
```

### Memory files (`memory/`, JSON backend)

With no `MONGODB_URI` the bot keeps memory in JSON files committed back to `main` by the workflow after every run. With MongoDB these files are only a weekly backup, with older history in `memory/archive/` (see below).

| File | Contents |
|------|----------|
| `signals.json` | Signal ledger: every BUY/SELL/WATCH with entry price, status, reaffirm count, return and win/loss/flat outcome. Same open ticker+action is reaffirmed, not duplicated; a BUY/SELL reversal closes the old call. Signals close after 5 calendar days. |
| `digests.json` | Last 7 alert digests (more if `ALERTS_PER_DAY` > 1) plus up to 8 weekly summaries (older digests are compressed by an LLM chain, with a no-LLM fallback). |
| `tickers.json` | Per-ticker notes (signal history, mention count, last note) held in a LangGraph `InMemoryStore` while running. |

Missing files start empty. Invalid JSON logs a warning and starts empty. Memory never crashes a run.

### MongoDB Atlas backend

Production memory lives in a free MongoDB Atlas M0 cluster, database `stock_alert_bot`. The pipeline and the pure memory logic are unchanged; `bot/storage.py` provides the backend (`JsonBackend` or `MongoBackend`) that the graph nodes load from and save to.

| Collection | One document per | Notes |
|------------|------------------|-------|
| `signals` | signal | Same fields as the JSON ledger. Unique index on `id`, index on (`ticker`, `status`). Saved with a bulk upsert by `id`. |
| `digests` | bot run | Unique index on `at`. Digests are never deleted: when weekly compression folds one into a summary it gets `compressed: true` and no longer appears in the "recent" list sent to Claude. |
| `weekly_digests` | ISO week | Unique index on `week`, upserted. |
| `tickers` | ticker | The official LangGraph store (`langgraph-store-mongodb`, `MongoDBStore`), namespace `("tickers",)`. |

Settings (environment variables):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MONGODB_URI` | unset | Atlas connection string (`mongodb+srv://...`). In GitHub it is the `MONGODB_URI` secret. |
| `MONGODB_DB` | `stock_alert_bot` | Database name. |
| `MEMORY_BACKEND` | `auto` | `auto` uses MongoDB if `MONGODB_URI` is set, otherwise JSON. `json` or `mongo` force one. |

**Fail-safe.** MongoDB problems never stop the alert. If the connection, ping or load fails, the run logs a loud warning, reads the JSON snapshot in `memory/` (read-only) and skips every memory write, so JSON and MongoDB cannot diverge. If a single save fails, it logs a warning and the alert is still delivered. `DRY_RUN=1` never writes to MongoDB or JSON. Ticker notes are read into an in-memory working copy at the start of the run, so no step of the analysis depends on the network after loading.

**Weekly JSON backup.** When `MONGODB_URI` is set, the workflow only exports and commits on Fridays (UTC): `scripts/export_memory.py` writes the files below from MongoDB and commits `memory/` (including `memory/archive/`) as `chore(memory): weekly backup from MongoDB [skip ci]`. Without the secret the workflow keeps committing after every run.

```
memory/
├── signals.json            open signals + closed ones from the last BACKUP_RECENT_DAYS (90) days
├── digests.json            recent + weekly digests, the format the bot loads
├── tickers.json            per-ticker notes
└── archive/
    ├── signals-YYYY.json   older closed signals, by year of closed_at
    ├── digests-YYYY.json   every digest incl. compressed ones (with the `compressed` flag), by year
    └── weekly-YYYY.json    every weekly summary, by the year of its ISO week
```

The backup is bounded and complete: the three main files stay small however long the bot runs, while the archive holds the full history. Past-year archive files are written deterministically and only when their content changed, so once a year is over its file never shows up in a git diff. Archive years that have no data in MongoDB are never deleted. `BACKUP_RECENT_DAYS` lives in `bot/config.py`.

When MongoDB is unreachable the alert still goes out, with a visible line (`⚠️ Memory offline: using last backup, nothing saved this run`) below the header in Telegram and a `⚠️ Memory` field in Discord. If some saves fail during a run, it says `⚠️ Memory partially saved: <parts> failed`. Choosing JSON on purpose (no `MONGODB_URI`) or `DRY_RUN` shows no warning; error details stay in the logs.

The JSON fallback (MongoDB unreachable) reads only the three main files, never the archive. The track-record prompt uses the open signals plus the last 30 closed ones; signals close after 5 days, so 30 closed signals are usually a few weeks of calls and 90 days of closed signals is more than enough.

**Full restore** from the backup (main files and archive, safe to run twice): `python scripts/migrate_json_to_mongo.py`. If a signal is in both `signals.json` and an archive file, `signals.json` wins; digests keep the `compressed` flag from the archive.

**One-time migration** of the existing JSON memory (safe to run twice, only reads the JSON files):

```bash
pip install -r requirements.txt
export MONGODB_URI='mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/'
python scripts/migrate_json_to_mongo.py --dry-run   # counts only (incl. archive), no connection
python scripts/migrate_json_to_mongo.py             # upsert into MongoDB
```

**Viewing data:** Atlas → your cluster → **Browse Collections** (Data Explorer) → database `stock_alert_bot`.

**Atlas setup (5 minutes):**
1. Create a free **M0** cluster at [mongodb.com/atlas](https://www.mongodb.com/atlas).
2. **Database Access** → add a database user with a password (read and write to any database is fine).
3. **Network Access** → add IP `0.0.0.0/0`. GitHub runners have no fixed IP, so this is required; the database user's password is the protection.
4. **Connect** → Drivers → copy the `mongodb+srv://...` string, put in your password, and save it as the `MONGODB_URI` repository secret. Paste it without quotes.

### Where LangChain / LangGraph features are used

| Feature | Where |
|---------|-------|
| `ChatAnthropic` | `bot/chains.py` `get_llm()` (model `claude-sonnet-4-6`, retries and timeout built in) |
| `ChatPromptTemplate` | `bot/chains.py` analysis, digest and weekly prompts |
| `with_structured_output(MarketAlert)` | `bot/chains.py` analysis chain, Pydantic models in `bot/schemas.py` |
| LCEL `prompt \| llm \| StrOutputParser()` | `bot/chains.py` weekly summary chain |
| `StateGraph`, conditional edges | `bot/graph.py` (validate then retry loop) |
| `InMemoryStore` | `bot/memory.py` per-ticker notes (working copy during a run; the JSON backend's store) |
| `MongoDBStore` (`langgraph-store-mongodb`) | `bot/storage.py` persistent per-ticker notes in MongoDB Atlas |
| LangSmith tracing | automatic via env vars, see below |

### Validation

Every signal ticker must exist in the fetched quotes and BUY/SELL entry prices must be within 1.5% of the live quote. Failures trigger one retry with the problems fed back to Claude. After that, invalid signals are dropped and mover prices are overwritten with the real quotes. If the Claude call fails completely, a plain fallback message with prices is delivered.

### Dry run

```bash
export FINNHUB_API_KEY=... ANTHROPIC_API_KEY=...
DRY_RUN=1 python stock_alert_bot.py
```

Runs everything including Claude, prints the Telegram text and Discord embed JSON, sends nothing and writes no memory (neither JSON nor MongoDB). No delivery channel is needed in dry-run mode.

### Optional: LangSmith tracing

Add a `LANGSMITH_API_KEY` repository secret. The workflow then sets `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT=stock-alert-bot`, and every chain and graph node shows up in LangSmith. The workflow also sets `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` because this project's LangSmith account is in the EU region; for a US account, remove that line (uploads to the wrong region fail with 403 Forbidden). Locally:

```bash
export LANGSMITH_API_KEY=... LANGSMITH_TRACING=true LANGSMITH_PROJECT=stock-alert-bot
```

### Tests

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The same tests run in CI on every push and pull request (`.github/workflows/tests.yml`; pushes that only touch `memory/**` are skipped). No network or API key needed. MongoDB tests use `mongomock` (from `requirements-dev.txt`) and are skipped if it is not installed.

---

## Costs

| Service | Monthly Cost |
|---------|-------------|
| GitHub Actions | **$0** (uses ~15 of 2,000 free minutes) |
| Finnhub API | **$0** (free tier: 60 calls/min) |
| Telegram Bot | **$0** |
| Claude API (Sonnet) | **~⅓ of the old 3×/day cost** (~22 runs × 2 calls: analysis + digest) |
| **Total** | **Claude API only; scales with `ALERTS_PER_DAY`** |

---

## Troubleshooting

**No Telegram message received?**
- Did you message your bot first? (Send it "hi")
- Check your TELEGRAM_CHAT_ID — it should be numeric
- Go to Actions tab → click the failed run → read the logs

**Workflow not running on schedule?**
- GitHub disables scheduled workflows on inactive repos after 60 days
- Fix: Make any commit (even editing README) to re-activate
- Or manually trigger it from the Actions tab

**No Discord message received?**
- Make sure `DISCORD_WEBHOOK_URL` is set in repository secrets
- Verify the webhook URL is from the correct channel (Server Settings → Integrations → Webhooks)
- Long alerts are automatically split into multiple messages — this is normal

**Claude API error?**
- Check your API key starts with `sk-ant-`
- Verify you have billing set up at console.anthropic.com

---

## Example Alert

```
🤖 Stock Alert Bot
⏰ 2026-03-01 15:00 UTC
────────────────────

📊 Market Overview
🟢 NVDA: $142.50 (+3.2%) | Strong momentum
🟢 AAPL: $238.10 (+0.8%) | Steady gains
🔴 TSLA: $312.40 (-2.1%) | Selling pressure
🟡 MSFT: $445.20 (+0.1%) | Flat, wait for earnings

📰 Key Headlines
• NVIDIA announces new AI chip partnership with major cloud provider
• Fed signals possible rate hold in April meeting

🟢 BUY: NVDA — Positive sentiment across 3 sources,
   AI infrastructure spending accelerating
🔴 SELL: TSLA — EU regulatory headwinds,
   breaking below key support level
🟡 WATCH: MSFT — Earnings next week, hold position
```
