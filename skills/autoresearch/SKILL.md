---
name: autoresearch
description: Iteratively improve an ML model using a local workspace or GitHub tools and Actions, preserving experiment history and producing a readable research report.
argument-hint: <model-dir> [--mode workspace|github] [--repository owner/repo] [--max-iterations n]
---

You manage research, code changes, experiment decisions, and reporting. Use one shared loop with the selected mode's operations; GitHub mode requires neither a local checkout nor a separate coding agent.

## Prepare

Ask for MODEL_DIR if missing. Infer workspace for local execution or github for remote execution through GitHub. Honor explicit mode selection; if the requested execution cannot be supported in that mode, clarify rather than mixing modes. Read only the selected reference:
- [Workspace](docs/workspace.md): reusable worktrees, SQLite, local training and evaluation only.
- [GitHub](docs/github.md): plugin-based editing, branches, PR comments, and remote execution through Actions or its configured downstream runner.

Inspect repository instructions, model code, evaluation, and execution configuration through that mode. Confirm scaffolding before using templates/ for an absent model. Before presenting the final contract, assess any user-supplied BASELINE and prepare its iteration-0 record using [baseline preparation](docs/experiment_loop.md#supplied-baseline-before-contract-confirmation). Print the contract in a human-readable format for review, and ask user for confirmation before launching experiments and each time it may change; preserve confirmed contracts on resume.

| Parameter | Default | Meaning |
|---|---|---|
| MODE | Requested environment | workspace or github; fixed for a session |
| MODEL_DIR | Required | Model directory relative to repository root |
| REPOSITORY | Selected checkout in workspace mode | Target repository URL or owner/name; required for GitHub |
| BASE_REF | Current branch / repository default | Starting ref, resolved once when creating a session |
| RESEARCH_BRANCH | Auto-created using the naming rule below | Latest adopted model plus report checkpoints; explicit user choice takes precedence |
| ENTRYPOINT | Infer from model | Training and evaluation command relative to MODEL_DIR |
| REQUIREMENTS | Infer from MODEL_DIR | Model-specific dependency manifest, environment, or setup command; none if unnecessary |
| EXECUTION | Infer from model setup and available tools | Runner/workflow, launch mechanism (including baseline), resources, and retrieval/retention of final status, evaluated commit, metrics, and logs |
| METRICS | Infer and confirm | Names, directions, evaluation protocol, adoption rule |
| IMMUTABLE_FILES | None | Paths relative to MODEL_DIR that cannot change |
| MAX_ITERATIONS | Unlimited | Numbered batch attempts across resumes, excluding baseline |
| NUM_WORKERS | 1 | Number of parallel experiments per non-baseline batch; positive integer |
| OUTPUT_DIR | MODEL_DIR/.autoresearch | Mode-specific state and report location |
| BASELINE | None | Optional prior baseline: commit, metrics, and available context |

Discover dependencies in MODEL_DIR first. Inspect repository-level manifests or shared setup only when the model's configuration, imports, or documentation indicates they are needed; do not install repository-wide dependencies by default. Resolve dependency paths relative to MODEL_DIR unless explicitly stated otherwise.

References, scripts, and templates belong to the installed skill package, not the target repository. Resolve them relative to this skill through the host's resource access; SKILL_DIR denotes its filesystem location when available. GitHub mode must not require a local shell or access to the repository hosting this skill. Store confirmed contract versions through the selected mode. Confirm material changes before the next batch.

### Branch naming (both modes)

For a new session, derive model-name from the final MODEL_DIR component: lowercase, replace runs of non-ASCII-alphanumeric characters with a hyphen, trim hyphens, and use `model` if empty. Generate session-id as `YYYYMMDD-<8 random lowercase hex characters>` using the user's timezone. Persist `branch_prefix = autoresearch/<model-name>/<session-id>` in the contract and reuse it on resume.

- Default research branch: `<branch_prefix>/best`.
- Experiment branch and exp_id: `<branch_prefix>/exp-<iteration>-<worker>`, with numeric components padded to at least 3 and 2 digits respectively. Use only the final `exp-...` component as exp_id.
- Baseline attempts: `baseline-<attempt>` (at least 3 digits), used as exp_id and, when a separate baseline branch is needed, `<branch_prefix>/baseline-<attempt>`. Attempts start at 1 and never reuse an ID.

Example: `autoresearch/example-model/20261116-a3f91c2e/best` and `autoresearch/example-model/20261116-a3f91c2e/exp-001-01`. Branches are siblings: never create a branch at branch_prefix itself, since a ref cannot also be a directory prefix for another ref.

Before creation, check exact names and ref-prefix conflicts in the selected mode's repository: local refs for workspace, remote refs for github. For a generated session collision, generate a new suffix before persisting the contract. For an explicit research branch or a resumed session, preserve the name and reconcile existing refs; never overwrite a conflicting branch. Explicit research names do not change the session's experiment prefix.

## Iterate and report

Follow [the shared loop](docs/experiment_loop.md). The selected mode implements state access, commit preparation, execution, evidence collection, branch advancement, and cleanup. Never mix two authoritative stores for one session.

Keep contract/report updates on RESEARCH_BRANCH using the shared loop's checkpoint procedure. After any loop exit, follow [report generation](docs/generate_report.md).

## Scope

- Read default branches as needed, but never write experiments to main, master, or the repository default branch. Do not merge into the target branch without a user request.
- Change only authorized model and output/state paths; preserve immutable files and unrelated work.
- Workflow/infrastructure changes require explicit scope; unavailable execution does not authorize expanding it.
- Only one orchestrator writes a session's state and research branch. Retain experiment branches by default.
