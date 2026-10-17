"""
Persistent memory — signal ledger, digests, per-ticker notes (JSON on disk).

Everything here is defensive: missing file → empty memory, corrupt JSON →
warning + empty memory. Memory problems must never crash a run.
"""

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from langgraph.store.memory import InMemoryStore

from bot import config

TICKER_NS = ("tickers",)


# ──────────────────────────────────────────────
# JSON helpers
# ──────────────────────────────────────────────
def _load_json(path: Path, expected: type, default):
    path = Path(path)
    if not path.exists():
        print(f"  🧠 {path.name}: not found, starting empty")
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠ {path.name}: invalid JSON ({e}) — starting empty, file will be replaced on save")
        return default
    if not isinstance(data, expected):
        print(f"  ⚠ {path.name}: unexpected structure ({type(data).__name__}) — starting empty")
        return default
    return data


def _save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts, fallback: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return fallback


# ──────────────────────────────────────────────
# Signal ledger
# ──────────────────────────────────────────────
def load_signals(path: Path = None) -> list:
    data = _load_json(path or config.SIGNALS_PATH, list, [])
    ledger = [s for s in data if isinstance(s, dict) and s.get("ticker") and s.get("action")]
    if ledger:
        open_n = len([s for s in ledger if s.get("status") == "open"])
        print(f"  🧠 Ledger: {len(ledger)} signals ({open_n} open)")
    return ledger


def save_signals(ledger: list, path: Path = None) -> None:
    _save_json(path or config.SIGNALS_PATH, ledger)


def _return_pct(action: str, entry: float, price: float) -> float:
    pct = (price - entry) / entry * 100
    return round(-pct if action == "SELL" else pct, 2)


def _outcome(return_pct: Optional[float]) -> Optional[str]:
    if return_pct is None:
        return None
    if return_pct > config.WIN_THRESHOLD_PCT:
        return "win"
    if return_pct < -config.WIN_THRESHOLD_PCT:
        return "loss"
    return "flat"


def _close(sig: dict, price: Optional[float], now: datetime) -> None:
    if price is not None and sig.get("entry_price") and sig["action"] in ("BUY", "SELL"):
        sig["last_price"] = round(price, 2)
        sig["return_pct"] = _return_pct(sig["action"], sig["entry_price"], price)
    sig["status"] = "closed"
    sig["closed_at"] = iso(now)
    sig["outcome"] = _outcome(sig.get("return_pct")) if sig["action"] in ("BUY", "SELL") else None


def find_open(ledger: list, ticker: str, action: str = None) -> Optional[dict]:
    for s in ledger:
        if s.get("status") == "open" and s["ticker"] == ticker and (action is None or s["action"] == action):
            return s
    return None


def add_signals(ledger: list, signals: list, prices: dict, now: datetime) -> list:
    """
    Record this run's signals. Same open ticker+action → reaffirmed += 1.
    BUY vs SELL reversal → old signal is closed at the current price.
    `signals` are schemas.Signal; `prices` maps ticker → current price.
    """
    for sig in signals:
        existing = find_open(ledger, sig.ticker, sig.action)
        if existing:
            existing["reaffirmed"] = existing.get("reaffirmed", 0) + 1
            continue
        opposite = {"BUY": "SELL", "SELL": "BUY"}.get(sig.action)
        if opposite:
            old = find_open(ledger, sig.ticker, opposite)
            if old:
                _close(old, prices.get(sig.ticker, sig.entry_price), now)
                print(f"  🔄 {sig.ticker}: {old['action']} closed (reversed to {sig.action})")
        ledger.append({
            "id": uuid.uuid4().hex[:8],
            "ticker": sig.ticker,
            "action": sig.action,
            "entry_price": sig.entry_price,
            "confidence": sig.confidence,
            "reasoning": sig.reasoning[:300],
            "opened_at": iso(now),
            "status": "open",
            "reaffirmed": 0,
            "last_price": sig.entry_price,
            "return_pct": 0.0 if sig.action in ("BUY", "SELL") else None,
            "closed_at": None,
            "outcome": None,
        })
    return ledger


def score_signals(ledger: list, price_fn: Callable, now: datetime) -> list:
    """Update open signals with live prices; close those past the horizon."""
    scored = 0
    for sig in ledger:
        if sig.get("status") != "open":
            continue
        age_days = (now - _parse(sig.get("opened_at"), now)).days
        price = None
        if sig["action"] in ("BUY", "SELL") and sig.get("entry_price"):
            try:
                price = price_fn(sig["ticker"])
            except Exception:
                price = None
            if price:
                sig["last_price"] = round(price, 2)
                sig["return_pct"] = _return_pct(sig["action"], sig["entry_price"], price)
                scored += 1
        if age_days >= config.SIGNAL_HORIZON_DAYS:
            _close(sig, price, now)
    if scored:
        print(f"  📈 Scored {scored} open signals")
    return ledger


