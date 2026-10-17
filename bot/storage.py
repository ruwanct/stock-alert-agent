"""
Memory storage backends — JSON files (dev, tests, fallback, backup) or MongoDB Atlas.

Graph nodes only talk to the small backend interface below:

    load_signals / save_signals    list of signal dicts (business key: "id")
    load_digests / save_digests    {"recent": [...], "weekly": [...]}
    load_tickers / save_tickers    per-ticker notes as a LangGraph store
    close()

Fail-safe rule: a MongoDB problem must never stop the alert. `open_memory()`
falls back to a read-only JSON snapshot (and the run then skips every memory
write, so JSON and MongoDB can't diverge). Failed saves only log a warning.
"""

from typing import NamedTuple, Optional

from langgraph.store.memory import InMemoryStore

from bot import config, memory

try:  # pymongo is a normal requirement, but never let a missing wheel break JSON mode
    from pymongo import MongoClient, ReplaceOne
except ImportError:  # pragma: no cover
    MongoClient = ReplaceOne = None

TICKER_COLLECTION = "tickers"   # backing collection of the LangGraph MongoDBStore


# ──────────────────────────────────────────────
# JSON backend (thin wrapper over the memory.py functions)
# ──────────────────────────────────────────────
class JsonBackend:
    name = "json"

    def load_signals(self) -> list:
        return memory.load_signals()

    def save_signals(self, ledger: list) -> None:
        memory.save_signals(ledger)

    def load_digests(self) -> dict:
        return memory.load_digests()

    def save_digests(self, digests: dict, compressed: Optional[list] = None) -> None:
        memory.save_digests(digests)

    def load_tickers(self) -> InMemoryStore:
        return memory.load_tickers()

    def save_tickers(self, store) -> None:
        memory.save_tickers(store)

    def close(self) -> None:
        pass


# ──────────────────────────────────────────────
# MongoDB backend
# ──────────────────────────────────────────────
def _clean(doc: dict, *drop: str) -> dict:
    return {k: v for k, v in doc.items() if k not in ("_id",) + drop}


class MongoBackend:
    """
    signals          one doc per signal, unique `id`
    digests          one doc per run, unique `at`; `compressed: true` once folded into a weekly summary (never deleted)
    weekly_digests   one doc per ISO week, unique `week`
    tickers          LangGraph MongoDBStore (namespace ("tickers",)), one doc per ticker
    """
    name = "mongo"

    def __init__(self, uri: str = None, db_name: str = None, client=None):
        if client is None:
            if MongoClient is None:
                raise RuntimeError("pymongo is not installed")
            if not (uri or config.MONGODB_URI):
                raise RuntimeError("MONGODB_URI is not set")
            client = MongoClient(
                uri or config.MONGODB_URI,
                serverSelectionTimeoutMS=config.MONGO_TIMEOUT_MS,
                appName=config.MONGO_APP_NAME,
            )
        self.client = client
        self.db = client[db_name or config.MONGODB_DB]
        self.client.admin.command("ping")
        self._ensure_indexes()
        self._store = None
        self._snapshot: dict = {}

    def _ensure_indexes(self) -> None:
        self.db.signals.create_index("id", unique=True)
        self.db.signals.create_index([("ticker", 1), ("status", 1)])
        self.db.digests.create_index("at", unique=True)
        self.db.weekly_digests.create_index("week", unique=True)

    # signals ------------------------------------------------------------
    def load_signals(self) -> list:
        docs = [_clean(d) for d in self.db.signals.find({})]
        ledger = [s for s in docs if s.get("id") and s.get("ticker") and s.get("action")]
        ledger.sort(key=lambda s: s.get("opened_at") or "")
        if ledger:
            open_n = len([s for s in ledger if s.get("status") == "open"])
            print(f"  🧠 Ledger (MongoDB): {len(ledger)} signals ({open_n} open)")
        return ledger

    def save_signals(self, ledger: list) -> None:
        ops = [ReplaceOne({"id": s["id"]}, dict(s), upsert=True) for s in ledger if s.get("id")]
        if ops:
            self.db.signals.bulk_write(ops, ordered=False)

    # digests ------------------------------------------------------------
    def load_digests(self) -> dict:
        recent = [_clean(d, "compressed") for d in self.db.digests.find({"compressed": {"$ne": True}})]
        recent.sort(key=lambda d: d.get("at") or "")
        weekly = [_clean(w) for w in self.db.weekly_digests.find({})]
        weekly.sort(key=lambda w: w.get("week") or "")
        return {"recent": recent[-config.MAX_RECENT_DIGESTS:],
                "weekly": weekly[-config.MAX_WEEKLY_DIGESTS:]}

    def load_all_digests(self) -> list:
        """Full digest history incl. compressed ones (with their `compressed` flag), sorted by `at`. Backup only."""
        docs = [{**_clean(d), "compressed": bool(d.get("compressed"))} for d in self.db.digests.find({})]
        docs.sort(key=lambda d: d.get("at") or "")
        return docs

    def load_all_weekly(self) -> list:
        """Every weekly summary ever written, sorted by `week`. Backup only."""
        docs = [_clean(w) for w in self.db.weekly_digests.find({})]
        docs.sort(key=lambda w: w.get("week") or "")
        return docs

    def save_digests(self, digests: dict, compressed: Optional[list] = None) -> None:
        ops = [ReplaceOne({"at": d["at"]}, {**d, "compressed": False}, upsert=True)
               for d in digests.get("recent", []) if d.get("at")]
        ops += [ReplaceOne({"at": d["at"]}, {**d, "compressed": True}, upsert=True)
                for d in (compressed or []) if d.get("at")]
        if ops:
            self.db.digests.bulk_write(ops, ordered=False)
        weekly = [ReplaceOne({"week": w["week"]}, dict(w), upsert=True)
                  for w in digests.get("weekly", []) if w.get("week")]
        if weekly:
            self.db.weekly_digests.bulk_write(weekly, ordered=False)

    # tickers ------------------------------------------------------------
    def _persistent_store(self):
        """Official LangGraph MongoDBStore (langgraph-store-mongodb) on the `tickers` collection."""
        if self._store is None:
            from langgraph.store.mongodb import MongoDBStore
            self._store = MongoDBStore(self.db[TICKER_COLLECTION])
        return self._store

    def load_tickers(self) -> InMemoryStore:
        """Read all notes into a working InMemoryStore, so mid-run reads can't fail on the network."""
        working = InMemoryStore()
        items = self._persistent_store().search(memory.TICKER_NS, limit=100000)
        self._snapshot = {i.key: i.value for i in items if isinstance(i.value, dict)}
        for key, value in self._snapshot.items():
            working.put(memory.TICKER_NS, key, value)
        return working

    def save_tickers(self, store) -> None:
        """Write notes that changed since load_tickers() to the persistent MongoDBStore."""
        persistent = self._persistent_store()
        for key, value in memory.dump_tickers(store).items():
            if self._snapshot.get(key) != value:
                persistent.put(memory.TICKER_NS, key, value)
                self._snapshot[key] = value

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


