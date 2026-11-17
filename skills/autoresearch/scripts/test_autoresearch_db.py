"""Regression tests for experiment lifecycle and interrupted recovery."""

import tempfile
import sqlite3
import unittest
from pathlib import Path

import autoresearch_db as db


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(str(Path(self.tmp.name) / "state.db"))
        db.init_db(self.conn)
        db.put_contract(self.conn, "1", {"metric": "auc"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def start(self, exp_id, iteration=0, commit="base", parent="before", **extra):
        record = dict(exp_id=exp_id, iteration=iteration, contract_version="1",
                      workspace="branch/" + str(exp_id), start_time="2026-01-01T00:00:00Z",
                      commit=commit, parent_commit=parent, hypothesis="test", status="running")
        record.update(extra)
        db.put_experiment(self.conn, record)

    def finish(self, exp_id, status="kept"):
        db.put_experiment(self.conn, dict(exp_id=exp_id, status=status,
            end_time="2026-01-01T01:00:00Z", metrics={"auc": .8}, summary="observations"))

    def baseline(self):
        self.start("baseline")
        self.finish("baseline")

    def import_baseline(self, **changes):
        db.put_contract(self.conn, "2", {"BASELINE": {"commit": "historical"}})
        record = dict(exp_id="import", iteration=0, contract_version="2",
                      commit="historical", parent_commit="contract-base",
                      status="kept", metrics={"auc": .8}, summary="Imported; code reviewed")
        record.update(changes)
        db.put_experiment(self.conn, record)

    def test_import_keeps_source_sha_but_branches_from_contract_baseline(self):
        self.import_baseline()
        self.import_baseline()  # replay does not create another baseline
        baseline = db.latest_kept(self.conn)
        self.assertEqual(baseline["commit"], "historical")
        self.assertIsNone(baseline["start_time"])
        self.assertEqual(db.recovery_state(self.conn, "contract-base")["state"], "clean")
        self.assertEqual(db.recovery_state(self.conn, "historical")["state"], "inconsistent")
        with self.assertRaises(ValueError):
            self.start("wrong", 1, "wrong", "historical", contract_version="2")
        self.start("candidate", 1, "candidate", "contract-base", contract_version="2")
        self.assertEqual(db.recovery_state(self.conn, "candidate")["state"], "integrated-pending")
        self.finish("candidate")
        self.assertEqual(db.recovery_state(self.conn, "candidate")["state"], "clean")
        self.start("next", 2, "next", "candidate", contract_version="2")

    def test_import_requires_metrics_source_and_anchor(self):
        for changes in ({"metrics": {}}, {"metrics": None},
                        {"parent_commit": None}, {"commit": "unconfirmed"},
                        {"status": "running"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.import_baseline(**changes)

    def test_baseline_retry_preserves_history(self):
        self.assertEqual(db.recovery_state(self.conn, "before")["state"], "fresh")
        self.start("b1")
        self.finish("b1", "failed")
        self.assertEqual(db.recovery_state(self.conn, "base")["state"], "baseline-required")
        self.baseline()
        self.assertEqual(len(db.experiments(self.conn)), 2)
        self.assertEqual(db.recovery_state(self.conn, "base")["next_iteration"], 1)

    def test_all_dropped_and_failed_batches_advance(self):
        self.baseline()
        self.start("a", 1, "a", "base")
        self.start("b", 1, "b", "base")
        self.finish("a", "dropped")
        self.finish("b", "failed")
        state = db.recovery_state(self.conn, "base")
        self.assertEqual((state["state"], state["next_iteration"]), ("clean", 2))
        with self.assertRaises(ValueError):
            self.start("reuse", 1, "reuse", "base")
        self.start("c", 2, "c", "base")
        self.finish("c", "failed")
        self.assertEqual(db.recovery_state(self.conn, "base")["next_iteration"], 3)

    def test_crash_after_fast_forward(self):
        self.baseline()
        self.start("a", 1, "a", "base")
        self.start("b", 1, "b", "base")
        self.finish("a", "running")
        self.assertEqual(db.recovery_state(self.conn, "a")["state"], "integrated-pending")
        self.finish("a")
        self.assertEqual(db.recovery_state(self.conn, "a")["state"], "interrupted")
        self.finish("b", "dropped")
        self.assertEqual(db.recovery_state(self.conn, "a")["state"], "clean")

    def test_ambiguous_commit_and_external_head(self):
        self.baseline()
        self.start("a", 1, "same", "base")
        self.start("b", 1, "same", "base")
        self.assertEqual(db.recovery_state(self.conn, "same")["state"], "inconsistent")
        self.assertEqual(db.recovery_state(self.conn, "external")["state"], "inconsistent")
        self.assertEqual(db.recovery_state(self.conn, "base")["state"], "interrupted")

    def test_input_validation_and_terminal_immutability(self):
        for extra in ({"exp_id": None}, {"iteration": True}, {"log_path": "oops"},
                      {"status": "kept"}, {"hypothesis": " "}, {"status": []}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.start("bad", **extra) if "exp_id" not in extra else self.start(None)
        self.start("b")
        with self.assertRaises(ValueError):
            db.put_experiment(self.conn, {"exp_id": "b", "status": "dropped"})
        with self.assertRaises(ValueError):
            db.put_experiment(self.conn, {"exp_id": "b", "metrics": {"auc": float("nan")}})
        self.finish("b")
        self.finish("b")  # identical retries are idempotent
        with self.assertRaises(ValueError):
            db.put_experiment(self.conn, {"exp_id": "b", "summary": "overwrite"})
        record = db.experiments(self.conn)[0]
        self.assertEqual(record["contract_version"], "1.0")
        self.assertTrue(all(record[field] is not None for field in db.IDENTITY_FIELDS))

    def test_batch_boundaries(self):
        self.baseline()
        self.start("a", 1, "a", "base")
        with self.assertRaises(ValueError):
            self.start("b", 2, "b", "base")
        db.put_contract(self.conn, "2", {})
        with self.assertRaises(ValueError):
            self.start("c", 1, "c", "base", contract_version="2")
        self.finish("a")
        with self.assertRaises(ValueError):
            self.start("d", 2, "d", "base")
        self.start("e", 2, "e", "a")

    def test_schema_and_contract_constraints(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            db.put_contract(self.conn, "1.0", {"changed": True})
        self.conn.execute("DROP INDEX one_kept_per_iteration")
        self.conn.execute("CREATE UNIQUE INDEX one_kept_per_iteration ON experiments(exp_id) WHERE status = 'kept'")
        with self.assertRaises(ValueError):
            db.validate_schema(self.conn)

    def test_second_winner_is_rejected_without_losing_running_record(self):
        self.baseline()
        self.start("a", 1, "a", "base")
        self.start("b", 1, "b", "base")
        self.finish("a")
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish("b")
        self.assertEqual(db.experiment_by_commit(self.conn, "b")[0]["status"], "running")

    def test_initialization_rolls_back_on_failure(self):
        conn = db.connect(str(Path(self.tmp.name) / "interrupted.db"))
        conn.set_authorizer(lambda action, arg1, arg2, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
            and arg1 == "user_version" and arg2 is not None else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            with conn:
                db.init_db(conn)
        conn.set_authorizer(None)
        self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        db.init_db(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
