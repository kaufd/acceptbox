# CLI and configuration reference

**English** · [Русский](usage.ru.md)

This page documents every `acceptbox` command, flag, configuration key, exit code, reason code, and report rule. For a step-by-step introduction, see [onboarding](onboarding.md); for ready-to-copy stack templates, see [project setup & templates](setup.md).

## Installation check (`--installation`)

```sh
acceptbox --installation
```

Prints format-`1` JSON containing:
- `format`: `1`
- `report_format`: `1`
- `distribution`: `"acceptbox"`
- `version`: installed version (e.g. `"0.2.0"`)
- `git`: status (`"ok" | "missing" | "error"`), path, version
- `witnessbox`: path, version, `supported: ">=0.10.0"`, status (`"compatible" | "missing" | "incompatible" | "error"`)

Exit code is `0` whenever `git` and `acceptbox` are healthy, regardless of whether `witnessbox` is installed. Exit code `2` is returned only if `git` is missing/broken or AcceptBox diagnostics fail.

`acceptbox --version` prints `acceptbox <version>`.

## Project configuration (`.acceptbox.toml`)

Place `.acceptbox.toml` in the repository root and commit it to the base branch (e.g. `main`):

```toml
format = 1
mode = "command"               # optional: "command" | "witnessbox" (default: "command")
timeout = 600                  # optional: finite positive number of seconds
protect = ["pyproject.toml"]   # optional: merged (union) with CLI --protect
env = ["PYTHONPATH=src"]       # optional: KEY or KEY=VAL (BASE, EBT_* and git local-env-vars forbidden)

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
# ebt_repo is optional when ebt/ lives in the same repository (defaults to --repo);
# if specified, it must be a URL or an absolute path.
```

### Trust, validation, and precedence rules (fail-closed)

- **Read strictly from `base`:** `.acceptbox.toml` is read directly from Git objects of the `base` commit (`git ls-tree` + `git cat-file`), never from the working tree or from `repro`/`head`. In `calibrate`, it is read only when `--base` is passed.
- **Auto-protected:** `.acceptbox.toml` is automatically added to protected paths for `base..repro` and `base..head` (identical to `--protect .acceptbox.toml`).
- **`--base` trust boundary:** the config is only as trustworthy as `--base`. Pass `--base-ref main` (CLI-only — `base_ref` is rejected inside `.acceptbox.toml`) so AcceptBox verifies `--base` is an ancestor of `main` from `origin` *before* loading `.acceptbox.toml`. Without `--base-ref`, the acceptor (human, CI, or external orchestrator) must provide `--base`.
- **Strict schema validation:** all present sections are validated fail-closed. Unknown keys, per-task fields (`inv`, `ebt_sha`, `approved_tree`, `base`, `repro`, `head`, `repo`, `out`, `base_ref`), relative local `[witnessbox].ebt_repo` paths, symlinks/directories instead of a regular file, and TOML syntax errors fail with `ERROR` (`config-invalid`, exit code `2`). In that report, `inputs.config` records the `commit` and `sha256` of the rejected file.
- **Environment allowlist:** `env` allows ordinary variables (including e.g. `GIT_SSH_COMMAND` or `GIT_AUTHOR_NAME`) but forbids `BASE`, any `EBT_*`, and git isolation variables from `git rev-parse --local-env-vars`.
- **CLI precedence:**
  - `protect` from config and CLI `--protect` are combined as a union (CLI cannot weaken protections from `base`).
  - `env` merges with CLI precedence by key; passing `--env KEY` without a value when `KEY` is absent in the host environment unsets `KEY` from config.
  - `mode`, `timeout`, `tests`, `argv`, `ebt_repo`, and `ebt_ref` are overridden by explicit CLI flags.

## Pinning the EBT tree (`pin`)

```sh
acceptbox pin --ebt-repo /path/to/project --ebt-sha "$(git -C /path/to/project rev-parse HEAD)"
```

Reads the `ebt/` tree directly from Git objects of `--ebt-sha` (ignoring uncommitted changes) and prints a 64-character lowercase SHA-256 hex digest. Both `--ebt-repo` and `--ebt-sha` (full 40/64-hex SHA) are required.

## Accepting a commit chain (`accept`)

### Defaults

