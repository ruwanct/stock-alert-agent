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
# CUSTOMIZE YOUR WATCHLIST HERE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META"]


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
        return r.json()[:5]
    except Exception as e:
        print(f"  ⚠ Error fetching news for {symbol}: {e}")
        return []


# ──────────────────────────────────────────────
# Claude AI Analysis
# ──────────────────────────────────────────────
def analyze_with_claude(prices: list, news: list) -> str:
    """Send market data to Claude for analysis."""

    # Format prices
    price_lines = []
    for p in prices:
        if "error" in p:
            price_lines.append(f"- {p['symbol']}: Error")
            continue
        emoji = "🟢" if p["change_pct"] >= 0 else "🔴"
        price_lines.append(
            f"{emoji} {p['symbol']}: ${p['current']:.2f} "
            f"({'+' if p['change_pct'] >= 0 else ''}{p['change_pct']:.2f}%) "
            f"| H: ${p['high']:.2f} L: ${p['low']:.2f}"
        )

    # Format news
    news_lines = []
    for n in news[:20]:
        source = n.get("source", "Unknown")
        headline = n.get("headline", "")
        news_lines.append(f"- [{source}] {headline}")

    prompt = f"""You are a stock market analyst. Analyze this data and give actionable insights.

## Stock Prices
{chr(10).join(price_lines)}

## Latest News
{chr(10).join(news_lines) if news_lines else "No news available."}

## Instructions
1. Analyze news sentiment for each watchlist stock
2. Identify BUY / SELL / HOLD signals based on news + price action
3. Flag major market-moving events
4. Be concise and actionable

Format for Telegram with emojis:
- 🟢 BUY signals
- 🔴 SELL signals
- 🟡 HOLD/WATCH
- 📰 Important news
- 📊 Price summary

KEEP UNDER 1800 CHARACTERS. Be direct, no fluff."""

try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        print(f"  Claude API status: {r.status_code}")
        print(f"  Claude API response: {r.text[:500]}")
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"  ⚠ Claude API error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  ⚠ Response body: {e.response.text[:500]}")
        return f"⚠️ Analysis unavailable: {e}\n\n📊 Prices:\n" + "\n".join(price_lines)


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
    if not FINNHUB_API_KEY: missing.append("FINNHUB_API_KEY")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")
    if not TELEGRAM_BOT_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print(f"❌ Missing secrets: {', '.join(missing)}")
        print("Add them in GitHub → Settings → Secrets → Actions")
        sys.exit(1)

    # 1. Fetch stock prices
    print(f"\n📊 Fetching prices for: {', '.join(WATCHLIST)}")
    prices = []
    for symbol in WATCHLIST:
        quote = fetch_quote(symbol)
        prices.append(quote)
        if "error" not in quote:
            emoji = "🟢" if quote["change_pct"] >= 0 else "🔴"
            print(f"  {emoji} {symbol}: ${quote['current']:.2f} ({quote['change_pct']:+.2f}%)")
        time.sleep(0.15)

    # 2. Fetch news
    print("\n📰 Fetching market news...")
    general_news = fetch_news("general", 10)
    tech_news = fetch_news("technology", 5)
    all_news = general_news + tech_news
    print(f"  Got {len(all_news)} articles")

    # Fetch company news for top 3 stocks
    for symbol in WATCHLIST[:3]:
        company = fetch_company_news(symbol)
        all_news.extend(company)
        time.sleep(0.15)

    # Deduplicate
    seen = set()
    unique_news = []
    for article in all_news:
        aid = article.get("id", id(article))
        if aid not in seen:
            seen.add(aid)
            unique_news.append(article)
    print(f"  {len(unique_news)} unique articles after dedup")

    # 3. Analyze with Claude
    print("\n🧠 Analyzing with Claude...")
    analysis = analyze_with_claude(prices, unique_news)
    print(f"  Analysis generated ({len(analysis)} chars)")

    # 4. Send Telegram alert
    print("\n📱 Sending Telegram alert...")
    header = f"🤖 *Stock Alert Bot*\n⏰ {now}\n{'─' * 20}\n\n"
    success = send_telegram(header + analysis)

    if success:
        print("\n✅ Run complete! Check your Telegram.")
    else:
        print("\n❌ Run completed but Telegram delivery failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
