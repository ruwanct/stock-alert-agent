"""
LangGraph pipeline:

  load_memory → gather_data → score_past_signals → analyze → validate
        ↑                                              │   (errors & retries < 1)
        └──────────────────────────────────────────────┘
                              validate → update_memory → deliver → END
"""

import json
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from bot import chains, config, data, delivery, formatters, memory
from bot.schemas import DailyDigest, MarketAlert


class BotState(TypedDict, total=False):
    now: datetime
    session: str
    ledger: list
    digests: dict
    store: Any                 # langgraph InMemoryStore of ticker notes
    watchlist: list
    prices: list
    valid: list
    news: list
    coverage: str
    alert: MarketAlert
    analysis_error: str
    errors: list
    retries: int
    validation_feedback: str
    delivered: dict


# ──────────────────────────────────────────────
# Validation (pure functions — unit tested)
# ──────────────────────────────────────────────
def validate_alert(alert: MarketAlert, quotes: dict) -> list:
    """Return a list of human-readable problems (empty = valid). `quotes`: symbol → quote dict."""
    errors = []
    for m in alert.top_gainers + alert.top_losers:
        if m.ticker not in quotes:
            errors.append(f"Mover {m.ticker} has no price data — use only tickers from the Prices section.")
    for s in alert.signals:
        q = quotes.get(s.ticker)
        if q is None:
            errors.append(f"Signal {s.action} {s.ticker}: ticker not found in fetched prices.")
            continue
        if s.action in ("BUY", "SELL"):
            if not s.entry_price:
                errors.append(f"Signal {s.action} {s.ticker}: missing entry price (live price is ${q['current']:.2f}).")
            elif abs(s.entry_price - q["current"]) / q["current"] * 100 > config.PRICE_TOLERANCE_PCT:
                errors.append(
                    f"Signal {s.action} {s.ticker}: entry ${s.entry_price:.2f} is more than "
                    f"{config.PRICE_TOLERANCE_PCT}% from live price ${q['current']:.2f}."
                )
    return errors


def sanitize_alert(alert: MarketAlert, quotes: dict) -> MarketAlert:
    """Drop invalid signals/movers; overwrite mover price and change with actual quotes."""
    def fix(movers):
        fixed = []
        for m in movers:
            q = quotes.get(m.ticker)
            if q:
                fixed.append(m.model_copy(update={"price": q["current"], "change_pct": round(q.get("change_pct", 0), 2)}))
        return fixed

    good = []
    for s in alert.signals:
        q = quotes.get(s.ticker)
        if q is None:
            continue
        if s.action in ("BUY", "SELL"):
            if not s.entry_price or abs(s.entry_price - q["current"]) / q["current"] * 100 > config.PRICE_TOLERANCE_PCT:
                continue
        good.append(s)
    return alert.model_copy(update={
        "top_gainers": fix(alert.top_gainers),
        "top_losers": fix(alert.top_losers),
        "signals": good,
    })


def _price_lines(prices: list) -> tuple:
    gainers, losers, errors = [], [], []
    for p in prices:
        if "error" in p:
            errors.append(p["symbol"])
            continue
        emoji = "🟢" if p["change_pct"] >= 0 else "🔴"
        line = f"{emoji} {p['symbol']}: ${p['current']:.2f} ({'+' if p['change_pct'] >= 0 else ''}{p['change_pct']:.2f}%)"
        (gainers if p["change_pct"] >= 0 else losers).append((p["change_pct"], line))
    gainers.sort(key=lambda x: x[0], reverse=True)
    losers.sort(key=lambda x: x[0])
    return [l for _, l in gainers] + [l for _, l in losers], errors


# ──────────────────────────────────────────────
# Nodes
# ──────────────────────────────────────────────
def load_memory(state: BotState) -> dict:
    print("\n🧠 Loading memory...")
    now = datetime.now(timezone.utc)
    return {
        "now": now,
        "session": formatters.session_label(now.hour),
        "ledger": memory.load_signals(),
        "digests": memory.load_digests(),
        "store": memory.load_tickers(),
        "retries": 0,
        "errors": [],
        "validation_feedback": "None — first attempt.",
    }


def gather_data(state: BotState) -> dict:
    watchlist = data.build_watchlist()
    prices = data.fetch_prices(watchlist)
    news = data.aggregate_news(config.CORE_WATCHLIST)
    valid = [p for p in prices if "error" not in p]
    coverage = (
        f"Core: {', '.join(config.CORE_WATCHLIST)}\n"
        f"Sectors: {', '.join(config.SECTORS.keys())}\n"
        f"Dynamic: news-trending + big movers (>{config.BIG_MOVER_THRESHOLD}%)\n"
        f"Total tracking: {len(watchlist)} stocks"
    )
    return {"watchlist": watchlist, "prices": prices, "valid": valid, "news": news, "coverage": coverage}


def score_past_signals(state: BotState) -> dict:
    print("\n📈 Scoring past signals...")
    ledger = memory.score_signals(state["ledger"], data.get_price, state["now"])
    return {"ledger": ledger}


