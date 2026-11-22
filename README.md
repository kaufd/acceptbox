# AcceptBox

**English** · [Русский](https://github.com/kaufd/acceptbox/blob/main/README.ru.md)

**Independent bugfix acceptance gate for Git commit chains (`base → repro → head`).**

When a developer or AI coding agent submits a bugfix, a passing test suite at the final commit does not prove the bug was genuinely reproduced and fixed: the test might have passed before the fix, or the fix commit might have weakened or deleted the test.

AcceptBox checks the three-commit bugfix chain in isolated temporary Git worktrees (without touching your working tree) and writes an atomic, machine-readable verdict (`accept.json`):

1. **`base`** — the starting commit (e.g., on `main`) where the bug exists.
2. **`repro`** — a commit that *only* adds a failing reproduction test (proving the bug really fails).
3. **`head`** — the fix commit that makes the tests pass *without editing, renaming, or deleting* the reproduction test or protected files.

The runtime uses only the Python standard library (`>=3.11`) and has zero third-party dependencies.

## Two acceptance modes

- **`command` mode (default, standalone)** — works with any test runner (`unittest`, `pytest`, `vitest`, `go test`, `cargo test`, `flutter test`, Gradle, etc.) with no extra tools. Verifies that `repro` changes only paths under `--tests` and fails with exit code `1` (`red`), and that `head` leaves those test files untouched and passes with exit code `0` (`green`).
- **`witnessbox` mode (optional)** — integrates with [WitnessBox](https://github.com/kaufd/witnessbox) (`>=0.10.0`) for evidence-based testing (EBT). Verifies a human-approved `ebt/` tree against a pinned SHA-256 (`acceptbox pin`), confirms both `base` and `repro` fail on the target invariant(s) (`FALSE_PROOF` / `CONFIRMED_VIOLATION`), and confirms `head` passes.

## Quick start

### 1. Install

```sh
uv tool install acceptbox
acceptbox --installation
```

*(Optional, only for `--mode witnessbox`: `uv tool install 'witnessbox>=0.10.0'`.)*

### 2. Configure the project once (`.acceptbox.toml` on `main`)

Commit `.acceptbox.toml` to your base branch so nobody has to repeat flags on every run:

```toml
format = 1
mode = "command"
protect = ["pyproject.toml"]

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
```

AcceptBox reads `.acceptbox.toml` strictly from Git objects of the `base` commit and automatically protects it from changes in `base..repro` and `base..head`.

### 3. Fix a bug as `base → repro → head`

```sh
# Starting from a commit on main (base):
# 1) Add a failing reproduction test in tests/ and commit it:
git commit -m "test: reproduce bug #123"
REPRO=$(git rev-parse HEAD)

# 2) Fix the bug in application code (without touching the repro test) and commit:
git commit -m "fix: handle empty input (#123)"
```

### 4. Run acceptance

```sh
acceptbox accept \
  --base-ref main \
  --base "$(git merge-base main HEAD)" \
  --repro "$REPRO"
```

AcceptBox prints the verdict and the path to `accept.json` (created atomically in a new directory under `TMPDIR`, or in `--out <empty-dir>`):

- Exit code `0` — **`ACCEPTED`** (all checks passed).
- Exit code `1` — **`REJECTED`** (acceptance rule failed: e.g., `base-not-on-ref`, `repro-outside-tests`, `repro-tests-modified`, `protected-paths-changed`, `repro-green`, `head-not-green`).
- Exit code `2` — **`ERROR`** (CLI/config/environment/git error or test runner failure with exit code `>= 2`).

> **Using an AI coding agent?** Point it to the [agent instructions](https://github.com/kaufd/acceptbox/blob/main/docs/agent.md) to set up `.acceptbox.toml`, test wrappers, and ongoing project agent rules automatically — with explicit human-approval stop points.

## When to use AcceptBox (and when not to)

- **Bugfixes** (`command` or `witnessbox` mode): the primary use case for `base → repro → head`.
- **Human-approved invariants** (`witnessbox` mode): works for both bugfixes and features when a human approves `ebt/spec.toml` before implementation.
- **New features without an approved spec**: a failing `repro` test written by the same agent that writes the implementation does not give a strong guarantee — the agent can fit the test and the code to each other. Use a human-approved specification (`witnessbox` mode) and/or independent review.
- **Refactorings** (`--policy refactor`, `base` green → `head` green without `--repro`): orthogonal to `--mode`; verifies that both `base` and `head` pass while automatically protecting `.acceptbox.toml` and `[command].tests` (or `ebt/` and `tool/ebt_check` in `witnessbox` mode).

## Guarantees and limits

AcceptBox enforces commit structure, file immutability, and clean-worktree execution, but it is **not an OS sandbox**: test commands run as the invoking OS user.

- **`.acceptbox.toml` trust boundary:** the config is read only from `base` and auto-protected, so it is as trustworthy as `--base`. Pass `--base-ref main` (CLI-only) so AcceptBox checks that `--base` is an ancestor of `main` from `origin` *before* reading the config. Add `--strict-refs` in CI to reject weak `"local"` fallback refs fail-closed (`ref-source-local`). Without `--base-ref`, `--base` must be supplied by the acceptor (human, CI, or external orchestrator), not by the agent under test.
- **Local `.git/config` trust boundary:** for a local `--repo` / `--ebt-repo`, AcceptBox fetches `--base-ref` and `--ebt-ref` from remote `origin` (falling back to the local branch with `ref_source_kind: "local"` if no `origin` exists, unless `--strict-refs` is passed). An agent with write access to `.git/config` could repoint or remove `origin`. Always inspect `ref_source` and `ref_source_kind` in `accept.json`; for full assurance, run acceptance outside the agent's reach (CI) or pass `--repo` / `--ebt-repo` as repository URLs.
- **Test quality in `command` mode:** `command` mode proves that `repro` only touched `--tests`, failed with exit code `1`, and was not altered by `head`, which then passed with exit code `0`. Reviewing that the test actually tests the right thing remains a human check — or use `witnessbox` mode for independent invariant checking.
- **Clean-worktree setup:** every commit runs in a fresh detached worktree with a sanitized environment. If your stack needs dependency installation or compilation, wrap it in a script that exits with code `>= 2` on setup failure and `1` *only* on test failure (see [Project setup & templates](https://github.com/kaufd/acceptbox/blob/main/docs/setup.md)).

## Documents

- [Quick start & onboarding](https://github.com/kaufd/acceptbox/blob/main/docs/onboarding.md) — step-by-step walkthrough for `command` mode, `witnessbox` mode, and `calibrate`.
- [Project setup & templates](https://github.com/kaufd/acceptbox/blob/main/docs/setup.md) — stack-by-stack `.acceptbox.toml` recipes, the clean-worktree wrapper script contract, and a ready-to-copy ongoing `AGENTS.md` rules template for connected projects.
- [Autonomous CI pipeline](https://github.com/kaufd/acceptbox/blob/main/docs/ci.md) — reusable GitHub Actions (`actions/setup` + `actions/accept`) for zero-operator bugfix and refactoring PR acceptance.
- [Instructions for a coding agent](https://github.com/kaufd/acceptbox/blob/main/docs/agent.md) — full playbook for AI agents: connecting AcceptBox to a repository (with human stop points) and working on bugfix branches afterward.
- [CLI & configuration reference](https://github.com/kaufd/acceptbox/blob/main/docs/usage.md) — complete reference for `accept`, `calibrate`, `pin`, `--installation`, `.acceptbox.toml`, exit codes, reason codes, and report rules.
- [Decisions](https://github.com/kaufd/acceptbox/blob/main/docs/decisions.md) — append-only architectural decision log (`L8`–`L30`).

Documentation is available in English and Russian; every page links to its translation.

## License

[MIT](https://github.com/kaufd/acceptbox/blob/main/LICENSE).
