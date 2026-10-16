# Git-Powered Configuration Management CLI

`config-cli` keeps application configuration files (JSON and YAML) in a Git
repository and gives them a simple, safe workflow:

- every change is a **commit**, so there is a full history with authors and dates;
- changes can be prepared on a **draft** (a Git branch) and **published** to
  `main` when ready;
- **merge conflicts** are detected, explained and resolved on the draft, never
  on the published configuration;
- any file can be **rolled back** to an earlier version without rewriting history;
- every action is written to a structured **audit log**.

Users never need to type Git commands: the tool drives Git through
[GitPython](https://gitpython.readthedocs.io/).

---

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [The Git workflow](#the-git-workflow)
- [Command reference](#command-reference)
- [Resolving a merge conflict](#resolving-a-merge-conflict)
- [Audit log](#audit-log)
- [Errors and exit codes](#errors-and-exit-codes)
- [Architecture](#architecture)
- [Development](#development)
- [Demo walkthrough](#demo-walkthrough)

---

## Installation

Requirements: **Python 3.11+** and **Git** on your `PATH`.

With [Poetry](https://python-poetry.org/) (recommended):

```bash
poetry install
poetry run config-cli --help
```

With pip:

```bash
pip install -r requirements.txt
pip install -e .
config-cli --help
```

The tool can also be run as a module: `python -m config_cli --help`.

All commands work on the repository in the current folder. Use `-C PATH`
(or `--repo PATH`) to point at another one, like `git -C`.

---

## Quick start

```bash
config-cli -C configs init                       # create a config repository
config-cli -C configs set app.yaml database.port 5432
config-cli -C configs show app.yaml
config-cli -C configs history app.yaml
```

```text
Initialized configuration repository in /home/me/configs
Saved app.yaml on main (commit 4efd138).
database:
  port: 5432
4efd138  2026-10-16 09:12  Taylor  Set database.port in app.yaml
```

---

## The Git workflow

```text
main:            A ── B ─────────────── M      published configuration
                       \               /
draft/new-port:         C ── D ───────          work in progress
```

| Concept | In Git | Why |
|---|---|---|
| A saved change | a commit on the current branch | full history; nothing is ever lost |
| A draft | a branch named `draft/<name>`, always started from `main` | changes can be prepared without touching the live config |
| Publishing | `git merge --no-ff draft/<name>` into `main`, then the draft is deleted | every publish is one visible merge commit in the history |
| Syncing | merging `main` **into** the draft | conflicts are fixed on the draft, where it is safe |
| Rollback | `git checkout <commit> -- <file>`, then a new commit | history is never rewritten, so a rollback can itself be undone |

Safety rules the tool enforces:

- A new configuration repository commits a `.gitignore` (editor backups, OS
  clutter) and a `.gitattributes` that forces LF line endings, so files look
  the same on Windows, macOS and Linux.
- Branches are never switched, merged or restored while there are uncommitted
  hand edits or an unfinished sync, because that could lose work.
- A failed publish leaves `main`, the draft and the checked-out branch exactly
  as they were.
- File names cannot escape the repository (`../x.yaml`) or touch `.git`.
- Saving content identical to the current version makes no empty commit.

---

## Command reference

Global options (before the command):

| Option | Meaning |
|---|---|
| `-C PATH`, `--repo PATH` | the configuration repository (default: current folder) |
| `-v`, `--verbose` | also print the audit records to the terminal |
| `--version` | print the version |
| `-h`, `--help` | help for the tool or for any command (`config-cli set -h`) |

Commands that commit accept `-m MESSAGE` to replace the default message.

### Repository

| Command | What it does |
|---|---|
| `init` | Create a new configuration repository on branch `main`. |
| `clone SOURCE [--branch B]` | Copy an existing configuration repository (URL or path) into `-C`. |
| `status` | Show the current branch or draft, and any sync waiting to be finished. |

```bash
config-cli -C configs init
config-cli -C my-copy clone https://example.com/team/configs.git
config-cli status
```

### Reading and changing configuration

| Command | What it does |
|---|---|
| `save NAME --from FILE` | Copy a local JSON/YAML file into the repo as `NAME` and commit it. The format follows `NAME`'s extension, so JSON can be saved as YAML. |
| `set NAME KEY VALUE` | Set one value. `KEY` uses dots for nesting (`database.port`). Missing sections, and the file itself, are created. |
| `show NAME [KEY] [--branch B]` | Print the whole file, or one value. `--branch` reads another branch without switching to it. |

`VALUE` is read as JSON when possible, otherwise as text:

| You type | Stored as |
|---|---|
| `5432` | number |
| `true` / `false` / `null` | boolean / null |
| `[1, 2]` or `{"a": 1}` | list / section |
| `localhost` | text |
| `'"5432"'` | the text `"5432"` (JSON quotes force text) |

```bash
config-cli save app.yaml --from ./local-app.json -m "Import app settings"
config-cli set app.yaml database.host db1.internal
config-cli set app.yaml features '["search", "export"]'
config-cli show app.yaml database.port
config-cli show app.yaml --branch draft/new-port
```

### Drafts and publishing

| Command | What it does |
|---|---|
| `draft start NAME` | Create `draft/NAME` from the latest `main` and switch to it. |
| `draft switch NAME` | Switch to an existing draft; later changes go there. |
| `draft main` | Switch back to `main`. |
| `draft list` | List drafts; the current one is marked with `*`. |
| `publish NAME` | Merge the draft into `main` and delete it. On a conflict nothing changes. |
| `sync NAME` | Merge `main`'s latest changes into the draft. |
| `sync --finish` | Commit a sync after fixing its conflicts. |
| `sync --abort` | Undo a sync that stopped on conflicts. |

```bash
config-cli draft start new-port
config-cli set app.yaml database.port 6000
config-cli draft list
config-cli publish new-port -m "Move database to port 6000"
```

### History and rollback

| Command | What it does |
|---|---|
| `history NAME [-n N]` | List the commits that changed `NAME`, newest first. |
| `rollback NAME REVISION` | Restore `NAME` as it was in `REVISION`, as a new commit. |

`REVISION` is a commit ID from `history` (short or full) or anything Git
understands, such as `HEAD~1`. Only commits on the current branch are accepted.

```bash
config-cli history app.yaml -n 3
config-cli rollback app.yaml 7e31ce4
```

---

## Resolving a merge conflict

A conflict happens when the draft and `main` both changed the same line.
Publishing refuses to merge and leaves `main` untouched:

```text
$ config-cli publish new-port
Error: Merge conflict with 'draft/new-port' in: app.yaml. The publish was
cancelled and main is unchanged. Sync the draft with main, fix the conflicts
there, then publish again. Run 'config-cli sync new-port' to resolve it.
```

1. **Bring `main`'s changes into the draft.** The conflict now happens on the draft:

   ```bash
   config-cli sync new-port
   config-cli status
   ```

   ```text
   On draft 'new-port' (branch draft/new-port)
   A sync is in progress.
   Files to fix:
     app.yaml
   Then run 'config-cli sync --finish', or 'sync --abort' to undo.
   ```

2. **Edit the file.** Git marks both versions:

   ```yaml
   database:
   <<<<<<< HEAD
     port: 6000
   =======
     port: 7000
   >>>>>>> main
   ```

   Keep the value you want and delete the three marker lines.

3. **Finish the sync.** The tool refuses if any marker is left or the file is
   no longer valid JSON/YAML, so a broken config can never be committed:

   ```bash
   config-cli sync --finish
   ```

   Changed your mind? `config-cli sync --abort` puts the draft back.

4. **Publish again.** The draft now already contains `main`'s changes:

   ```bash
   config-cli publish new-port
   ```

---

## Audit log

Every command and every Git action is appended to
`<repository>/.git/config-cli-audit.log` as one JSON object per line
([JSON Lines](https://jsonlines.org/)). The log belongs to the repository but is
never committed or pushed.

```json
{"timestamp": "2026-10-16T14:52:37+00:00", "level": "INFO", "logger": "config_cli.cli", "event": "command_started", "command": "set", "repo": "configs"}
{"timestamp": "2026-10-16T14:52:38+00:00", "level": "INFO", "logger": "config_cli.git_repository", "event": "committed", "branch": "main", "sha": "4efd138…", "files": ["app.yaml"], "message": "Set database.port in app.yaml"}
{"timestamp": "2026-10-16T14:53:02+00:00", "level": "WARNING", "logger": "config_cli.git_repository", "event": "merge_conflict", "source": "draft/new-port", "target": "main", "files": ["app.yaml"], "kept_open": false}
```

Events: `repository_initialized`, `repository_cloned`, `command_started`,
`command_failed`, `committed`, `commit_skipped`, `branch_created`,
`branch_checked_out`, `branch_deleted`, `merged`, `merge_conflict`,
`merge_failed`, `merge_finished`, `merge_aborted`, `file_restored`.

Search it with ordinary tools, e.g. `grep merge_conflict .git/config-cli-audit.log`,
or show records live with `-v`.

---

## Errors and exit codes

| Exit code | Meaning |
|---|---|
| `0` | success |
| `1` | the tool reported a problem (printed to stderr as `Error: …`) |
| `2` | the command line was invalid (unknown command, missing argument) |

All expected problems are subclasses of `ConfigCliError`, so the CLI handles
them in one place; anything else is a bug and is shown with a full traceback.

| Error | Raised when |
|---|---|
| `ConfigNotFound` | a config file (or a file at a given commit) does not exist |
| `InvalidConfigError` | a file is malformed, has an unsupported format, or a name/key is invalid |
| `KeyNotFoundError` | a dotted key is not in the file |
| `InvalidRepoError` | the path is not a usable repository, or a clone failed |
| `BranchError` | a branch/draft is missing, already exists, or the working tree is not clean |
| `CommitNotFoundError` | a rollback revision does not exist or is on another branch |
| `MergeConflictError` | a merge stopped on conflicting changes; the message says what to do next |

---

## Architecture

```text
            cli.py  (argparse commands, output, exit codes)
               │ uses Services: factories typed by the abstractions below
               ▼
      config_manager.py  (the workflow: drafts, publish, sync, rollback, keys)
          │                                   │
          ▼ ConfigFormat (Protocol)           ▼ VersionStore (Protocol)
  config_parser.py                     git_repository.py
  JSON/YAML only, no Git               Git only (GitPython), no JSON/YAML
```

| Module | Responsibility |
|---|---|
| `config_parser.py` | `ConfigParser`: read/write/format JSON and YAML. Safe YAML loading, stable key order, LF endings. |
| `git_repository.py` | `GitRepository`: every Git operation; translates GitPython errors into the tool's exceptions; `temporary_clone()` context manager. |
| `config_manager.py` | `ConfigManager`: combines the two into user-level actions. Knows the draft rules; never touches Git or file formats directly. |
| `interfaces.py` | `ConfigFormat` and `VersionStore` Protocols that the manager and CLI depend on (Dependency Inversion). |
| `exceptions.py` | The `ConfigCliError` family. |
| `audit.py` | JSON Lines audit logging. |
| `cli.py` | Commands and their output. |

Design choices worth knowing:

- **Single responsibility.** The parser knows nothing about Git and the
  repository knows nothing about JSON/YAML. The manager coordinates them and
  passes a validation callback into `finish_merge`, so the Git layer can check
  resolved files without knowing their format.
- **Dependency inversion.** `ConfigManager(repo, parser)` takes any objects
  matching the Protocols; the CLI gets its factories from a `Services` object.
  Tests use this to run the manager and the CLI against fakes, with no Git.
- **Open/closed CLI.** Each sub-command stores its handler with
  `set_defaults(handler=...)`; `main` runs any command the same way, so adding a
  command never changes `main`.
- **Context managers.** `GitRepository` is a context manager that releases
  GitPython's resources, and `temporary_clone()` gives a throwaway clone that
  is always closed and deleted. `show --branch` uses it to read another branch
  without switching the user's checkout.

---

## Development

```bash
poetry install
poetry run pre-commit install        # run the checks before every commit
```

| Task | Command |
|---|---|
| Run the tests | `poetry run pytest` |
| Tests with coverage | `poetry run pytest --cov=config_cli --cov-report=term-missing` |
| HTML coverage report | `poetry run pytest --cov=config_cli --cov-report=html:reports/htmlcov` |
| Format | `poetry run black src tests` |
| Lint | `poetry run ruff check src tests` |
| Type-check (strict) | `poetry run mypy` |
| All pre-commit hooks | `poetry run pre-commit run --all-files` |

The pre-commit hooks run ruff, Black, strict mypy and basic file checks
(trailing whitespace, final newline, valid YAML/TOML, leftover conflict
markers).

### How the tests work

- `tests/conftest.py` provides a `config_repo` fixture that creates a **real,
  temporary Git repository** for each test, and fixes the Git author so the
  tests also pass on machines without a Git identity.
- Tests check the resulting Git state (commits, parents, branches, file bytes)
  with GitPython directly, rather than trusting the code under test.
- Merge conflicts are created on purpose by changing the same line on two branches.
- Failures that are hard to cause for real (a non-conflict `git merge` failure,
  disk errors, byte-encoded commit messages) are simulated with
  `unittest.mock` and `monkeypatch`.
- The CLI is tested end to end through `main([...])`, checking stdout, stderr,
  exit codes, Git state and the audit log.

The latest coverage report is in [`reports/coverage.txt`](reports/coverage.txt)
(247 tests; 100% of statements and 99.5% including branch coverage).

---

## Demo walkthrough

A script for showing the whole lifecycle (draft → publish → update → conflict
→ resolve → publish → rollback). Run it in an empty folder.

```bash
# 1. Create the repository and a first configuration
config-cli -C demo init
cd demo
config-cli set app.yaml database.host db1
config-cli set app.yaml database.port 5432
config-cli show app.yaml

# 2. Prepare a change on a draft and publish it
config-cli draft start new-host
config-cli set app.yaml database.host db2
config-cli show app.yaml --branch main        # main still has db1
config-cli publish new-host
config-cli show app.yaml database.host        # db2

# 3. Update: a draft and main change the same value -> conflict
config-cli draft start new-port
config-cli set app.yaml database.port 6000
config-cli draft main
config-cli set app.yaml database.port 7000
config-cli publish new-port                    # Error: merge conflict, main unchanged

# 4. Resolve the conflict on the draft
config-cli sync new-port                       # conflict markers now in app.yaml
config-cli status
#   (edit app.yaml: keep "port: 6500", delete the <<<<<<< ======= >>>>>>> lines)
config-cli sync --finish
config-cli publish new-port

# 5. History, rollback and the audit trail
config-cli history app.yaml
config-cli rollback app.yaml HEAD~1
config-cli show app.yaml
tail -5 .git/config-cli-audit.log
```
