#!/usr/bin/env python3
"""Transactional state store for the autoresearch skill."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path


STATUSES = {"running", "failed", "kept", "dropped"}
TERMINAL_STATUSES = {"failed", "kept", "dropped"}
SCHEMA_VERSION = 2
IDENTITY_FIELDS = {
    "exp_id", "iteration", "contract_version", "workspace", "start_time",
    "commit", "parent_commit", "hypothesis",
}
EXPERIMENT_FIELDS = IDENTITY_FIELDS | {"end_time", "metrics", "status", "summary", "log"}


def connect(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    if current == 0 and has_tables:
        raise ValueError("unversioned autoresearch database; recreate autoresearch.db")
    if current == 0:
        conn.executescript(
            """
        BEGIN IMMEDIATE;
        CREATE TABLE contracts (
            version TEXT PRIMARY KEY,
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE experiments (
            exp_id TEXT PRIMARY KEY,
            iteration INTEGER NOT NULL,
            contract_version TEXT NOT NULL REFERENCES contracts(version),
            workspace TEXT,
            start_time TEXT,
            end_time TEXT,
            commit_hash TEXT,
            parent_commit TEXT,
            hypothesis TEXT,
            metrics_json TEXT,
            status TEXT NOT NULL CHECK (status IN ('running', 'failed', 'kept', 'dropped')),
            summary TEXT,
            log_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX one_kept_per_iteration
        ON experiments(iteration) WHERE status = 'kept';
        PRAGMA user_version = 1;
        COMMIT;
        """
        )
        current = 1
    if current == 1:
        validate_schema(conn)
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE checkpoints (
                commit_hash TEXT PRIMARY KEY,
                parent_commit TEXT NOT NULL,
                kept_exp_id TEXT NOT NULL REFERENCES experiments(exp_id),
                contract_version TEXT NOT NULL REFERENCES contracts(version),
                paths_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            PRAGMA user_version = 2;
            COMMIT;
        """)
        current = 2
    if current != SCHEMA_VERSION:
        raise ValueError(f"unsupported database schema version: {current}")
    validate_schema(conn)


def validate_schema(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] == 2:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(checkpoints)")}
        if columns != {"commit_hash", "parent_commit", "kept_exp_id", "contract_version", "paths_json", "created_at"}:
            raise ValueError("invalid checkpoint schema")
    expected_contracts = {"version", "content_json", "created_at"}
    expected_experiments = {
        "exp_id", "iteration", "contract_version", "workspace", "start_time",
        "end_time", "commit_hash", "parent_commit", "hypothesis", "metrics_json",
        "status", "summary", "log_path", "created_at",
    }
    contracts = {row["name"] for row in conn.execute("PRAGMA table_info(contracts)")}
    experiments = {row["name"] for row in conn.execute("PRAGMA table_info(experiments)")}
    if contracts != expected_contracts or experiments != expected_experiments:
        raise ValueError("invalid schema v1; recreate autoresearch.db")
    foreign_keys = {
        (row["table"], row["from"], row["to"])
        for row in conn.execute("PRAGMA foreign_key_list(experiments)")
    }
    index = next(
        (row for row in conn.execute("PRAGMA index_list(experiments)")
         if row["name"] == "one_kept_per_iteration"),
        None,
    )
    index_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("one_kept_per_iteration",),
    ).fetchone()
    index_sql = "" if index_sql_row is None else re.sub(
        r"\s+", " ", index_sql_row["sql"].lower()
    )
    table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'experiments'"
    ).fetchone()
    table_sql = "" if table_sql_row is None else re.sub(
        r"\s+", " ", table_sql_row["sql"].lower()
    )
    if ("contracts", "contract_version", "version") not in foreign_keys:
        raise ValueError("invalid schema v1: missing contract foreign key")
    index_columns = [row["name"] for row in conn.execute(
        "PRAGMA index_info(one_kept_per_iteration)"
    )]
    if (index is None or not index["unique"] or not index["partial"]
            or index_columns != ["iteration"]
            or not index_sql.rstrip("; ").endswith("where status = 'kept'")):
        raise ValueError("invalid schema v1: invalid one_kept_per_iteration index")
    if "check (status in ('running', 'failed', 'kept', 'dropped'))" not in table_sql:
        raise ValueError("invalid schema v1: invalid status constraint")


def read_json(path: str | None) -> object:
    text = Path(path).read_text() if path else sys.stdin.read()
    return json.loads(text)


