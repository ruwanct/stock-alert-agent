"""Storage backend tests — JSON/Mongo selection, Mongo semantics (mongomock), fail-safe, scripts."""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from langgraph.store.memory import InMemoryStore

from bot import config, data, graph, memory, storage
from bot.schemas import DailyDigest
from tests.test_graph import FakeChain, good, quote

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None



def _replace_one_bulk_write(self, requests, ordered=True, **kwargs):
    """mongomock 4.3.0 can't execute pymongo 4.19 bulk ops (unexpected `sort` kwarg); emulate ReplaceOne."""
    for op in requests:
        self.replace_one(op._filter, op._doc, upsert=op._upsert)


if mongomock is not None:
    mongomock.collection.Collection.bulk_write = _replace_one_bulk_write


class CountingStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.put_keys = []

    def put(self, namespace, key, value, *args, **kwargs):
        self.put_keys.append(key)
        return super().put(namespace, key, value, *args, **kwargs)


ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def signal(i, status="open", ticker="NVDA"):
    return {"id": f"id{i}", "ticker": ticker, "action": "BUY", "entry_price": 100.0, "confidence": "HIGH",
            "reasoning": "r", "opened_at": f"2026-10-1{i}T10:00:00+00:00", "status": status, "reaffirmed": 0,
            "last_price": 100.0, "return_pct": 0.0, "closed_at": None, "outcome": None}


def digest(i):
    return {"at": f"2026-10-{i:02d}T10:00:00+00:00", "session": "x", "summary": f"s{i}", "themes": ["t"]}


class PatchedConfig(unittest.TestCase):
    def patch_config(self, **kw):
        tmp = Path(tempfile.mkdtemp())
        values = dict(SIGNALS_PATH=tmp / "signals.json", DIGESTS_PATH=tmp / "digests.json",
                      TICKERS_PATH=tmp / "tickers.json", MONGODB_URI="", MEMORY_BACKEND="auto", DRY_RUN=False)
        values.update(kw)
        for k, v in values.items():
            p = mock.patch.object(config, k, v)
            p.start()
            self.addCleanup(p.stop)
        return tmp


class SelectionTests(PatchedConfig):
    def test_auto_without_uri_is_json(self):
        self.patch_config()
        self.assertEqual(storage.backend_mode(), "json")

    def test_auto_with_uri_is_mongo(self):
        self.patch_config(MONGODB_URI="mongodb+srv://x")
        self.assertEqual(storage.backend_mode(), "mongo")

    def test_force_json_even_with_uri(self):
        self.patch_config(MONGODB_URI="mongodb+srv://x", MEMORY_BACKEND="json")
        self.assertEqual(storage.backend_mode(), "json")
        self.assertIsInstance(storage.open_memory().backend, storage.JsonBackend)

    def test_force_mongo_without_uri_falls_back_read_only(self):
        self.patch_config(MEMORY_BACKEND="mongo")
        loaded = storage.open_memory()
        self.assertIsInstance(loaded.backend, storage.JsonBackend)
        self.assertFalse(loaded.writable)

    def test_unknown_value_is_auto(self):
        self.patch_config(MEMORY_BACKEND="banana", MONGODB_URI="mongodb+srv://x")
        self.assertEqual(storage.backend_mode(), "mongo")

    def test_json_backend_writable_unless_dry_run(self):
        self.patch_config()
        self.assertTrue(storage.open_memory().writable)
        config.DRY_RUN = True
        self.assertFalse(storage.open_memory().writable)


