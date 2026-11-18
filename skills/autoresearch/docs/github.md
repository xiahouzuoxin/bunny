# GitHub operations

Use connected GitHub tools for repository reads, edits, state, PRs, and evidence. All training and evaluation run remotely through Actions or its configured downstream runner. The orchestrator edits code itself; no local checkout, SQLite, or separate coding agent is required.

## Establish execution

Inspect current tool schemas and the target workflow before creating experiments. Verify repository access, tree/commit/branch operations, guarded ref updates, and a complete trigger → discover run → retrieve final results path. When no baseline is supplied, verify baseline execution too, including when its commit has no code diff.

Do not assume workflow_dispatch is exposed. If discovery only supports PR runs, use an existing compatible PR workflow; a push trigger alone is insufficient. If the path is unavailable, resolve the missing capability before launching experiments. Propose workflow setup separately.

Discover execution from the target repository's workflows and documentation; do not assume a workflow filename, runner, cloud provider, or scheduler. If a workflow submits training to another system, require its final status and metrics through an accessible results interface, not just submission success.

Record the discovered workflow, triggers, resources, result retrieval, and retention in contract.EXECUTION. Resolve REQUIREMENTS from MODEL_DIR first, following only relevant references to shared dependency setup. PR workflows must evaluate the recorded head commit, not an implicit merge commit. Verify that state/report comments do not trigger experiments and research-branch advancement does not launch duplicate training.

## Branches and state

After contract confirmation, create RESEARCH_BRANCH using the shared naming rule from resolved BASE_REF. Resolve OUTPUT_DIR as a repository-relative output path (default MODEL_DIR/.autoresearch). Commit the initial contract snapshot to OUTPUT_DIR/contract.json, then open a draft research PR against the starting branch. If BASE_REF is a SHA/tag, confirm the PR target branch first. Return and retain the PR URL as the session locator; on resume, read that PR instead of creating another session.

The initial contract commit is the baseline starting point; subsequent batches use the recorded research parent. For supplied BASELINE, use the shared import rules and prevent PR creation from triggering a redundant baseline run. Otherwise, evaluate this commit as baseline: Register baseline before triggering evaluation. PR creation must not launch baseline before registration; verify a separate available trigger, a gated PR workflow, or an initially created baseline record that can reference its evidence location before PR creation. Do not assume the initial PR event alone provides a safely registered baseline run.

Use research PR comments as the authoritative state store:
- Contract versions: one marked comment per version, immutable after confirmation; seed version 1.0 from the committed contract. Update the file from confirmed comments at each checkpoint.
- Experiments: one comment per exp_id, marked `autoresearch:experiment:<exp_id>`, containing the complete shared record and readable evidence. Create running before launch (except imported baseline); update the same comment and never modify terminal records. Log points to the comment or durable evidence URL.
- Checkpoints: one immutable comment per report commit marked `autoresearch:checkpoint:<full-SHA>`, with the shared checkpoint fields. Read alongside experiment comments for recovery.
- Report: a marked comment with readable tables and a link to the committed report. The PR body contains a rebuildable summary and links, not independent state.

Before updates, re-read records and enforce the shared lifecycle. Only the orchestrator edits marked records; comments have no transactional or expected-SHA protection. Save and use comment IDs. After ambiguous writes, search by marker and exp_id before retrying; duplicate/conflicting records require reconciliation. Register every batch candidate before launching any job; interrupted partial registration is handled by shared recovery.

For recovery, read all marked comments, including every page:

- Sort experiments by iteration, start_time (missing first), then exp_id. The highest kept iteration is the current best.
- Group checkpoints by `kept_exp_id`. Follow parent links from each kept record’s effective commit; reject forks, gaps, or conflicting markers. The latest kept record’s chain determines the research parent; older chains remain history.

Store key metrics, evaluated SHA, and final outcome in records; expiring artifacts alone are insufficient. Keep credentials, datasets, and model binaries out of comments. No STATE_BRANCH or local state mirror is needed.

## Prepare and launch

1. Read model files and repository instructions at parent_commit. Resolve its tree, preserve unaffected files/modes, and build modified blobs/tree using that base tree.
2. Create experiment commits with parent_commit as sole parent. Baseline may reuse the starting commit or contain authorized preparation changes.
3. Register all candidates in research PR comments before publishing an execution-triggering branch/PR. Creating an unreferenced commit is preparation.
4. Publish each experiment branch using the shared naming rule at its recorded commit. For a PR trigger, open its PR against RESEARCH_BRANCH after registration. Save PR/run references. On timeouts, locate an existing branch/PR/run before retrying to avoid duplicate submissions.
5. Match results to exact commit, workflow, and run attempt; collect final training/evaluation evidence before adoption.

When no baseline is supplied, a no-change baseline cannot rely on an empty PR. Use the verified baseline trigger or existing complete evaluation evidence for that commit under the confirmed contract; otherwise resolve the setup dependency before launching.

## Adopt and report

Verify research HEAD equals parent_commit and candidate ancestry. Advance the ref without force and with an expected-old-HEAD guard (using the available tool's equivalent parameters), then finalize the experiment comment as kept. If the tool cannot provide that protection, preserve pending state until that capability is available. Interruption between these operations follows shared adoption recovery.

Experiment PRs are execution/review surfaces. Do not squash/merge them to manufacture a different evaluated SHA. Retain experiment branches until the shared cleanup confirmation step. Include closing settled experiment PRs in the proposed cleanup scope if needed; never close the research PR as experiment cleanup.

For report commits, use the same expected-old-HEAD guard as adoption. After confirming the ref update, append the checkpoint comment; check its marker before retrying a write.

Update the report comment and link the committed report from the PR body and final response. If checkpoint conditions are unmet, publish only the comment for now. Do not merge the research PR without a user request. Retain unresolved job references if cancellation is unavailable.

After cleanup confirmation, recheck exact remote branch heads and delete only approved session experiment branches through an available deletion tool. Do not assume closing a PR deletes its branch. If deletion is unavailable, retain the branches and report the limitation. Runner workspace lifecycle belongs to the remote execution service; do not claim to have cleaned remote disks through branch deletion. Preserve research PR comments and evidence.
