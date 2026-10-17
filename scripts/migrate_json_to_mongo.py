"""
One-time (and idempotent) migration: memory/*.json → MongoDB.

    export MONGODB_URI='mongodb+srv://...'
    python scripts/migrate_json_to_mongo.py --dry-run   # just count what would be migrated
    python scripts/migrate_json_to_mongo.py             # upsert into MongoDB

Safe to run twice: signals upsert by `id`, digests by `at`, weekly by `week`,
tickers by key. The JSON files are only read, never modified.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, memory, storage  # noqa: E402


def migrate(backend=None, dry_run: bool = False) -> dict:
    """Read memory/*.json and upsert into `backend` (a MongoBackend). Returns counts per collection."""
    ledger = memory.load_signals()
    digests = memory.load_digests()
    tickers = memory.load_tickers()
    counts = {
        "signals": len([s for s in ledger if s.get("id")]),
        "digests": len([d for d in digests["recent"] if d.get("at")]),
        "weekly_digests": len([w for w in digests["weekly"] if w.get("week")]),
        "tickers": len(memory.dump_tickers(tickers)),
    }
    if not dry_run:
        backend.save_signals(ledger)
        backend.save_digests(digests)
        backend.save_tickers(tickers)
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="read the JSON files and print counts; write nothing")
    args = ap.parse_args(argv)

    backend = None
    if not args.dry_run:
        if not config.MONGODB_URI:
            print("MONGODB_URI is not set. Export it first (or use --dry-run to only count the JSON data).")
            return 1
        backend = storage.MongoBackend()
    try:
        counts = migrate(backend, args.dry_run)
    finally:
        if backend:
            backend.close()
    verb = "would migrate" if args.dry_run else "upserted"
    target = "" if args.dry_run else f" into database '{config.MONGODB_DB}'"
    print(f"\nMigration {verb}{target}:")
    for name, n in counts.items():
        print(f"  {name:<15} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
