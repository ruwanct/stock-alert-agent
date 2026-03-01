"""
Stock Alert Telegram Bot — GitHub Actions Version
Multi-source news aggregation: Finnhub + Google News + Company-specific news
Fetches stock prices + aggregated news → Claude analysis → Telegram alert.
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

# Optional — add these for even more news (free tiers)
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY", "")  # gnews.io (100 req/day free)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOMIZE YOUR WATCHLISTS HERE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

SECTORS = {
    "Tech": ["META", "TSLA", "AMD", "INTC", "CRM"],
    "Finance": ["JPM", "GS", "V", "MA"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV"],
    "Energy": ["XOM", "CVX", "NEE"],
    "German/EU": ["SAP", "SIE.DE", "BMW.DE"],
    "ETFs": ["SPY", "QQQ", "VGT"],
    "Crypto-adjacent": ["COIN", "MSTR"],
}

MAX_DYNAMIC_STOCKS = 5


# ──────────────────────────────────────────────
# Dynamic Stock Discovery
# ──────────────────────────────────────────────
def get_market_movers() -> list:
    movers = []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        mentioned_tickers = set()
        for article in r.json()[:20]:
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


def build_watchlist() -> list:
    watchlist = list(CORE_WATCHLIST)
    for symbols in SECTORS.values():
        for s in symbols[:2]:
            if s not in watchlist:
                watchlist.append(s)
    for s in get_market_movers():
        if s not in watchlist:
            watchlist.append(s)
    return watchlist[:25]


# ──────────────────────────────────────────────
# Finnhub API
# ──────────────────────────────────────────────
def fetch_quote(symbol: str) -> dict:
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("c", 0) == 0 and d.get("o", 0) == 0:
            return {"symbol": symbol, "error": "No data"}
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


# ──────────────────────────────────────────────
# NEWS SOURCE 1: Finnhub (market + sector news)
# ──────────────────────────────────────────────
def fetch_finnhub_news() -> list:
    """Fetch market and tech news from Finnhub."""
    articles = []
    for category in ["general", "technology"]:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/news",
                params={"category": category, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            r.raise_for_status()
            for item in r.json()[:8]:
                articles.append({
                    "source": f"Finnhub/{item.get('source', 'Unknown')}",
                    "headline": item.get("headline", ""),
                    "summary": item.get("summary", "")[:200],
                    "url": item.get("url", ""),
                })
            time.sleep(0.12)
        except Exception as e:
            print(f"  ⚠ Finnhub {category} news error: {e}")
    print(f"  [Finnhub] Got {len(articles)} articles")
    return articles


# ──────────────────────────────────────────────
# NEWS SOURCE 2: Finnhub Company News (per ticker)
# ──────────────────────────────────────────────
def fetch_finnhub_company_news(symbols: list) -> list:
    """Fetch company-specific news for given symbols."""
    articles = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    for symbol in symbols:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": symbol, "from": past, "to": today, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            r.raise_for_status()
            for item in r.json()[:3]:
                articles.append({
                    "source": f"Finnhub/{item.get('source', 'Unknown')}",
                    "headline": f"[{symbol}] {item.get('headline', '')}",
                    "summary": item.get("summary", "")[:200],
                    "url": item.get("url", ""),
                })
            time.sleep(0.12)
        except Exception as e:
            print(f"  ⚠ Finnhub company news error for {symbol}: {e}")
    print(f"  [Finnhub Company] Got {len(articles)} articles for {len(symbols)} tickers")
    return articles


# ──────────────────────────────────────────────
# NEWS SOURCE 3: Google News via gnews package
# ──────────────────────────────────────────────
def fetch_google_news() -> list:
    """Fetch stock market news from Google News RSS (no API key needed)."""
    articles = []
    queries = ["stock market today", "Wall Street", "tech stocks", "S&P 500"]

    for query in queries:
        try:
            # Google News RSS feed — free, no key needed
            url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=10)
            r.raise_for_status()

            # Simple XML parsing (avoid importing extra libraries)
            import re
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:3]:
                title_match = re.search(r'<title>(.*?)</title>', item)
                source_match = re.search(r'<source[^>]*>(.*?)</source>', item)
                if title_match:
                    title = title_match.group(1).replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                    source = source_match.group(1) if source_match else "Google News"
                    articles.append({
                        "source": f"Google/{source}",
                        "headline": title,
                        "summary": "",
                        "url": "",
                    })
        except Exception as e:
            print(f"  ⚠ Google News error for '{query}': {e}")

    print(f"  [Google News] Got {len(articles)} articles")
    return articles


# ──────────────────────────────────────────────
# NEWS SOURCE 4: GNews API (optional, if key provided)
# ──────────────────────────────────────────────
def fetch_gnews_api() -> list:
    """Fetch business news from GNews.io API (100 free requests/day)."""
    if not GNEWS_API_KEY:
        return []

    articles = []
    try:
        r = requests.get(
            "https://gnews.io/api/v4/top-headlines",
            params={
                "category": "business",
                "lang": "en",
                "max": 10,
                "apikey": GNEWS_API_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        for item in r.json().get("articles", []):
            articles.append({
                "source": f"GNews/{item.get('source', {}).get('name', 'Unknown')}",
                "headline": item.get("title", ""),
                "summary": item.get("description", "")[:200],
                "url": item.get("url", ""),
            })
    except Exception as e:
        print(f"  ⚠ GNews API error: {e}")

    print(f"  [GNews API] Got {len(articles)} articles")
    return articles


# ──────────────────────────────────────────────
# News Aggregator
# ──────────────────────────────────────────────
def aggregate_news(core_symbols: list) -> list:
    """Pull news from all sources, deduplicate, and return unified list."""
    print("\n📰 Aggregating news from multiple sources...")

    all_articles = []

    # Source 1: Finnhub market news
    all_articles.extend(fetch_finnhub_news())

    # Source 2: Finnhub company-specific news
    all_articles.extend(fetch_finnhub_company_news(core_symbols[:5]))

    # Source 3: Google News RSS (free, no key)
    all_articles.extend(fetch_google_news())

    # Source 4: GNews API (optional)
    all_articles.extend(fetch_gnews_api())

    # Deduplicate by headline similarity
    seen_headlines = set()
    unique = []
    for article in all_articles:
        # Use first 50 chars of headline as dedup key
        key = article["headline"][:50].lower().strip()
        if key and key not in seen_headlines:
            seen_headlines.add(key)
            unique.append(article)

    # Sort: company-specific news first, then general
    unique.sort(key=lambda x: (0 if "[" in x["headline"] else 1))

    print(f"  📊 Total: {len(all_articles)} raw → {len(unique)} unique articles")
    print(f"  Sources: Finnhub Market, Finnhub Company, Google News" +
          (", GNews API" if GNEWS_API_KEY else ""))

    return unique


# ──────────────────────────────────────────────
# Claude AI Analysis
# ──────────────────────────────────────────────
def analyze_with_claude(prices: list, news: list, watchlist_info: str) -> str:
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

    gainers.sort(key=lambda x: x[0], reverse=True)
    losers.sort(key=lambda x: x[0])
    price_lines = [line for _, line in gainers] + [line for _, line in losers]

    # Format news with source attribution
    news_lines = []
    for n in news[:25]:
        source = n.get("source", "Unknown")
        headline = n.get("headline", "")
        summary = n.get("summary", "")
        line = f"- [{source}] {headline}"
        if summary:
            line += f" — {summary[:100]}"
        news_lines.append(line)

    # Count sources for context
    sources = set()
    for n in news:
        main_source = n.get("source", "").split("/")[0]
        if main_source:
            sources.add(main_source)

    prompt = f"""You are a senior stock market analyst creating a Telegram alert from MULTIPLE news sources.

