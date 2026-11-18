# Workspace operations

Use a local checkout and a fixed pool of worktrees for local training and evaluation. This mode does not push experiment branches or submit remote jobs.

This mode requires filesystem access, Git, and a Python runtime compatible with the bundled database script. Discover training dependencies from MODEL_DIR first and record execution tools in contract.EXECUTION; do not assume this skill's host environment is the training environment.

## Setup and state

For a new session, resolve BASE_REF (default current HEAD) and create RESEARCH_BRANCH using the shared naming rule unless explicitly supplied. On resume, reuse the recorded branch. Default branches may be read as starting points but never used as the research branch. Inspect existing modifications before switching; preserve user changes and resolve them before switching if they prevent a safe switch or would affect evaluation.

Resolve OUTPUT_DIR once to an absolute path in the primary workspace. Keep it under MODEL_DIR unless the user confirms an external directory. Exclude runtime outputs through Git’s info/exclude. Check staged files too; ignore rules do not protect already tracked files.

If OUTPUT_DIR is outside the repository, record MODEL_DIR/.autoresearch as the tracked report destination in the contract. Keep runtime state in OUTPUT_DIR; copy report files to the tracked destination for versioning.

Use `${SKILL_DIR}/scripts/autoresearch_db.py --db "$OUTPUT_DIR/autoresearch.db"` for all state access. Read top-level and relevant command --help first. Initialize with init; SQLite is the only authoritative store. The CLI handles lifecycle and recovery, including migration from schema v1 to v2 without changing experiment records.

Store execution evidence under `$OUTPUT_DIR/experiments/$EXP_ID/`, including commands and job identifiers for recovery.

Choose worker paths during preparation and include them in the contract before confirmation. Create exactly NUM_WORKERS worker worktrees for the first non-baseline batch, excluding the primary research workspace, and reuse them across batches. Validate paths on resume; reuse surviving session-owned worktrees and recreate only missing slots after recovery. If NUM_WORKERS changes in a confirmed contract, resize the pool between batches after jobs are settled.

## Prepare, execute, adopt

- Baseline (unless imported): commit only authorized preparation changes in the primary feature workspace before registering and evaluating; reuse HEAD if unchanged.
- Experiments: in each worker worktree, create and switch to a new branch named by the shared rule from parent_commit (for example, `git switch -c <experiment-branch> <parent_commit>`). Keep previous experiment branches and commits. Prefer temporary worktree directories and sparse checkout when the model is self-contained. Commit before registration.
- Local execution: run ENTRYPOINT from MODEL_DIR in the worktree; write outputs outside tracked source files.
- Adoption: verify research HEAD equals parent_commit and fast-forward to the winner. Preserve unrelated primary-workspace changes; resolve blockers before integration, then resume.

Before reusing a worker, ensure its process has stopped, its previous experiment is terminal, and its commit and outputs are preserved. Keep per-experiment outputs in OUTPUT_DIR, outside the reusable worktree. The worktree must have no uncommitted changes or leftover generated files that could affect the next run; preserve unexpected files and resolve them before reuse, then resume. Do not use hard reset, forced checkout, or blanket clean to switch experiments.

## Report checkpoints

After committing report updates on the primary research branch, record the checkpoint with the database CLI:

```text
put-checkpoint --repo <primary-worktree> --commit <full-SHA> \
  --contract-version <version> --path <repo-relative-file> ...
```

Repeat `--path` for each reviewed file. The helper checks Git ancestry, changed paths, HEAD, contract existence, and settled records; you must still review content for effects on experiments.

Use `list-checkpoints` for history and `recovery-state` for the current `research_parent` and `latest_kept`. After an interrupted commit, follow step 5’s recovery checks before retrying `put-checkpoint`.

## Exit

After the shared cleanup confirmation, remove the approved session-owned worker worktrees with Git worktree removal before deleting their approved local experiment branches. Do not force removal of dirty worktrees; preserve unexpected files and report the blocker. Dropped/failed branches may be unmerged: delete them only when confirmation covers losing that branch's code reference. This mode does not delete remote branches. Never remove the primary workspace or OUTPUT_DIR; retain unresolved worktrees and branches.
