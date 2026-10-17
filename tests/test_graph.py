"""End-to-end graph test with mocked data + chains (no network, no API)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from langchain_core.messages import AIMessage

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


class FakeResearchModel:
    """Scripted chat model: each invoke pops the next AIMessage; records whether tools were bound."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bound = []      # tools bound (or None) per model call
        self.seen = []       # messages per call

    def build(self, tools=None, llm=None):
        self.bound.append([t.name for t in tools] if tools else None)
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_msg(*tickers_kinds):
    calls = [{"name": n, "args": {"ticker": t}, "id": f"c{i}", "type": "tool_call"}
             for i, (n, t) in enumerate(tickers_kinds)]
    return AIMessage(content="", tool_calls=calls)


NEWS, HIST = "get_company_news", "recall_ticker_history"


class GraphTests(unittest.TestCase):
    def run_graph(self, analysis_results, dry_run=False, research=None, news=None):
        """research: a FakeModel to enable the research agent; None disables research."""
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
            mock.patch.object(config, "RESEARCH_ENABLED", research is not None),
            mock.patch.object(graph.chains, "build_research_model", research.build if research else None),
            mock.patch.object(graph.research_tools_mod.data, "fetch_company_news",
                              lambda sym, days=2: news if news is not None else []),
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

    def test_analysis_credits_error_gives_friendly_fallback(self):
        raw = "Your credit balance is too low (secret-detail-123)"

        class Credits(Exception):
            status_code = 400
            body = {"error": {"type": "invalid_request_error", "message": raw}}

        result, analysis, _ = self.run_graph([Credits(raw)])
        self.assertEqual(result["analysis_error_code"], "credits")
        self.assertEqual(len(analysis.calls), 1)  # no retry
        self.assertIn("credits ran out", self.sent[0])
        self.assertIn("auto-reload", self.sent[0])
        self.assertNotIn("secret-detail-123", self.sent[0])
        self.assertNotIn("Analysis unavailable", self.sent[0])

    def test_retry_failure_keeps_previous_alert(self):
        result, _, _ = self.run_graph([good(price=150.0), RuntimeError("boom")])
        self.assertIsNotNone(result["alert"])
        self.assertEqual(result["analysis_error_code"], "unknown")

    def test_analyze_prompt_gets_research_notes(self):
        result, analysis, _ = self.run_graph([good()], research=FakeResearchModel([AIMessage(content="NVDA: up on AI")]))
        self.assertEqual(analysis.calls[0]["research_notes"], "NVDA: up on AI")

    def test_research_off_passes_none(self):
        _, analysis, _ = self.run_graph([good()])
        self.assertEqual(analysis.calls[0]["research_notes"], "None")

    # ── research agent ──
    def test_research_two_tool_calls_then_notes(self):
        model = FakeResearchModel([tool_msg((NEWS, "NVDA"), (HIST, "NVDA")), AIMessage(content="NVDA: news says X\nNVDA: no history")])
        result, analysis, _ = self.run_graph([good()], research=model, news=[{"headline": "Big deal", "summary": "sum"}])
        self.assertEqual(result["research_notes"], "NVDA: news says X\nNVDA: no history")
        self.assertEqual(result["research_tool_calls"], 2)
        self.assertEqual(result["research_lookups"], ["NVDA (news)", "NVDA (history)"])
        self.assertEqual(model.bound[0], [NEWS, HIST])
        self.assertIsNone(model.bound[1])  # budget used up → final call has no tools
        tool_results = [m.content for m in result["research_messages"] if m.type == "tool"]
        self.assertIn("Big deal", tool_results[0])
        self.assertIn("No history", tool_results[1])
        self.assertEqual(analysis.calls[0]["research_notes"], result["research_notes"])

    def test_research_cap_ignores_third_call(self):
        model = FakeResearchModel([
            tool_msg((NEWS, "NVDA"), (HIST, "NVDA"), (NEWS, "TSLA")),
            AIMessage(content="NVDA: done"),
        ])
        with mock.patch.object(graph.research_tools_mod.data, "fetch_company_news") as fetch:
            fetch.return_value = []
            result, _, _ = self.run_graph([good()], research=model)
        self.assertEqual(result["research_tool_calls"], 2)
        self.assertEqual(result["research_lookups"], ["NVDA (news)", "NVDA (history)"])
        self.assertEqual(len([m for m in result["research_messages"] if m.type == "tool"]), 2)
        self.assertEqual(result["research_notes"], "NVDA: done")

    def test_research_round_cap_forces_notes_without_tools(self):
        model = FakeResearchModel([tool_msg((NEWS, "A")), tool_msg((NEWS, "B")), AIMessage(content="A: x")])
        with mock.patch.object(config, "MAX_TOOL_CALLS", 5):
            result, _, _ = self.run_graph([good()], research=model)
        self.assertEqual(len(model.bound), config.MAX_RESEARCH_ROUNDS)
        self.assertIsNone(model.bound[-1])
        self.assertEqual(result["research_notes"], "A: x")

    def test_research_exception_continues(self):
        model = FakeResearchModel([RuntimeError("boom")])
        result, analysis, _ = self.run_graph([good()], research=model)
        self.assertEqual(result["research_notes"], "")
        self.assertEqual(analysis.calls[0]["research_notes"], "None")
        self.assertTrue(result["delivered"]["telegram"])

    def test_research_failure_midloop_continues(self):
        model = FakeResearchModel([tool_msg((NEWS, "NVDA")), RuntimeError("credit balance is too low")])
        result, _, _ = self.run_graph([good()], research=model)
        self.assertEqual(result.get("research_notes", ""), "")
        self.assertTrue(result["delivered"]["telegram"])

    def test_research_disabled_skips_model(self):
        model = FakeResearchModel([])
        with mock.patch.object(config, "RESEARCH_ENABLED", False), \
                mock.patch.object(graph.chains, "build_research_model", model.build):
            state = graph.research({})
        self.assertEqual(state["research_notes"], "")
        self.assertFalse(state["research_pending"])
        self.assertEqual(model.bound, [])

    def test_research_prompt_is_short_and_has_no_news(self):
        model = FakeResearchModel([AIMessage(content="NVDA: x")])
        self.run_graph([good()], research=model, news=[{"headline": "x"}])
        system, human = model.seen[0]
        self.assertEqual(system.content[0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Top movers today: NVDA +1.0% $100.00", human.content)
        self.assertLess(len(human.content), 600)

    def test_dry_run_still_researches(self):
        model = FakeResearchModel([AIMessage(content="NVDA: x")])
        result, _, _ = self.run_graph([good()], dry_run=True, research=model)
        self.assertEqual(result["research_notes"], "NVDA: x")

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
