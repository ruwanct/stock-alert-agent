"""
Stock Alert Telegram Bot — GitHub Actions Version
Runs once per execution (scheduled by GitHub Actions cron).
Fetches stock prices + news → Claude analysis → Telegram alert.
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOMIZE YOUR WATCHLISTS HERE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Your core holdings — always tracked
CORE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

# Sector-based picks — diversify across industries
SECTORS = {
    "Tech": ["META", "TSLA", "AMD", "INTC", "CRM"],
    "Finance": ["JPM", "GS", "V", "MA"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV"],
    "Energy": ["XOM", "CVX", "NEE"],
    "German/EU": ["SAP", "SIE.DE", "BMW.DE"],
    "ETFs": ["SPY", "QQQ", "VGT"],         # Market & tech index ETFs
    "Crypto-adjacent": ["COIN", "MSTR"],     # Crypto exposure via stocks
}

# How many extra stocks to add from dynamic sources
MAX_DYNAMIC_STOCKS = 5


# ──────────────────────────────────────────────
# Dynamic Stock Discovery
# ──────────────────────────────────────────────
def get_market_movers() -> list:
    """Fetch top gainers and losers from Finnhub — stocks making big moves today."""
    movers = []

    # Get stocks with unusual activity via Finnhub's market news
    # We extract tickers mentioned in breaking news
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        articles = r.json()[:20]

        # Extract tickers mentioned in news using Finnhub
        mentioned_tickers = set()
        for article in articles:
            related = article.get("related", "")
            if related:
                for ticker in related.split(","):
                    ticker = ticker.strip().upper()
                    if ticker and len(ticker) <= 5 and ticker.isalpha():
                        mentioned_tickers.add(ticker)

        movers = list(mentioned_tickers)[:MAX_DYNAMIC_STOCKS]
        print(f"  📈 News-mentioned tickers: {movers}")
    except Exception as e:
        print(f"  ⚠ Error fetching movers: {e}")

    return movers


def get_sector_rotation_picks() -> list:
    """Pick 1-2 stocks from each sector for diversified coverage."""
    picks = []
    for sector_name, symbols in SECTORS.items():
        # Take first 1-2 from each sector to keep total manageable
        selected = symbols[:2]
        picks.extend(selected)
    return picks


def build_watchlist() -> list:
    """Build the final watchlist: core + sectors + dynamic movers."""
    watchlist = list(CORE_WATCHLIST)

    # Add sector picks
    sector_picks = get_sector_rotation_picks()
    for s in sector_picks:
        if s not in watchlist:
            watchlist.append(s)

    # Add dynamically discovered movers
    dynamic = get_market_movers()
    for s in dynamic:
        if s not in watchlist:
            watchlist.append(s)

    # Cap at 25 to keep within API limits and Telegram message size
    watchlist = watchlist[:25]
    return watchlist


# ──────────────────────────────────────────────
# Finnhub API
# ──────────────────────────────────────────────
def fetch_quote(symbol: str) -> dict:
    """Fetch real-time quote for a stock symbol."""
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        # Skip if no data (all zeros means market closed or invalid ticker)
        if d.get("c", 0) == 0 and d.get("o", 0) == 0:
            return {"symbol": symbol, "error": "No data available"}
        return {
            "symbol": symbol,
            "current": d.get("c", 0),
            "open": d.get("o", 0),
            "high": d.get("h", 0),
            "low": d.get("l", 0),
            "prev_close": d.get("pc", 0),
            "change": d.get("d", 0),
            "change_pct": d.get("dp", 0),
        }
    except Exception as e:
        print(f"  ⚠ Error fetching {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


def fetch_news(category: str = "general", count: int = 15) -> list:
    """Fetch latest market news."""
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()[:count]
    except Exception as e:
        print(f"  ⚠ Error fetching {category} news: {e}")
        return []


def fetch_company_news(symbol: str) -> list:
    """Fetch company-specific news (last 2 days)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": symbol, "from": past, "to": today, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()[:3]
    except Exception as e:
        print(f"  ⚠ Error fetching news for {symbol}: {e}")
        return []


