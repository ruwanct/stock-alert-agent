"""
Export MongoDB memory → memory/*.json (the weekly backup the workflow commits).

    export MONGODB_URI='mongodb+srv://...'
    python scripts/export_memory.py

Writes signals.json, digests.json ({"recent": [...], "weekly": [...]}) and
tickers.json in exactly the formats the JSON backend reads.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, memory, storage  # noqa: E402


def export(backend) -> dict:
    ledger = backend.load_signals()
    digests = backend.load_digests()
    tickers = backend.load_tickers()
    memory.save_signals(ledger)
    memory.save_digests(digests)
    memory.save_tickers(tickers)
    return {"signals": len(ledger), "digests": len(digests["recent"]),
            "weekly_digests": len(digests["weekly"]), "tickers": len(memory.dump_tickers(tickers))}


def main() -> int:
    if not config.MONGODB_URI:
        print("MONGODB_URI is not set — nothing to export.")
        return 1
    backend = storage.MongoBackend()
    try:
        counts = export(backend)
    finally:
        backend.close()
    print("Exported to memory/: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
