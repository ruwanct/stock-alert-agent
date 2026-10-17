"""Research tools — news formatting/limits/failure, history from ledger + store."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from langgraph.store.memory import InMemoryStore

from bot import memory, tools
from bot.schemas import Signal

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def items(n):
    return [{"headline": f"H{i}", "summary": "s" * 400} for i in range(n)]


class NewsToolTests(unittest.TestCase):
    def tool(self):
        return tools.build_tools([], InMemoryStore())[0]

    def test_formats_and_limits(self):
        with mock.patch.object(tools.data, "fetch_company_news", return_value=items(9)) as f:
            out = self.tool().invoke({"ticker": "nvda"})
        f.assert_called_once_with("NVDA", days=2)
        lines = out.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("- H0 — s"))
        self.assertEqual(len(lines[0]), len("- H0 — ") + 150)

    def test_empty(self):
        with mock.patch.object(tools.data, "fetch_company_news", return_value=[]):
            self.assertIn("No news", self.tool().invoke({"ticker": "NVDA"}))

    def test_failure_returns_string(self):
        with mock.patch.object(tools.data, "fetch_company_news", side_effect=RuntimeError("key=secret")):
            out = self.tool().invoke({"ticker": "NVDA"})
        self.assertIn("failed", out)
        self.assertNotIn("secret", out)

    def test_descriptions_present(self):
        for t in tools.build_tools([], InMemoryStore()):
            self.assertTrue(t.description)


class HistoryToolTests(unittest.TestCase):
    def setUp(self):
        self.ledger, self.store = [], InMemoryStore()
        sig = Signal(ticker="NVDA", action="BUY", entry_price=100.0, confidence="HIGH", reasoning="r")
        memory.add_signals(self.ledger, [sig], {"NVDA": 100.0}, NOW)
        self.ledger[0]["return_pct"] = 3.8
        self.store.put(memory.TICKER_NS, "NVDA", {
            "signal_history": [{"date": "2026-10-14", "action": "BUY", "price": 100.0}],
            "mention_count": 3, "last_mentioned": "2026-10-14", "last_note": "AI demand"})

    def hist(self):
        return tools.build_tools(self.ledger, self.store)[1]

    def test_reads_ledger_and_store(self):
        out = self.hist().invoke({"ticker": "nvda"})
        self.assertIn("BUY @ $100.00 opened 2026-10-14 [open] return +3.8%", out)
        self.assertIn("mentioned 3x", out)
        self.assertIn("AI demand", out)
        self.assertIn("BUY@100.0 2026-10-14", out)

    def test_sees_later_state_changes(self):
        sig = Signal(ticker="TSLA", action="SELL", entry_price=50.0, confidence="LOW", reasoning="r")
        h = self.hist()
        memory.add_signals(self.ledger, [sig], {}, NOW)  # same list object, mutated after build
        self.assertIn("SELL", h.invoke({"ticker": "TSLA"}))

    def test_no_history(self):
        self.assertIn("No history", self.hist().invoke({"ticker": "ZZZ"}))


class ResearchToolsNodeTests(unittest.TestCase):
    """Regression: research_tools must execute the REAL tools (no mocked tool execution).
    A hand-invoked langgraph ToolNode failed here with a missing-config error in production."""

    def test_executes_real_tools_and_links_tool_call_ids(self):
        from langchain_core.messages import AIMessage
        from bot import graph
        store = InMemoryStore()
        store.put(memory.TICKER_NS, "NVDA", {"mention_count": 2, "last_note": "AI capex", "signal_history": []})
        ledger = [{"id": "a", "ticker": "NVDA", "action": "BUY", "entry_price": 180.0, "return_pct": 2.1,
                   "status": "open", "opened_at": "2026-10-15T21:30:00+00:00"}]
        ask = AIMessage(content="", tool_calls=[
            {"name": "recall_ticker_history", "args": {"ticker": "NVDA"}, "id": "t1"},
            {"name": "get_company_news", "args": {"ticker": "NVDA"}, "id": "t2"}])
        state = {"ledger": ledger, "store": store, "research_messages": [ask]}
        with mock.patch("bot.data.fetch_company_news", return_value=[{"headline": "NVDA beats", "summary": "ok"}]):
            out = graph.research_tools(state)
        self.assertNotIn("research_failed", out)
        tool_msgs = [m for m in out["research_messages"] if m.type == "tool"]
        self.assertEqual([m.tool_call_id for m in tool_msgs], ["t1", "t2"])
        self.assertIn("BUY @ $180.00", tool_msgs[0].content)
        self.assertIn("NVDA beats", tool_msgs[1].content)
        self.assertEqual(out["research_lookups"], ["NVDA (history)", "NVDA (news)"])


if __name__ == "__main__":
    unittest.main()