## Watchlist
{watchlist_info}

## Stock Prices (sorted by performance)
{chr(10).join(price_lines)}
{f"No data: {', '.join(errors)}" if errors else ""}

## Aggregated News ({len(news)} articles from {', '.join(sources)})
{chr(10).join(news_lines)}

## FORMAT YOUR RESPONSE EXACTLY LIKE THIS:

🌡 MARKET PULSE: [one-line overall sentiment]

━━━━━━━━━━━━━━━━━━━━

📊 TOP MOVERS
▲ [TICKER] +X.XX% ($XXX) — [reason]
▲ [TICKER] +X.XX% ($XXX) — [reason]
▼ [TICKER] -X.XX% ($XXX) — [reason]
▼ [TICKER] -X.XX% ($XXX) — [reason]

━━━━━━━━━━━━━━━━━━━━

🎯 SIGNALS

🟢 BUY: [TICKER] @ $XXX
   → [one-line reasoning with news source]

🟢 BUY: [TICKER] @ $XXX
   → [one-line reasoning with news source]

🔴 SELL: [TICKER] @ $XXX
   → [one-line reasoning]

🟡 WATCH: [TICKER]
   → [what to watch for]

━━━━━━━━━━━━━━━━━━━━

📰 KEY NEWS (cross-referenced from {len(sources)} sources)
• [headline + which sources confirm it + impact]
• [headline + impact]
• [headline + impact]

