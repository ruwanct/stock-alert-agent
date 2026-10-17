"""Formatter tests — Telegram cap, omitted sections, Discord limits."""

import unittest
from datetime import datetime, timedelta, timezone

from bot import memory
from bot.formatters import (DISCORD_DESC_LIMIT, DISCORD_FIELD_LIMIT, DISCORD_TOTAL_LIMIT,
                            build_discord_embed, build_telegram, berlin_time)
from bot.schemas import MarketAlert, Mover, SectorHeat, Signal

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
NEWS = [{"source": "Finnhub/Reuters", "headline": "x"}, {"source": "Google/CNBC", "headline": "y"}]


def minimal():
    return MarketAlert(pulse="Markets up", bottom_line="Hold steady")


def full(n=1, text="word " * 60):
    movers = [Mover(ticker=f"T{i}", change_pct=1.5, price=10.0, reason=text) for i in range(3 * n)]
    sigs = [Signal(ticker=f"T{i}", action="BUY", entry_price=10.0, confidence="HIGH", reasoning=text, sources=["Reuters"])
            for i in range(3 * n)]
    return MarketAlert(
        pulse=text, top_gainers=movers, top_losers=movers, signals=sigs,
        key_news=[text] * (5 * n), sectors=[SectorHeat(sector=f"S{i}", direction="up") for i in range(n * 5)],
        continuing_stories=[text] * (3 * n), lessons=text * n, bottom_line=text,
    )


def ledger_with_history():
    ledger = []
    from bot.schemas import Signal as S
    memory.add_signals(ledger, [S(ticker="NVDA", action="BUY", entry_price=100, confidence="HIGH", reasoning="r")], {}, NOW - timedelta(days=2))
    memory.score_signals(ledger, lambda t: 104, NOW)
    ledger[0]["reaffirmed"] = 2
    return ledger


class TelegramTests(unittest.TestCase):
    def tg(self, alert, ledger=None):
        return build_telegram(alert, ledger or [], "🌅 PRE-MARKET", NOW, 30, 20, 10, NEWS)

    def test_empty_sections_omitted(self):
        t = self.tg(minimal())
        for header in ("TRACK RECORD", "CONTINUING STORIES", "LESSONS", "KEY NEWS", "SIGNALS", "TOP MOVERS"):
            self.assertNotIn(header, t)
        self.assertIn("MARKET PULSE", t)
        self.assertIn("BOTTOM LINE", t)
        self.assertIn("STOCK ALERT BOT v6", t)

    def test_length_cap(self):
        t = self.tg(full(n=4))
        self.assertLessEqual(len(t), 4000)

    def test_no_markdown_chars(self):
        a = MarketAlert(pulse="*bold* and_under", bottom_line="a_b*c")
        t = self.tg(a)
        self.assertNotIn("*", t)
        self.assertNotIn("_", t)

    def test_track_record_and_reaffirmed(self):
        ledger = ledger_with_history()
        a = MarketAlert(pulse="p", bottom_line="b", signals=[
            Signal(ticker="NVDA", action="BUY", entry_price=104.0, confidence="HIGH", reasoning="r")])
        t = self.tg(a, ledger)
        self.assertIn("TRACK RECORD", t)
        self.assertIn("✅", t)
        self.assertIn("reaffirmed ×2", t)

    def test_track_record_hides_signals_opened_this_run(self):
        ledger = []
        memory.add_signals(ledger, [Signal(ticker="AMD", action="BUY", entry_price=50, confidence="LOW", reasoning="r")], {}, NOW)
        self.assertNotIn("TRACK RECORD", self.tg(minimal(), ledger))

    def test_optional_sections_present(self):
        a = MarketAlert(pulse="p", bottom_line="b", continuing_stories=["defense rally day 4"], lessons="Avoid chasing")
        t = self.tg(a)
        self.assertIn("CONTINUING STORIES", t)
        self.assertIn("LESSONS", t)

    def test_berlin_dst(self):
        self.assertEqual(berlin_time(NOW), "12:00")  # October: CEST, UTC+2
        self.assertEqual(berlin_time(datetime(2026, 1, 14, 10, 0, tzinfo=timezone.utc)), "11:00")


class DiscordTests(unittest.TestCase):
    def check_limits(self, embed):
        self.assertLessEqual(len(embed["description"]), DISCORD_DESC_LIMIT)
        for f in embed["fields"]:
            self.assertLessEqual(len(f["value"]), DISCORD_FIELD_LIMIT)
        total = len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
        total += sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
        self.assertLessEqual(total, DISCORD_TOTAL_LIMIT)

    def test_limits_when_huge(self):
        e = build_discord_embed(full(n=6, text="word " * 200), ledger_with_history(), "s", NOW, 30, 20, 10, NEWS)
        self.check_limits(e)
        self.assertTrue(any(f["name"].startswith("📊") for f in e["fields"]))

    def test_small_alert_and_color(self):
        e = build_discord_embed(minimal(), [], "s", NOW, 30, 20, 10, NEWS)
        self.check_limits(e)
        self.assertEqual(e["color"], 0x2ECC71)
        self.assertEqual(build_discord_embed(minimal(), [], "s", NOW, 30, 10, 20, NEWS)["color"], 0xE74C3C)
        self.assertEqual(build_discord_embed(minimal(), [], "s", NOW, 30, 15, 15, NEWS)["color"], 0xF1C40F)
        self.assertIn("v6", e["title"])


if __name__ == "__main__":
    unittest.main()
