"""
One-time (and idempotent) migration: memory/*.json → MongoDB.

    export MONGODB_URI='mongodb+srv://...'
    python scripts/migrate_json_to_mongo.py --dry-run   # just count what would be migrated
    python scripts/migrate_json_to_mongo.py             # upsert into MongoDB

Also reads memory/archive/*.json (signals-*, digests-*, weekly-*), so this one
command is a full restore from the backup. Safe to run twice: signals upsert by
`id`, digests by `at` (the `compressed` flag is preserved), weekly by `week`,
tickers by key. The JSON files are only read, never modified.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, memory, storage  # noqa: E402


def _load_archive(prefix: str) -> list:
    """All records from memory/archive/<prefix>-*.json, oldest year first."""
    out = []
    for path in sorted(Path(config.ARCHIVE_DIR).glob(f"{prefix}-*.json")):
        out.extend(d for d in memory._load_json(path, list, []) if isinstance(d, dict))
    return out


def _merge(archived: list, main: list, key: str) -> list:
    """Dedupe by `key`; for a record in both, the one from `main` wins."""
    merged = {}
    for d in archived + main:
        if d.get(key):
            merged[d[key]] = d
    return list(merged.values())


def migrate(backend=None, dry_run: bool = False) -> dict:
    """
    Read memory/*.json and memory/archive/*.json and upsert into `backend` (a MongoBackend).
    Returns counts per collection (after de-duplication).

    Overlap rules: a signal in both signals.json and an archive file -> signals.json wins
    (it is the fresher export). A digest in both -> the archive wins, because it carries the
    `compressed` flag that digests.json (recent only) does not.
    """
    ledger = memory.load_signals()
    digests = memory.load_digests()
    tickers = memory.load_tickers()

    signals = _merge(_load_archive("signals"), [s for s in ledger if isinstance(s, dict)], "id")
    archived_digests = _merge(_load_archive("digests"), [], "at")
    archived_at = {d["at"] for d in archived_digests}
    recent = [d for d in digests["recent"] if d.get("at") and d["at"] not in archived_at]
    recent += [d for d in archived_digests if not d.get("compressed")]
    compressed = [d for d in archived_digests if d.get("compressed")]
    weekly = _merge(_load_archive("weekly"), digests["weekly"], "week")

    counts = {
        "signals": len(signals),
        "digests": len(recent) + len(compressed),
        "weekly_digests": len(weekly),
        "tickers": len(memory.dump_tickers(tickers)),
    }
    archive_files = len(list(Path(config.ARCHIVE_DIR).glob("*.json")))
    if archive_files:
        counts["archive_files"] = archive_files
    if not dry_run:
        backend.save_signals(signals)
        backend.save_digests({"recent": [{k: v for k, v in d.items() if k != "compressed"} for d in recent],
                              "weekly": weekly},
                             compressed=[{k: v for k, v in d.items() if k != "compressed"} for d in compressed])
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