━━━━━━━━━━━━━━━━━━━━

🏢 SECTOR HEAT MAP
🟩 [sector] | 🟩 [sector] | 🟥 [sector] | 🟥 [sector]

━━━━━━━━━━━━━━━━━━━━

⚡ BOTTOM LINE
[2-3 sentence actionable summary]

## RULES:
- Cross-reference news across sources for higher confidence signals
- If multiple sources report the same story, flag it as HIGH CONFIDENCE
- Show only TOP 2 gainers and TOP 2 losers
- 2-3 BUY signals, 1-2 SELL signals with exact prices
- Sector heat map: 🟩 up 🟥 down 🟨 flat
- NO markdown (no * or _ characters)
- KEEP UNDER 2800 CHARACTERS"""

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
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (truncated)"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        print("  ✅ Telegram message sent!")
        return True
    except Exception as e:
        print(f"  ❌ Telegram failed: {e}")
        return False


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print("🤖 Stock Alert Bot v3 - Multi-Source News Aggregation")
    print("=" * 50)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    berlin_time = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%H:%M")

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

    # Build watchlist
    print("\n🔍 Building watchlist...")
    watchlist = build_watchlist()
    sector_names = ", ".join(SECTORS.keys())
    watchlist_info = f"Core: {', '.join(CORE_WATCHLIST)} | Sectors: {sector_names} | Total: {len(watchlist)}"
    print(f"  Tracking {len(watchlist)} stocks")

    # 1. Fetch prices
    print(f"\n📊 Fetching prices...")
    prices = []
    for symbol in watchlist:
        quote = fetch_quote(symbol)
        prices.append(quote)
        if "error" not in quote:
            emoji = "🟢" if quote["change_pct"] >= 0 else "🔴"
            print(f"  {emoji} {symbol}: ${quote['current']:.2f} ({quote['change_pct']:+.2f}%)")
        time.sleep(0.12)

    valid_prices = [p for p in prices if "error" not in p]
    print(f"  Got {len(valid_prices)}/{len(watchlist)} prices")

    # 2. Aggregate news from all sources
    news = aggregate_news(CORE_WATCHLIST)

    # 3. Claude analysis
    print("\n🧠 Analyzing with Claude (multi-source)...")
    analysis = analyze_with_claude(prices, news, watchlist_info)
    print(f"  Analysis: {len(analysis)} chars")

    # 4. Build message and send
    print("\n📱 Sending alert...")

    utc_hour = datetime.now(timezone.utc).hour
    if utc_hour < 9:
        session = "🌅 PRE-MARKET"
    elif utc_hour < 16:
        session = "🔔 MARKET OPEN"
    else:
        session = "🌙 AFTER-HOURS"

    num_gainers = len([p for p in valid_prices if p.get("change_pct", 0) >= 0])
    num_losers = len(valid_prices) - num_gainers

    # Count news sources
    sources = set()
    for n in news:
        main_source = n.get("source", "").split("/")[0]
        if main_source:
            sources.add(main_source)

    header = (
        f"{'═' * 28}\n"
        f"   🤖 STOCK ALERT BOT\n"
        f"   {session}\n"
        f"{'═' * 28}\n"
        f"🕐 {berlin_time} Berlin | {now}\n"
        f"📋 {len(valid_prices)} stocks | "
        f"🟢 {num_gainers} up | 🔴 {num_losers} down\n"
        f"📰 {len(news)} articles from {len(sources)} sources\n"
        f"{'═' * 28}\n\n"
    )

    footer = (
        f"\n\n{'═' * 28}\n"
        f"  Sources: {', '.join(sorted(sources))}\n"
        f"  Powered by Finnhub + Claude AI\n"
        f"{'═' * 28}"
    )

    success = send_telegram(header + analysis + footer)

    if success:
        print("\n✅ Complete! Check Telegram.")
    else:
        print("\n❌ Telegram delivery failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
