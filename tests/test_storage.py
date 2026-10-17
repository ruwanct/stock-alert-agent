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
                      TICKERS_PATH=tmp / "tickers.json", ARCHIVE_DIR=tmp / "archive", MONGODB_URI="", MEMORY_BACKEND="auto", DRY_RUN=False)
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

    def test_fallback_is_degraded_with_warning(self):
        self.patch_config(MONGODB_URI="mongodb+srv://x")
        with mock.patch.object(storage, "MongoClient", side_effect=RuntimeError("no route to host 10.0.0.1")):
            loaded = storage.open_memory()
        self.assertFalse(loaded.writable)
        self.assertEqual(loaded.warning, storage.MEMORY_OFFLINE_WARNING)
        self.assertIn("Memory offline", loaded.warning)
        self.assertNotIn("10.0.0.1", loaded.warning)

    def test_json_by_choice_has_no_warning(self):
        self.patch_config()
        self.assertEqual(storage.open_memory().warning, "")
        self.patch_config(MONGODB_URI="mongodb+srv://x", MEMORY_BACKEND="json")
        self.assertEqual(storage.open_memory().warning, "")
        config.DRY_RUN = True
        self.assertEqual(storage.open_memory().warning, "")

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
        self.assertEqual(result["memory_warning"], "⚠️ Memory partially saved: signals failed")

    def test_connection_failure_falls_back_to_read_only_json(self):
        tmp = self.patch_config(MONGODB_URI="mongodb+srv://x")
        snapshot = [signal(1)]
        (tmp / "signals.json").write_text(json.dumps(snapshot))
        before = {p.name: p.read_text() for p in tmp.iterdir()}
        sent_tg, sent_dc = [], []
        data.QUOTE_CACHE.clear()
        data.QUOTE_CACHE["NVDA"] = quote("NVDA", 100.0)
        patches = [
            mock.patch.object(storage, "MongoClient", side_effect=RuntimeError("no route to host")),
            mock.patch.object(graph.data, "build_watchlist", lambda: ["NVDA"]),
            mock.patch.object(graph.data, "aggregate_news", lambda c: []),
            mock.patch.object(graph.chains, "build_analysis_chain", lambda: FakeChain([good()])),
            mock.patch.object(graph.chains, "build_digest_chain",
                              lambda: FakeChain([DailyDigest(summary="s", themes=[])])),
            mock.patch.object(graph.delivery, "send_telegram", lambda t: sent_tg.append(t) or True),
            mock.patch.object(graph.delivery, "send_discord", lambda e: sent_dc.append(e) or True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        result = graph.build_graph().invoke({})
        self.assertTrue(result["delivered"]["telegram"])
        self.assertEqual(result["memory_warning"], storage.MEMORY_OFFLINE_WARNING)
        self.assertIn(storage.MEMORY_OFFLINE_WARNING, sent_tg[0])
        self.assertNotIn("no route to host", sent_tg[0])
        self.assertEqual(sent_dc[0]["fields"][0]["name"], "⚠️ Memory")
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


def closed_sig(i, opened, closed, status="closed"):
    s = signal(1, status=status)
    s.update(id=f"c{i}", opened_at=opened, closed_at=closed)
    return s


@unittest.skipIf(mongomock is None, "mongomock not installed (pip install -r requirements-dev.txt)")
class ArchiveBackupTests(PatchedConfig):
    def setUp(self):
        self.shared_store = InMemoryStore()
        self.export = load_script("export_memory")
        self.migrate = load_script("migrate_json_to_mongo")
        self.tmp = self.patch_config()
        self.client = mongomock.MongoClient()
        self.b = self.backend()
        self.b.save_signals([
            signal(1),                                                              # open, always main
            closed_sig(1, "2026-09-20T10:00:00+00:00", "2026-09-25T10:00:00+00:00"),  # inside 90d
            closed_sig(2, "2026-03-01T10:00:00+00:00", "2026-03-06T10:00:00+00:00"),  # old, 2026
            closed_sig(3, "2025-12-29T10:00:00+00:00", "2026-01-03T10:00:00+00:00"),  # old, year of closed_at
            closed_sig(4, "2025-05-01T10:00:00+00:00", "2025-05-06T10:00:00+00:00"),  # old, 2025
            {**closed_sig(5, "2025-06-01T10:00:00+00:00", None)},                     # no closed_at -> opened_at
        ])
        d = {"recent": [digest(3), digest(4)], "weekly": [{"week": "2025-W52", "summary": "a"},
                                                          {"week": "2026-W40", "summary": "b"}]}
        self.b.save_digests(d, compressed=[{"at": "2025-12-30T10:00:00+00:00", "session": "x", "summary": "old"},
                                           digest(1)])
        store = InMemoryStore()
        store.put(memory.TICKER_NS, "NVDA", {"mention_count": 3})
        self.b._store = self.shared_store
        self.b.save_tickers(store)

    def backend(self, client=None):
        b = storage.MongoBackend(client=client or self.client, db_name="testdb")
        b._store = self.shared_store
        return b

    def read(self, name):
        return json.loads((self.tmp / name).read_text(encoding="utf-8"))

    def do_export(self):
        return self.export.export(self.b, now=NOW)

    def test_signal_window_split(self):
        self.do_export()
        self.assertEqual({s["id"] for s in self.read("signals.json")}, {"id1", "c1"})
        self.assertEqual([s["id"] for s in self.read("archive/signals-2026.json")], ["c3", "c2"])
        self.assertEqual([s["id"] for s in self.read("archive/signals-2025.json")], ["c4", "c5"])
        self.assertFalse((self.tmp / "archive/signals-2024.json").exists())

    def test_main_files_keep_bot_shape_and_digest_archives_are_complete(self):
        self.do_export()
        main = self.read("digests.json")
        self.assertEqual([d["at"] for d in main["recent"]], [digest(3)["at"], digest(4)["at"]])
        self.assertTrue(all("compressed" not in d for d in main["recent"]))
        self.assertEqual(len(main["weekly"]), 2)
        d2026 = self.read("archive/digests-2026.json")
        self.assertEqual([(d["at"][:10], d["compressed"]) for d in d2026],
                         [("2026-10-01", True), ("2026-10-03", False), ("2026-10-04", False)])
        d2025 = self.read("archive/digests-2025.json")
        self.assertEqual([(d["summary"], d["compressed"]) for d in d2025], [("old", True)])
        self.assertEqual([w["week"] for w in self.read("archive/weekly-2025.json")], ["2025-W52"])
        self.assertEqual([w["week"] for w in self.read("archive/weekly-2026.json")], ["2026-W40"])

    def test_digests_main_not_changed_for_bot(self):
        self.assertEqual(set(self.b.load_digests()), {"recent", "weekly"})
        self.assertEqual(len(self.b.load_all_digests()), 4)
        self.assertEqual(len(self.b.load_digests()["recent"]), 2)

    def test_reexport_is_byte_identical_and_unwritten(self):
        self.do_export()
        files = sorted((self.tmp / "archive").glob("*.json"))
        before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in files}
        counts = self.do_export()
        self.assertEqual(counts["files_written"], 0)
        for f in files:
            self.assertEqual((f.read_bytes(), f.stat().st_mtime_ns), before[f.name])
        self.assertTrue(before["signals-2025.json"][0].endswith(b"\n"))

    def test_changed_year_is_overwritten_and_empty_years_are_kept(self):
        self.do_export()
        stale = self.tmp / "archive" / "signals-2019.json"
        stale.write_text("[]\n", encoding="utf-8")
        self.b.save_signals([closed_sig(9, "2025-07-01T10:00:00+00:00", "2025-07-06T10:00:00+00:00")])
        self.do_export()
        self.assertTrue(stale.exists())
        self.assertEqual([s["id"] for s in self.read("archive/signals-2025.json")], ["c4", "c5", "c9"])

    def test_full_restore_from_backup_is_idempotent(self):
        before = {
            "signals": sorted(self.b.load_signals(), key=lambda s: s["id"]),
            "digests": self.b.load_all_digests(),
            "weekly": self.b.load_all_weekly(),
        }
        self.do_export()
        for _ in range(2):  # wipe, restore, restore again
            fresh = mongomock.MongoClient()
            self.shared_store = InMemoryStore()
            nb = self.backend(fresh)
            counts = self.migrate.migrate(nb, False)
            self.assertEqual(counts["signals"], 6)
            self.assertEqual(counts["digests"], 4)
            self.assertEqual(counts["weekly_digests"], 2)
            self.assertEqual(counts["archive_files"], 6)
            self.migrate.migrate(nb, False)
            self.assertEqual(sorted(nb.load_signals(), key=lambda s: s["id"]), before["signals"])
            self.assertEqual(nb.load_all_digests(), before["digests"])
            self.assertEqual(nb.load_all_weekly(), before["weekly"])
            self.assertEqual(self.shared_store.get(memory.TICKER_NS, "NVDA").value, {"mention_count": 3})
        dry = self.migrate.migrate(None, dry_run=True)
        self.assertEqual((dry["signals"], dry["digests"], dry["archive_files"]), (6, 4, 6))

    def test_signals_json_wins_on_overlap(self):
        self.do_export()
        newer = closed_sig(4, "2025-05-01T10:00:00+00:00", "2025-05-06T10:00:00+00:00")
        newer["return_pct"] = 7.5
        memory.save_signals(self.read("signals.json") + [newer])
        nb = self.backend(mongomock.MongoClient())
        self.migrate.migrate(nb, False)
        by_id = {s["id"]: s for s in nb.load_signals()}
        self.assertEqual(by_id["c4"]["return_pct"], 7.5)
        self.assertEqual(len(by_id), 6)

    def test_json_fallback_loads_only_main_files(self):
        self.do_export()
        jb = storage.JsonBackend()
        self.assertEqual({s["id"] for s in jb.load_signals()}, {"id1", "c1"})
        self.assertEqual(len(jb.load_digests()["recent"]), 2)
        self.assertEqual(memory.dump_tickers(jb.load_tickers()), {"NVDA": {"mention_count": 3}})


if __name__ == "__main__":
    unittest.main()
