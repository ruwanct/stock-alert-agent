"""
Export MongoDB memory → memory/*.json (the weekly backup the workflow commits).

    export MONGODB_URI='mongodb+srv://...'
    python scripts/export_memory.py

Layout (every file stays bounded, the backup is complete):

    memory/signals.json        open signals + closed ones newer than BACKUP_RECENT_DAYS
    memory/digests.json        {"recent": [...], "weekly": [...]} exactly as the bot loads it
    memory/tickers.json        per-ticker notes
    memory/archive/signals-YYYY.json   older closed signals, by year of closed_at (else opened_at)
    memory/archive/digests-YYYY.json   EVERY digest (incl. compressed), by year of `at`
    memory/archive/weekly-YYYY.json    EVERY weekly summary, by the year in `week`

Output is deterministic and a file is only written when its content changed, so
past-year archives never show up in git. Archive years that have no data in
MongoDB are left untouched, never deleted.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, memory, storage  # noqa: E402


def _render(data) -> str:
    """Same formatting as memory._save_json."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_if_changed(path: Path, data) -> bool:
    text = _render(data)
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _year(ts) -> str:
    """Four-digit year of an ISO timestamp / ISO week string, or None."""
    ts = ts if isinstance(ts, str) else ""
    return ts[:4] if len(ts) >= 4 and ts[:4].isdigit() else None


def split_signals(ledger: list, now: datetime, days: int = None) -> tuple:
    """-> (main, {year: [signals]}). Open (or undated) signals always stay in main."""
    cutoff = now - timedelta(days=config.BACKUP_RECENT_DAYS if days is None else days)
    main, archive = [], defaultdict(list)
    for s in ledger:
        ts = s.get("closed_at") or s.get("opened_at")
        year = _year(ts)
        old = (s.get("status") == "closed" and year is not None
               and memory._parse(ts, now) < cutoff)
        if old:
            archive[year].append(s)
        else:
            main.append(s)
    for sigs in archive.values():
        sigs.sort(key=lambda s: (s.get("opened_at") or "", s.get("id") or ""))
    main.sort(key=lambda s: (s.get("opened_at") or "", s.get("id") or ""))
    return main, dict(archive)


def _by_year(docs: list, key: str) -> dict:
    out = defaultdict(list)
    for d in docs:
        year = _year(d.get(key))
        if year:
            out[year].append(d)
    for items in out.values():
        items.sort(key=lambda d: d.get(key) or "")
    return dict(out)


def export(backend, now: datetime = None, archive_dir: Path = None) -> dict:
    now = now or datetime.now(timezone.utc)
    archive_dir = Path(archive_dir or config.ARCHIVE_DIR)
    ledger = backend.load_signals()
    digests = backend.load_digests()
    tickers = backend.load_tickers()
    all_digests = backend.load_all_digests()
    all_weekly = backend.load_all_weekly()

    main, old_signals = split_signals(ledger, now)
    written = 0
    written += _write_if_changed(config.SIGNALS_PATH, main)
    written += _write_if_changed(config.DIGESTS_PATH, digests)
    written += _write_if_changed(config.TICKERS_PATH, dict(sorted(memory.dump_tickers(tickers).items())))
    for prefix, groups in (("signals", old_signals),
                           ("digests", _by_year(all_digests, "at")),
                           ("weekly", _by_year(all_weekly, "week"))):
        for year, docs in sorted(groups.items()):
            written += _write_if_changed(archive_dir / f"{prefix}-{year}.json", docs)
    return {"signals": len(main), "archived_signals": sum(len(v) for v in old_signals.values()),
            "digests": len(digests["recent"]), "weekly_digests": len(digests["weekly"]),
            "archived_digests": len(all_digests), "archived_weekly": len(all_weekly),
            "tickers": len(memory.dump_tickers(tickers)), "files_written": int(written)}


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