# ──────────────────────────────────────────────
# Claude AI Analysis
# ──────────────────────────────────────────────
def analyze_with_claude(prices: list, news: list, watchlist_info: str) -> str:
    """Send market data to Claude for analysis."""

    # Format prices — separate gainers and losers
    gainers = []
    losers = []
    errors = []
    for p in prices:
        if "error" in p:
            errors.append(p["symbol"])
            continue
        emoji = "🟢" if p["change_pct"] >= 0 else "🔴"
        line = (
            f"{emoji} {p['symbol']}: ${p['current']:.2f} "
            f"({'+' if p['change_pct'] >= 0 else ''}{p['change_pct']:.2f}%) "
            f"| H: ${p['high']:.2f} L: ${p['low']:.2f}"
        )
        if p["change_pct"] >= 0:
            gainers.append((p["change_pct"], line))
        else:
            losers.append((p["change_pct"], line))

    # Sort: biggest gainers first, biggest losers first
    gainers.sort(key=lambda x: x[0], reverse=True)
    losers.sort(key=lambda x: x[0])

    price_lines = [line for _, line in gainers] + [line for _, line in losers]

    # Format news
    news_lines = []
    for n in news[:20]:
        source = n.get("source", "Unknown")
        headline = n.get("headline", "")
        news_lines.append(f"- [{source}] {headline}")

    prompt = f"""You are a senior stock market analyst. Analyze this data and give actionable insights.

## Watchlist Coverage
{watchlist_info}

## Stock Prices (sorted by performance)
{chr(10).join(price_lines)}
{f"Skipped (no data): {', '.join(errors)}" if errors else ""}

## Latest News
{chr(10).join(news_lines) if news_lines else "No news available."}

## Instructions
1. Identify the TOP 3 BUY opportunities with brief reasoning
2. Identify any SELL signals with reasoning
3. Note any major macro/geopolitical events affecting the market
4. Mention any sector-wide trends (e.g., "tech selling off", "energy rallying")
5. Give a 1-line overall market sentiment

Format for Telegram with emojis:
- 🟢 BUY signals (top 3 only)
- 🔴 SELL signals
- 🟡 HOLD/WATCH
- 📰 Key news
- 📊 Market overview

KEEP UNDER 2500 CHARACTERS. Be direct and actionable."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        print(f"  Claude API status: {r.status_code}")
        if r.status_code != 200:
            print(f"  Claude API response: {r.text[:500]}")
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"  ⚠ Claude API error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"  ⚠ Response body: {e.response.text[:500]}")
        return f"⚠️ Analysis unavailable: {e}\n\n📊 Prices:\n" + "\n".join(price_lines[:10])


# ──────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    """Send message via Telegram."""
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (truncated)"

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        print("  ✅ Telegram message sent!")
        return True
    except Exception as e:
        print(f"  ⚠ Telegram error (trying without markdown): {e}")
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
                timeout=10,
            )
            r.raise_for_status()
            print("  ✅ Telegram message sent (plain text)!")
            return True
        except Exception as e2:
            print(f"  ❌ Telegram failed completely: {e2}")
            return False


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print("🤖 Stock Alert Bot - Starting run...")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Validate secrets
    missing = []
    if not FINNHUB_API_KEY:
        missing.append("FINNHUB_API_KEY")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print(f"❌ Missing secrets: {', '.join(missing)}")
        sys.exit(1)

    # Build dynamic watchlist
    print("\n🔍 Building watchlist...")
    watchlist = build_watchlist()
    watchlist_info = (
        f"Core: {', '.join(CORE_WATCHLIST)} | "
        f"Sectors: {', '.join(SECTORS.keys())} | "
        f"Total: {len(watchlist)} stocks"
    )
    print(f"  Tracking {len(watchlist)} stocks: {', '.join(watchlist)}")

    # 1. Fetch stock prices
    print(f"\n📊 Fetching prices...")
    prices = []
    for symbol in watchlist:
        quote = fetch_quote(symbol)
        prices.append(quote)
        if "error" not in quote:
            emoji = "🟢" if quote["change_pct"] >= 0 else "🔴"
            print(f"  {emoji} {symbol}: ${quote['current']:.2f} ({quote['change_pct']:+.2f}%)")
        time.sleep(0.12)

    # Filter out errors for cleaner analysis
    valid_prices = [p for p in prices if "error" not in p]
    print(f"  Got prices for {len(valid_prices)}/{len(watchlist)} stocks")

    # 2. Fetch news
    print("\n📰 Fetching market news...")
    general_news = fetch_news("general", 10)
    tech_news = fetch_news("technology", 5)
    all_news = general_news + tech_news
    print(f"  Got {len(all_news)} articles")

    # Fetch company news for core watchlist
    for symbol in CORE_WATCHLIST[:3]:
        company = fetch_company_news(symbol)
        all_news.extend(company)
        time.sleep(0.12)

    # Deduplicate
    seen = set()
    unique_news = []
    for article in all_news:
        aid = article.get("id", id(article))
        if aid not in seen:
            seen.add(aid)
            unique_news.append(article)
    print(f"  {len(unique_news)} unique articles")

    # 3. Analyze with Claude
    print("\n🧠 Analyzing with Claude...")
    analysis = analyze_with_claude(prices, unique_news, watchlist_info)
    print(f"  Analysis generated ({len(analysis)} chars)")

    # 4. Send Telegram alert
    print("\n📱 Sending Telegram alert...")
    header = f"🤖 *Stock Alert Bot*\n⏰ {now}\n📋 Tracking {len(valid_prices)} stocks across {len(SECTORS)} sectors\n{'─' * 25}\n\n"
    success = send_telegram(header + analysis)

    if success:
        print("\n✅ Run complete! Check your Telegram.")
    else:
        print("\n❌ Run completed but Telegram delivery failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
