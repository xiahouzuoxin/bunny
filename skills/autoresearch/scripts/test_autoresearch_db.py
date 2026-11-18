"""Regression tests for experiment lifecycle and interrupted recovery."""

import tempfile
import sqlite3
import subprocess
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
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
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

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def commit_file(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", "--", path)
        self.git("commit", "-qm", "test change")
        return self.git("rev-parse", "HEAD")

    def git_baseline(self, imported=False):
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        base = self.commit_file("model.py", "model = 1")
        if imported:
            self.import_baseline(parent_commit=base)
        else:
            self.start("baseline", commit=base)
            self.finish("baseline")
        return base

    def checkpoint(self, commit, version="1"):
        db.put_checkpoint(self.conn, str(self.repo), commit, version, ["out/REPORT.md"])

    def test_report_crash_recovery_and_next_adoption(self):
        base = self.git_baseline()
        original = db.latest_kept(self.conn)
        report = self.commit_file("out/REPORT.md", "baseline findings")
        self.assertEqual(db.recovery_state(self.conn, report)["state"], "inconsistent")
        self.checkpoint(report)
        self.checkpoint(report)
        self.assertEqual(len(db.list_checkpoints(self.conn)), 1)
        self.assertEqual(db.latest_kept(self.conn), original)
        state = db.recovery_state(self.conn, report)
        self.assertEqual((state["state"], state["research_parent"]), ("clean", report))
        with self.assertRaises(ValueError):
            self.start("wrong", 1, "wrong", base)
        self.start("winner", 1, "winner", report)
        self.assertEqual(db.recovery_state(self.conn, "winner")["state"], "integrated-pending")
        self.finish("winner")
        self.assertEqual(db.recovery_state(self.conn, "winner")["state"], "clean")
        self.start("next", 2, "next", "winner")

    def test_reports_follow_successive_winners_without_reusing_old_chain(self):
        self.git_baseline()
        first_report = self.commit_file("out/REPORT.md", "baseline")
        self.checkpoint(first_report)
        winner = self.commit_file("model.py", "model = 2")
        self.start("winner", 1, winner, first_report)
        self.start("loser", 1, "loser", first_report)
        self.finish("winner")
        self.assertEqual(db.recovery_state(self.conn, winner)["state"], "interrupted")
        self.finish("loser", "dropped")
        second_report = self.commit_file("out/REPORT.md", "winner findings")
        self.checkpoint(second_report)
        state = db.recovery_state(self.conn, second_report)
        self.assertEqual((state["state"], state["research_parent"]), ("clean", second_report))
        self.assertEqual(state["latest_kept"]["commit"], winner)
        self.assertEqual([item["kept_exp_id"] for item in db.list_checkpoints(self.conn)],
                         ["baseline", "winner"])
        self.checkpoint(first_report)  # replay an older checkpoint without moving the anchor
        self.assertEqual(db.research_parent(self.conn, db.latest_kept(self.conn)), second_report)
        with self.assertRaises(ValueError):
            self.start("stale", 2, "stale", first_report)
        self.start("next", 2, "next", second_report)

    def test_no_winner_and_imported_baseline_checkpoints(self):
        self.git_baseline(imported=True)
        first = self.commit_file("out/REPORT.md", "imported")
        self.checkpoint(first, "2")
        self.start("dropped", 1, "dropped", first, contract_version="2")
        self.finish("dropped", "dropped")
        second = self.commit_file("out/REPORT.md", "no improvement")
        self.checkpoint(second, "2")
        self.assertEqual(db.latest_kept(self.conn)["commit"], "historical")
        self.assertEqual(db.recovery_state(self.conn, second)["next_iteration"], 2)
        self.start("next", 2, "next", second, contract_version="2")

    def test_checkpoint_rejects_active_batch_and_unreviewed_changes(self):
        base = self.git_baseline()
        self.start("running", 1, "candidate", base)
        report = self.commit_file("out/REPORT.md", "findings")
        with self.assertRaises(ValueError):
            self.checkpoint(report)
        self.finish("running", "failed")
        self.checkpoint(report)
        model = self.commit_file("model.py", "model = 2")
        with self.assertRaises(ValueError):
            self.checkpoint(model)
        after_model = self.commit_file("out/REPORT.md", "more findings")
        with self.assertRaises(ValueError):
            self.checkpoint(after_model)
        self.assertEqual(db.recovery_state(self.conn, after_model)["state"], "inconsistent")

    def test_checkpoint_rejects_conflicting_retry_and_unknown_contract(self):
        self.git_baseline()
        report = self.commit_file("out/REPORT.md", "findings")
        with self.assertRaises(ValueError):
            self.checkpoint(report, "9")
        self.checkpoint(report)
        db.put_contract(self.conn, "2", {})
        with self.assertRaises(ValueError):
            self.checkpoint(report, "2")
        with self.assertRaises(ValueError):
            db.put_checkpoint(self.conn, str(self.repo), report, "1", ["out/REPORT.md", "model.py"])

    def test_checkpoint_rejects_empty_merge_and_non_head_commits(self):
        base = self.git_baseline()
        report = self.commit_file("out/REPORT.md", "findings")
        tree = self.git("rev-parse", report + "^{tree}")
        merge = self.git("commit-tree", tree, "-p", report, "-p", base, "-m", "merge")
        with self.assertRaises(ValueError):
            self.checkpoint(merge)
        self.git("checkout", "--detach", base)
        with self.assertRaises(ValueError):
            self.checkpoint(report)
        self.git("commit", "--allow-empty", "-qm", "empty")
        with self.assertRaises(ValueError):
            self.checkpoint(self.git("rev-parse", "HEAD"))
        self.assertEqual(db.list_checkpoints(self.conn), [])

    def test_v1_migration_preserves_records(self):
        self.baseline()
        before = db.experiments(self.conn)
        self.conn.execute("DROP TABLE checkpoints")
        self.conn.execute("PRAGMA user_version = 1")
        db.init_db(self.conn)
        db.init_db(self.conn)
        self.assertEqual(db.experiments(self.conn), before)
        self.assertEqual(db.list_checkpoints(self.conn), [])
        self.assertEqual(db.recovery_state(self.conn, "base")["state"], "clean")

    def test_v1_migration_failure_can_retry(self):
        self.baseline()
        self.conn.execute("DROP TABLE checkpoints")
        self.conn.execute("PRAGMA user_version = 1")
        self.conn.set_authorizer(lambda action, arg1, arg2, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
            and arg1 == "user_version" and arg2 == "2" else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            with self.conn:
                db.init_db(self.conn)
        self.conn.set_authorizer(None)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertFalse(self.conn.execute("SELECT name FROM sqlite_master WHERE name='checkpoints'").fetchall())
        db.init_db(self.conn)
        self.assertEqual(db.latest_kept(self.conn)["exp_id"], "baseline")

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
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        conn.close()


if __name__ == "__main__":
    unittest.main()
