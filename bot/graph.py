"""
LangGraph pipeline:

  load_memory → gather_data → score_past_signals → research ⇄ research_tools
                                                       │ (tool loop, capped)
                                                       ▼
                                  analyze → validate → update_memory → deliver → END
                                     ↑          │   (errors & retries < 1)
                                     └──────────┘
"""

import json
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from bot import chains, config, data, delivery, errors as llm_errors, formatters, memory, storage, tools as research_tools_mod
from bot.schemas import DailyDigest, MarketAlert


class BotState(TypedDict, total=False):
    now: datetime
    session: str
    ledger: list
    digests: dict
    store: Any                 # langgraph InMemoryStore of ticker notes (working copy)
    backend: Any               # storage.JsonBackend / MongoBackend
    writable: bool             # False → memory writes are skipped this run
    compressed: list           # digests folded into weekly summaries this run
    watchlist: list
    prices: list
    valid: list
    news: list
    coverage: str
    alert: MarketAlert
    analysis_error: str        # friendly message (never raw exception text)
    analysis_error_code: str   # credits / auth / rate_limit / overloaded / network / unknown / ""
    research_notes: str        # "" when research is off, failed or found nothing
    research_messages: list    # the research loop's chat history
    research_rounds: int
    research_tool_calls: int
    research_lookups: list     # e.g. ["NVDA (news)", "TSLA (history)"]
    research_pending: bool     # True → the last model message asked for tools
    research_failed: bool
    errors: list
    retries: int
    validation_feedback: str
    delivered: dict
    memory_warning: str        # "" when memory is fine; shown in the alert otherwise


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
    loaded = storage.open_memory()  # never raises: Mongo trouble → read-only JSON snapshot
    return {
        "now": now,
        "session": formatters.session_label(now.hour),
        "backend": loaded.backend,
        "writable": loaded.writable,
        "memory_warning": loaded.warning,
        "ledger": loaded.ledger,
        "digests": loaded.digests,
        "store": loaded.store,
        "compressed": [],
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


def _research_context(state: BotState) -> str:
    """Short prompt: top movers, open signals, 1-2 line track record (no news list)."""
    movers = sorted(state["valid"], key=lambda p: abs(p.get("change_pct", 0)), reverse=True)[:6]
    mover_lines = [f"{p['symbol']} {p['change_pct']:+.1f}% ${p['current']:.2f}" for p in movers]
    st = memory.track_record_stats(state["ledger"])
    open_lines = [f"{s['ticker']} {s['action']} {s['return_pct']:+.1f}%" if s.get("return_pct") is not None
                  else f"{s['ticker']} {s['action']}" for s in st["open"][:8]]
    record = memory._stats_line(st) or "No closed signals yet."
    return ("Top movers today: " + (", ".join(mover_lines) or "none") + "\n"
            "Open signals: " + (", ".join(open_lines) or "none") + "\n"
            "Track record: " + record)


def _tool_label(call: dict) -> str:
    kind = "news" if call["name"] == "get_company_news" else "history"
    return f"{str(call['args'].get('ticker', '?')).upper()} ({kind})"


def _research_step(state: BotState) -> dict:
    tools = research_tools_mod.build_tools(state["ledger"], state["store"])
    messages = list(state.get("research_messages") or chains.research_messages(_research_context(state)))
    rounds = state.get("research_rounds", 0)
    used = state.get("research_tool_calls", 0)
    remaining = config.MAX_TOOL_CALLS - used
    # the last allowed model call never gets tools, so it must produce the notes
    use_tools = remaining > 0 and rounds < config.MAX_RESEARCH_ROUNDS - 1

    resp = chains.build_research_model(tools if use_tools else None).invoke(messages)
    calls = list(getattr(resp, "tool_calls", None) or []) if use_tools else []
    if calls:
        kept = calls[:remaining]  # hard cap: calls beyond the budget are never executed
        if len(kept) < len(calls):
            print(f"  🔎 Research: tool cap reached, ignoring {len(calls) - len(kept)} extra call(s)")
        resp = AIMessage(content=resp.text, tool_calls=kept, id=getattr(resp, "id", None))
        return {"research_messages": messages + [resp], "research_rounds": rounds + 1,
                "research_tool_calls": used + len(kept), "research_pending": True}

    notes = (resp.text or "").strip()[:800]
    lookups = state.get("research_lookups", [])
    n = len([l for l in notes.splitlines() if l.strip()])
    print(f"  🔎 Research: looked up {', '.join(lookups) or 'nothing'} — {n} notes")
    return {"research_messages": messages + [resp], "research_rounds": rounds + 1,
            "research_notes": notes, "research_pending": False}


def research(state: BotState) -> dict:
    print("\n🔎 Research agent...")
    if not config.RESEARCH_ENABLED:
        print("  ⏭ RESEARCH_ENABLED is off — skipping")
        return {"research_notes": "", "research_pending": False}
    try:
        return _research_step(state)
    except Exception as e:  # research must never block the alert
        code, _ = llm_errors.classify_llm_error(e)
        print(f"  ⚠ Research failed [{code}] ({type(e).__name__}: {str(e)[:200]}) — continuing without notes")
        return {"research_notes": "", "research_pending": False, "research_failed": True}


def research_tools(state: BotState) -> dict:
    """Run the requested tool calls (tools bound to this run's memory).

    Each call is executed with `tool.invoke(tool_call)`, which returns a ToolMessage
    linked to the call id. (langgraph's ToolNode can't be invoked by hand inside
    another node: it needs graph runtime config, and the tools only exist per run.)
    """
    try:
        by_name = {t.name: t for t in research_tools_mod.build_tools(state["ledger"], state["store"])}
        last = state["research_messages"][-1]
        results = []
        for call in last.tool_calls:
            t = by_name.get(call["name"])
            if t is None:
                results.append(ToolMessage(content=f"Unknown tool {call['name']}.", tool_call_id=call["id"]))
            else:
                results.append(t.invoke({**call, "type": "tool_call"}))
        labels = [_tool_label(c) for c in last.tool_calls]
        print(f"  🔧 Tools run: {', '.join(labels)}")
        return {"research_messages": state["research_messages"] + results,
                "research_lookups": state.get("research_lookups", []) + labels}
    except Exception as e:
        print(f"  ⚠ Research tools failed ({type(e).__name__}: {str(e)[:200]}) — continuing without notes")
        return {"research_notes": "", "research_pending": False, "research_failed": True}


def route_after_research(state: BotState) -> str:
    if state.get("research_failed") or not state.get("research_pending"):
        return "analyze"
    return "research_tools"


def route_after_tools(state: BotState) -> str:
    return "analyze" if state.get("research_failed") else "research"


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
        "research_notes": state.get("research_notes") or "None",
        "validation_feedback": state.get("validation_feedback", "None — first attempt."),
    }
    try:
        alert = chains.build_analysis_chain().invoke(inputs)
        if not isinstance(alert, MarketAlert):
            raise ValueError("model did not return a structured MarketAlert")
        print(f"  Generated alert: {len(alert.signals)} signals, "
              f"{len(alert.top_gainers)}+{len(alert.top_losers)} movers")
        return {"alert": alert, "analysis_error": "", "analysis_error_code": ""}
    except Exception as e:
        code, friendly = llm_errors.classify_llm_error(e)
        print(f"  ⚠ Claude error [{code}] ({type(e).__name__}): {str(e)[:500]}")
        # On a retry failure keep the previous (flawed) alert; validate will sanitize it.
        # A first-call failure leaves no alert, so validate never loops back (no retry on credits/auth).
        keep = state.get("alert") if state.get("retries") else None
        return {"alert": keep, "analysis_error": friendly, "analysis_error_code": code}


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
    backend, compressed = state["backend"], state.get("compressed", [])

    if alert:
        quotes = data.valid_quotes()
        prices = {s: q["current"] for s, q in quotes.items()}
        memory.add_signals(ledger, alert.signals, prices, now)

        try:
            digest = chains.build_digest_chain().invoke({"alert": alert.model_dump_json()})
        except Exception as e:
            code, _ = llm_errors.classify_llm_error(e)
            print(f"  ⚠ Digest chain failed [{code}] ({type(e).__name__}: {str(e)[:200]}), using pulse as summary")
            digest = DailyDigest(summary=alert.pulse, themes=alert.continuing_stories[:5])

        def summarize(text: str) -> str:
            try:
                return chains.build_weekly_chain().invoke({"text": text})
            except Exception as e:
                code, _ = llm_errors.classify_llm_error(e)
                print(f"  ⚠ Weekly summary failed [{code}] ({type(e).__name__}: {str(e)[:200]})")
                raise

        memory.add_digest(
            digests,
            {"at": now.isoformat(timespec="seconds"), "session": state["session"],
             "summary": digest.summary, "themes": digest.themes},
            summarize, now, compressed_out=compressed,
        )
        memory.update_tickers(store, alert, now)
    else:
        print("  ⏭ No analysis — skipping signal/digest/ticker updates (scoring still saved)")

    warning = state.get("memory_warning", "")
    try:
        if config.DRY_RUN:
            print("  🧪 DRY_RUN: memory NOT written")
        elif not state.get("writable", True):
            print("  ⏭ Memory is read-only this run (backend fallback) — nothing written")
        else:
            failed = []
            for label, save in (
                ("signals", lambda: backend.save_signals(ledger)),
                ("digests", lambda: backend.save_digests(digests, compressed)),
                ("tickers", lambda: backend.save_tickers(store)),
            ):
                try:
                    save()
                except Exception as e:  # a failed save must never block delivery
                    failed.append(label)
                    print(f"  ⚠ Saving {label} to {backend.name} failed ({type(e).__name__}: {str(e)[:200]})")
            if failed:
                warning = f"⚠️ Memory partially saved: {', '.join(failed)} failed"
            print(f"  💾 Memory saved ({backend.name})" if not failed
                  else f"  ⚠ Memory partially saved ({backend.name}); failed: {', '.join(failed)}")
    finally:
        backend.close()
    return {"ledger": ledger, "digests": digests, "memory_warning": warning}


