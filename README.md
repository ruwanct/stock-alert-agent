# 🤖 Stock Alert Telegram Bot (GitHub Actions — 100% Free)

AI-powered stock alerts delivered to your Telegram, running entirely on GitHub Actions for **$0/month** (except ~$0.50 for Claude API).

## How It Works

```
GitHub Actions (cron: 3x daily Mon-Fri)
    │
    ├── Finnhub API → stock prices + news (free)
    │
    ├── Claude AI → sentiment analysis + buy/sell signals (~$0.50/mo)
    │
    └── Telegram Bot → sends alert to your chat (free)
```

No servers. No hosting. No sign-ups beyond what you already have.

---

## Setup (10 minutes)

### Step 1: Get your API keys (5 min)

| What | Where | Cost |
|------|-------|------|
| Finnhub API key | [finnhub.io/register](https://finnhub.io/register) | Free |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) | ~$0.50/mo |
| Telegram Bot token | Message [@BotFather](https://t.me/BotFather) → send `/newbot` | Free |
| Telegram Chat ID | Message [@userinfobot](https://t.me/userinfobot) → send `/start` | Free |

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
├── stock_alert_bot.py         ← The bot script
└── README.md                  ← This file
```

**Easiest way:** Use GitHub's web UI:
1. Click **"Add file"** → **"Upload files"** for `stock_alert_bot.py` and `README.md`
2. Click **"Create new file"**, type `.github/workflows/stock-alert.yml` as the filename
3. Paste the contents of `stock-alert.yml` into the editor

### Step 4: Add your secrets (2 min)

1. In your repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **"New repository secret"** and add each one:

| Secret Name | Value |
|------------|-------|
| `FINNHUB_API_KEY` | Your Finnhub API key |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (starts with `sk-ant-`) |
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your numeric chat ID |

### Step 5: Test it! (1 min)

1. Go to **Actions** tab in your repo
2. Click **"Stock Alert Bot"** in the left sidebar
3. Click **"Run workflow"** → **"Run workflow"**
4. Wait ~30 seconds
5. Check your Telegram — you should see an alert! 🎉

---

## Schedule

The bot runs automatically 3 times a day, Monday through Friday:

| Berlin Time | UTC | Alert |
|------------|-----|-------|
| ~08:00 | 06:00 | Morning market overview |
| ~12:00 | 10:00 | Midday update |
| ~17:00 | 15:00 | End of day summary |

*Note: GitHub Actions cron can have delays of 5-15 minutes. This is normal.*

---

## Customizing

### Change your watchlist
Edit `stock_alert_bot.py`, line 18:
```python
WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META"]
```

### Change alert schedule
Edit `.github/workflows/stock-alert.yml`:
```yaml
schedule:
  - cron: '0 6 * * 1-5'   # Change these times (UTC)
  - cron: '0 10 * * 1-5'
  - cron: '0 15 * * 1-5'
```

### Add more stocks
Just add symbols to the `WATCHLIST` array. Finnhub supports all major US stocks.

---

## Costs

| Service | Monthly Cost |
|---------|-------------|
| GitHub Actions | **$0** (uses ~45 of 2,000 free minutes) |
| Finnhub API | **$0** (free tier: 60 calls/min) |
| Telegram Bot | **$0** |
| Claude API (Sonnet) | **~$0.50** (~90 calls × $0.005 each) |
| **Total** | **~$0.50/month** |

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
