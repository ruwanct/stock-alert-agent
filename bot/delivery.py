"""
Delivery — Telegram text and Discord rich embed.
"""

import requests

from bot import config


def send_telegram(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("  ⏭ Telegram not configured, skipping")
        return False

    if len(text) > 4000:
        text = text[:3950] + "\n\n... (truncated)"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        print("  ✅ Telegram sent!")
        return True
    except Exception as e:
        print(f"  ❌ Telegram failed: {e}")
        return False


def send_discord(embed: dict) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        print("  ⏭ Discord not configured, skipping")
        return False
    try:
        r = requests.post(
            config.DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        r.raise_for_status()
        print("  ✅ Discord sent!")
        return True
    except Exception as e:
        print(f"  ❌ Discord failed: {e}")
        return False