# ──────────────────────────────────────────────
# Selection + fail-safe loading
# ──────────────────────────────────────────────
def backend_mode() -> str:
    """Resolve MEMORY_BACKEND to "json" or "mongo"."""
    mode = config.MEMORY_BACKEND
    if mode not in ("auto", "json", "mongo"):
        print(f"  ⚠ Unknown MEMORY_BACKEND={mode!r} — using auto")
        mode = "auto"
    if mode == "auto":
        return "mongo" if config.MONGODB_URI else "json"
    return mode


class Loaded(NamedTuple):
    backend: object
    writable: bool     # False → skip every memory write this run
    ledger: list
    digests: dict
    store: object
    warning: str = ""  # short, secret-free text for the alert when Mongo was expected but unavailable


MEMORY_OFFLINE_WARNING = "⚠️ Memory offline: using last backup, nothing saved this run"


def open_memory() -> Loaded:
    """Pick the backend and load memory. Never raises: Mongo trouble → read-only JSON snapshot."""
    mode = backend_mode()
    if mode == "mongo":
        backend = None
        try:
            backend = MongoBackend()
            loaded = Loaded(backend, not config.DRY_RUN, backend.load_signals(),
                            backend.load_digests(), backend.load_tickers())
            print(f"  🍃 Memory backend: MongoDB ({config.MONGODB_DB})")
            return loaded
        except Exception as e:
            print("  " + "!" * 60)
            print(f"  ⚠⚠ MongoDB unavailable ({type(e).__name__}: {str(e)[:200]})")
            print("  ⚠⚠ Falling back to the JSON snapshot in memory/ (READ-ONLY).")
            print("  ⚠⚠ Memory will NOT be updated this run. The alert is still delivered.")
            print("  " + "!" * 60)
            if backend is not None:
                backend.close()
        json_backend = JsonBackend()
        return Loaded(json_backend, False, json_backend.load_signals(),
                      json_backend.load_digests(), json_backend.load_tickers(),
                      MEMORY_OFFLINE_WARNING)
    backend = JsonBackend()
    print("  📁 Memory backend: JSON files")
    return Loaded(backend, not config.DRY_RUN, backend.load_signals(),
                  backend.load_digests(), backend.load_tickers())
