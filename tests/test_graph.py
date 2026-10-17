"""End-to-end graph test with mocked data + chains (no network, no API)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import config, data, graph
from bot.schemas import DailyDigest, MarketAlert, Mover, Signal


class FakeChain:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def invoke(self, inputs):
        self.calls.append(inputs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def quote(sym, price, pct=1.0):
    return {"symbol": sym, "current": price, "open": price, "high": price, "low": price,
            "prev_close": price, "change": 0, "change_pct": pct}


def good(price=100.0, ticker="NVDA"):
    return MarketAlert(pulse="p", bottom_line="b",
                       top_gainers=[Mover(ticker=ticker, change_pct=1, price=price, reason="r")],
                       signals=[Signal(ticker=ticker, action="BUY", entry_price=price, confidence="HIGH", reasoning="r")])


class GraphTests(unittest.TestCase):
    def run_graph(self, analysis_results, dry_run=False):
        self.sent = []
        tmp = Path(tempfile.mkdtemp())
        data.QUOTE_CACHE.clear()
        data.QUOTE_CACHE["NVDA"] = quote("NVDA", 100.0)
        analysis = FakeChain(analysis_results)
        digest = FakeChain([DailyDigest(summary="sum", themes=["ai"])])
        patches = [
            mock.patch.object(config, "SIGNALS_PATH", tmp / "signals.json"),
            mock.patch.object(config, "DIGESTS_PATH", tmp / "digests.json"),
            mock.patch.object(config, "TICKERS_PATH", tmp / "tickers.json"),
            mock.patch.object(config, "DRY_RUN", dry_run),
            mock.patch.object(graph.data, "build_watchlist", lambda: ["NVDA"]),
            mock.patch.object(graph.data, "aggregate_news", lambda c: []),
            mock.patch.object(graph.chains, "build_analysis_chain", lambda: analysis),
            mock.patch.object(graph.chains, "build_digest_chain", lambda: digest),
            mock.patch.object(graph.delivery, "send_telegram", lambda t: self.sent.append(t) or True),
            mock.patch.object(graph.delivery, "send_discord", lambda e: True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        result = graph.build_graph().invoke({})
        return result, analysis, tmp

    def test_happy_path_writes_memory(self):
        result, analysis, tmp = self.run_graph([good()])
        self.assertEqual(len(analysis.calls), 1)
        self.assertTrue((tmp / "signals.json").exists())
        self.assertEqual(len(result["ledger"]), 1)
        self.assertTrue(result["delivered"]["telegram"])

    def test_retry_with_feedback_then_success(self):
        result, analysis, tmp = self.run_graph([good(price=150.0), good()])
        self.assertEqual(len(analysis.calls), 2)
        self.assertIn("more than", analysis.calls[1]["validation_feedback"])
        self.assertEqual(result["ledger"][0]["entry_price"], 100.0)

    def test_still_invalid_after_retry_dropped(self):
        result, analysis, _ = self.run_graph([good(ticker="FAKE"), good(ticker="FAKE")])
        self.assertEqual(len(analysis.calls), 2)
        self.assertEqual(result["alert"].signals, [])
        self.assertEqual(result["ledger"], [])

    def test_analysis_failure_falls_back(self):
        result, _, tmp = self.run_graph([RuntimeError("boom")])
        self.assertIsNone(result.get("alert"))
        self.assertEqual(result["ledger"], [])
        self.assertTrue(result["delivered"]["telegram"])
        self.assertTrue((tmp / "signals.json").exists())  # scoring still saved

    def test_save_failure_sets_warning_and_still_delivers(self):
        with mock.patch.object(graph.storage.JsonBackend, "save_signals",
                               side_effect=RuntimeError("secret-host:27017 refused")):
            result, _, _ = self.run_graph([good()])
        sent = self.sent
        self.assertEqual(result["memory_warning"], "⚠️ Memory partially saved: signals failed")
        self.assertTrue(result["delivered"]["telegram"])
        self.assertIn("Memory partially saved: signals failed", sent[0])
        self.assertNotIn("secret-host", sent[0])

    def test_healthy_run_has_no_warning(self):
        result, _, _ = self.run_graph([good()])
        sent = self.sent
        self.assertEqual(result["memory_warning"], "")
        self.assertNotIn("Memory", sent[0])

    def test_dry_run_has_no_warning(self):
        result, _, _ = self.run_graph([good()], dry_run=True)
        self.assertEqual(result["memory_warning"], "")

    def test_dry_run_writes_nothing(self):
        result, _, tmp = self.run_graph([good()], dry_run=True)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertTrue(result["delivered"]["dry_run"])


if __name__ == "__main__":
    unittest.main()
