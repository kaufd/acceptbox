# Agent instructions: deploying and working with AcceptBox

**English** · [Русский](agent.ru.md)

This document is addressed to an AI coding agent (Claude Code, Codex, Cursor, Antigravity, and the like). It covers **two workflows**:

1. **Part I — Quick deployment:** connecting AcceptBox to a repository for the first time. A human only needs to give the agent one line:
   ```
   Connect AcceptBox to this project following the instructions at https://github.com/kaufd/acceptbox/blob/main/docs/agent.md
   ```
2. **Part II — Ongoing work:** fixing bugs day-to-day in a repository where AcceptBox is already enabled.

For a human, the same concepts are covered in [onboarding](onboarding.md), [project setup & templates](setup.md), and the [CLI reference](usage.md).

---

# Part I: Connecting AcceptBox to a project

## Role and core rule

You configure AcceptBox so bugfix commit chains (`base → repro → head`) can be verified independently in isolated Git worktrees.

**The human approves the configuration before it becomes the trust anchor.** You inspect the repository, draft `.acceptbox.toml` (and a wrapper script if clean worktrees need setup), and **stop for human approval** before committing to the base branch. Because `.acceptbox.toml` is read from `base` and controls which command runs and which paths are protected, the human owns what goes into it.

## Hard prohibitions

