"""
Formatters — Telegram plain text and Discord rich embed from a MarketAlert.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot import config, memory
from bot.schemas import MarketAlert

VERSION = "v6"
LINE = "━" * 20
BOX = "═" * 28
TELEGRAM_LIMIT = 4000

DISCORD_DESC_LIMIT = 4096
DISCORD_FIELD_LIMIT = 1024
DISCORD_TOTAL_LIMIT = 6000


def session_label(utc_hour: int) -> str:
    if utc_hour < 9:
        return "🌅 PRE-MARKET"
    elif utc_hour < 16:
        return "🔔 MARKET OPEN"
    return "🌙 AFTER-HOURS"


def schedule_text() -> str:
    if config.ALERTS_PER_DAY == 1:
        return "Daily alert · Mon–Fri"
    return f"{config.ALERTS_PER_DAY} alerts per weekday · Mon–Fri"


def berlin_time(now: datetime) -> str:
    try:
        tz = ZoneInfo("Europe/Berlin")
    except Exception:  # tzdata missing
        tz = timezone(timedelta(hours=1))
    return now.astimezone(tz).strftime("%H:%M")


def _clean(text: str) -> str:
    return str(text).replace("*", "").replace("_", " ")


def _trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _mover_line(m) -> str:
    arrow = "▲" if m.change_pct >= 0 else "▼"
    return f"{arrow} {m.ticker} {m.change_pct:+.2f}% (${m.price:.2f}) — {_clean(m.reason)}"


def _signal_lines(alert: MarketAlert, ledger: list) -> list:
    icons = {"BUY": "🟢", "SELL": "🔴", "WATCH": "🟡"}
    lines = []
    for s in alert.signals:
        price = f" @ ${s.entry_price:.2f}" if s.entry_price else ""
        conf = f" [{s.confidence}]" if s.action != "WATCH" else ""
        lines.append(f"{icons[s.action]} {s.action}: {s.ticker}{price}{conf}")
        open_sig = memory.find_open(ledger, s.ticker, s.action)
        if open_sig and open_sig.get("reaffirmed"):
            lines.append(f"   🔁 reaffirmed ×{open_sig['reaffirmed']}")
        src = f" ({', '.join(s.sources)})" if s.sources else ""
        lines.append(f"   → {_clean(s.reasoning)}{_clean(src)}")
        lines.append("")
    return lines[:-1] if lines else lines


def _sector_text(alert: MarketAlert) -> str:
    icon = {"up": "🟩", "down": "🟥", "flat": "🟨"}
    return " | ".join(f"{icon[x.direction]} {_clean(x.sector)}" for x in alert.sectors)


def build_telegram(alert: MarketAlert, ledger: list, session: str, now: datetime,
                   num_valid: int, num_up: int, num_down: int, news: list,
                   memory_warning: str = "") -> str:
    sources = sorted(set(n.get("source", "").split("/")[0] for n in news if n.get("source")))
    header = (
        f"{BOX}\n   🤖 STOCK ALERT BOT {VERSION}\n   {session}\n{BOX}\n"
        f"🕐 {berlin_time(now)} Berlin | {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"📋 {num_valid} stocks across {len(config.SECTORS)} sectors\n"
        f"🟢 {num_up} up | 🔴 {num_down} down\n"
        f"📰 {len(news)} news from {', '.join(sources)}\n{BOX}\n"
    )

    if memory_warning:
        header += f"{memory_warning}\n{BOX}\n"

    sections = [f"🌡 MARKET PULSE: {_clean(alert.pulse)}"]

    movers = [_mover_line(m) for m in alert.top_gainers + alert.top_losers]
    if movers:
        sections.append("📊 TOP MOVERS\n" + "\n".join(movers))
    if alert.signals:
        sections.append("🎯 SIGNALS\n\n" + "\n".join(_signal_lines(alert, ledger)))

    record = memory.track_record_alert_lines(ledger, exclude_opened_at=memory.iso(now))
    if record:
        sections.append("📈 TRACK RECORD\n" + "\n".join(record))
    if alert.continuing_stories:
        sections.append("🔁 CONTINUING STORIES\n" + "\n".join(f"• {_clean(s)}" for s in alert.continuing_stories))
    if alert.key_news:
        sections.append("📰 KEY NEWS\n" + "\n".join(f"• {_clean(n)}" for n in alert.key_news))
    if alert.sectors:
        sections.append("🏢 SECTOR HEAT MAP\n" + _sector_text(alert))
    if alert.lessons.strip():
        sections.append("🧠 LESSONS\n" + _clean(alert.lessons.strip()))
    sections.append("⚡ BOTTOM LINE\n" + _clean(alert.bottom_line))

    footer = (
        f"\n{BOX}\n  🔄 {schedule_text()}\n"
        f"  📡 Finnhub + Google News + Claude AI (LangChain + LangGraph)\n{BOX}"
    )
    text = header + "\n" + f"\n\n{LINE}\n\n".join(sections) + "\n" + footer
    if len(text) > TELEGRAM_LIMIT:
        text = text[:TELEGRAM_LIMIT - 50] + "\n\n... (truncated)"
    return text


def build_fallback_text(error: str, price_lines: list, session: str, now: datetime,
                        memory_warning: str = "") -> str:
    text = (
        f"{BOX}\n   🤖 STOCK ALERT BOT {VERSION}\n   {session}\n{BOX}\n"
        f"🕐 {berlin_time(now)} Berlin | {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        + (f"{memory_warning}\n\n" if memory_warning else "") +
        f"{error}\n\n📊 Prices:\n" + "\n".join(price_lines[:10])
    )
    return text[:TELEGRAM_LIMIT]


def _embed_size(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for f in embed.get("fields", []):
        total += len(f["name"]) + len(f["value"])
    return total


def build_discord_embed(alert: MarketAlert, ledger: list, session: str, now: datetime,
                        num_valid: int, num_up: int, num_down: int, news: list,
                        memory_warning: str = "") -> dict:
    sources = sorted(set(n.get("source", "").split("/")[0] for n in news if n.get("source")))

    if num_up > num_down:
        color = 0x2ECC71
    elif num_down > num_up:
        color = 0xE74C3C
    else:
        color = 0xF1C40F

    body = [f"🌡 {_clean(alert.pulse)}", ""]
    body += [_mover_line(m) for m in alert.top_gainers + alert.top_losers]
    if alert.signals:
        body += ["", *_signal_lines(alert, ledger)]
    inner_limit = DISCORD_DESC_LIMIT - 8  # room for the ``` fences
    description = f"```\n{_trunc(chr(10).join(body), inner_limit)}\n```"

    fields = []
    if memory_warning:
        fields.append({"name": "⚠️ Memory", "value": _trunc(memory_warning, DISCORD_FIELD_LIMIT), "inline": False})
    record = memory.track_record_alert_lines(ledger, exclude_opened_at=memory.iso(now))
    if record:
        fields.append({"name": "📈 Track Record", "value": _trunc("\n".join(record), DISCORD_FIELD_LIMIT), "inline": False})
    if alert.continuing_stories:
        fields.append({"name": "🔁 Continuing Stories",
                       "value": _trunc("\n".join(f"• {_clean(s)}" for s in alert.continuing_stories), DISCORD_FIELD_LIMIT),
                       "inline": False})
    if alert.sectors:
        fields.append({"name": "🏢 Sectors", "value": _trunc(_sector_text(alert), DISCORD_FIELD_LIMIT), "inline": False})
    if alert.key_news:
        fields.append({"name": "📰 Key News",
                       "value": _trunc("\n".join(f"• {_clean(n)}" for n in alert.key_news), DISCORD_FIELD_LIMIT),
                       "inline": False})
    if alert.lessons.strip():
        fields.append({"name": "🧠 Lessons", "value": _trunc(_clean(alert.lessons.strip()), DISCORD_FIELD_LIMIT), "inline": False})
    fields.append({
        "name": "📊 Market Snapshot",
        "value": _trunc(f"**{num_valid}** stocks tracked  •  🟢 **{num_up}** up  •  🔴 **{num_down}** down\n"
                        f"📰 {', '.join(sources) if sources else 'N/A'}", DISCORD_FIELD_LIMIT),
        "inline": False,
    })

    embed = {
        "title": f"🤖 Stock Alert Bot {VERSION}  |  {session}",
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Finnhub + Google News + Claude AI (LangChain + LangGraph)  •  {now.strftime('%Y-%m-%d %H:%M UTC')}"},
    }

    # Discord caps the whole embed at 6000 chars (and 25 fields): shrink description first.
    excess = _embed_size(embed) - DISCORD_TOTAL_LIMIT
    if excess > 0:
        keep = max(200, inner_limit - excess - 50)
        embed["description"] = f"```\n{_trunc(chr(10).join(body), keep)}\n```"
    protected = 2 if memory_warning else 1  # the warning (first) and the snapshot (last) are kept
    while _embed_size(embed) > DISCORD_TOTAL_LIMIT and len(embed["fields"]) > protected:
        embed["fields"].pop(-2)  # drop lowest-priority field
    return embed


def build_discord_fallback(error: str, price_lines: list, session: str, now: datetime,
                           memory_warning: str = "") -> dict:
    text = (f"{memory_warning}\n\n" if memory_warning else "") + f"{error}\n\n📊 Prices:\n" + "\n".join(price_lines[:10])
    return {
        "title": f"🤖 Stock Alert Bot {VERSION}  |  {session}",
        "description": f"```\n{_trunc(text, DISCORD_DESC_LIMIT - 8)}\n```",
        "color": 0xF1C40F,
        "footer": {"text": f"Finnhub + Claude AI  •  {now.strftime('%Y-%m-%d %H:%M UTC')}"},
    }