def _closed_recent(ledger: list) -> list:
    closed = [s for s in ledger if s.get("status") == "closed" and s["action"] in ("BUY", "SELL")]
    closed.sort(key=lambda s: s.get("closed_at") or "")
    return closed[-config.TRACK_RECORD_CLOSED:]


def track_record_stats(ledger: list) -> dict:
    closed = _closed_recent(ledger)
    open_sigs = [s for s in ledger if s.get("status") == "open" and s["action"] in ("BUY", "SELL")]

    def hit(action):
        subset = [s for s in closed if s["action"] == action]
        if not subset:
            return None, 0, 0
        wins = len([s for s in subset if s.get("outcome") == "win"])
        return wins / len(subset) * 100, wins, len(subset)

    buy_rate, buy_w, buy_n = hit("BUY")
    sell_rate, sell_w, sell_n = hit("SELL")
    rets = [s["return_pct"] for s in closed if s.get("return_pct") is not None]
    return {
        "closed": closed,
        "open": open_sigs,
        "buy_hit_rate": buy_rate, "buy_wins": buy_w, "buy_total": buy_n,
        "sell_hit_rate": sell_rate, "sell_wins": sell_w, "sell_total": sell_n,
        "avg_return": sum(rets) / len(rets) if rets else None,
    }


def _mark(ret: Optional[float]) -> str:
    if ret is None:
        return "➖"
    return "✅" if ret > config.WIN_THRESHOLD_PCT else "❌" if ret < -config.WIN_THRESHOLD_PCT else "➖"


def _sig_line(s: dict) -> str:
    ret = s.get("return_pct")
    when = _parse(s.get("opened_at"), datetime.now(timezone.utc)).strftime("%a")
    entry = f" @ ${s['entry_price']:.2f}" if s.get("entry_price") else ""
    ret_txt = f"{ret:+.1f}%" if ret is not None else "n/a"
    return f"{s['ticker']} {s['action']}{entry} ({when}) → {ret_txt} {_mark(ret)}"


def _stats_line(st: dict) -> str:
    parts = []
    if st["buy_total"]:
        parts.append(f"BUY {st['buy_hit_rate']:.0f}% ({st['buy_wins']}/{st['buy_total']})")
    if st["sell_total"]:
        parts.append(f"SELL {st['sell_hit_rate']:.0f}% ({st['sell_wins']}/{st['sell_total']})")
    if not parts:
        return ""
    avg = f" | avg {st['avg_return']:+.1f}%" if st["avg_return"] is not None else ""
    return "Hit rate: " + " | ".join(parts) + avg


def track_record_prompt(ledger: list) -> str:
    st = track_record_stats(ledger)
    if not st["open"] and not st["closed"]:
        return "No past signals yet (first runs)."
    lines = []
    if st["open"]:
        lines.append("Open signals:")
        for s in st["open"]:
            extra = f" [reaffirmed x{s['reaffirmed']}]" if s.get("reaffirmed") else ""
            lines.append(f"- {_sig_line(s)}{extra}")
    if st["closed"]:
        lines.append("Recently closed:")
        for s in st["closed"][-10:]:
            lines.append(f"- {_sig_line(s)} [{s.get('outcome')}] why: {s.get('reasoning', '')[:80]}")
    stats = _stats_line(st)
    if stats:
        lines.append(stats)
    return "\n".join(lines)


def track_record_alert_lines(ledger: list, max_open: int = 4, max_closed: int = 4,
                             exclude_opened_at: str = None) -> list:
    """
    Lines for the alert's TRACK RECORD section. Empty list → omit section.
    `exclude_opened_at` hides signals opened this run (already shown under SIGNALS).
    """
    st = track_record_stats(ledger)
    open_sigs = [s for s in st["open"] if s.get("opened_at") != exclude_opened_at]
    if not open_sigs and not st["closed"]:
        return []
    lines = [_sig_line(s) for s in open_sigs[-max_open:]]
    lines += [_sig_line(s) for s in st["closed"][-max_closed:]]
    stats = _stats_line(st)
    if stats:
        lines.append(stats)
    return lines


# ──────────────────────────────────────────────
# Digests (recent + weekly compression)
# ──────────────────────────────────────────────
def load_digests(path: Path = None) -> dict:
    data = _load_json(path or config.DIGESTS_PATH, dict, {})
    recent = data.get("recent") if isinstance(data.get("recent"), list) else []
    weekly = data.get("weekly") if isinstance(data.get("weekly"), list) else []
    return {"recent": [d for d in recent if isinstance(d, dict)],
            "weekly": [w for w in weekly if isinstance(w, dict)]}


def save_digests(digests: dict, path: Path = None) -> None:
    _save_json(path or config.DIGESTS_PATH, digests)


def _iso_week(ts: str, fallback: datetime) -> str:
    y, w, _ = _parse(ts, fallback).isocalendar()
    return f"{y}-W{w:02d}"