def deliver(state: BotState) -> dict:
    now, session = state["now"], state["session"]
    valid, news, alert = state["valid"], state["news"], state.get("alert")
    num_up = len([p for p in valid if p.get("change_pct", 0) >= 0])
    num_down = len(valid) - num_up

    warning = state.get("memory_warning", "")
    if alert:
        args = (alert, state["ledger"], session, now, len(valid), num_up, num_down, news)
        text = formatters.build_telegram(*args, memory_warning=warning)
        embed = formatters.build_discord_embed(*args, memory_warning=warning)
    else:
        price_lines, _ = _price_lines(state["prices"])
        err = state.get("analysis_error") or llm_errors.MESSAGES[llm_errors.UNKNOWN]
        text = formatters.build_fallback_text(err, price_lines, session, now, warning)
        embed = formatters.build_discord_fallback(err, price_lines, session, now, warning)

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
    g.add_node("research", research)
    g.add_node("research_tools", research_tools)
    g.add_node("analyze", analyze)
    g.add_node("validate", validate)
    g.add_node("update_memory", update_memory)
    g.add_node("deliver", deliver)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "gather_data")
    g.add_edge("gather_data", "score_past_signals")
    g.add_edge("score_past_signals", "research")
    g.add_conditional_edges("research", route_after_research, {"research_tools": "research_tools", "analyze": "analyze"})
    g.add_conditional_edges("research_tools", route_after_tools, {"research": "research", "analyze": "analyze"})
    g.add_edge("analyze", "validate")
    g.add_conditional_edges("validate", route_after_validate, {"analyze": "analyze", "update_memory": "update_memory"})
    g.add_edge("update_memory", "deliver")
    g.add_edge("deliver", END)
    return g.compile()