1. Do not commit `.acceptbox.toml` or a test wrapper script to the base branch (`main`) until the human has reviewed and approved `tests`, `argv`, `protect`, and `env`.
2. Do not write a wrapper script that exits with code `1` on dependency install, compilation, or environment errors. Setup/build failures **must** exit `>= 2` (`gate-error`); exit `1` is reserved strictly for test assertion failures (`red`).
3. Do not leave secrets, credentials, `BASE`, `EBT_*`, or git isolation variables in `.acceptbox.toml`'s `env`.
4. Do not claim the setup works until you have verified **both** a passing run (`green`, exit `0`) and a failing run on an injected defect (`red`, exit `1`) in a clean worktree — without polluting the user's branches.
5. Do not edit `.git/config` or move `origin` / `main` to make checks pass.
6. If connecting `--mode witnessbox`, do not create or modify `ebt/spec.toml` without human approval (follow WitnessBox's [agent instructions](https://github.com/kaufd/witnessbox/blob/main/docs/agent.md) first).

## Stop points

Stop and ask the human instead of guessing when:

- **multiple test suites exist** (e.g. unit vs integration vs e2e) and it is unclear which one should gate bugfixes;
- **a clean worktree needs network access, private registries, or credentials** to install dependencies;
- **the draft `.acceptbox.toml` and wrapper script are ready** — mandatory stop (Stage 2);
- **the existing test suite is already red on `HEAD`** — report it; do not hide failing tests or weaken assertions;
- **`acceptbox calibrate` or the trial run fails with exit code `2` (`gate-error`)** and the cause is not fixed by adjusting the wrapper/config.

## Stage 0. Environment

```sh
acceptbox --installation || uv tool install acceptbox==0.2.0
acceptbox --installation
```

Requirements: Python `>=3.11`, `git`. `acceptbox --installation` must return exit code `0` (`git.status == "ok"`). Only if the project also uses `--mode witnessbox`, verify `witnessbox.status == "compatible"` (`>=0.10.0`).

Check whether the checkout has an `origin` remote:

```sh
git remote get-url origin
```

Record the result: if `origin` exists, `--base-ref main` will verify ancestry against `origin` (`ref_source_kind: "origin"`); if not, note that local checks will report `ref_source_kind: "local"` (weak check).

## Stage 1. Project reconnaissance (read-only)

Without editing files, inspect the repository to determine:

1. **Default branch name** (`main` vs `master`, etc.).
2. **Stack and test runner** (`pyproject.toml` / `unittest` / `pytest`, `package.json` / `vitest` / `jest`, `pubspec.yaml` / `flutter test`, `go.mod` / `go test`, `Cargo.toml` / `cargo test`, `build.gradle` / `pom.xml`, etc.).
3. **Test paths** for `[command].tests` (e.g. `["tests/"]`, `["src/**/*.test.ts", "tests/"]`, `["test/"]`).
4. **Clean-worktree dependency needs:** if you check out `HEAD` into a fresh `git worktree` with no `node_modules` / `.venv` / `build`, does the test command run directly or does it need a wrapper script?
5. **Exit code behaviour on build/compile errors vs test failures:** verify whether the runner exits `1` on compile errors (`go test`, `mvn test`) or uses a non-`1` failure code (`cargo test` exits `101`). Use the stack recipes in [setup.md](setup.md) to handle these cleanly.
6. **Files to protect:** manifest/config files, lockfiles, test runner configs, wrapper scripts, and `.acceptbox/AGENTS.md`.
7. **WitnessBox presence:** check whether an approved `ebt/spec.toml` directory exists; if so, also add a `[witnessbox]` section (`ebt_ref = "<base-branch>"`).

## Stage 2. Draft config and stop for approval

Prepare (without committing to the base branch yet):

1. `.acceptbox.toml` in the repository root (see recipes in [setup.md](setup.md)).
2. `scripts/acceptbox-test.sh` (only if clean worktrees need setup or exit-code normalization), `chmod +x`, and listed in `protect`.
3. `.acceptbox/AGENTS.md` from the ongoing agent rules template in [setup.md](setup.md#ongoing-agent-rules-template-acceptboxagentsmd), adapted with the repository's base branch name.

**STOP here.** Show the human:
- the proposed `.acceptbox.toml`;
- the wrapper script (if any) and how it guarantees exit `>= 2` on setup/build errors and `1` only on test failures;
- the list of `protect` paths;
- where ongoing agent rules will be linked (`AGENTS.md`, `CLAUDE.md`, or other tool-specific rule files).

Wait for explicit approval before committing.

## Stage 3. Wire up ongoing agent rules and commit

After human approval:

1. Link `.acceptbox/AGENTS.md` from the repository's root agent instruction files so every future coding agent reads the rules:
   - In root `AGENTS.md` (create if neither `AGENTS.md` nor `CLAUDE.md` exists): add `AcceptBox rules for this project: .acceptbox/AGENTS.md`.
   - In root `CLAUDE.md` (if it exists and does not already load `AGENTS.md` via `@AGENTS.md`): add `@.acceptbox/AGENTS.md`.
   - If the project uses Cursor rules or similar, add the pointer there as well.
2. Commit `.acceptbox.toml`, any wrapper script, `.acceptbox/AGENTS.md`, and the root pointer(s) on the base branch (or on a dedicated setup PR branch if the base branch is protected).

## Stage 4. Sensitivity and clean-worktree verification

A config that has never been tested in a fresh detached worktree is unverified. Test **both** green and red without touching the human's branch:

1. **Green check with `calibrate`:**
   ```sh
   BASE=$(git rev-parse HEAD)
   acceptbox calibrate --base "$BASE" --green "$BASE"
   ```
   Expected: exit code `0` (`CALIBRATED`). If you get exit `2` (`gate-error`), inspect the run log printed in `calibrate.json`, fix the wrapper/`env` (with human approval), and re-run.

2. **Full `base → repro → head` trial in a throwaway detached worktree:**
   ```sh
   BASE=$(git rev-parse HEAD)
   git worktree add --detach ../acceptbox-trial "$BASE"
   # In ../acceptbox-trial:
   # 1) Add a tiny failing test inside [command].tests and commit -> REPRO
   # 2) Fix the test/code? No: for a true trial, either test a tiny dummy bug or use
   #    `calibrate --base "$BASE" --green "$BASE" --red "$TEMP_DEFECT"` on a temporary defect commit.
   ```
   The cleanest non-invasive check is a temporary defect commit in the detached worktree:
   ```sh
   # Inside ../acceptbox-trial (detached HEAD):
   # Introduce a deliberate bug (break an existing assertion or return wrong value) and commit:
   git -C ../acceptbox-trial commit -am "temp: defect for acceptbox calibration"
   DEFECT=$(git -C ../acceptbox-trial rev-parse HEAD)
   acceptbox calibrate --repo ../acceptbox-trial --base "$BASE" --green "$BASE" --red "$DEFECT"
   echo "exit=$?"
   git worktree remove --force ../acceptbox-trial
   ```
   Expected: exit code `0` (`CALIBRATED`), with `$BASE` → `green` (exit `0`) and `$DEFECT` → `red` (exit `1`).
   - If `$DEFECT` produces `error` (`gate-error`), your test runner exited with something other than `1` on test failure — fix exit-code mapping in the wrapper.
   - If `$DEFECT` produces `green`, your test command is not actually running tests.

## Stage 5. Report to the human

```
AcceptBox version: <from acceptbox --installation>
Stack & test command: <stack>, <argv>
Config committed: <commit sha> (.acceptbox.toml, <wrapper if any>)
Protected paths: <list>
Agent rules: .acceptbox/AGENTS.md, linked from <AGENTS.md / CLAUDE.md>
Trusted ref source: <origin URL or "no origin (local fallback)">
Calibration: green=<BASE> (code 0), red=<temporary defect> (code 1) -> CALIBRATED
Not covered / limits:
- command mode verifies commit structure, test immutability, and exit codes, not semantic test quality;
- local runs rely on .git/config origin; run AcceptBox in CI (or pass --repo URL) for full trust;
- applies to bugfixes (and human-approved WitnessBox invariants), not unapproved new features or refactors.
```

---

# Part II: Ongoing work in a repository with AcceptBox

Follow this playbook whenever you fix a bug in a repository that already has `.acceptbox.toml`.

## 1. Check scope before writing code

- **Bugfix?** Yes → use the `base → repro → head` workflow below (`--policy bugfix`, default).
- **Refactoring (no behaviour change)?** Yes → use `--policy refactor` (`base` green → `head` green without a `repro` commit; do not modify tests or `ebt/`).
- **Task with human-approved WitnessBox invariants (`--mode witnessbox`)?** Yes → follow the `ebt/` rules too; never edit `ebt/` or `tool/ebt_check`.
- **New feature without approved EBT spec?** Do **not** fake a `repro` commit; follow the repository's normal feature workflow and note in the PR why `base → repro → head` was not used.

## 2. Inspect `.acceptbox.toml` first (read-only)

Read `.acceptbox.toml` on the base branch so you know:
- `[command].tests` — the exact pathspecs where `repro` is allowed to touch files;
- `protect` — files you must not touch in `base..repro` or `base..head`;
- `[command].argv` — the exact test command that will run in clean worktrees.

## 3. Build the clean `base → repro → head` chain

1. **Start from a commit on the base branch (`base`):**
   ```sh
   BASE=$(git merge-base main HEAD)
   ```
2. **Commit 1 — `repro` (test only):**
   - Add or extend a test **strictly inside `[command].tests`** that reproduces the reported bug.
   - Do **not** edit application code, `.acceptbox.toml`, or any `protect` path.
   - Run the test command and confirm it **fails because of the bug** with exit code `1`.
   - Commit:
     ```sh
     git commit -m "test: reproduce <bug>"
     REPRO=$(git rev-parse HEAD)
     ```
3. **Commit 2 — `head` (fix only):**
   - Implement the fix in application code.
   - **Never modify, rename, or delete any file touched in `repro`.** (If you later realize the repro test itself had a mistake, stop and cleanly rewrite your branch history *before* acceptance so `repro` has the right test and `head` does not touch it.)
   - Never edit `.acceptbox.toml` or any path in `protect` (nor `ebt/` / `tool/ebt_check` in `witnessbox` mode).
   - Run the test command and confirm it **passes** with exit code `0`.
   - Commit:
     ```sh
     git commit -m "fix: <description>"
     ```
   - Multiple fix commits after `repro` are allowed as long as `base → repro → head` ancestry holds and the cumulative diff `repro..head` does not touch any file changed in `base..repro` or any protected path.

## 4. Run local self-check

Remember: AcceptBox tests committed Git objects only — uncommitted working-tree changes are ignored.

`command` mode:

```sh
acceptbox accept --base-ref main
```

*(For refactorings: `acceptbox accept --policy refactor --base-ref main`. If your branch has helper commits before the repro commit, add `AcceptBox-Role: repro` as a trailer in the repro commit message, or pass `--repro "$REPRO"` explicitly.)*

`witnessbox` mode (either with `--pin-from-ref` locally, or explicit `--ebt-sha` / `--approved-tree`; `--inv` is optional and auto-inferred from `base` → `repro` when omitted):

```sh
acceptbox accept --mode witnessbox --base-ref main --pin-from-ref
```

## 5. Diagnosing results (`accept.json`)

| Result / `reason_code` | Cause | What to do |
|---|---|---|
| `ACCEPTED` (code `0`) | All checks passed | Record `base`, `repro`, `head`, and `ref_source_kind` in the PR |
| `base-not-on-ref` (code `1`) | `--base` is not an ancestor of `--base-ref` from `origin` (or ref missing) | Fetch `origin`, rebase onto a commit in `origin/main`, and recompute `--base` |
| `repro-not-descendant` / `repro-not-ancestor` (code `1`) | Broken commit ancestry | Verify `base → repro → head` commit order (`git log --oneline base..head`) |
| `protected-paths-changed` (code `1`) | Edited `.acceptbox.toml`, a `protect` path, or `ebt/` | Revert protected file edits; if config/test infra genuinely needs a change, stop and ask the human |
| `repro-empty` (code `1`) | `base..repro` has no file changes | Put the reproduction test in `repro` |
| `repro-outside-tests` (code `1`) | `repro` changed files outside `[command].tests` | Move non-test edits out of `repro` (into `head`) |
| `repro-tests-modified` (code `1`) | `head` modified, renamed, or deleted a file from `repro` | Do not edit repro test files in `head`; fix the repro commit properly before fixing the bug |
| `repro-green` (code `1`) | Tests passed on `repro` | Your repro test did not reproduce the bug; write a failing test that actually triggers it |
| `head-not-green` (code `1`) | Tests did not pass on `head` | Read `runs/head/...` log referenced in `accept.json` and finish the fix |
| `base-green` / `base-red-wrong-reason` (code `1`, `witnessbox`) | `base` did not fail on target `--inv` | Stop and report to the human — the bug may not violate the specified invariant |
| `inputs-untrusted` (code `1`, `witnessbox`) | `ebt/` SHA or tree hash mismatch | Verify `--ebt-sha`, `--ebt-ref`, and `--approved-tree` with the human |
| `config-invalid` (code `2`) | `.acceptbox.toml` in `base` failed schema validation | Stop and report to the human (do not edit `.acceptbox.toml` in the bugfix branch) |
| `gate-error` (code `2`) | Test command exited `>= 2`, timed out, or git/env failed | Read `message` and the run log in `accept.json`; check clean-worktree dependencies |

## 6. Pull request report template

Include this section in your PR description:

```markdown
## Acceptance

- Mode: `command` (or `witnessbox`, invariants: `...`)
- Commits: `base=<sha>`, `repro=<sha>`, `head=<sha>`
- Local self-check: `ACCEPTED` (exit `0`), `ref_source_kind: <origin|remote|local>`
- Not verified: <edge cases outside the repro test, manual/UI checks, or "awaiting CI acceptance">
```

Keep the original `repro` and `head` commits intact until the acceptor finishes acceptance (do not squash before acceptance, L13).
