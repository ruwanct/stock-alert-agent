"""
Tools for the research agent. Built per run by `build_tools(ledger, store)` so the
history tool reads the run's in-state memory (scored ledger + working ticker store).
Tools never raise: failures come back as short strings the model can read.
"""

from langchain_core.tools import tool

from bot import data, memory

MAX_NEWS_ITEMS = 5


def company_news_text(ticker: str) -> str:
    """Last 2 days of Finnhub company news for one ticker, formatted for the model."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return "No ticker given."
    try:
        items = data.fetch_company_news(ticker, days=2)
    except Exception as e:
        return f"News lookup failed for {ticker} ({type(e).__name__})."
    lines = [f"- {i.get('headline', '')} — {(i.get('summary') or '')[:150]}" for i in items[:MAX_NEWS_ITEMS]]
    return "\n".join(lines) or f"No news for {ticker} in the last 2 days."


def ticker_history_text(ledger: list, store, ticker: str) -> str:
    """The bot's own memory for one ticker: ledger signals plus the ticker note."""
    ticker = (ticker or "").strip().upper()
    try:
        sigs = sorted((s for s in ledger if s.get("ticker") == ticker), key=lambda s: s.get("opened_at") or "")
        lines = []
        for s in sigs[-6:]:
            entry = f" @ ${s['entry_price']:.2f}" if s.get("entry_price") else ""
            ret = s.get("return_pct")
            ret_txt = f"{ret:+.1f}%" if ret is not None else "n/a"
            outcome = f", outcome {s['outcome']}" if s.get("outcome") else ""
            lines.append(f"- {s['action']}{entry} opened {(s.get('opened_at') or '?')[:10]} "
                         f"[{s.get('status')}] return {ret_txt}{outcome}")
        item = store.get(memory.TICKER_NS, ticker) if store is not None else None
        if item:
            v = item.value
            hist = ", ".join(f"{h['action']}@{h.get('price')} {h['date']}" for h in v.get("signal_history", [])[-5:])
            lines.append(f"Note: mentioned {v.get('mention_count', 0)}x, last {v.get('last_mentioned')}; "
                         f"last note: {v.get('last_note', '')}; signal history: {hist or 'none'}")
        return "\n".join(lines) or f"No history for {ticker}."
    except Exception as e:
        return f"History lookup failed for {ticker} ({type(e).__name__})."


def build_tools(ledger: list, store) -> list:
    """Create the tool list bound to this run's ledger and ticker store."""

    @tool
    def get_company_news(ticker: str) -> str:
        """Get the last 2 days of company news for ONE stock ticker (e.g. NVDA), max 5 items,
        each as 'headline — short summary'. Use it to find out why a big mover moved."""
        return company_news_text(ticker)

    @tool
    def recall_ticker_history(ticker: str) -> str:
        """Recall this bot's own memory for ONE stock ticker (e.g. TSLA): its past and open
        BUY/SELL/WATCH signals with entry price, return and outcome, plus the ticker note
        (mention count, last note, recent signal history). Returns 'No history' if empty."""
        return ticker_history_text(ledger, store, ticker)

    return [get_company_news, recall_ticker_history]