- `--mode`: from `.acceptbox.toml`, or `command`.
- `--repo`: `.`.
- `--head`: `HEAD` of `--repo`, resolved to a full SHA immediately after checking `git` (failure produces `gate-error` with a report). An explicitly passed `--head` must be a full 40/64-hex SHA. **Note:** uncommitted working-tree edits are never tested; when `--repo` is a URL, omitting `--head` resolves remote `HEAD` via `git ls-remote` (usually the default branch, not a feature branch — pass `--head` explicitly for URLs).
- `--ebt-repo` (`witnessbox` mode): from `[witnessbox].ebt_repo`, or `--repo`.
- `--timeout`: from `.acceptbox.toml`, or `600` seconds.
- `--out`: atomically creates a new empty directory in system `TMPDIR` and prints the path to `accept.json`. When `--out` is passed explicitly, it must be non-existent or empty (`O_EXCL` lock).

### Trusted ref source (`--base-ref` and `--ebt-ref`)

- If the repository argument (`--repo` for `--base-ref`, `--ebt-repo` for `--ebt-ref`) is a URL or scp-like remote, the ref is fetched directly from it (`ref_source_kind: "remote"`).
- If it is a local path, AcceptBox queries `git remote get-url origin` and fetches the ref from `origin` (`ref_source_kind: "origin"`), so an agent moving local branches cannot forge ancestry. Short ref names resolve `refs/heads/<ref>` first, then `refs/tags/<ref>`.
- If the local repository has no `origin` remote, AcceptBox falls back to the local repository and marks the report `ref_source_kind: "local"` (weak check). Any other failure reading `origin` (e.g. `safe.directory`) is `gate-error`.
- **Trust boundary:** `origin`'s URL lives in `.git/config` of the local checkout. Inspect `ref_source` and `ref_source_kind` in `accept.json`, or run acceptance outside the agent's reach (CI) / pass URLs for `--repo` and `--ebt-repo`.

### `command` mode

Minimal invocation (with `.acceptbox.toml` committed in `base`):

```sh
acceptbox accept --base-ref main --base "$(git merge-base main HEAD)" --repro <full-sha>
```

Explicit invocation (without `.acceptbox.toml`):

```sh
acceptbox accept \
  --mode command \
  --repo /path/to/project \
  --base <full-sha> \
  --repro <full-sha> \
  --head <full-sha> \
  --tests tests/ \
  [--base-ref main] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-run] \
  -- python3 -m unittest discover -s tests
```

The command after `--` (or `[command].argv`) runs with `shell=False` in a dedicated process session (the whole process group is killed on timeout and after completion) with a sanitized environment. No test-runner autodetection is performed. Exit code `0` → `green`, exit code `1` → `red`, any other exit code (`>= 2`), signal, `OSError`, or timeout → `gate-error` (exit code `2`).

### `witnessbox` mode

```sh
acceptbox accept \
  --mode witnessbox \
  --base <full-sha> \
  --repro <full-sha> \
  --inv INV-1,INV-2 \
  --ebt-ref main \
  --ebt-sha <full-sha> \
  --approved-tree <64-hex-sha256> \
  [--base-ref main] \
  [--repo /path/to/project] \
  [--head <full-sha>] \
  [--ebt-repo /path/to/ebt-repo] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-ebt-run]
```

In `witnessbox` mode, `ebt` and `tool/ebt_check` are always added to protected paths, `base` and `repro` must both be `red` with `FALSE_PROOF` or `CONFIRMED_VIOLATION` on at least one invariant from `--inv`, and `head` must be `green`.

## Sensitivity calibration (`calibrate`)

Checks reference `--green` and `--red` commits and writes `<out>/calibrate.json` (never `accept.json`). Ancestry, `--protect`, `base..repro` / `repro..head` diffs, and `--base-ref` are **not** applied. `--tests` is not accepted. If `--base <sha>` is passed, `.acceptbox.toml` is loaded from that commit (and `BASE` is passed to WitnessBox).

`command` mode:

```sh
acceptbox calibrate \
  --green <sha> \
  --red <sha> \
  [--repo /path/to/project] \
  [--base <base_sha>] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/calibrate-run] \
  -- python3 -m unittest discover -s tests
```

`witnessbox` mode:

```sh
acceptbox calibrate \
  --mode witnessbox \
  --green <sha> \
  --red <sha> \
  --inv INV-1 \
  --ebt-ref main \
  --ebt-sha <full-sha> \
  --approved-tree <64-hex-sha256> \
  [--repo /path/to/project] \
  [--ebt-repo /path/to/ebt-repo] \
  [--base <base_sha>] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/calibrate-ebt-run]
```

