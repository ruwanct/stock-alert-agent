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
MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
SIGNALS_PATH = MEMORY_DIR / "signals.json"
DIGESTS_PATH = MEMORY_DIR / "digests.json"
TICKERS_PATH = MEMORY_DIR / "tickers.json"

SIGNAL_HORIZON_DAYS = 5      # calendar days before a signal is closed
WIN_THRESHOLD_PCT = 0.5      # return > +0.5% = win, < -0.5% = loss, else flat
TRACK_RECORD_CLOSED = 30     # closed signals used for stats
MAX_RECENT_DIGESTS = 15
MAX_WEEKLY_DIGESTS = 8
MAX_TICKER_HISTORY = 10
MAX_TICKER_NOTE_CHARS = 200