@unittest.skipIf(mongomock is None, "mongomock not installed (pip install -r requirements-dev.txt)")
class MongoBackendTests(PatchedConfig):
    def make(self, client=None):
        b = storage.MongoBackend(client=client or mongomock.MongoClient(), db_name="testdb")
        # MongoDBStore needs a real server (mongomock lacks create_index(keys=...)); any BaseStore
        # stands in for it here, the backend only uses its put/search interface.
        b._store = CountingStore()
        return b

    def test_indexes_created_idempotently(self):
        client = mongomock.MongoClient()
        self.make(client)
        b = self.make(client)
        self.assertIn("id_1", b.db.signals.index_information())
        self.assertTrue(b.db.signals.index_information()["id_1"]["unique"])
        self.assertIn("ticker_1_status_1", b.db.signals.index_information())
        self.assertTrue(b.db.digests.index_information()["at_1"]["unique"])
        self.assertTrue(b.db.weekly_digests.index_information()["week_1"]["unique"])

    def test_signals_upsert_idempotent_and_no_object_id(self):
        b = self.make()
        ledger = [signal(1), signal(2)]
        b.save_signals(ledger)
        b.save_signals(ledger)
        self.assertEqual(b.db.signals.count_documents({}), 2)
        ledger[0]["status"] = "closed"
        b.save_signals(ledger)
        loaded = b.load_signals()
        self.assertEqual(len(loaded), 2)
        self.assertTrue(all("_id" not in s for s in loaded))
        self.assertEqual([s["status"] for s in loaded], ["closed", "open"])
        self.assertEqual(loaded[0], ledger[0])

    def test_digests_compression_flag_and_load_shape(self):
        b = self.make()
        d = {"recent": [], "weekly": []}
        compressed = []
        for i in range(1, config.MAX_RECENT_DIGESTS + 4):
            compressed.clear()
            memory.add_digest(d, digest(i), lambda t: "weekly sum", NOW, compressed_out=compressed)
            b.save_digests(d, compressed)
        total = config.MAX_RECENT_DIGESTS + 3
        self.assertEqual(b.db.digests.count_documents({}), total)  # nothing is ever deleted
        self.assertEqual(b.db.digests.count_documents({"compressed": True}), 3)
        loaded = b.load_digests()
        self.assertEqual(set(loaded), {"recent", "weekly"})
        self.assertEqual(len(loaded["recent"]), config.MAX_RECENT_DIGESTS)
        self.assertEqual([x["summary"] for x in loaded["recent"]], [x["summary"] for x in d["recent"]])
        self.assertTrue(all(set(x) == {"at", "session", "summary", "themes"} for x in loaded["recent"]))
        self.assertEqual(loaded["recent"], sorted(loaded["recent"], key=lambda x: x["at"]))
        self.assertEqual(loaded["weekly"], d["weekly"])

    def test_weekly_upsert_by_week(self):
        b = self.make()
        b.save_digests({"recent": [], "weekly": [{"week": "2026-W41", "summary": "a"}]})
        b.save_digests({"recent": [], "weekly": [{"week": "2026-W41", "summary": "b"},
                                                 {"week": "2026-W40", "summary": "c"}]})
        self.assertEqual(b.db.weekly_digests.count_documents({}), 2)
        self.assertEqual(b.load_digests()["weekly"],
                         [{"week": "2026-W40", "summary": "c"}, {"week": "2026-W41", "summary": "b"}])

    def test_tickers_round_trip_only_writes_changes(self):
        b = self.make()
        b._store.put(memory.TICKER_NS, "AAPL", {"mention_count": 1})
        working = b.load_tickers()
        self.assertEqual(memory.dump_tickers(working), {"AAPL": {"mention_count": 1}})
        working.put(memory.TICKER_NS, "NVDA", {"mention_count": 2})
        b._store.put_keys.clear()
        b.save_tickers(working)
        self.assertEqual(b._store.put_keys, ["NVDA"])
        self.assertEqual(b._store.get(memory.TICKER_NS, "NVDA").value, {"mention_count": 2})
        fresh = b.load_tickers()
        self.assertEqual(set(memory.dump_tickers(fresh)), {"AAPL", "NVDA"})

    def test_connection_options(self):
        with mock.patch.object(storage, "MongoClient") as mc:
            mc.return_value = mongomock.MongoClient()
            storage.MongoBackend(uri="mongodb+srv://u:p@h/", db_name="d")
            kwargs = mc.call_args.kwargs
            self.assertEqual(kwargs["serverSelectionTimeoutMS"], 8000)
            self.assertEqual(kwargs["appName"], "stock-alert-bot")

    def test_official_store_is_the_persistent_store(self):
        b = storage.MongoBackend(client=mongomock.MongoClient(), db_name="testdb")
        with mock.patch("langgraph.store.mongodb.MongoDBStore") as cls:
            b._persistent_store()
            self.assertEqual(cls.call_args.args[0].name, storage.TICKER_COLLECTION)


