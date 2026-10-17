"""
Stock Alert Bot v6 — GitHub Actions Version
LangChain + LangGraph: structured analysis, persistent memory across runs,
dynamic watchlist, multi-source news, Telegram + Discord delivery.

Thin entry point: validates env, builds and invokes the graph.
"""

import sys

from bot import config, errors
from bot.graph import build_graph


def main():
    print("🤖 Stock Alert Bot v6")
    print("LangGraph + Memory + Multi-Source News + Telegram + Discord")
    print("=" * 58)

    # Validate — only Finnhub and Anthropic are required
    # Telegram and Discord are optional (at least one should be set)
    missing = []
    if not config.FINNHUB_API_KEY:
        missing.append("FINNHUB_API_KEY")
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"❌ Missing required: {', '.join(missing)}")
        sys.exit(1)

    has_telegram = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
    has_discord = bool(config.DISCORD_WEBHOOK_URL)

    if config.DRY_RUN:
        print("  🧪 DRY_RUN=1 — nothing will be sent, memory will not be written")
    elif not has_telegram and not has_discord:
        print("❌ No delivery channel! Set TELEGRAM or DISCORD secrets.")
        sys.exit(1)

    channels = [c for c, ok in (("Telegram", has_telegram), ("Discord", has_discord)) if ok]
    print(f"  📡 Delivery: {' + '.join(channels) or 'none (dry run)'}")

    result = build_graph().invoke({})
    delivered = result.get("delivered", {})

    if delivered.get("dry_run"):
        print("\n✅ Dry run complete.")
        return

    results = []
    if has_telegram:
        results.append(f"Telegram: {'✅' if delivered.get('telegram') else '❌'}")
    if has_discord:
        results.append(f"Discord: {'✅' if delivered.get('discord') else '❌'}")
    if delivered.get("telegram") or delivered.get("discord"):
        print(f"\n✅ Done! {' | '.join(results)}")
        # Credits/auth problems will not fix themselves: fail the run so GitHub emails the owner.
        # Transient errors (rate limit, overloaded, network) exit 0.
        code = result.get("analysis_error_code", "")
        if code in errors.FATAL_CODES:
            print(f"\n❌ Claude API problem ({code}): {errors.MESSAGES[code]}")
            sys.exit(1)
    else:
        print("\n❌ All delivery channels failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