## Exit codes, reason codes, and report rules

| Exit code | `accept` result | `calibrate` result | Meaning |
|---|---|---|---|
| `0` | `ACCEPTED` | `CALIBRATED` | All checks/expectations succeeded |
| `1` | `REJECTED` | `FAILED` | Acceptance rule or calibration expectation failed |
| `2` | `ERROR` | `ERROR` | CLI/config/git/WitnessBox/runtime error |

### `REJECTED` / `FAILED` reason codes (exit code `1`)

| Reason code | Mode / command | Meaning |
|---|---|---|
| `base-not-on-ref` | `accept` (with `--base-ref`) | `--base-ref` was not found in the trusted source or `--base` is not its ancestor |
| `no-invariants` | `witnessbox` | `--inv` contained no invariants |
| `inputs-untrusted` | `witnessbox` | `--ebt-ref` missing, `--ebt-sha` not ancestor of `--ebt-ref`, `ebt/` hash ≠ `--approved-tree`, or `ebt/spec.toml` missing |
| `repro-not-descendant` | `accept` | `base` is not an ancestor of `repro` |
| `repro-not-ancestor` | `accept` | `repro` is not an ancestor of `head` |
| `protected-paths-changed` | `accept` | A protected path (including `.acceptbox.toml`, or `ebt`/`tool/ebt_check` in `witnessbox` mode) changed in `base..repro` or `base..head` |
| `repro-empty` | `accept` (`command`) | `base..repro` diff is empty |
| `repro-outside-tests` | `accept` (`command`) | `base..repro` modified files outside `--tests` |
| `repro-tests-modified` | `accept` (`command`) | `repro..head` modified, renamed, or deleted files changed in `base..repro` |
| `base-green` / `base-report-invalid` / `base-red-wrong-reason` | `accept` (`witnessbox`) | `base` did not fail for the expected invariant |
| `repro-green` / `repro-report-invalid` / `repro-red-wrong-reason` | `accept` | `repro` passed or failed for the wrong reason |
| `head-not-green` | `accept` | `head` did not pass |
| `mismatch` | `calibrate` | At least one `--green` / `--red` commit did not match its expected status |

### `ERROR` reason codes (exit code `2`) and report-writing rule

- **No report written:** CLI syntax errors, `out-not-empty`, and missing required arguments (`--tests`, command after `--`, `--inv`, `--ebt-ref`, `--ebt-sha`, `--approved-tree`) or cross-mode flag mixing discovered after reading `base:.acceptbox.toml`. If the current invocation created an empty `--out` directory, it is removed.
- **Report written atomically** (and its path printed to `stderr`) for all other exit-code-`2` errors after acquiring `--out`: `config-invalid`, `witnessbox-missing`, `witnessbox-incompatible`, and `gate-error`.

## Development, building, and releasing

```sh
python3 -m unittest discover -s tests
python3 tests/release_check.py
```

`tests/release_check.py` builds the sdist and wheel from it, verifies stdlib-only imports and version consistency, runs the full test suite from the unpacked sdist (zero skips), confirms byte-identical wheel rebuilding, tests the installed wheel in a clean venv, and writes `build/release/<version>/release.json` and `SHA256SUMS`.

### Release workflow (GitHub Actions)

1. Bump `version` in `pyproject.toml` and fallback `__version__` in `src/acceptbox/__init__.py`, merge to `main`.
2. Tag and push: `git tag -a v<version> -m "AcceptBox <version>" && git push origin v<version>`.
   The [Release](../.github/workflows/release.yml) workflow runs `tests/release_check.py` and creates a GitHub **prerelease** with the wheel, sdist, `release.json`, and `SHA256SUMS` attached. (You can also run it manually via `gh workflow run release.yml -f tag=v<version>`.)
3. Publish the verified artifacts to PyPI (OIDC Trusted Publishing; builds nothing and verifies SHA-256 against `release.json`):
   ```sh
   gh workflow run publish.yml -f tag=v<version>
   ```
4. Verify the PyPI install (`uv tool install acceptbox==<version> && acceptbox --installation`) and promote the GitHub Release:
   ```sh
   gh release edit v<version> --prerelease=false --latest
   ```
