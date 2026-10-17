"""
Configuration — env vars, stock universe, thresholds, memory paths.
"""

import os
from pathlib import Path

# ──────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# DRY_RUN=1 → run everything (incl. Claude) but print instead of sending,
# and do NOT write memory files.
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

# How many times the workflow cron runs per weekday. The cron times themselves
# live in .github/workflows/stock-alert.yml (GitHub can't read them from a
# variable); this only tunes memory retention and the footer to match.
try:
    ALERTS_PER_DAY = max(1, int(os.environ.get("ALERTS_PER_DAY") or 1))
except ValueError:
    ALERTS_PER_DAY = 1

# ──────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"  # the only model this API key supports

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# YOUR STOCK UNIVERSE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

SECTORS = {
    "Tech": ["META", "TSLA", "AMD", "INTC", "CRM", "ORCL"],
    "Finance": ["JPM", "GS", "V", "MA", "BRK.B"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV", "LLY"],
    "Energy": ["XOM", "CVX", "NEE", "OXY", "SLB"],
    "Defense/Military": ["LMT", "RTX", "NOC", "GD", "BA", "LHX"],
    "Semiconductors": ["TSM", "AVGO", "ASML", "QCOM", "MU"],
    "AI/Cloud": ["PLTR", "SNOW", "NET", "DDOG", "AI"],
    "Consumer": ["WMT", "COST", "NKE", "SBUX", "MCD"],
    "German/EU": ["SAP", "SIE.DE", "BMW.DE", "ADS.DE", "DTE.DE"],
    "ETFs": ["SPY", "QQQ", "VGT", "XLF", "XLE", "ITA"],
    "Crypto-adjacent": ["COIN", "MSTR", "MARA"],
}

BIG_MOVER_THRESHOLD = 2.0
MAX_DYNAMIC_STOCKS = 10
MAX_WATCHLIST = 30

# ──────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────
PRICE_TOLERANCE_PCT = 1.5   # BUY/SELL entry price must be within this % of live quote
MAX_VALIDATION_RETRIES = 1

# ──────────────────────────────────────────────
# Memory
# ──────────────────────────────────────────────
# Backend: "auto" → MongoDB if MONGODB_URI is set, else JSON files in memory/.
# "json" or "mongo" force one. JSON stays the dev/test backend and the weekly backup.
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
MONGODB_DB = os.environ.get("MONGODB_DB", "").strip() or "stock_alert_bot"
MEMORY_BACKEND = (os.environ.get("MEMORY_BACKEND", "").strip().lower() or "auto")
MONGO_TIMEOUT_MS = 8000
MONGO_APP_NAME = "stock-alert-bot"

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
SIGNALS_PATH = MEMORY_DIR / "signals.json"
DIGESTS_PATH = MEMORY_DIR / "digests.json"
TICKERS_PATH = MEMORY_DIR / "tickers.json"
ARCHIVE_DIR = MEMORY_DIR / "archive"   # yearly backup files (signals-YYYY.json, digests-YYYY.json, weekly-YYYY.json)

SIGNAL_HORIZON_DAYS = 5      # calendar days before a signal is closed
WIN_THRESHOLD_PCT = 0.5      # return > +0.5% = win, < -0.5% = loss, else flat
TRACK_RECORD_CLOSED = 30     # closed signals used for stats
MAX_RECENT_DIGESTS = max(7, 5 * ALERTS_PER_DAY)  # roughly 1–1.5 trading weeks of alerts
MAX_WEEKLY_DIGESTS = 8
BACKUP_RECENT_DAYS = 90      # closed signals newer than this stay in memory/signals.json; older go to archive/
MAX_TICKER_HISTORY = 10
MAX_TICKER_NOTE_CHARS = 200
