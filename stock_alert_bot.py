"""
Stock Alert Telegram Bot v5 — GitHub Actions Version
Dynamic watchlist + Multi-source news + Sector scanning
Sends to BOTH Telegram and Discord simultaneously.
"""

import os
import sys
import time
import re
import requests
from datetime import datetime, timezone, timedelta

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

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


# ──────────────────────────────────────────────
# Dynamic Stock Discovery
# ──────────────────────────────────────────────
def get_news_mentioned_tickers() -> list:
    movers = []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        mentioned = set()
        for article in r.json()[:20]:
            related = article.get("related", "")
            if related:
                for ticker in related.split(","):
                    ticker = ticker.strip().upper()
                    if ticker and len(ticker) <= 5 and ticker.isalpha():
                        mentioned.add(ticker)
        movers = list(mentioned)[:MAX_DYNAMIC_STOCKS]
        if movers:
            print(f"  📰 News-mentioned tickers: {', '.join(movers)}")
    except Exception as e:
        print(f"  ⚠ Error fetching news tickers: {e}")
    return movers


def get_top_movers_by_price() -> list:
    scan_list = set()
    for symbols in SECTORS.values():
        scan_list.update(symbols)

    print(f"  🔍 Scanning {len(scan_list)} stocks for big movers (>{BIG_MOVER_THRESHOLD}%)...")

    movers = []
    for symbol in sorted(scan_list):
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            pct = d.get("dp", 0)
            price = d.get("c", 0)
            if price > 0 and abs(pct) >= BIG_MOVER_THRESHOLD:
                movers.append({"symbol": symbol, "change_pct": pct, "price": price})
            time.sleep(0.1)
        except Exception:
            pass

    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    top = movers[:MAX_DYNAMIC_STOCKS]

    if top:
        print(f"  🔥 Big movers found:")
        for m in top:
            emoji = "▲" if m["change_pct"] > 0 else "▼"
            print(f"     {emoji} {m['symbol']}: {m['change_pct']:+.2f}% (${m['price']:.2f})")
    else:
        print(f"  😴 No stocks moving >{BIG_MOVER_THRESHOLD}% today")

    return [m["symbol"] for m in top]


def build_watchlist() -> list:
    print("\n🔍 Building watchlist...")

    watchlist = list(CORE_WATCHLIST)
    print(f"  Core: {', '.join(CORE_WATCHLIST)}")

    sector_picks = []
    for sector, symbols in SECTORS.items():
        sector_picks.extend(symbols[:2])
    for s in sector_picks:
        if s not in watchlist:
            watchlist.append(s)
    print(f"  Sector picks: +{len(sector_picks)} from {len(SECTORS)} sectors")

    news_tickers = get_news_mentioned_tickers()
    added_news = 0
    for s in news_tickers:
        if s not in watchlist:
            watchlist.append(s)
            added_news += 1
    if added_news:
        print(f"  News discoveries: +{added_news} trending tickers")

    big_movers = get_top_movers_by_price()
    added_movers = 0
    for s in big_movers:
        if s not in watchlist:
            watchlist.append(s)
            added_movers += 1
    if added_movers:
        print(f"  Big movers: +{added_movers} stocks moving >{BIG_MOVER_THRESHOLD}%")

    watchlist = watchlist[:30]
    print(f"  📋 Final watchlist: {len(watchlist)} stocks")
    return watchlist


# ──────────────────────────────────────────────
# Finnhub Price API
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
        return {"symbol": symbol, "error": str(e)}


# ──────────────────────────────────────────────
# NEWS SOURCE 1: Finnhub Market News
# ──────────────────────────────────────────────
def fetch_finnhub_news() -> list:
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
                })
            time.sleep(0.12)
        except Exception as e:
            print(f"  ⚠ Finnhub {category} error: {e}")
    print(f"  [Finnhub Market] {len(articles)} articles")
    return articles


# ──────────────────────────────────────────────
# NEWS SOURCE 2: Finnhub Company News
# ──────────────────────────────────────────────
def fetch_finnhub_company_news(symbols: list) -> list:
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
                })
            time.sleep(0.12)
        except Exception:
            pass
    print(f"  [Finnhub Company] {len(articles)} articles for {len(symbols)} tickers")
    return articles


# ──────────────────────────────────────────────
# NEWS SOURCE 3: Google News RSS (free, no key)
# ──────────────────────────────────────────────
def fetch_google_news() -> list:
    articles = []
    queries = [
        "stock market today",
        "Wall Street trading",
        "tech stocks earnings",
        "S&P 500 nasdaq",
        "defense military stocks",
        "oil energy stocks",
    ]
    for query in queries:
        try:
            url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:3]:
                title_match = re.search(r'<title>(.*?)</title>', item)
                source_match = re.search(r'<source[^>]*>(.*?)</source>', item)
                if title_match:
                    title = title_match.group(1)
                    title = title.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
                    source = source_match.group(1) if source_match else "Google News"
                    articles.append({
                        "source": f"Google/{source}",
                        "headline": title,
                        "summary": "",
                    })
        except Exception:
            pass
    print(f"  [Google News] {len(articles)} articles")
    return articles


