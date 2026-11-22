# AcceptBox Decision Log

**English** · [Русский](decisions.ru.md)

Append-only: when something changes, add a new entry with a date, reason, and status; earlier entries are never rewritten.
Decisions `L8`–`L14` and `L17`–`L21` were migrated from the original `qa` project decision log (`docs/product/sources/decisions.md`).
External references below: `L1`–`L7` and `L15`–`L16` live in the original `qa/docs/product/sources/decisions.md`; `D4`, `D63`, `D65`–`D69` live in the [WitnessBox](https://github.com/kaufd/witnessbox) decision log.

## L8 — Acceptance ships as a standalone product `acceptbox`
2026-11-12. Accepted by owner. Product and command name: `acceptbox`, package directory `src/acceptbox/`, standalone distribution, CLI version, and `accept.json` report format (format 1). Reason: goals Ц1/З12 were declared without a step or acceptance criterion; `scripts/accept.py` is not a product. Consequence: stage 3 rewritten, criteria 5.1/5.5 updated (removing the core, not arbitrary packaging).

## L9 — Verdict rule: failing `base` is mandatory
2026-11-12. Accepted by owner (default). Accepted only if simultaneously: trusted inputs are verified; ancestry `base → repro → head` holds; `base` is red for the right reason on at least one `--inv`; `repro` is red for the right reason; `head` is green. Reason: the earlier pair ("repro red + head green + ancestry") passes a fabricated bug (break in `repro`, fix in `head`). "Red for the right reason" is determined from `findings[].kind` in `report.json`, not only `checker_failed`; the category list is pinned against a live installation (step 1.3). Replaces criterion 6.2 of the earlier revision.

## L10 — Approving `ebt/`: owner's commit on the product's main branch
2026-11-12. Accepted by owner (default). No separate approval file is introduced. QA checks out `ebt/` from a clean checkout of the pinned SHA on the main branch, verifies that the SHA is an ancestor of the main branch, and compares the `ebt/` tree hash against the approved value passed as input. The value is produced by `acceptbox pin` and recorded by the owner upon approval (PR/task plan).

## L11 — v1 scope: EBT only, with explicit uncovered reporting
2026-11-12. Accepted by owner (default). The report records checked invariants, skipped checks, and unchecked requirements. Reviews and UI checks have separate status and do not affect the v1 verdict. A task without invariants produces an explicit `no-invariants` rejection rather than silence. Merging scopes is a separate decision.

## L12 — Supported WitnessBox: `>=0.9.0,<0.10`
2026-11-12. Accepted by owner (default). A missing command or out-of-range version returns exit code 2 with a hint, not "rejected". (Updated by decision L21.)

## L13 — Delivery after acceptance
2026-11-12. Accepted by owner (default). Acceptance runs before squashing; original commits are preserved. If delivery modifies the verified tree, re-acceptance is mandatory. The report links verified SHAs to the delivered commit.

## L14 — Packaging: build dependency only, stdlib-only runtime
2026-11-12. Accepted by owner (default). `pyproject.toml` with `hatchling` is used only at build time; the `acceptbox` runtime has no third-party dependencies. The "no new runtime dependencies" rule is upheld.

## L17 — Run isolation and gate environment
2026-11-12. Accepted by owner (default). QA operates in a temporary clone with separate detached worktrees for `base`/`repro`/`head`; the user's clone is never switched. QA constructs the gate environment (`BASE`, `EBT_INV`, plus a minimal allowlist); inherited `EBT_*`, including `EBT_CHECK` and `EBT_TESTS`, are stripped. `--out` must be empty; `accept.json` is written atomically at the end; an interrupted run never leaves an "accepted" result.

## L18 — Sensitivity calibration separated from acceptance
2026-11-12. Accepted by owner (default). Mutant `893bc0b5` is a descendant of accepted `58e99b2d`; such a pair triggers `repro-not-ancestor`. Calibration runs via a separate `acceptbox calibrate` subcommand and never issues an acceptance verdict. Sequence checks run on a real `base`/`repro`/`head` triple. Step 3.4 of the earlier revision is corrected.

## L19 — Verdict rule for `command` mode (without `witnessbox`) and `acceptbox accept` 0.1.0 contract
2026-11-20. Proposed in the standalone v0.1.0 plan (requires owner confirmation). Rule L9 (failing `base` checked via `findings[].kind` on `--inv`) applies only in `--mode witnessbox`. In `--mode command`, the new reproduction test is added in `repro` (it does not exist on `base`), so core rules apply:
- `--mode <command|witnessbox>` and `--head <sha>` (full Git SHA) are required in `0.1.0` with no defaults;
- in `--mode command`, `--tests <pathspec>` (repeatable) is required; the command is passed as `argv` after `--` without `shell=True`; timeout is set via `--timeout` (default `600` s); preparing dependencies in a clean worktree is the responsibility of the passed command/script (on setup failure it must return exit code `>= 2`);
- before runs, check ancestry `base → repro → head` (`repro-not-descendant`, `repro-not-ancestor`), `--protect` across `base..repro` and `base..head` (`protected-paths-changed`), non-empty `base..repro` (`repro-empty`), all `base..repro` paths inside `--tests` (`repro-outside-tests`), and immutability of those paths in `repro..head` with `--no-renames` (`repro-tests-modified`);
- `base` is not executed; `repro` (exit `1` → `red`) and `head` (exit `0` → `green`) are executed; exit codes `>= 2`, signals, `OSError`, and timeouts → `error` (`gate-error`, exit code `2`).
Guarantee boundary: `command` restricts which paths `repro` may touch and prevents `head` from modifying or deleting reproduction tests, while semantic correctness of the test is verified by a human during review; independent invariant verification is provided by `witnessbox` mode.

## L20 — Extracting `acceptbox` without stages 1–2 and optional `witnessbox` in `--installation`
2026-11-20. Proposed in the standalone v0.1.0 plan (requires owner confirmation). Supplements L8, L12, L16:
- Extracting `acceptbox` into a separate repository is done as a clean start based on `packaging/acceptbox/` (`packaging/` is untracked in `qa`) and is decoupled from stages 1–2 of `2026-11-12-acceptbox-product.md`.
- `witnessbox` (`>=0.10.0`, L21) is optional and needed only in `--mode witnessbox`.
- `acceptbox --installation` contract changes: exit code `0` whenever `git` and `acceptbox` are available regardless of `witnessbox` status; exit code `2` only if `git` is missing/broken or `acceptbox` diagnostics fail. JSON includes `format: 1`, `report_format: 1`, `git` (`ok | missing | error`), and `witnessbox` (`compatible | missing | incompatible | error`). No `--require-witnessbox` flag is added; `packaging/agentflowbox/{cli.cjs,package.json,README.md}` are updated for `0.1.0` and optional `witnessbox`.

## L21 — Supported WitnessBox updated to `>=0.10.0` (no upper bound) and API drift monitoring
2026-11-20. Confirmed by owner (updates L12 and L20 regarding version and canonical slug). On PyPI and GitHub (canonical slug `kaufd/witnessbox`, D63), the current WitnessBox release is `0.10.0` (`v0.10.0`, 2026-11-16, D65–D69). The minimum supported version for `acceptbox 0.1.0` is set to `>=0.10.0` without an upper bound (`(major, minor, patch) >= (0, 10, 0)` in `cli.py` and `witnessbox.py`, dropping `0.9.x`). Because future `witnessbox` versions without an upper bound may change CLI or JSON contracts, two protection and monitoring layers are introduced:
- **Fail-closed runtime schema validation:** strict validation of required keys and types in `witnessbox --installation`, `gate.json` (including `format == 1`), and `report.json`; on format or structure changes, `acceptbox` terminates with an explicit `gate-error` (`error`, exit code `2`) or `invalid-report`, preventing false `ACCEPTED` results and unhandled exceptions.
- **Periodic CI monitoring:** a weekly CI job (`schedule` + `workflow_dispatch`) installs the latest `witnessbox` release from PyPI and runs the `--mode witnessbox` contract smoke/e2e test, detecting API breakage immediately after a new release.
Revisit if: a new `witnessbox` version breaks backwards compatibility of format-`1` `gate.json`, `report.json`, or `--installation` — then update the adapter and/or pin an upper bound.

## L22 — Ergonomic `acceptbox` CLI defaults (supplements L19, L20)
2026-11-21. Accepted by owner (planned for implementation before releasing `v0.1.0`). To reduce manual typing without weakening fail-closed guarantees:
- `--mode` becomes optional with default `command` (`--mode witnessbox` is specified explicitly; cross-mode flag checks remain, no false `ACCEPTED` is possible);
- `--repo` becomes optional with default `.`;
- `--head` becomes optional with default `HEAD` from `--repo`, resolved immediately to a full commit SHA on startup (explicitly passed `--head` is still accepted only as a full SHA);
- `--ebt-repo` in `accept` and `calibrate` for `--mode witnessbox` becomes optional with default `--repo` (per L5, `ebt/` lives in the product repository; an external path remains optional; in `acceptbox pin`, `--ebt-repo` stays required);
- `--out` becomes optional: when omitted, a new empty directory is atomically created in system `TMPDIR` (avoiding working-tree pollution and preserving L17), and the path to `accept.json`/`calibrate.json` is printed in the summary; explicit `--out` retains strict `out-not-empty` + `O_EXCL` enforcement;
- autodetection of the test command after `--` is not introduced (violates fail-closed; repeatable settings move to the project config per L23).

## L23 — Project configuration (`.acceptbox.toml`)
2026-11-21. Accepted by owner (schema design required before implementation). To avoid repeating project parameters (`--tests`, `--protect`, the test command after `--`, mode settings), introduce a project config (candidate: `.acceptbox.toml`).
Mandatory trust rule (fail-closed):
- the config is read strictly from the base/approved commit (`base`, never from the checked branch `repro`/`head` or the working tree);
- the config file is automatically protected against edits in `base..repro` and `base..head` (like `--protect`), preventing an agent from swapping the test command, weakening `--tests`, or removing protected paths.

## L24 — TDD (`base → repro → head`) applicability scope: bugfixes only (clarifies L19)
2026-11-21. Accepted by owner. The `base → repro → head` chain with a failing reproduction commit is intended for **bugfixes** (as well as invariants approved by a human prior to implementation in `--mode witnessbox`). For **features**, a failing repro test written by the same model does not provide a useful guarantee because the model can fit test and implementation to each other — feature acceptance rests on a human-approved specification (D4/L10) and/or independent review and behavioural verification. A policy for **refactorings** (`base` green → `head` green with protected paths and tests without `repro`) is orthogonal to `--mode` and remains a separate task.

## L25 — `.acceptbox.toml` schema (clarifies L23)
2026-11-21. Accepted by owner and implemented alongside L22–L24. Approved format-`1` sectioned schema for `.acceptbox.toml` (using stdlib `tomllib`, Python `>=3.11`):
- top level: `format = 1` (required integer), optional `mode` (`"command"` | `"witnessbox"`), `timeout` (finite positive number), `protect` (list of pathspecs), `env` (list of `KEY` / `KEY=VAL`, forbidding `BASE`, `EBT_*`, and git isolation variables);
- `[command]`: optional `tests` (non-empty list of pathspecs) and `argv` (non-empty list of strings, no shell);
- `[witnessbox]`: optional `ebt_repo` and `ebt_ref`.
The sectioned layout allows storing both modes' settings in one project without flag-mixing conflicts. Every present section is validated fail-closed: unknown keys, per-task fields (`inv`, `ebt_sha`, `approved_tree`, `base`, `repro`, `head`, `repo`, `out`), symlinks/directories instead of regular files, and TOML errors return `ERROR` (`config-invalid`, exit code `2`).
Precedence and trust rules:
- read strictly from Git objects of commit `base` (in `calibrate`, only when `--base` is passed), never from the working tree or `repro`/`head`;
- `.acceptbox.toml` is automatically included in protected paths for `base..repro` and `base..head`;
- `protect` is unioned (config + `--protect`), `env` merges with CLI precedence by key; `mode`, `timeout`, `tests`, `argv`, `ebt_repo`, and `ebt_ref` are overridden by explicit CLI flags;
- `inputs.config` in `accept.json`/`calibrate.json` records `path`, `commit` (`base`), and `sha256` of the loaded config (or `null`).

## L26 — Confirmation of L19–L20, `--base` trust boundary, stage ordering, and report writing on CLI errors after config load
2026-11-21. Accepted by owner (supplements L19, L20, L22, L23, L25).
- Decisions **L19** and **L20** are confirmed by owner (implementation merged to `main`, PR #1). The L19 requirement for mandatory `--mode` and `--head` without defaults is superseded by **L22**.
- **`--base` trust boundary (for `.acceptbox.toml` and `command` mode):** `acceptbox` does not verify that `--base` belongs to the main branch; the immutability guarantee of `.acceptbox.toml` across `base..repro` and `base..head` holds provided `--base` is supplied by the acceptor (human, CI, or external orchestrator, e.g. via `git merge-base main HEAD`) rather than the agent under test. An optional `--base-ref` flag for automatic ancestry checking modeled on L10 is deferred past `0.1.0`.
- **Stage ordering with `.acceptbox.toml`:** temporary clone of `--repo`, fetching `base`, and reading `base:.acceptbox.toml` run before `witnessbox --installation` and the empty `--inv` check, since `mode` and `env` may come from the project config.
- **Report-writing rule on exit code `2`:** if required arguments (`--tests`, command after `--`, `--inv`, `--ebt-ref`, `--ebt-sha`, `--approved-tree`) are missing or mode flags are mixed after loading `base:.acceptbox.toml` and merging CLI flags, this is classified as a CLI input error: exit code `2` is returned to `stderr` **without** writing `accept.json`/`calibrate.json`, and an empty `--out` directory created by the current run is removed. On an invalid `.acceptbox.toml` in `base` (`config-invalid`), a report with `result: "ERROR"` and exit code `2` is written normally.

## L27 — Code review fixes for L22–L26 (clarifies L22, L25, L26)
2026-11-22. Proposed from code review (requires owner confirmation). Behavioural changes:
- `[witnessbox].ebt_repo` in `.acceptbox.toml` accepts only a URL or an absolute path: a relative path would depend on the caller's cwd rather than `--repo` (`config-invalid`). On `config-invalid`, `inputs.config` records the `commit` and `sha256` of the rejected file.
- Omitted `--head` is resolved right after the `git` check; resolution failure yields `gate-error` with `accept.json`, not a CLI error. Leading `~` in `--repo`/`--ebt-repo`/`ebt_repo` is expanded once during validation.
- Environment errors (`OSError`) after acquiring `--out` are written to the report as `gate-error`; temporary directory cleanup failures do not change the already-written verdict or exit code.
- `command` and `witnessbox gate` runs execute in a separate process session; on timeout and after completion, the entire process group is killed.
- Short `--ebt-ref` names resolve explicitly: `refs/heads/<ref>` first, then `refs/tags/<ref>` (a same-named tag cannot shadow a branch).
- `--env KEY` without a value when `KEY` is absent from host env removes `KEY` from config (CLI precedence by key).
- `calibrate` no longer accepts `--tests`: no calibration check used it.
- `report.json` findings with non-string `kind`/`inv` are ignored as non-matching (previously an unhandled exception exited `1` with no report).
Documented limitation without code change: when `--ebt-repo` is a local checkout (L22 default), `--ebt-ref` ancestry is checked against the local branch; `--approved-tree` remains the trust anchor. Revisit if: forbidding local `--ebt-repo` by default or verifying refs against a remote repository is needed.

## L28 — Confirmation of L27
2026-11-22. Accepted by owner. Decision **L27** is confirmed as written on 2026-11-22 without changes: `[witnessbox].ebt_repo` in `.acceptbox.toml` must be a URL or absolute path; `calibrate` does not accept `--tests`; with a local `--ebt-repo` (L22 default), the weak `--ebt-ref` ancestry check remains a documented limitation with `--approved-tree` as the trust anchor. All other items of L27 (resolving `--head` after `git` check, recording `OSError` in the report, killing the process group, `--ebt-ref` resolution order, `--env KEY` precedence, ignoring non-string `kind`/`inv`, fingerprinting rejected configs) take effect as described.

## L29 — Trusted refs: `--base-ref` and `--ebt-ref` from `origin` (clarifies L10, L22, L26, L27)
2026-11-22. Accepted by owner. Optional `--base-ref <ref>` flag (CLI-only, disallowed in `.acceptbox.toml`) verifies before reading the config that `--base` is an ancestor of the ref; failure returns `base-not-on-ref` (exit code `1`). The ref for `--base-ref` and `--ebt-ref` is taken from a trusted source: a URL as-is, a local path via its remote `origin`; without `origin`, from the local repository itself with `ref_source_kind: "local"` recorded in the report (weak check, as in L27). The `origin` URL is validated (no leading `-`, no control characters) and passed to `git fetch` after `--`; any failure reading `origin` other than "no such remote" yields `gate-error`. Trust boundary: `.git/config` of the local checkout — an agent with write access to it can repoint or delete `origin`; the ref source is recorded in the report (`ref_source`, `ref_source_kind`), and full assurance requires running outside the agent's reach (CI) or passing `--repo`/`--ebt-repo` as URLs. The L22 default for `--ebt-repo` is unchanged. Revisit if: disabling the `local` fallback or adding `--base-ref` to `calibrate` is needed.

## L30 — Strict trusted refs (`--strict-refs`), refactoring policy (`--policy refactor`), and local auto-resolution (fulfills L24, extends L22, L29)
2026-11-22. Accepted by owner.
- **`--strict-refs` (CLI-only on `accept`, rejected in `.acceptbox.toml`):** forbids silent degradation to `ref_source_kind == "local"` for `--base-ref` and `--ebt-ref` → `REJECTED` (`ref-source-local`, exit code `1`) before reading `.acceptbox.toml` or checking out `ebt/`. In `command` mode, `--strict-refs` requires `--base-ref`.
- **`--policy {bugfix,refactor}` (CLI-only on `accept`, default `bugfix`, rejected in `.acceptbox.toml`):** fulfills the L24 refactoring policy orthogonally to `--mode`. In `refactor` policy, `--repro` is forbidden, `base` must be an ancestor of `head` (`head-not-descendant`), `base..head` must be non-empty (`refactor-empty`), `[command].tests` (in `command` mode) or `ebt`/`tool/ebt_check` (in `witnessbox` mode) plus `.acceptbox.toml` and `protect` are protected across `base..head`, and both `base` (`base-not-green`) and `head` (`head-not-green`) must pass (`green`). In `witnessbox` + `refactor`, `--inv` is optional (checks the full spec when omitted; explicit empty `--inv ""` still fails with `no-invariants`).
- **Local auto-resolution for one-line invocations:**
  - `--base` may be omitted when `--base-ref <ref>` is supplied: AcceptBox computes `merge-base <trusted ref> <head>` before loading `.acceptbox.toml` (honoring `--strict-refs`). Omitting both `--base` and `--base-ref` remains a CLI error (exit `2`).
  - `--repro` may be omitted in `bugfix` policy: AcceptBox picks the unique commit in `base..head` carrying trailer `AcceptBox-Role: repro` (multiple → `repro-ambiguous`, exit `1`), or falls back to the first first-parent commit in `base..head` (recorded in `inputs.repro_source`).
  - `--pin-from-ref` (CLI-only, `witnessbox` mode, mutually exclusive with `--ebt-sha`/`--approved-tree`): takes `--ebt-sha` from the tip of trusted `--ebt-ref` and computes `--approved-tree` directly from its Git objects (recorded as `pinned_from_ref: true`).
  - `--inv` becomes optional in `witnessbox` mode (`accept` and `calibrate`): when omitted, AcceptBox runs the full `ebt/spec.toml`, infers the violated invariant(s) on `base`, requires `repro` to fail on at least one of those same invariant(s), and requires `head` to pass (`green`); explicit empty `--inv ""` still returns `no-invariants`.
  - `--config <path>` (CLI-only on `accept` and `calibrate`): loads the format-`1` project config from an external regular file (rejecting symlinks/missing files with `config-invalid`, exit `2`) instead of `base:.acceptbox.toml`, enabling zero-footprint local usage without committing AcceptBox files to the target repository. Demonstrated in `example/`.
Revisit if: reusable GitHub Actions become needed alongside the local workflow.

## L31 — Threat model: non-adversarial agent; verdict meaning in CLI output (clarifies L29, L30)
2026-11-22. Accepted by owner.
- **Threat model:** the agent under test may err, hallucinate, or cut corners, but does not deliberately attack the host (moving local branches, rewriting `.git/config`, editing external config files to cheat). Checks target accidental failure modes; bypasses that need deliberate tampering are out of scope. Full adversarial resistance means running in CI with URL sources and `--strict-refs`. Hence `--pin-from-ref` and `--config` (L30) get no extra anti-tamper restrictions; the L29 trust boundary stays documented.
- **The CLI output carries the verdict's meaning:** a human reads the summary line, not `accept.json`. The line always shows `[policy=…]`; `ACCEPTED` under `refactor` says in its message that there are no regressions against existing checks and behaviour preservation is not verified. Lines `acceptbox: внимание: …` follow for `--pin-from-ref`, `ref_source_kind: "local"` of `--base-ref`/`--ebt-ref`, and an auto-picked `first-commit` repro; the same texts go to `accept.json` → `warnings` (additive key, format stays `1`).
- **`--pin-from-ref`:** `inputs.ebt.approved_tree_sha256` is `null` (pinned is not approved); the hash is in `actual_tree_sha256`.
- **`--policy refactor` in `witnessbox` mode:** on `ACCEPTED`, `coverage.checked` lists every invariant in scope (`--inv`, or all `[inv.*]` of `ebt/spec.toml` when omitted); with `--inv` omitted, a spec without invariants is `inputs-untrusted`.
- **`AcceptBox-Role`** is read only from the commit's trailer block (parsed by `git`); a matching line in the body is ignored.
- **`--config`** is opened with `O_NOFOLLOW | O_NONBLOCK` and checked as a regular file via `fstat`, with no check-then-read race.
- **Auto-resolved `--repro`:** if `base` is not an ancestor of `head`, the reason is `head-not-descendant` (as in `refactor`).
Revisit if: the agent must be treated as adversarial — then `--pin-from-ref` should require a non-`local` ref source and `--config` should be restricted.

## L32 — Human-readable report rendering (`acceptbox render`, extends L31)
2026-11-22. Accepted by owner.
- **Command:** `acceptbox render <report.json> --format md|html [--out <path>]` renders an existing format-`1` `accept.json` or `calibrate.json` report into GitHub-flavored Markdown (suitable for PR comments and CI summaries) or a self-contained HTML5 document (inline CSS only, zero external CDN/fonts/scripts).
- **Pure stdlib-only module (`src/acceptbox/render.py`):** rendering is a pure function of the parsed JSON report dict; it never invokes `git` or any subprocess and never reads external run log files from disk. `load_report()` validates fail-closed that the input is a readable regular file containing a format-`1` JSON object (`accept` via `checks`/`runs`, or `calibrate` via `results`); invalid, missing, or unsupported reports exit with code `2` to `stderr` without creating `--out`.
- **Verdict meaning stays prominent (L31):** the top header/banner always displays `result`, `[policy=…]` (for `accept`), `mode`, `reason_code`, and `message`; non-empty `warnings` are always rendered prominently near the top and never collapsed or hidden.
- **One report model, two serializers:** `render.py` builds one section model from the report; Markdown and HTML only serialize it, so a new report field is added once.
- **Strict escaping:** HTML escapes every dynamic string with `html.escape(..., quote=True)`. In Markdown, free text (message, warnings, run reasons) is kept on one line with GFM specials backslash-escaped (``\ ` * _ [ ] < > & | ~ ! # $ @``), so `src/pkg/__init__.py` does not turn bold, `<module>` is not swallowed as a tag, `$…$` is not math, and `@name` does not notify anyone in a PR comment; identifiers stay in code spans with `|` escaped.
- **Run log links (HTML):** `runs/...` log paths become relative links computed from the page location to the report directory (`--out` elsewhere still links correctly; on `stdout` links assume the page is saved next to the report). Links are never read, so rendering stays pure; absolute paths, `..`, schemes, and control characters are not linked.
- **Atomic, non-destructive output:** with `--out <path>` the file is written atomically via a temporary file in the same directory + `fsync` + `os.replace`; `--out` pointing at the source report (`os.path.samefile`), a missing parent directory, or extra `--` arguments fail closed with exit code `2`. Without `--out`, output goes to `stdout`.
- **Labels in English:** section and table labels are English (they mirror report keys); report content — `message`, `warnings`, reasons — is rendered verbatim in its original language.
- **No schema or runner changes:** `acceptbox accept` and `acceptbox calibrate` behavior and format-`1` report schemas remain unchanged.
Revisit if: rendering needs to embed truncated run log excerpts or support a future format-`2` report schema.

## L33 — English CLI/report output and rendered report layout polish (extends L31, L32)
2026-11-22. Accepted by owner.
- **English CLI and report output:** all user-facing CLI messages, `--help` descriptions, error messages, summary lines (`acceptbox: … (report: …)`), warning lines (`acceptbox: warning: …`), and report text fields (`message`, `warnings`, `runs.*.reason`, `checks.*.reason`) in `src/acceptbox/` are written in English. Format-`1` JSON structure, keys, and `reason_code` values are unchanged.
- **Rendered report layout (`acceptbox render`):**
  - `Checks`, `Runs`, and `Coverage` (and `Results` for `calibrate`) are placed directly below the top verdict banner and `Warnings`, ahead of `Repository & Commits` and `Inputs & Configuration`.
  - `Repository & Commits` and `Inputs & Configuration` are rendered collapsed by default (`<details>` with click-to-expand, zero JavaScript in HTML; `<details>` blocks in GFM Markdown). `Warnings` remain always visible and never collapsed (L31).
  - First-column table cells and badges in HTML use `white-space: nowrap` and `word-break: normal` with a minimum column width so short identifiers (such as `protected_paths` and `repro`) do not wrap mid-word.
Revisit if: localized CLI output (`gettext` / `--lang`) is ever requested.

## L34 — Feature policy (`--policy feature`) and orchestrated feature verification (extends L24, L30, L31)
2026-11-22. Accepted by owner.
- **`--policy feature` (CLI-only on `accept`, rejected in `.acceptbox.toml`):** fulfills the L24 feature policy orthogonally to `--mode`. `--repro` is forbidden (exit code `2`, `--repro is not used in feature policy`); `base` must be an ancestor of `head` (`head-not-descendant`); `base..head` must be non-empty (`feature-empty`). Protected paths across `base..head`: `.acceptbox.toml`, `protect`, and in `witnessbox` mode `ebt`/`tool/ebt_check`; in `command` mode `[command].tests` are **not** protected as a whole.
- **Stage 9 checks in `command` mode**, in this order: `feature-empty` → `existing-tests-modified` → `feature-no-tests`.
  - `existing-tests-modified`: any change in `[command].tests` in `base..head` other than a pure addition (`--diff-filter=a`) — edit, delete, typechange; with `--no-renames` a rename splits into delete + add, so renames also trigger it. Editing a shared helper/fixture under `[command].tests` also triggers it (intentional strictness).
  - `feature-no-tests`: no newly added files under `[command].tests` (`--diff-filter=A`).
  - In `witnessbox` mode only `feature-empty` applies at this stage.
- **Runs:** only `head` is run and must be `green` (`head-not-green`); `base` and `repro` are skipped (`not run in feature policy`). In `witnessbox` mode, `coverage.checked` lists every invariant in scope (`--inv`, or all `[inv.*]` of `ebt/spec.toml`).
- **New reason codes (exit code `1`):** `feature-empty` (`diff base..head is empty: feature changes no files`), `existing-tests-modified` (`feature modifies existing test files: <paths>`), `feature-no-tests` (`feature adds no new test files under --tests`).
- **`ACCEPTED` message:** `head is green and existing checks are unchanged; feature correctness against the intended behaviour is not verified (policy=feature)`. Per L31 the CLI line always carries the caveat: `acceptbox: ACCEPTED [policy=feature]: head is green and existing checks are unchanged; feature correctness against the intended behaviour is not verified (policy=feature) (report: ...)`.
- **Why only `head` is run:** the owner requires no extra runs, and running `base` proves nothing for a new feature — a green `base` is expected, and a red `base` says nothing about the new behaviour.
- **Why new test files are required while existing tests cannot be modified:** under L31 the agent under test cuts corners and the easiest shortcut is editing an existing test to fit the implementation. Requiring at least one new test file keeps the new behaviour covered, and forbidding edits to existing tests — including shared helpers/fixtures under `[command].tests` — makes a shared-helper change an intentional stop point for a human.
- **UI verification is an agent procedure, not AcceptBox code:** it lives in `docs/agent.md`, section 7 (feature verification for the orchestrator or verifier agent). Its outcome is the verifier agent's judgment with screenshots, not a deterministic check.
Revisit if: approved UI scenarios need to be stored in the repository as regression checks.
