"""Memory, scoring and validation tests — stdlib unittest, no network, no API."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import config, memory
from bot.graph import sanitize_alert, validate_alert
from bot.schemas import MarketAlert, Mover, Signal

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def sig(ticker="NVDA", action="BUY", price=100.0, conf="HIGH"):
    return Signal(ticker=ticker, action=action, entry_price=None if action == "WATCH" else price,
                  confidence=conf, reasoning="because", sources=["Reuters"])


def alert(**kw):
    base = dict(pulse="up", bottom_line="bl")
    base.update(kw)
    return MarketAlert(**base)


class LedgerTests(unittest.TestCase):
    def test_dedupe_reaffirms(self):
        ledger = []
        memory.add_signals(ledger, [sig()], {"NVDA": 100}, NOW)
        memory.add_signals(ledger, [sig()], {"NVDA": 101}, NOW + timedelta(hours=4))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["reaffirmed"], 1)

    def test_reversal_closes_old_at_current_price(self):
        ledger = []
        memory.add_signals(ledger, [sig(action="BUY", price=100)], {}, NOW)
        memory.add_signals(ledger, [sig(action="SELL", price=110)], {"NVDA": 110}, NOW + timedelta(days=1))
        self.assertEqual(len(ledger), 2)
        old, new = ledger
        self.assertEqual(old["status"], "closed")
        self.assertEqual(old["return_pct"], 10.0)
        self.assertEqual(old["outcome"], "win")
        self.assertEqual(new["status"], "open")
        self.assertEqual(new["action"], "SELL")


class ScoringTests(unittest.TestCase):
    def scored(self, action, entry, price, days=1):
        ledger = []
        memory.add_signals(ledger, [sig(action=action, price=entry)], {}, NOW)
        memory.score_signals(ledger, lambda t: price, NOW + timedelta(days=days))
        return ledger[0]

    def test_buy_return(self):
        s = self.scored("BUY", 100, 104)
        self.assertEqual(s["return_pct"], 4.0)
        self.assertEqual(s["status"], "open")

    def test_sell_return_inverted(self):
        self.assertEqual(self.scored("SELL", 100, 104)["return_pct"], -4.0)
        self.assertEqual(self.scored("SELL", 100, 96)["return_pct"], 4.0)

    def test_outcome_thresholds_on_close(self):
        days = config.SIGNAL_HORIZON_DAYS
        self.assertEqual(self.scored("BUY", 100, 100.6, days)["outcome"], "win")
        self.assertEqual(self.scored("BUY", 100, 99.4, days)["outcome"], "loss")
        self.assertEqual(self.scored("BUY", 100, 100.5, days)["outcome"], "flat")
        self.assertEqual(self.scored("SELL", 100, 101, days)["outcome"], "loss")

    def test_horizon_closing(self):
        s = self.scored("BUY", 100, 102, config.SIGNAL_HORIZON_DAYS - 1)
        self.assertEqual(s["status"], "open")
        s = self.scored("BUY", 100, 102, config.SIGNAL_HORIZON_DAYS)
        self.assertEqual(s["status"], "closed")
        self.assertIsNotNone(s["closed_at"])

    def test_watch_closes_without_outcome(self):
        ledger = []
        memory.add_signals(ledger, [sig(action="WATCH")], {}, NOW)
        memory.score_signals(ledger, lambda t: 1.0, NOW + timedelta(days=6))
        self.assertEqual(ledger[0]["status"], "closed")
        self.assertIsNone(ledger[0]["outcome"])

    def test_missing_price_does_not_crash(self):
        ledger = []
        memory.add_signals(ledger, [sig()], {}, NOW)
        memory.score_signals(ledger, lambda t: None, NOW + timedelta(days=1))
        self.assertEqual(ledger[0]["return_pct"], 0.0)

    def test_track_record_stats(self):
        ledger = []
        for t, p in [("A", 110), ("B", 90), ("C", 110)]:
            memory.add_signals(ledger, [sig(ticker=t, price=100)], {}, NOW)
            memory.score_signals(ledger, lambda x, p=p: p if x == t else None, NOW + timedelta(days=6))
        st = memory.track_record_stats(ledger)
        self.assertEqual(st["buy_total"], 3)
        self.assertEqual(st["buy_wins"], 2)
        self.assertIn("Hit rate: BUY 67% (2/3)", memory.track_record_alert_lines(ledger)[-1])
        self.assertEqual(memory.track_record_alert_lines([]), [])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_missing_files_are_empty(self):
        self.assertEqual(memory.load_signals(self.tmp / "nope.json"), [])
        self.assertEqual(memory.load_digests(self.tmp / "nope.json"), {"recent": [], "weekly": []})

    def test_corrupt_json_starts_empty_and_logs(self):
        p = self.tmp / "signals.json"
        p.write_text("{not json")
        self.assertEqual(memory.load_signals(p), [])
        self.assertEqual(p.read_text(), "{not json")  # load never overwrites
        d = self.tmp / "digests.json"
        d.write_text("[1,2]")  # wrong structure
        self.assertEqual(memory.load_digests(d), {"recent": [], "weekly": []})
        t = self.tmp / "tickers.json"
        t.write_text("garbage")
        self.assertEqual(memory.dump_tickers(memory.load_tickers(t)), {})

    def test_roundtrip(self):
        ledger = []
        memory.add_signals(ledger, [sig()], {}, NOW)
        p = self.tmp / "s.json"
        memory.save_signals(ledger, p)
        self.assertEqual(memory.load_signals(p), json.loads(p.read_text()))

    def test_ticker_store_roundtrip_and_update(self):
        store = memory.load_tickers(self.tmp / "none.json")
        a = alert(top_gainers=[Mover(ticker="NVDA", change_pct=3, price=100, reason="chips")],
                  signals=[sig("NVDA", "BUY", 100)])
        for i in range(12):
            memory.update_tickers(store, a, NOW + timedelta(days=i))
        p = self.tmp / "t.json"
        memory.save_tickers(store, p)
        note = json.loads(p.read_text())["NVDA"]
        self.assertEqual(note["mention_count"], 12)
        self.assertEqual(len(note["signal_history"]), config.MAX_TICKER_HISTORY)
        self.assertLessEqual(len(note["last_note"]), config.MAX_TICKER_NOTE_CHARS)
        self.assertIn("NVDA", memory.ticker_notes_text(memory.load_tickers(p), ["NVDA"]))


class DigestTests(unittest.TestCase):
    def digests(self, n):
        d = {"recent": [], "weekly": []}
        for i in range(n):
            at = (NOW - timedelta(days=n - i)).isoformat(timespec="seconds")
            d["recent"].append({"at": at, "session": "x", "summary": f"s{i}", "themes": [f"t{i}"]})
        return d

    def test_compression_via_llm(self):
        d = memory.add_digest(self.digests(15), {"at": NOW.isoformat(), "session": "x", "summary": "new", "themes": []},
                              lambda text: "WEEK SUMMARY", NOW)
        self.assertEqual(len(d["recent"]), config.MAX_RECENT_DIGESTS)
        self.assertTrue(d["weekly"])
        self.assertEqual(d["weekly"][0]["summary"], "WEEK SUMMARY")
        self.assertRegex(d["weekly"][0]["week"], r"^\d{4}-W\d{2}$")

    def test_compression_fallback_on_failure(self):
        def boom(_):
            raise RuntimeError("llm down")
        d = memory.add_digest(self.digests(15), {"at": NOW.isoformat(), "session": "x", "summary": "new", "themes": []}, boom, NOW)
        self.assertEqual(len(d["recent"]), config.MAX_RECENT_DIGESTS)
        self.assertIn("t0", d["weekly"][0]["summary"])

    def test_weekly_cap(self):
        d = {"recent": [], "weekly": [{"week": f"2026-W{w:02d}", "summary": "x"} for w in range(1, 9)]}
        d["recent"] = self.digests(15)["recent"]
        memory.add_digest(d, {"at": NOW.isoformat(), "session": "x", "summary": "n", "themes": []}, lambda t: "s", NOW)
        self.assertLessEqual(len(d["weekly"]), config.MAX_WEEKLY_DIGESTS)


class ValidationTests(unittest.TestCase):
    quotes = {"NVDA": {"current": 100.0, "change_pct": 2.0}, "AAPL": {"current": 200.0, "change_pct": -1.0}}

    def test_valid_alert(self):
        a = alert(signals=[sig("NVDA", "BUY", 101), sig("AAPL", "WATCH")],
                  top_gainers=[Mover(ticker="NVDA", change_pct=2, price=100, reason="r")])
        self.assertEqual(validate_alert(a, self.quotes), [])

    def test_hallucinated_ticker_and_bad_price(self):
        a = alert(signals=[sig("FAKE", "BUY", 10), sig("NVDA", "SELL", 110), sig("AAPL", "BUY", 200.5)],
                  top_losers=[Mover(ticker="ZZZ", change_pct=-2, price=5, reason="r")])
        errs = validate_alert(a, self.quotes)
        self.assertEqual(len(errs), 3)
        clean = sanitize_alert(a, self.quotes)
        self.assertEqual([s.ticker for s in clean.signals], ["AAPL"])
        self.assertEqual(clean.top_losers, [])

    def test_mover_prices_fixed_to_quotes(self):
        a = alert(top_gainers=[Mover(ticker="NVDA", change_pct=9.9, price=1.0, reason="r")])
        m = sanitize_alert(a, self.quotes).top_gainers[0]
        self.assertEqual((m.price, m.change_pct), (100.0, 2.0))

    def test_missing_entry_price_invalid(self):
        s = Signal(ticker="NVDA", action="BUY", entry_price=None, confidence="LOW", reasoning="r")
        self.assertTrue(validate_alert(alert(signals=[s]), self.quotes))


if __name__ == "__main__":
    unittest.main()
