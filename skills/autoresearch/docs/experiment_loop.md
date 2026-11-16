# Experiment Loop

Both modes follow this lifecycle; their references supply the concrete operations.

```mermaid
flowchart TD
    R[Reconcile state and research HEAD] --> B{Baseline exists?}
    B -- No --> BL[Prepare, register, evaluate baseline]
    BL --> BK[Adopt successful baseline; record kept]
    BK --> R
    B -- Yes --> L{Iteration budget remains?}
    L -- No --> E[Reconcile jobs, clean up safely, report]
    L -- Yes --> P[Freeze contract and parent commit P]
    P --> W[Prepare and register experiment commits from P]
    W --> T[Execute and persist evaluation evidence]
    T --> H{Research HEAD still P?}
    H -- No --> S[Record stale outcomes; stop]
    S --> E
    H -- Yes --> K{Candidate beats previous best?}
    K -- Yes --> FF[Fast-forward to evaluated commit]
    FF --> D[Finalize winner kept and others dropped]
    K -- No --> D
    D --> R
```

## Shared state

Each batch is one numbered attempt. Baseline attempts use iteration 0 and unique IDs; positive iterations start at 1. Once any candidate is registered, the batch counts toward MAX_ITERATIONS even if failed, dropped, stale, or interrupted. After settling all records, use max(iteration) + 1, never latest_kept.iteration + 1.

Both stores use these experiment fields:

- Immutable identity: exp_id, iteration, contract_version, workspace (branch), start_time, commit, parent_commit, hypothesis.
- Results: end_time, metrics (JSON object), status, summary, log (evidence path or URL).

Identity fields are required; text is non-empty, iteration is a non-negative integer, and contract_version references a stored contract. Reject unknown fields and non-finite JSON numbers. Create records as running; preserve identity on updates. Terminal records are immutable. Kept/dropped require end_time, metrics, and summary; failed requires end_time and summary. Successful completed runs stay running until a decision is recorded.

At most one record per iteration may have status kept. Register the whole batch before executing or finalizing any candidate; all share contract_version and parent_commit. Start a new batch only after all existing running records are settled. Positive iterations derive from latest kept, with a distinct experiment commit. Baseline retries require the previous attempt to be terminal and no kept baseline.

Contract versions are immutable numeric dotted versions, starting at 1.0 and compared numerically. Append only newer versions; identical retries are idempotent. Load the latest contract only at each batch/baseline boundary, then freeze it. Recovery uses the versions referenced by existing records.

## 1. Reconcile and prepare

Read all records and research HEAD at startup and every batch boundary. Workspace offers recovery-state; GitHub applies the same checks to the research PR's marked experiment comments.

- Multiple running records matching HEAD: inconsistent; stop.
- Exactly one running record matches HEAD, and is a baseline with no kept record or derives from latest kept in a later iteration: possible adoption before state write. Verify saved evaluation and ancestry; ask the user before finalizing kept. Without evidence, preserve it and stop.
- Otherwise HEAD must equal latest kept if one exists. A mismatch is inconsistent; stop without rewriting history.
- Reconcile remaining running jobs: wait for or stop them, preserve successful evaluated runs as dropped with an interrupted-decision explanation, and record unsuccessful/unverifiable runs as failed. Retain observations and metrics. If termination/results are uncertain, preserve running state and stop.
- Repeat after recovery. With no running records, run baseline if none is kept; otherwise use the next iteration and stop if it exceeds MAX_ITERATIONS.

Prepare baseline code as needed, commit before execution, and register iteration 0 with a unique ID. Reuse HEAD if no preparation was needed; no empty commit is required. Evaluate with the same evidence rules as other experiments. Adopt a successful baseline before recording kept. Preserve failed attempts before retrying.

For normal batches, freeze the contract and research HEAD as parent_commit. Resolve incompatible evaluation-protocol changes before comparing with the kept model.

## 2. Form hypotheses

Review logs, metric trends, feature diagnostics, and past outcomes. Explain each hypothesis as symptom → likely cause → proposed change. Favor focused, related changes so results can be attributed: tune hyper-parameters, investigate feature and data engineering, then explore architecture or loss changes as evidence warrants. Try useful variations of promising directions without repeating failed attempts.

Use [data analysis](data_analysis.md) when evidence is insufficient. After several batches without improvement, consult relevant and high-quality research papers, for instance, recent publications in the field with highly-cited or peer-reviewed results, and retain findings in the mode's output location. Dropped may reflect interruption or stale parent, not a bad hypothesis.

## 3. Prepare and execute

Prepare exactly NUM_WORKERS independent experiment branches per non-baseline batch from the same parent, each with exactly one experiment commit. Workspace mode switches its fixed worker worktrees onto new branches; GitHub mode creates remote commits and branches through its tools.

Register all candidates before the execution-triggering operation. Set log to a durable evidence location; retain commands, branch/worktree, job/run IDs, attempt, and output links there. Never change an evaluated branch during execution or collection. Fixes use a new ID and commit in the next batch; baseline retries remain iteration 0.

Run training and evaluation using EXECUTION and REQUIREMENTS from the frozen contract. Require evidence of the actual evaluated commit, final outcome, and contract metrics. Submission success is not training success. Save successful evidence, end time, metrics, and analysis while status stays running; record execution failures as failed. Missing required metrics cannot support adoption.

## 4. Decide and adopt

Compare candidates against latest kept under the frozen contract. Check research HEAD against parent_commit for every batch, even without a winner. If changed, preserve failures, mark successes dropped with stale parent, and stop.

For a winner, fast-forward research HEAD to the exact evaluated commit, then record kept. Never squash, rebase, cherry-pick, or create a merge commit as a substitute. If updating HEAD fails or its outcome is uncertain, retain running state and reconcile. Finalize other successes as dropped with evidence and rationale.

Repeat from reconciliation until the budget is reached or the user interrupts.

## Finally

On any exit, inspect HEAD and running records, wait for or stop jobs, and finalize using the recovery rules. Preserve running records matching HEAD for adoption recovery. Never finalize jobs whose completion/termination cannot be confirmed. Save evidence and generate the report before offering cleanup.

List the exact session-owned worker worktrees and experiment branches eligible for cleanup, including whether branches are local or remote, and ask for user confirmation. Without confirmation, retain them. Explain that deleting the last branch referencing a dropped/failed commit can make its code unrecoverable; a recorded SHA alone is not an archive. Keep such branches unless the user accepts that loss or requests a durable archive first.

After confirmation, recheck ownership, job completion, uncommitted changes, and branch heads; delete only the approved unchanged targets using the selected mode. Exclude unresolved experiments. Preserve the primary workspace, RESEARCH_BRANCH, research PR, contract, records, logs, and report. Report what was removed, retained, or blocked and whether recovery is possible.