def version_key(version: str) -> tuple[int, ...]:
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError(f"version must be numeric dotted notation: {version}")
    return tuple(int(part) for part in version.split("."))


def normalize_version(version: str) -> str:
    parts = list(version_key(version))
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    while len(parts) < 2:
        parts.append(0)
    return ".".join(str(part) for part in parts)


def put_contract(conn: sqlite3.Connection, version: str, content: object) -> None:
    version = normalize_version(version)
    if not isinstance(content, dict):
        raise ValueError("contract must be a JSON object")
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row = conn.execute(
        "SELECT content_json FROM contracts WHERE version = ?", (version,)
    ).fetchone()
    if row:
        if row["content_json"] != encoded:
            raise ValueError(f"contract version {version} already exists with different content")
        return
    latest = latest_contract(conn)
    if latest is not None and version_key(version) <= version_key(str(latest["version"])):
        raise ValueError(f"contract version {version} must be newer than {latest['version']}")
    with conn:
        conn.execute(
            "INSERT INTO contracts(version, content_json) VALUES (?, ?)",
            (version, encoded),
        )


def latest_contract(conn: sqlite3.Connection) -> dict[str, object] | None:
    rows = conn.execute("SELECT version, content_json FROM contracts").fetchall()
    if not rows:
        return None
    row = max(rows, key=lambda item: version_key(item["version"]))
    return {"version": row["version"], "contract": json.loads(row["content_json"])}


def get_contract(conn: sqlite3.Connection, version: str) -> dict[str, object] | None:
    version = normalize_version(version)
    row = conn.execute(
        "SELECT version, content_json FROM contracts WHERE version = ?", (version,)
    ).fetchone()
    if row is None:
        return None
    return {"version": row["version"], "contract": json.loads(row["content_json"])}


