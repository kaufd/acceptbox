# Project setup and templates

**English** · [Русский](setup.ru.md)

Unlike WitnessBox (which ships a multi-file `ebt/` kit via `witnessbox scaffold`), AcceptBox needs only a minimal set of files committed to your base branch. This guide is the AcceptBox equivalent of a project scaffold: what files to create, ready-to-copy stack recipes, the wrapper-script contract, and a drop-in `AGENTS.md` rules block for every AI coding agent working in your repository.

To have a coding agent set up these files for you automatically (stopping for your approval before committing), give it the [agent instructions](agent.md).

## What goes into a connected project

| File | Required? | Purpose |
|---|---|---|
| `.acceptbox.toml` | Recommended | Project defaults (`mode`, `timeout`, `protect`, `env`, `[command]`, `[witnessbox]`). Read strictly from `base` and auto-protected. |
| `scripts/acceptbox-test.sh` (or similar) | Only if clean worktrees need setup | Installs dependencies / builds before running tests, preserving the exit-code contract (`0` pass, `1` test failure, `>= 2` setup/build error). Add its path to `protect`. |
| `.acceptbox/AGENTS.md` (or a section in root `AGENTS.md`) | Recommended for AI workflows | Rules for every coding agent that works on bugfixes in this repository: `base → repro → head` commit discipline, protected files, self-check commands, and PR acceptance reporting. Link it from root `AGENTS.md` and `CLAUDE.md`. |

## Stack recipes

### Python (`unittest` or `pytest`)

With standard-library `unittest` (no dependency install needed):

```toml
format = 1
mode = "command"
timeout = 300
protect = ["pyproject.toml"]
env = ["PYTHONPATH=src"]

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
```