def fallback_week_summary(text: str) -> str:
    return text[:400]


def add_digest(digests: dict, digest: dict, summarize: Callable, now: datetime) -> dict:
    """
    Append a digest {at, session, summary, themes}. Beyond MAX_RECENT_DIGESTS the
    oldest are compressed (grouped by ISO week) into weekly summaries via
    `summarize(text) -> str`; if that fails, themes are concatenated instead.
    """
    digests["recent"].append(digest)
    overflow = len(digests["recent"]) - config.MAX_RECENT_DIGESTS
    if overflow > 0:
        old, digests["recent"] = digests["recent"][:overflow], digests["recent"][overflow:]
        by_week = defaultdict(list)
        for d in old:
            by_week[_iso_week(d.get("at"), now)].append(d)
        weekly = {w["week"]: w for w in digests["weekly"] if "week" in w}
        for week, entries in sorted(by_week.items()):
            parts = [weekly[week]["summary"]] if week in weekly else []
            parts += [f"{d.get('summary', '')} (themes: {', '.join(d.get('themes', []))})" for d in entries]
            text = "\n".join(parts)
            try:
                summary = summarize(text)
                if not summary or not str(summary).strip():
                    raise ValueError("empty summary")
            except Exception as e:
                print(f"  ⚠ Weekly summary failed ({e}), using themes fallback")
                themes = []
                if week in weekly:
                    themes.append(weekly[week]["summary"])
                for d in entries:
                    themes.extend(d.get("themes", []))
                summary = fallback_week_summary("; ".join(dict.fromkeys(themes)) or text)
            weekly[week] = {"week": week, "summary": str(summary).strip()}
        digests["weekly"] = sorted(weekly.values(), key=lambda w: w["week"])[-config.MAX_WEEKLY_DIGESTS:]
    return digests


def recent_digests_text(digests: dict) -> str:
    if not digests["recent"]:
        return "None yet."
    return "\n".join(
        f"- [{d.get('at', '?')[:16]} {d.get('session', '')}] {d.get('summary', '')}"
        for d in digests["recent"][-8:]
    )


def weekly_digests_text(digests: dict) -> str:
    if not digests["weekly"]:
        return "None yet."
    return "\n".join(f"- {w['week']}: {w['summary']}" for w in digests["weekly"])


# ──────────────────────────────────────────────
# Per-ticker notes (LangGraph InMemoryStore ⇄ JSON)
# ──────────────────────────────────────────────
def load_tickers(path: Path = None) -> InMemoryStore:
    store = InMemoryStore()
    data = _load_json(path or config.TICKERS_PATH, dict, {})
    for ticker, note in data.items():
        if isinstance(note, dict):
            store.put(TICKER_NS, ticker, note)
    return store


def dump_tickers(store: InMemoryStore) -> dict:
    return {item.key: item.value for item in store.search(TICKER_NS, limit=100000)}


def save_tickers(store: InMemoryStore, path: Path = None) -> None:
    _save_json(path or config.TICKERS_PATH, dict(sorted(dump_tickers(store).items())))


def update_tickers(store: InMemoryStore, alert, now: datetime) -> None:
    """Update entity notes from this run's signals and movers (one mention per ticker per run)."""
    today = now.strftime("%Y-%m-%d")
    notes = {}  # ticker → (note text, signal or None)
    for m in list(alert.top_gainers) + list(alert.top_losers):
        notes[m.ticker] = (m.reason, None)
    for s in alert.signals:
        notes[s.ticker] = (s.reasoning, s)
    for ticker, (text, sig) in notes.items():
        item = store.get(TICKER_NS, ticker)
        note = dict(item.value) if item else {}
        history = list(note.get("signal_history", []))
        if sig is not None:
            history.append({"date": today, "action": sig.action, "price": sig.entry_price})
        store.put(TICKER_NS, ticker, {
            "signal_history": history[-config.MAX_TICKER_HISTORY:],
            "mention_count": note.get("mention_count", 0) + 1,
            "last_mentioned": today,
            "last_note": text[:config.MAX_TICKER_NOTE_CHARS],
        })


def ticker_notes_text(store: InMemoryStore, symbols: list, limit: int = 12) -> str:
    rows = []
    for sym in symbols:
        item = store.get(TICKER_NS, sym)
        if item:
            rows.append((item.value.get("mention_count", 0), sym, item.value))
    if not rows:
        return "None yet."
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = []
    for _, sym, v in rows[:limit]:
        hist = ", ".join(f"{h['action']}@{h.get('price')} {h['date']}" for h in v.get("signal_history", [])[-3:])
        lines.append(f"- {sym}: mentioned {v.get('mention_count', 0)}x, last {v.get('last_mentioned')}; "
                     f"history: {hist or 'none'}; note: {v.get('last_note', '')}")
    return "\n".join(lines)
