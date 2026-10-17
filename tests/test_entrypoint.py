"""Entry point exit codes: credits/auth fail the run (GitHub emails the owner); transient errors do not."""

import unittest
from unittest import mock

import stock_alert_bot


class FakeGraph:
    def __init__(self, result):
        self.result = result

    def invoke(self, _):
        return self.result


class ExitCodeTests(unittest.TestCase):
    def run_main(self, code, delivered=True):
        result = {"delivered": {"discord": delivered, "telegram": False, "dry_run": False}, "analysis_error_code": code}
        patches = [
            mock.patch.object(stock_alert_bot.config, "FINNHUB_API_KEY", "x"),
            mock.patch.object(stock_alert_bot.config, "ANTHROPIC_API_KEY", "x"),
            mock.patch.object(stock_alert_bot.config, "DISCORD_WEBHOOK_URL", "https://example.test/hook"),
            mock.patch.object(stock_alert_bot.config, "DRY_RUN", False),
            mock.patch.object(stock_alert_bot, "build_graph", lambda: FakeGraph(result)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        try:
            stock_alert_bot.main()
        except SystemExit as e:
            return e.code
        return 0

    def test_credits_and_auth_exit_1(self):
        self.assertEqual(self.run_main("credits"), 1)
        self.assertEqual(self.run_main("auth"), 1)

    def test_transient_and_ok_exit_0(self):
        for code in ("rate_limit", "overloaded", "network", "unknown", ""):
            self.assertEqual(self.run_main(code), 0, code)

    def test_delivery_failure_still_exit_1(self):
        self.assertEqual(self.run_main("", delivered=False), 1)


if __name__ == "__main__":
    unittest.main()
