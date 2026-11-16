# Bunny

A collection of reusable, self-contained skills for ChatGPT and Codex. Install the Bunny plugin to get all bundled skills, or install an individual skill from its directory. No build step or package installation is required; runtime dependencies vary by skill.

## Available skills

| Skill                                       | Purpose                                                                                                                |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [autoresearch](skills/autoresearch/SKILL.md) | Iteratively improve ML models locally or through GitHub, preserving experiment history and producing readable reports. |

### Autoresearch modes

- **Workspace:** local checkout, reusable worker worktrees, local training, and SQLite history. Requires a shell, Git, Python 3, and model dependencies.
- **GitHub:** connected tools edit code and manage branches; a research PR and comments preserve history; Actions or its downstream runner execute experiments. No local checkout or Copilot is required.

GitHub mode requires the capabilities listed in [github.md](skills/autoresearch/docs/github.md), including writes, execution, evidence retrieval, and guarded branch advancement. A read-only connection is insufficient. This package supplies instructions, not credentials, a runner, or a background scheduler. Verify available capabilities before starting.

## Install the plugin

After publishing this repository to GitHub, replace `OWNER/REPO` below with its actual name and run:

```sh
codex plugin marketplace add OWNER/REPO
```

Alternatively, use its Git URL, or select a branch/tag explicitly:

```sh
codex plugin marketplace add https://github.com/OWNER/REPO.git
codex plugin marketplace add OWNER/REPO --ref main
```

Choose one command. Commit the included `.agents/plugins/marketplace.json`, `plugin.json`, and `skills/` directory to the repository. Private repositories require Git credentials with read access. The `codex` CLI must support plugin marketplaces.

For local testing in supported ChatGPT desktop/Codex clients, run from this directory:

```sh
codex plugin marketplace add .
```

Restart ChatGPT desktop, open the Plugins Directory, choose `bunny-marketplace`, and install `bunny`. Test in a new chat. The included catalog points to this standalone package. Local marketplace support varies by client; this does not publish the plugin to ChatGPT web.

For public ChatGPT distribution, ZIP `plugin.json` and `skills/` at the archive root. Upload at [OpenAI Plugins](https://platform.openai.com/plugins), resolve validation findings, submit for review, and publish after approval. Appropriate publishing permissions and developer verification are required. This package has not been submitted or approved.

See the official [packaging guide](https://developers.openai.com/plugins/build/plugins) and [submission guide](https://developers.openai.com/plugins/deploy/submission).

## Install an individual skill

In Codex, ask the built-in skill installer:

> Use skill-installer to install the skill from GitHub repository OWNER/REPO, path skills/autoresearch.

For filesystem-based Codex installation, copy `skills/autoresearch` into your personal skills directory (normally `~/.codex/skills/autoresearch`). Keep all supporting directories together. This is separate from ChatGPT plugin installation.

## Example: autoresearch

Invoke the installed skill/plugin with a target, for example:

> Use autoresearch to optimize `models/example` in `https://github.com/OWNER/REPO`, starting from `dev`, using GitHub execution with two workers and five iterations. Explore the model and propose a contract for confirmation first.

Or select a local checkout and request workspace mode. Training commands, dependencies, metrics, and execution configuration are discovered from the target model and confirmed in the contract. Cleanup requires user confirmation.

## Layout and validation

`plugin.json` identifies the Bunny plugin. Each `skills/<name>/SKILL.md` is an independent skill entry point. `.agents/plugins/marketplace.json` provides plugin discovery.

To add a skill, create `skills/<name>/SKILL.md` with a unique name and a description explaining when to use it, and add it to the table above. Keep its supporting scripts, documentation, and templates inside that directory and use relative paths. Avoid dependencies on sibling skills so individual installation continues to work. All bundled skills share the plugin's release version.

Run the autoresearch database tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/autoresearch/scripts -p 'test_*.py' -v
```

These tests cover local state handling, not live GitHub integration or plugin-directory approval.