def analyze(state: BotState) -> dict:
    print("\n🧠 Claude analysis (LangChain, structured output)...")
    price_lines, errors = _price_lines(state["prices"])
    news_lines = []
    for n in state["news"][:25]:
        line = f"- [{n.get('source', 'Unknown')}] {n.get('headline', '')}"
        if n.get("summary"):
            line += f" — {n['summary'][:80]}"
        news_lines.append(line)
    prices_text = "\n".join(price_lines) + (f"\nNo data: {', '.join(errors)}" if errors else "")

    inputs = {
        "coverage": state["coverage"],
        "prices": prices_text,
        "news": "\n".join(news_lines) or "No news available.",
        "track_record": memory.track_record_prompt(state["ledger"]),
        "recent_digests": memory.recent_digests_text(state["digests"]),
        "weekly_digests": memory.weekly_digests_text(state["digests"]),
        "ticker_notes": memory.ticker_notes_text(state["store"], state["watchlist"]),
        "validation_feedback": state.get("validation_feedback", "None — first attempt."),
    }
    try:
        alert = chains.build_analysis_chain().invoke(inputs)
        if not isinstance(alert, MarketAlert):
            raise ValueError("model did not return a structured MarketAlert")
        print(f"  Generated alert: {len(alert.signals)} signals, "
              f"{len(alert.top_gainers)}+{len(alert.top_losers)} movers")
        return {"alert": alert, "analysis_error": ""}
    except Exception as e:
        print(f"  ⚠ Claude error: {e}")
        # On a retry failure keep the previous (flawed) alert; validate will sanitize it
        keep = state.get("alert") if state.get("retries") else None
        return {"alert": keep, "analysis_error": str(e)[:300]}


def validate(state: BotState) -> dict:
    if not state.get("alert"):
        return {"errors": []}
    quotes = data.valid_quotes()
    errors = validate_alert(state["alert"], quotes)
    if not errors:
        print("  ✅ Validation passed")
        # Movers' price/change are not validated — always pin them to real quotes
        return {"errors": [], "alert": sanitize_alert(state["alert"], quotes)}
    print(f"  ⚠ Validation found {len(errors)} problem(s):")
    for e in errors:
        print(f"     - {e}")
    if state["retries"] < config.MAX_VALIDATION_RETRIES:
        feedback = "Your previous answer had these problems — fix them:\n" + "\n".join(f"- {e}" for e in errors)
        return {"errors": errors, "retries": state["retries"] + 1, "validation_feedback": feedback}
    print("  🧹 Out of retries — dropping invalid items")
    return {"errors": [], "alert": sanitize_alert(state["alert"], quotes)}


def route_after_validate(state: BotState) -> str:
    return "analyze" if state.get("errors") else "update_memory"


def update_memory(state: BotState) -> dict:
    print("\n🧠 Updating memory...")
    alert, now = state.get("alert"), state["now"]
    ledger, digests, store = state["ledger"], state["digests"], state["store"]

    if alert:
        quotes = data.valid_quotes()
        prices = {s: q["current"] for s, q in quotes.items()}
        memory.add_signals(ledger, alert.signals, prices, now)

        try:
            digest = chains.build_digest_chain().invoke({"alert": alert.model_dump_json()})
        except Exception as e:
            print(f"  ⚠ Digest chain failed ({e}), using pulse as summary")
            digest = DailyDigest(summary=alert.pulse, themes=alert.continuing_stories[:5])

        def summarize(text: str) -> str:
            return chains.build_weekly_chain().invoke({"text": text})

        memory.add_digest(
            digests,
            {"at": now.isoformat(timespec="seconds"), "session": state["session"],
             "summary": digest.summary, "themes": digest.themes},
            summarize, now,
        )
        memory.update_tickers(store, alert, now)
    else:
        print("  ⏭ No analysis — skipping signal/digest/ticker updates (scoring still saved)")

    if config.DRY_RUN:
        print("  🧪 DRY_RUN: memory files NOT written")
    else:
        memory.save_signals(ledger)
        memory.save_digests(digests)
        memory.save_tickers(store)
        print("  💾 Memory saved")
    return {"ledger": ledger, "digests": digests}


def deliver(state: BotState) -> dict:
    now, session = state["now"], state["session"]
    valid, news, alert = state["valid"], state["news"], state.get("alert")
    num_up = len([p for p in valid if p.get("change_pct", 0) >= 0])
    num_down = len(valid) - num_up

    if alert:
        args = (alert, state["ledger"], session, now, len(valid), num_up, num_down, news)
        text = formatters.build_telegram(*args)
        embed = formatters.build_discord_embed(*args)
    else:
        price_lines, _ = _price_lines(state["prices"])
        err = state.get("analysis_error", "unknown error")
        text = formatters.build_fallback_text(err, price_lines, session, now)
        embed = formatters.build_discord_fallback(err, price_lines, session, now)

    print("\n📱 Sending alerts...")
    if config.DRY_RUN:
        print("🧪 DRY_RUN — Telegram text:\n" + "-" * 40)
        print(text)
        print("-" * 40 + "\n🧪 DRY_RUN — Discord embed JSON:")
        print(json.dumps(embed, indent=2, ensure_ascii=False))
        return {"delivered": {"telegram": False, "discord": False, "dry_run": True}}

    return {"delivered": {
        "telegram": delivery.send_telegram(text),
        "discord": delivery.send_discord(embed),
        "dry_run": False,
    }}


# ──────────────────────────────────────────────
# Graph
# ──────────────────────────────────────────────
def build_graph():
    g = StateGraph(BotState)
    g.add_node("load_memory", load_memory)
    g.add_node("gather_data", gather_data)
    g.add_node("score_past_signals", score_past_signals)
    g.add_node("analyze", analyze)
    g.add_node("validate", validate)
    g.add_node("update_memory", update_memory)
    g.add_node("deliver", deliver)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "gather_data")
    g.add_edge("gather_data", "score_past_signals")
    g.add_edge("score_past_signals", "analyze")
    g.add_edge("analyze", "validate")
    g.add_conditional_edges("validate", route_after_validate, {"analyze": "analyze", "update_memory": "update_memory"})
    g.add_edge("update_memory", "deliver")
    g.add_edge("deliver", END)
    return g.compile()
