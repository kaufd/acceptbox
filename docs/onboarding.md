# Connecting a new project to AcceptBox

**English** · [Русский](onboarding.ru.md)

AcceptBox verifies a commit chain `base → repro → head` in an isolated temporary clone (without switching your working checkout) and writes a machine-readable verdict `accept.json`.

For stack-specific `.acceptbox.toml` templates, wrapper scripts, and a ready-to-copy `AGENTS.md` rules snippet for your repository, see [Project setup & templates](setup.md). To let an AI coding agent wire up the project for you, point it to the [agent instructions](agent.md). For full flag and schema details, see the [CLI reference](usage.md).

## 0. Applicability: when to use `command` vs `witnessbox` (L24)

- **Bugfixes (`--mode command` or `--mode witnessbox`):** the `base → repro → head` chain with a failing repro commit is specifically designed for bugfixes.
- **New features:** a failing reproduction test written by the same model that writes the implementation does not give a strong guarantee (the model can fit the test and code to each other). For features, use a human-approved specification (`--mode witnessbox`, D4/L10) and/or independent review and behavioural checks.
- **Refactorings:** a `base` green → `head` green policy with protected tests and no `repro` commit is orthogonal to `--mode` and remains a separate workflow.

## 1. Install and check the environment

```sh
uv tool install acceptbox==0.2.0
# Optional (only needed for --mode witnessbox):
uv tool install 'witnessbox>=0.10.0'

acceptbox --installation
```

`acceptbox --installation` returns exit code `0` as long as `git` and `acceptbox` work, even when `witnessbox` is not installed (`witnessbox.status = "missing"`).

## 2. Configure `.acceptbox.toml` (recommended, L23, L25, L26)

Add and commit `.acceptbox.toml` on your base branch (e.g. `main`) so you do not have to repeat project parameters on every run:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pyproject.toml"]

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
```

- The config is read **strictly from the `base` commit**, never from the working tree or the `repro`/`head` commits under check.
- `.acceptbox.toml` is automatically protected against edits, deletion, or renaming across `base..repro` and `base..head` (`protected-paths-changed`).
- **`--base` trust boundary (L26, L29):** pass `--base-ref main` so AcceptBox verifies *before* reading the config that `--base` is an ancestor of `main` from the checkout's `origin` remote. Without `--base-ref`, `--base` must be supplied by the acceptor (human, CI, or external orchestrator), not the agent under test — otherwise the agent could supply a forged `base` containing its own `.acceptbox.toml`. Ensure your checkout has remote `origin` pointing to the canonical repository; without `origin`, the check falls back to the local branch (`ref_source_kind: "local"`). Trust boundary: the `origin` URL itself comes from the checkout's `.git/config`, so an agent that can write `.git/config` could repoint or remove `origin`. Inspect `ref_source` and `ref_source_kind` in `accept.json`, and for full assurance run acceptance outside the agent's reach (CI) or pass `--repo` / `--ebt-repo` as the canonical repository URL.
- `protect` from config and `--protect` from CLI are unioned; `tests`, `argv`, `mode`, `timeout`, `ebt_repo`, and `ebt_ref` can be overridden with explicit CLI flags.
- `[witnessbox].ebt_repo` in config accepts only a URL or an absolute path.

## 3. Quick start in `command` mode (without WitnessBox)

In `command` mode (the default, L22), the `repro` commit adds a failing reproduction test inside `--tests`, and the `head` commit fixes the bug without modifying, renaming, or deleting the repro test files.

With `.acceptbox.toml` already committed in `base`, run from the repository root:

```sh
acceptbox accept --base-ref main --base "$(git merge-base main HEAD)" --repro <repro_sha>
```

Here `--repo` defaults to `.`, `--head` is immediately resolved from `HEAD` to a full SHA, and `--out` is atomically created in `TMPDIR` (the path to `accept.json` is printed in the summary).
Keep in mind: uncommitted working-tree changes are not tested, and if `--repo` is given as a URL without `--head`, `git ls-remote` resolves remote `HEAD` (usually the default branch, not your fix branch — pass `--head` explicitly for URLs).

Without `.acceptbox.toml`, pass `--tests` and the test command after `--` explicitly:

```sh
acceptbox accept \
  --base-ref main \
  --base "$(git merge-base main HEAD)" \
  --repro <repro_sha> \
  --tests tests/ \
  --protect pyproject.toml \
  -- python3 -m unittest discover -s tests
```

> **Clean-worktree dependencies (wrapper script):** every commit runs in a fresh detached worktree with a sanitized environment and no cached working-tree build artifacts. If your project needs dependency installation or compilation before running tests, wrap it in a script invoked via `[command].argv` or after `--`. The wrapper **must** exit with code `>= 2` if dependency/build setup fails and exit with code `1` **only** when tests themselves fail — otherwise an environment failure will be misclassified as `red` instead of `gate-error` (exit code `2`). See ready-made wrapper examples in [Project setup & templates](setup.md).

## 4. Connecting `witnessbox` mode (EBT)

1. Ensure the [WitnessBox](https://github.com/kaufd/witnessbox) CLI (`kaufd/witnessbox`, version `>=0.10.0`) is installed and your project's human-approved `ebt/` directory is committed on the base branch.
2. Pin the fingerprint of the approved `ebt/` tree (`--ebt-repo` is required for `pin`):
   ```sh
   APPROVED_TREE=$(acceptbox pin --ebt-repo /path/to/project --ebt-sha <ebt_sha>)
   ```
3. Run acceptance (verifies `ebt/` trust, ancestry, protection of `.acceptbox.toml`, `ebt`, and `tool/ebt_check`, failing `base` and `repro` on `--inv`, and passing `head`):
   ```sh
   acceptbox accept \
     --mode witnessbox \
     --base-ref main \
     --base <base_sha> \
     --repro <repro_sha> \
     --inv INV-1 \
     --ebt-ref main \
     --ebt-sha <ebt_sha> \
     --approved-tree "$APPROVED_TREE"
   ```
   `--repo` (defaults to `.`), `--ebt-repo` (defaults to `--repo`), `--head` (defaults to `HEAD`), and `--out` (defaults to a new directory in `TMPDIR`) are optional. If `mode = "witnessbox"` and `[witnessbox].ebt_ref` are set in `base:.acceptbox.toml`, you can omit `--mode` and `--ebt-ref` too. When `--ebt-repo` is a local path (the default), `--ebt-ref` is fetched from its `origin` remote; without `origin`, it falls back to the local branch with `ref_source_kind: "local"` in the report, leaving `--approved-tree` as the trust anchor.

## 5. Sensitivity calibration (`calibrate`)

To check reference good commits and mutants (without ancestry, `--protect`, or diff checks, and without creating `accept.json`), use `acceptbox calibrate`:

```sh
acceptbox calibrate \
  --green <good_sha> \
  --red <mutant_sha> \
  -- python3 -m unittest discover -s tests
```

If you pass `--base <base_sha>`, `calibrate` loads project settings from `base:.acceptbox.toml` (without `--base`, the config is not read).
The report is written atomically to `<out>/calibrate.json` (when `--out` is omitted, a directory is created in `TMPDIR` and its path is printed). Exit codes: `0` — all expectations matched (`CALIBRATED`), `1` — mismatch (`FAILED`), `2` — environment/CLI/config error (`ERROR`).
