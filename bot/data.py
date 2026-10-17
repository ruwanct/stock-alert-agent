"""
Data layer — Finnhub quotes/news, Google News RSS, watchlist building.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests

from bot import config

# Quote cache keyed by symbol. Filled by the big-mover scan (~55 stocks) and
# by fetch_quote(), so no ticker is fetched twice in one run.
QUOTE_CACHE: dict = {}


# ──────────────────────────────────────────────
# Finnhub Price API
# ──────────────────────────────────────────────
def fetch_quote(symbol: str) -> dict:
    if symbol in QUOTE_CACHE:
        return QUOTE_CACHE[symbol]
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": config.FINNHUB_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        time.sleep(0.12)  # Finnhub free tier: 60 calls/min
        if d.get("c", 0) == 0 and d.get("o", 0) == 0:
            quote = {"symbol": symbol, "error": "No data"}
        else:
            quote = {
                "symbol": symbol,
                "current": d.get("c", 0),
                "open": d.get("o", 0),
                "high": d.get("h", 0),
                "low": d.get("l", 0),
                "prev_close": d.get("pc", 0),
                "change": d.get("d", 0),
                "change_pct": d.get("dp", 0) or 0,
            }
        QUOTE_CACHE[symbol] = quote
        return quote
    except Exception as e:
        time.sleep(0.12)
        return {"symbol": symbol, "error": str(e)}  # not cached → may be retried


def get_price(symbol: str):
    """Current price for a symbol (cache first), or None."""
    q = fetch_quote(symbol)
    if "error" in q or not q.get("current"):
        return None
    return float(q["current"])


def valid_quotes() -> dict:
    """All successfully fetched quotes this run, keyed by symbol."""
    return {s: q for s, q in QUOTE_CACHE.items() if "error" not in q and q.get("current")}


# ──────────────────────────────────────────────
# Dynamic Stock Discovery
# ──────────────────────────────────────────────
def get_news_mentioned_tickers() -> list:
    movers = []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": config.FINNHUB_API_KEY},
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
        movers = list(mentioned)[:config.MAX_DYNAMIC_STOCKS]
        if movers:
            print(f"  📰 News-mentioned tickers: {', '.join(movers)}")
    except Exception as e:
        print(f"  ⚠ Error fetching news tickers: {e}")
    return movers


def get_top_movers_by_price() -> list:
    """Scan every sector stock; full quotes land in QUOTE_CACHE for reuse."""
    scan_list = set()
    for symbols in config.SECTORS.values():
        scan_list.update(symbols)

    print(f"  🔍 Scanning {len(scan_list)} stocks for big movers (>{config.BIG_MOVER_THRESHOLD}%)...")

    movers = []
    for symbol in sorted(scan_list):
        q = fetch_quote(symbol)
        if "error" in q:
            continue
        pct = q.get("change_pct", 0)
        price = q.get("current", 0)
        if price > 0 and abs(pct) >= config.BIG_MOVER_THRESHOLD:
            movers.append({"symbol": symbol, "change_pct": pct, "price": price})

    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    top = movers[:config.MAX_DYNAMIC_STOCKS]

    if top:
        print("  🔥 Big movers found:")
        for m in top:
            emoji = "▲" if m["change_pct"] > 0 else "▼"
            print(f"     {emoji} {m['symbol']}: {m['change_pct']:+.2f}% (${m['price']:.2f})")
    else:
        print(f"  😴 No stocks moving >{config.BIG_MOVER_THRESHOLD}% today")

    return [m["symbol"] for m in top]


def build_watchlist() -> list:
    print("\n🔍 Building watchlist...")

    watchlist = list(config.CORE_WATCHLIST)
    print(f"  Core: {', '.join(config.CORE_WATCHLIST)}")

    sector_picks = []
    for symbols in config.SECTORS.values():
        sector_picks.extend(symbols[:2])
    for s in sector_picks:
        if s not in watchlist:
            watchlist.append(s)
    print(f"  Sector picks: +{len(sector_picks)} from {len(config.SECTORS)} sectors")

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
        print(f"  Big movers: +{added_movers} stocks moving >{config.BIG_MOVER_THRESHOLD}%")

    watchlist = watchlist[:config.MAX_WATCHLIST]
    print(f"  📋 Final watchlist: {len(watchlist)} stocks")
    return watchlist


def fetch_prices(watchlist: list) -> list:
    print(f"\n📊 Fetching prices for {len(watchlist)} stocks...")
    prices = []
    for symbol in watchlist:
        quote = fetch_quote(symbol)
        prices.append(quote)
        if "error" not in quote:
            emoji = "🟢" if quote["change_pct"] >= 0 else "🔴"
            print(f"  {emoji} {symbol}: ${quote['current']:.2f} ({quote['change_pct']:+.2f}%)")
    valid = [p for p in prices if "error" not in p]
    print(f"  Got {len(valid)}/{len(watchlist)} prices")
    return prices


# ──────────────────────────────────────────────
# NEWS SOURCE 1: Finnhub Market News
# ──────────────────────────────────────────────
def fetch_finnhub_news() -> list:
    articles = []
    for category in ["general", "technology"]:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/news",
                params={"category": category, "token": config.FINNHUB_API_KEY},
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
def fetch_company_news(symbol: str, days: int = 2) -> list:
    """Raw Finnhub company-news items for one ticker (raises on HTTP errors; paces calls)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": symbol, "from": past, "to": today, "token": config.FINNHUB_API_KEY},
        timeout=10,
    )
    r.raise_for_status()
    items = r.json()
    time.sleep(0.12)
    return items if isinstance(items, list) else []


def fetch_finnhub_company_news(symbols: list) -> list:
    articles = []
    for symbol in symbols:
        try:
            for item in fetch_company_news(symbol)[:3]:
                articles.append({
                    "source": f"Finnhub/{item.get('source', 'Unknown')}",
                    "headline": f"[{symbol}] {item.get('headline', '')}",
                    "summary": item.get("summary", "")[:200],
                })
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