@unittest.skipIf(mongomock is None, "mongomock not installed (pip install -r requirements-dev.txt)")
class GraphWithMongoTests(PatchedConfig):
    def run_graph(self, dry_run=False, client=None, uri="mongodb+srv://x", analysis=None):
        tmp = self.patch_config(MONGODB_URI=uri, DRY_RUN=dry_run)
        client = client or mongomock.MongoClient()
        data.QUOTE_CACHE.clear()
        data.QUOTE_CACHE["NVDA"] = quote("NVDA", 100.0)

        real = storage.MongoBackend

        def factory():
            b = real(client=client, db_name="testdb")
            b._store = InMemoryStore()
            return b

        digest_chain = FakeChain([DailyDigest(summary="sum", themes=["ai"])])
        patches = [
            mock.patch.object(storage, "MongoBackend", factory),
            mock.patch.object(graph.data, "build_watchlist", lambda: ["NVDA"]),
            mock.patch.object(graph.data, "aggregate_news", lambda c: []),
            mock.patch.object(graph.chains, "build_analysis_chain", lambda: analysis or FakeChain([good()])),
            mock.patch.object(graph.chains, "build_digest_chain", lambda: digest_chain),
            mock.patch.object(graph.delivery, "send_telegram", lambda t: True),
            mock.patch.object(graph.delivery, "send_discord", lambda e: True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return graph.build_graph().invoke({}), client, tmp

    def test_run_writes_to_mongo_not_json(self):
        result, client, tmp = self.run_graph()
        self.assertEqual(client["testdb"].signals.count_documents({}), 1)
        self.assertEqual(client["testdb"].digests.count_documents({}), 1)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertTrue(result["delivered"]["telegram"])

    def test_dry_run_writes_nothing(self):
        result, client, tmp = self.run_graph(dry_run=True)
        for name in ("signals", "digests", "weekly_digests"):
            self.assertEqual(client["testdb"][name].count_documents({}), 0)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertTrue(result["delivered"]["dry_run"])

    def test_save_failure_still_delivers(self):
        client = mongomock.MongoClient()
        orig = storage.MongoBackend.save_signals
        storage.MongoBackend.save_signals = mock.Mock(side_effect=RuntimeError("write failed"))
        self.addCleanup(setattr, storage.MongoBackend, "save_signals", orig)
        client = mongomock.MongoClient()
        result, client, _ = self.run_graph(client=client)
        self.assertTrue(result["delivered"]["telegram"])
        self.assertEqual(client["testdb"].signals.count_documents({}), 0)
        self.assertEqual(client["testdb"].digests.count_documents({}), 1)  # other saves still attempted

    def test_connection_failure_falls_back_to_read_only_json(self):
        tmp = self.patch_config(MONGODB_URI="mongodb+srv://x")
        snapshot = [signal(1)]
        (tmp / "signals.json").write_text(json.dumps(snapshot))
        before = {p.name: p.read_text() for p in tmp.iterdir()}
        data.QUOTE_CACHE.clear()
        data.QUOTE_CACHE["NVDA"] = quote("NVDA", 100.0)
        patches = [
            mock.patch.object(storage, "MongoClient", side_effect=RuntimeError("no route to host")),
            mock.patch.object(graph.data, "build_watchlist", lambda: ["NVDA"]),
            mock.patch.object(graph.data, "aggregate_news", lambda c: []),
            mock.patch.object(graph.chains, "build_analysis_chain", lambda: FakeChain([good()])),
            mock.patch.object(graph.chains, "build_digest_chain",
                              lambda: FakeChain([DailyDigest(summary="s", themes=[])])),
            mock.patch.object(graph.delivery, "send_telegram", lambda t: True),
            mock.patch.object(graph.delivery, "send_discord", lambda e: True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        result = graph.build_graph().invoke({})
        self.assertTrue(result["delivered"]["telegram"])
        self.assertFalse(result["writable"])
        self.assertEqual(result["ledger"][0]["id"], "id1")  # read the JSON snapshot
        self.assertEqual({p.name: p.read_text() for p in tmp.iterdir()}, before)  # nothing written


@unittest.skipIf(mongomock is None, "mongomock not installed (pip install -r requirements-dev.txt)")
class ScriptTests(PatchedConfig):
    def seed_json(self):
        tmp = self.patch_config()
        memory.save_signals([signal(1), signal(2, status="closed")])
        d = {"recent": [digest(1), digest(2)], "weekly": [{"week": "2026-W40", "summary": "w"}]}
        memory.save_digests(d)
        store = InMemoryStore()
        store.put(memory.TICKER_NS, "NVDA", {"mention_count": 3})
        memory.save_tickers(store)
        return tmp

    def backend(self, client):
        b = storage.MongoBackend(client=client, db_name="testdb")
        b._store = self.shared_store
        return b

    def setUp(self):
        self.shared_store = InMemoryStore()

    def test_migration_idempotent_and_dry_run(self):
        self.seed_json()
        migrate = load_script("migrate_json_to_mongo")
        client = mongomock.MongoClient()
        counts = migrate.migrate(None, dry_run=True)
        self.assertEqual(counts, {"signals": 2, "digests": 2, "weekly_digests": 1, "tickers": 1})
        self.assertEqual(client["testdb"].signals.count_documents({}), 0)
        for _ in range(2):
            b = self.backend(client)
            self.assertEqual(migrate.migrate(b, False), counts)
        db = client["testdb"]
        self.assertEqual(db.signals.count_documents({}), 2)
        self.assertEqual(db.digests.count_documents({}), 2)
        self.assertEqual(db.weekly_digests.count_documents({}), 1)
        self.assertEqual(self.shared_store.get(memory.TICKER_NS, "NVDA").value, {"mention_count": 3})

    def test_migration_without_uri_explains(self):
        self.patch_config()
        migrate = load_script("migrate_json_to_mongo")
        with mock.patch("builtins.print") as pr:
            self.assertEqual(migrate.main([]), 1)
        self.assertIn("MONGODB_URI", pr.call_args.args[0])

    def test_export_produces_files_json_backend_can_load(self):
        self.seed_json()
        migrate, export = load_script("migrate_json_to_mongo"), load_script("export_memory")
        client = mongomock.MongoClient()
        migrate.migrate(self.backend(client), False)
        before = {name: memory._load_json(p, list if name == "signals" else dict, None)
                  for name, p in (("signals", config.SIGNALS_PATH), ("digests", config.DIGESTS_PATH))}
        tmp = self.patch_config()  # fresh empty memory dir
        export.export(self.backend(client))
        jb = storage.JsonBackend()
        self.assertEqual(jb.load_signals(), sorted(before["signals"], key=lambda s: s["opened_at"]))
        self.assertEqual(jb.load_digests(), before["digests"])
        self.assertEqual(memory.dump_tickers(jb.load_tickers()), {"NVDA": {"mention_count": 3}})
        self.assertTrue((tmp / "tickers.json").exists())


if __name__ == "__main__":
    unittest.main()