# ──────────────────────────────────────────────
# News Aggregator
# ──────────────────────────────────────────────
def aggregate_news(core_symbols: list) -> list:
    print("\n📰 Aggregating news from multiple sources...")

    all_articles = []
    all_articles.extend(fetch_finnhub_news())
    all_articles.extend(fetch_finnhub_company_news(core_symbols[:5]))
    all_articles.extend(fetch_google_news())

    seen = set()
    unique = []
    for article in all_articles:
        key = article["headline"][:50].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(article)

    unique.sort(key=lambda x: (0 if "[" in x["headline"] else 1))

    sources = set(n.get("source", "").split("/")[0] for n in unique if n.get("source"))
    print(f"  📊 Total: {len(all_articles)} raw → {len(unique)} unique from {len(sources)} sources")
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
            f"({'+' if p['change_pct'] >= 0 else ''}{p['change_pct']:.2f}%)"
        )
        if p["change_pct"] >= 0:
            gainers.append((p["change_pct"], line))
        else:
            losers.append((p["change_pct"], line))

    gainers.sort(key=lambda x: x[0], reverse=True)
    losers.sort(key=lambda x: x[0])
    price_lines = [line for _, line in gainers] + [line for _, line in losers]

    news_lines = []
    for n in news[:25]:
        source = n.get("source", "Unknown")
        headline = n.get("headline", "")
        summary = n.get("summary", "")
        line = f"- [{source}] {headline}"
        if summary:
            line += f" — {summary[:80]}"
        news_lines.append(line)

    sources = set(n.get("source", "").split("/")[0] for n in news if n.get("source"))

    prompt = f"""You are a senior stock market analyst. Create a beautiful Telegram/Discord alert from multi-source data.

## Coverage
{watchlist_info}

## Prices (sorted by performance)
{chr(10).join(price_lines)}
{f"No data: {', '.join(errors)}" if errors else ""}

## News ({len(news)} articles from {', '.join(sources)})
{chr(10).join(news_lines)}

## FORMAT EXACTLY LIKE THIS:

🌡 MARKET PULSE: [one-line sentiment with market direction]

━━━━━━━━━━━━━━━━━━━━

📊 TOP MOVERS
▲ [TICKER] +X.XX% ($XXX) — [reason]
▲ [TICKER] +X.XX% ($XXX) — [reason]
▲ [TICKER] +X.XX% ($XXX) — [reason]
▼ [TICKER] -X.XX% ($XXX) — [reason]
▼ [TICKER] -X.XX% ($XXX) — [reason]
▼ [TICKER] -X.XX% ($XXX) — [reason]

━━━━━━━━━━━━━━━━━━━━

🎯 SIGNALS

🟢 BUY: [TICKER] @ $XXX
   → [reasoning citing news source]

🟢 BUY: [TICKER] @ $XXX
   → [reasoning citing news source]

🟢 BUY: [TICKER] @ $XXX
   → [reasoning citing news source]

🔴 SELL: [TICKER] @ $XXX
   → [reasoning]

🟡 WATCH: [TICKER]
   → [catalyst to watch]

━━━━━━━━━━━━━━━━━━━━

📰 KEY NEWS
• [most impactful headline + market impact]
• [second headline + impact]
• [third headline + impact]

━━━━━━━━━━━━━━━━━━━━

🏢 SECTOR HEAT MAP
🟩 [sector] | 🟩 [sector] | 🟥 [sector] | 🟥 [sector]

━━━━━━━━━━━━━━━━━━━━

⚡ BOTTOM LINE
[2-3 sentence actionable summary: what to buy, what to avoid, key risk]

## RULES:
- Cross-reference news — if multiple sources report same story, mark HIGH CONFIDENCE
- TOP 3 gainers and TOP 3 losers in movers section
- 3 BUY signals with prices, 1-2 SELL signals
- Include defense/military sector in heat map if relevant
- Sector heat map: 🟩 up 🟥 down 🟨 flat
- NO markdown formatting (no * or _ characters)
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
                "model": "claude-sonnet-4-5",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        print(f"  Claude API status: {r.status_code}")
        if r.status_code != 200:
            print(f"  Claude response: {r.text[:500]}")
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"  ⚠ Claude error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"  ⚠ Body: {e.response.text[:500]}")
        return f"⚠️ Analysis unavailable: {e}\n\n📊 Prices:\n" + "\n".join(price_lines[:10])


# ──────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⏭ Telegram not configured, skipping")
        return False

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
        print("  ✅ Telegram sent!")
        return True
    except Exception as e:
        print(f"  ❌ Telegram failed: {e}")
        return False


# ──────────────────────────────────────────────
# Discord
# ──────────────────────────────────────────────
def send_discord(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("  ⏭ Discord not configured, skipping")
        return False

    # Discord has 2000 char limit per message — split if needed
    chunks = []
    if len(text) <= 2000:
        chunks = [text]
    else:
        # Split at section separators
        sections = text.split("━━━━━━━━━━━━━━━━━━━━")
        current = ""
        for section in sections:
            test = current + "━━━━━━━━━━━━━━━━━━━━" + section if current else section
            if len(test) > 1950:
                if current:
                    chunks.append(current)
                current = section
            else:
                current = test
        if current:
            chunks.append(current)

    success = True
    for i, chunk in enumerate(chunks):
        try:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json={"content": chunk},
                timeout=10,
            )
            r.raise_for_status()
            if i < len(chunks) - 1:
                time.sleep(0.5)  # Avoid rate limiting
        except Exception as e:
            print(f"  ❌ Discord failed (chunk {i+1}): {e}")
            success = False

    if success:
        print(f"  ✅ Discord sent! ({len(chunks)} message{'s' if len(chunks) > 1 else ''})")
    return success


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print("🤖 Stock Alert Bot v5")
    print("Dynamic Watchlist + Multi-Source News + Telegram + Discord")
    print("=" * 58)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    berlin_time = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%H:%M")

    # Validate — only Finnhub and Anthropic are required
    # Telegram and Discord are optional (at least one should be set)
    missing = []
    if not FINNHUB_API_KEY:
        missing.append("FINNHUB_API_KEY")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"❌ Missing required: {', '.join(missing)}")
        sys.exit(1)

    has_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    has_discord = bool(DISCORD_WEBHOOK_URL)

    if not has_telegram and not has_discord:
        print("❌ No delivery channel! Set TELEGRAM or DISCORD secrets.")
        sys.exit(1)

    channels = []
    if has_telegram:
        channels.append("Telegram")
    if has_discord:
        channels.append("Discord")
    print(f"  📡 Delivery: {' + '.join(channels)}")

    # 1. Build dynamic watchlist
    watchlist = build_watchlist()
    sector_names = ", ".join(SECTORS.keys())
    watchlist_info = (
        f"Core: {', '.join(CORE_WATCHLIST)}\n"
        f"Sectors: {sector_names}\n"
        f"Dynamic: news-trending + big movers (>{BIG_MOVER_THRESHOLD}%)\n"
        f"Total tracking: {len(watchlist)} stocks"
    )

    # 2. Fetch prices
    print(f"\n📊 Fetching prices for {len(watchlist)} stocks...")
    prices = []
    for symbol in watchlist:
        quote = fetch_quote(symbol)
        prices.append(quote)
        if "error" not in quote:
            emoji = "🟢" if quote["change_pct"] >= 0 else "🔴"
            print(f"  {emoji} {symbol}: ${quote['current']:.2f} ({quote['change_pct']:+.2f}%)")
        time.sleep(0.12)

    valid = [p for p in prices if "error" not in p]
    print(f"  Got {len(valid)}/{len(watchlist)} prices")

    # 3. Aggregate news
    news = aggregate_news(CORE_WATCHLIST)

    # 4. Claude analysis
    print("\n🧠 Claude analysis (multi-source)...")
    analysis = analyze_with_claude(prices, news, watchlist_info)
    print(f"  Generated {len(analysis)} chars")

    # 5. Build message
    utc_hour = datetime.now(timezone.utc).hour
    if utc_hour < 9:
        session = "🌅 PRE-MARKET"
    elif utc_hour < 16:
        session = "🔔 MARKET OPEN"
    else:
        session = "🌙 AFTER-HOURS"

    num_up = len([p for p in valid if p.get("change_pct", 0) >= 0])
    num_down = len(valid) - num_up
    sources = set(n.get("source", "").split("/")[0] for n in news if n.get("source"))

    header = (
        f"{'═' * 28}\n"
        f"   🤖 STOCK ALERT BOT v5\n"
        f"   {session}\n"
        f"{'═' * 28}\n"
        f"🕐 {berlin_time} Berlin | {now}\n"
        f"📋 {len(valid)} stocks across {len(SECTORS)} sectors\n"
        f"🟢 {num_up} up | 🔴 {num_down} down\n"
        f"📰 {len(news)} news from {', '.join(sorted(sources))}\n"
        f"{'═' * 28}\n\n"
    )

    footer = (
        f"\n\n{'═' * 28}\n"
        f"  🔄 Next alert in ~4 hours\n"
        f"  📡 Finnhub + Google News + Claude AI\n"
        f"{'═' * 28}"
    )

    full_message = header + analysis + footer

    # 6. Send to all channels
    print("\n📱 Sending alerts...")
    telegram_ok = send_telegram(full_message)
    discord_ok = send_discord(full_message)

    if telegram_ok or discord_ok:
        results = []
        if has_telegram:
            results.append(f"Telegram: {'✅' if telegram_ok else '❌'}")
        if has_discord:
            results.append(f"Discord: {'✅' if discord_ok else '❌'}")
        print(f"\n✅ Done! {' | '.join(results)}")
    else:
        print("\n❌ All delivery channels failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