With `pytest` (note: `pytest` exits `0` on pass, `1` on test failure, and `2`–`5` on usage/internal/collection/zero-tests errors — matching AcceptBox's contract out of the box):

```toml
format = 1
mode = "command"
timeout = 300
protect = ["pyproject.toml", "uv.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
uv sync --frozen --no-progress >/dev/null || exit 2
uv run pytest
```

### TypeScript / JavaScript (`vitest` or `jest` with `bun` / `npm` / `pnpm`)

`.acceptbox.toml`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["package.json", "bun.lock", "vitest.config.ts", "scripts/acceptbox-test.sh"]

[command]
tests = ["src/**/*.test.ts", "tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
bun install --frozen-lockfile || exit 2
exec bunx vitest run
```

*(For npm: replace `bun install --frozen-lockfile` with `npm ci || exit 2` and `bunx vitest run` with `npx vitest run` or `npx jest`.)*

### Dart / Flutter

`.acceptbox.toml`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pubspec.yaml", "pubspec.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["test/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
flutter pub get >/dev/null || exit 2
exec flutter test --no-pub
```

### Go

`go test` returns exit code `1` for *both* build failures and test failures. Compile test binaries first so a broken build returns `2` (`gate-error`) instead of a false `red`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["go.mod", "go.sum", "scripts/acceptbox-test.sh"]

[command]
tests = ["**/*_test.go"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
go test -run='^$' ./... >/dev/null || exit 2
exec go test ./...
```

### Rust (`cargo test`)

`cargo test` returns `101` on test failures (which AcceptBox would classify as `gate-error`, not `red`!). Use a wrapper that compiles first (`exit 2` on compile failure) and maps test failures (`101`) to `1`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["Cargo.toml", "Cargo.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
cargo test --no-run || exit 2
set +e
cargo test
rc=$?
if [ "$rc" -eq 0 ]; then
  exit 0
elif [ "$rc" -eq 101 ]; then
  exit 1
else
  exit 2
fi
```

### JVM (Gradle / Maven)

For Gradle:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "scripts/acceptbox-test.sh"]

[command]
tests = ["src/test/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
./gradlew testClasses --quiet || exit 2
exec ./gradlew test
```

*(For Maven, run `mvn -q test-compile || exit 2` before `mvn -q test` so compilation failures exit `2` rather than `1`.)*

### Combined `command` + `witnessbox` project

`.acceptbox.toml` supports both sections side by side without flag conflicts:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pyproject.toml", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]

[witnessbox]
ebt_ref = "main"
```

Run `acceptbox accept --base-ref main` for everyday bugfixes in `command` mode (or `--policy refactor` for refactorings), or run `acceptbox accept --mode witnessbox --base-ref main --pin-from-ref` when accepting against human-approved EBT invariants.

## Wrapper script contract

Whenever `[command].argv` invokes a wrapper script:

1. **Working directory:** root of a clean, detached Git worktree for the commit being tested (`repro` or `head`). Do not assume `node_modules/`, `.venv/`, or `build/` from the developer's checkout exist.
2. **Sanitized environment:** AcceptBox runs commands with `shell=False` and strips host environment variables outside a minimal allowlist plus explicit `env` entries. Pass any required non-sensitive variables in `.acceptbox.toml`'s `env` list.
3. **Exit codes are strict:**
   - `0` → `green` (tests ran and passed).
   - `1` → `red` (tests ran and failed on assertions).
   - `>= 2` → `gate-error` (dependency install failed, compilation failed, test binary missing, zero tests discovered, etc.). **Never** let a setup or build error exit `1`, or AcceptBox will treat a broken environment as a valid failing `repro` commit.
4. **Protect the wrapper:** always include the wrapper script path and lockfiles in `protect = [...]` inside `.acceptbox.toml` so a task branch cannot modify the test runner.
5. **Committed and executable:** commit the script with `chmod +x` (or invoke `["sh", "scripts/acceptbox-test.sh"]`).

## Ongoing agent rules template (`.acceptbox/AGENTS.md`)

Just like WitnessBox's `ebt/AGENTS.md`, copy the block below into `.acceptbox/AGENTS.md` in your repository (add `.acceptbox/AGENTS.md` to `protect` in `.acceptbox.toml` if you want it immutable on task branches) and link it from your root instructions:

- In root `AGENTS.md`: `AcceptBox rules for this project: .acceptbox/AGENTS.md`
- In root `CLAUDE.md`: `@.acceptbox/AGENTS.md`

```markdown
# Rules for coding agents: AcceptBox in this project

This project uses AcceptBox to verify bugfix commit chains (`base → repro → head`) in isolated Git
worktrees. The rules below apply to every coding agent working in this repository.

## Hard rules

1. **Know when AcceptBox applies.** Use `base → repro → head` (`--policy bugfix`) for **bugfixes**
   (and human-approved invariants in `--mode witnessbox`). Use `--policy refactor` (`base` green →
   `head` green, tests untouched) for **refactorings**. Do not fabricate a fake `repro` test for a
   brand-new feature without a human-approved specification.
2. **Strict two-commit bugfix structure (`base → repro → head`):**
   - Start from a clean commit `base` on the base branch (e.g. `main`).
   - **Commit 1 (`repro`):** modify **only** test files matching `[command].tests` in
     `.acceptbox.toml`. Do not touch application code or protected paths. The test must genuinely
     reproduce the bug and fail with exit code `1`.
   - **Commit 2 (`head`):** fix the bug in application code so the test suite passes with exit
     code `0`. **Do not edit, rename, or delete** any file touched in `repro`.
3. **Do not edit protected files on a task branch.** Never change `.acceptbox.toml`, test wrapper
   scripts, lockfiles listed in `protect`, or (in `witnessbox` mode) `ebt/` and `tool/ebt_check`
   in a bugfix branch — AcceptBox rejects such branches with `protected-paths-changed`. Changes to
   `.acceptbox.toml` or protected test infrastructure require human approval on the base branch.
4. **Never cheat the gate.** Do not weaken or skip tests, do not forge `--base`, do not move local
   branches or rewrite `.git/config` / `origin` to bypass `--base-ref`, and do not make a wrapper
   script exit `1` on setup/build failures.
5. **Treat `REJECTED` / `ERROR` as a real signal.** Read `reason_code`, `message`, and the run logs
   referenced in `accept.json`. Fix the underlying problem (e.g. wrong commit split, missing
   reproduction, incomplete fix) rather than bypassing checks.
6. **Report acceptance in the pull request.** Include an `## Acceptance` section with:
   - the output of `acceptbox render <path-to-accept.json> --format md` (verdict, policy, warnings,
     `base`/`repro`/`head` SHAs, and `ref_source_kind`); if `render` is unavailable, give the SHAs,
     the verdict with exit code, `ref_source_kind`, and every `acceptbox: warning:` line;
   - a **Not verified** list (behaviour outside the repro test, UI/manual paths, or if local
     acceptance had `ref_source_kind: "local"` and awaits CI verification).
   If you could not run AcceptBox locally, say so explicitly and why.

## Local self-check command

Run from the repository root after committing (uncommitted edits are ignored):

```sh
# Bugfix (command mode):
acceptbox accept --base-ref main

# Refactoring (command mode):
acceptbox accept --policy refactor --base-ref main

# WitnessBox mode (optional --inv, optional --policy refactor):
acceptbox accept --mode witnessbox --base-ref main --pin-from-ref
```

Note: local runs are a self-check; the authoritative verdict comes from a human or CI running
AcceptBox outside the agent's checkout (or passing `--repo` as a URL).
```

## After setup: verify it works

1. **Human review & commit:** review `.acceptbox.toml`, any wrapper script, and `.acceptbox/AGENTS.md`, then commit them to `main` (and push to `origin` if you have a remote).
2. **Environment check:** run `acceptbox --installation` (exit code `0`).
3. **Sensitivity check:** verify that your config actually runs in a clean detached worktree and distinguishes passing vs failing commits using `acceptbox calibrate` (or a temporary throwaway worktree — see Stage 4 in [agent instructions](agent.md)):
   ```sh
   acceptbox calibrate --base "$(git rev-parse HEAD)" --green "$(git rev-parse HEAD)"
   ```
   Confirm exit code `0` (`CALIBRATED`). Then test a temporary defect commit with `--red` to confirm it exits `1` (`red`) rather than `2` (`gate-error`).