def list_contracts(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute("SELECT version, content_json FROM contracts").fetchall()
    rows = sorted(rows, key=lambda row: version_key(row["version"]))
    return [
        {"version": row["version"], "contract": json.loads(row["content_json"])}
        for row in rows
    ]


def supplied_baseline(conn: sqlite3.Connection, record: dict) -> bool:
    contract = get_contract(conn, record["contract_version"])
    source = contract["contract"].get("BASELINE") if contract else None
    return (record.get("iteration") == 0 and isinstance(source, dict)
            and bool(source.get("commit")) and source["commit"] == record.get("commit"))


def effective_commit(conn: sqlite3.Connection, record: dict) -> str:
    """Imported metrics retain their source SHA; code starts at contract baseline."""
    return record["parent_commit"] if supplied_baseline(conn, record) else record["commit"]


def list_checkpoints(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM checkpoints ORDER BY rowid")]


def research_parent(conn: sqlite3.Connection, kept: dict) -> str:
    """Advance the branching anchor without changing the evaluated identity."""
    anchor = effective_commit(conn, kept)
    for checkpoint in list_checkpoints(conn):
        if checkpoint["kept_exp_id"] == kept["exp_id"]:
            if checkpoint["parent_commit"] != anchor:
                raise ValueError("broken checkpoint chain")
            anchor = checkpoint["commit_hash"]
    return anchor


def put_checkpoint(conn: sqlite3.Connection, repo: str, commit: str,
                   contract_version: str, paths: list[str]) -> None:
    """Record an already committed, reviewed report update; verify Git structure."""
    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return result.stdout.rstrip("\n")

    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("checkpoint requires a full commit SHA")
    version = normalize_version(contract_version)
    if get_contract(conn, version) is None:
        raise ValueError("checkpoint requires a stored contract")
    allowed = sorted(set(paths))
    if not allowed or any(not path or path.startswith("/") or ".." in Path(path).parts for path in allowed):
        raise ValueError("provide exact repository-relative report paths")
    parents = git("rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 2:
        raise ValueError("checkpoint must have exactly one parent")
    changed = set(git("diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", parents[1], commit).split("\0")) - {""}
    if not changed or not changed.issubset(allowed):
        raise ValueError("checkpoint changes files outside the reviewed report paths or is empty")
    existing = conn.execute("SELECT * FROM checkpoints WHERE commit_hash = ?", (commit,)).fetchone()
    if existing:
        if existing["contract_version"] != version or existing["paths_json"] != json.dumps(allowed):
            raise ValueError("checkpoint is immutable")
        return
    kept = latest_kept(conn)
    if kept is None or any(item["status"] == "running" for item in experiments(conn)):
        raise ValueError("checkpoint requires a kept model and no running experiments")
    if parents[1] != research_parent(conn, kept) or git("rev-parse", "HEAD") != commit:
        raise ValueError("checkpoint must advance the recorded research parent to current HEAD")
    with conn:
        conn.execute("INSERT INTO checkpoints(commit_hash, parent_commit, kept_exp_id, contract_version, paths_json) VALUES (?, ?, ?, ?, ?)",
                     (commit, parents[1], kept["exp_id"], version, json.dumps(allowed)))


def put_experiment(conn: sqlite3.Connection, record: object) -> None:
    if not isinstance(record, dict):
        raise ValueError("experiment record must be a JSON object")
    unknown = record.keys() - EXPERIMENT_FIELDS
    if unknown:
        raise ValueError(f"unknown experiment fields: {', '.join(sorted(unknown))}")
    exp_id = record.get("exp_id")
    if not isinstance(exp_id, str) or not exp_id.strip():
        raise ValueError("exp_id must be a non-empty string")
    record = dict(record)
    if "contract_version" in record:
        record["contract_version"] = normalize_version(record["contract_version"])
    existing_row = conn.execute(
        "SELECT * FROM experiments WHERE exp_id = ?", (exp_id,)
    ).fetchone()
    existing = {} if existing_row is None else row_to_experiment(existing_row)
    for field in IDENTITY_FIELDS:
        if field in record and existing.get(field) is not None and record[field] != existing[field]:
            raise ValueError(f"cannot change {field} for experiment {exp_id}")
    record = {**existing, **record, "exp_id": exp_id}
    required = {"exp_id", "iteration", "contract_version", "status"}
    missing = sorted(required - record.keys())
    if missing:
        raise ValueError(f"missing experiment fields: {', '.join(missing)}")
    if not isinstance(record["status"], str) or record["status"] not in STATUSES:
        raise ValueError(f"invalid experiment status: {record['status']}")
    if type(record["iteration"]) is not int or record["iteration"] < 0:
        raise ValueError("iteration must be a non-negative integer")
    if existing:
        previous_status = existing["status"]
        if previous_status in TERMINAL_STATUSES and record != existing:
            raise ValueError(f"terminal experiment {exp_id} is immutable")
    elif record["status"] != "running" and not (
            supplied_baseline(conn, record) and record["status"] == "kept"):
        raise ValueError("new experiments must start as running")
    validate_experiment(record, imported=supplied_baseline(conn, record))
    if not existing:
        history = experiments(conn)
        kept = latest_kept(conn)
        iteration = record["iteration"]
        active = [item for item in history if item["status"] == "running"]
        batch = [item for item in history if item["iteration"] == iteration]
        if iteration == 0:
            if kept or active:
                raise ValueError("finish the previous baseline attempt before retrying")
        else:
            if kept is None or record["parent_commit"] != research_parent(conn, kept):
                raise ValueError("experiments must start from the recorded research parent")
            if record["commit"] == record["parent_commit"]:
                raise ValueError("experiment commit must differ from its parent")
            if batch:
                if any(item["status"] != "running" for item in batch):
                    raise ValueError("register the whole batch before finalizing any experiment")
                if any(item["contract_version"] != record["contract_version"]
                       or item["parent_commit"] != record["parent_commit"] for item in batch):
                    raise ValueError("batch must share a contract and parent commit")
            elif active or iteration != max((item["iteration"] for item in history), default=0) + 1:
                raise ValueError("close the active batch and use the next iteration")
    metrics = record.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        raise ValueError("metrics must be a JSON object")
    metrics_json = None if metrics is None else json.dumps(metrics, sort_keys=True, allow_nan=False)
    values = (
        record["exp_id"],
        record["iteration"],
        str(record["contract_version"]),
        record.get("workspace"),
        record.get("start_time"),
        record.get("end_time"),
        record.get("commit"),
        record.get("parent_commit"),
        record.get("hypothesis"),
        metrics_json,
        record["status"],
        record.get("summary"),
        record.get("log"),
    )
    with conn:
        conn.execute(
            """
            INSERT INTO experiments (
                exp_id, iteration, contract_version, workspace, start_time,
                end_time, commit_hash, parent_commit, hypothesis, metrics_json,
                status, summary, log_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exp_id) DO UPDATE SET
                iteration=excluded.iteration,
                contract_version=excluded.contract_version,
                workspace=excluded.workspace,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                commit_hash=excluded.commit_hash,
                parent_commit=excluded.parent_commit,
                hypothesis=excluded.hypothesis,
                metrics_json=excluded.metrics_json,
                status=excluded.status,
                summary=excluded.summary,
                log_path=excluded.log_path
            """,
            values,
        )


def validate_experiment(record: dict[str, object], imported: bool = False) -> None:
    status = str(record["status"])
    required = set(IDENTITY_FIELDS)
    if status in TERMINAL_STATUSES:
        required |= {"end_time", "summary"}
    if status in {"kept", "dropped"}:
        required.add("metrics")
    if imported:
        required -= {"workspace", "start_time", "end_time", "hypothesis"}
        if record.get("status") != "kept" or not record.get("metrics"):
            raise ValueError("supplied baseline requires kept status and non-empty metrics")
    missing = sorted(field for field in required if record.get(field) is None)
    if missing:
        raise ValueError(f"{status} experiment requires: {', '.join(missing)}")
    text_fields = {
        "workspace", "start_time", "end_time", "commit", "parent_commit",
        "hypothesis", "summary", "log",
    }
    invalid = sorted(
        field for field in text_fields
        if field in record and record[field] is not None
        and (not isinstance(record[field], str) or not record[field].strip())
    )
    if invalid:
        raise ValueError(f"experiment fields must be non-empty strings: {', '.join(invalid)}")


def row_to_experiment(row: sqlite3.Row) -> dict[str, object]:
    return {
        "exp_id": row["exp_id"],
        "iteration": row["iteration"],
        "contract_version": row["contract_version"],
        "workspace": row["workspace"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "commit": row["commit_hash"],
        "parent_commit": row["parent_commit"],
        "hypothesis": row["hypothesis"],
        "metrics": None if row["metrics_json"] is None else json.loads(row["metrics_json"]),
        "status": row["status"],
        "summary": row["summary"],
        "log": row["log_path"],
    }


def experiments(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT * FROM experiments ORDER BY iteration, created_at, exp_id"
    ).fetchall()
    return [row_to_experiment(row) for row in rows]


def latest_kept(conn: sqlite3.Connection) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT * FROM experiments
        WHERE status = 'kept'
        ORDER BY iteration DESC, end_time DESC, exp_id DESC
        LIMIT 1
        """
    ).fetchone()
    return None if row is None else row_to_experiment(row)


def experiment_by_commit(conn: sqlite3.Connection, commit: str) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT * FROM experiments WHERE commit_hash = ? ORDER BY created_at, exp_id",
        (commit,),
    ).fetchall()
    return [row_to_experiment(row) for row in rows]


def recovery_state(conn: sqlite3.Connection, head: str) -> dict[str, object]:
    history = experiments(conn)
    kept = latest_kept(conn)
    running = [record for record in history if record["status"] == "running"]
    running_at_head = [record for record in running if record["commit"] == head]

    candidate = running_at_head[0] if len(running_at_head) == 1 else None
    valid_candidate = candidate is not None and (
        (kept is None and candidate["iteration"] == 0)
        or (kept is not None and candidate["parent_commit"] == research_parent(conn, kept)
            and candidate["iteration"] > kept["iteration"])
    )
    if len(running_at_head) > 1:
        state = "inconsistent"
    elif candidate and valid_candidate and (kept is None or research_parent(conn, kept) != head):
        state = "integrated-pending"
    elif kept is not None and research_parent(conn, kept) != head:
        state = "inconsistent"
    elif running:
        state = "interrupted"
    elif kept is None:
        state = "fresh" if not history else "baseline-required"
    elif research_parent(conn, kept) == head:
        state = "clean"
    else:
        state = "inconsistent"
    return {
        "state": state,
        "head": head,
        "latest_kept": kept,
        "research_parent": research_parent(conn, kept) if kept else None,
        "running": running,
        "experiment_count": len(history),
        "next_iteration": max((record["iteration"] for record in history), default=0) + 1,
    }


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Transactional state store for autoresearch contracts, experiments, "
            "and startup recovery. Run '<command> --help' for command-specific "
            "inputs and semantics."
        ),
    )
    parser.add_argument("--db", required=True, help="Path to autoresearch.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize or validate the schema")
    contract = sub.add_parser(
        "put-contract",
        help="Store an append-only contract version",
        description=(
            "Store a JSON object as an append-only numeric dotted contract version. "
            "The same version and content is idempotent; changed or older versions fail."
        ),
    )
    contract.add_argument("--version", required=True, help="Numeric dotted version, e.g. 1.0")
    contract.add_argument("--json-file", help="Read JSON from file instead of stdin")
    sub.add_parser("latest-contract", help="Print the newest contract")
    get_contract_parser = sub.add_parser("get-contract", help="Print one contract version")
    get_contract_parser.add_argument("--version", required=True)
    sub.add_parser("list-contracts", help="Print all contracts in version order")
    experiment = sub.add_parser(
        "put-experiment",
        help="Create or update an experiment record",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Create or update an experiment from a JSON object.

Fields:
  exp_id             unique experiment identifier
  iteration          batch attempt number; 0 for baseline, then 1, 2, ...
  contract_version   stored contract version used by the experiment
  workspace          workspace identifier, usually the experiment branch
  start_time         execution start timestamp
  end_time           execution end timestamp
  commit             evaluated commit hash
  parent_commit      derived-from commit; contract baseline SHA for supplied baseline
  hypothesis         what is being tried and why
  metrics            object of key metric names and values
  status             running, failed, kept, or dropped
  summary            outcome, observations, and decision
  log                optional experiment log path

Always required: exp_id, iteration, contract_version, workspace, start_time,
commit, parent_commit, hypothesis, status. All text must be non-empty strings;
iteration must be a non-negative integer. Unknown fields are rejected.
Additional terminal requirements:
  kept, dropped   end_time, metrics (JSON object), summary
  failed          end_time, summary

Supplied baseline: iteration 0 matching contract BASELINE.commit may be created
as kept with metrics and summary; workspace, timestamps, and hypothesis are
optional. commit tracks the source; parent_commit anchors branching and recovery.

Otherwise create as running before execution. Updates need only exp_id and changed fields.
Register all batch candidates before finalizing any. A new batch requires the
previous batch to be terminal and uses maximum recorded iteration + 1. Baseline
retries use 0 and unique IDs. Positive iterations derive from the recorded research parent (latest kept plus report checkpoints).
Identity fields (all required fields except status) never change. Terminal
records are immutable. Successful runs awaiting a decision stay running with
end_time, metrics, and summary saved. JSON is read from stdin unless
--json-file is provided.""",
    )
    experiment.add_argument("--json-file", help="Read JSON from file instead of stdin")
    sub.add_parser("list-experiments", help="Print all experiments in iteration order")
    sub.add_parser("latest-kept", help="Print the most recent kept experiment")
    checkpoint = sub.add_parser("put-checkpoint", help="Record a reviewed report commit at research HEAD after all jobs settle")
    checkpoint.add_argument("--repo", required=True, help="Primary research worktree")
    checkpoint.add_argument("--commit", required=True, help="Full report commit SHA")
    checkpoint.add_argument("--contract-version", required=True)
    checkpoint.add_argument("--path", action="append", required=True, help="Exact reviewed non-executable report/contract artifact path; repeat as needed")
    sub.add_parser("list-checkpoints", help="Print immutable report checkpoint history")
    recovery = sub.add_parser(
        "recovery-state",
        help="Classify startup state against the feature HEAD",
        description=(
            "Compare experiment state with the feature-branch HEAD and return one of: "
            "fresh, baseline-required, clean, interrupted, integrated-pending, or "
            "inconsistent. Includes running records and next_iteration (maximum "
            "recorded iteration + 1). Reconcile running records before using that "
            "number. This checks stored hashes, not Git ancestry or job status."
        ),
    )
    recovery.add_argument("--head", required=True, help="Current feature-branch HEAD commit")
    by_commit = sub.add_parser("by-commit", help="Print experiments for a commit")
    by_commit.add_argument("--commit", required=True, help="Commit hash to inspect")
    args = parser.parse_args()

    try:
        with connect(args.db) as conn:
            init_db(conn)
            if args.command == "init":
                return 0
            if args.command == "put-contract":
                put_contract(conn, args.version, read_json(args.json_file))
            elif args.command == "latest-contract":
                emit(latest_contract(conn))
            elif args.command == "get-contract":
                emit(get_contract(conn, args.version))
            elif args.command == "list-contracts":
                emit(list_contracts(conn))
            elif args.command == "put-experiment":
                put_experiment(conn, read_json(args.json_file))
            elif args.command == "list-experiments":
                emit(experiments(conn))
            elif args.command == "latest-kept":
                emit(latest_kept(conn))
            elif args.command == "put-checkpoint":
                put_checkpoint(conn, args.repo, args.commit, args.contract_version, args.path)
            elif args.command == "list-checkpoints":
                emit(list_checkpoints(conn))
            elif args.command == "recovery-state":
                emit(recovery_state(conn, args.head))
            elif args.command == "by-commit":
                emit(experiment_by_commit(conn, args.commit))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
