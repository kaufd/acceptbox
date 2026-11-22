# Implementation Plan: Trusted Refs for `--base-ref` and `--ebt-ref` (0.2.0)

**English** · [Русский](2026-11-22-trusted-refs-0.2.0.ru.md)

> **For the implementing agent:** execute task by task, marking steps with checkboxes (`- [ ]`).

**Goal:** close the trust caveats recorded in L26 and L27. Add an optional `--base-ref` flag that verifies `--base` lies on the main branch. Fetch the ref for `--base-ref` and `--ebt-ref` from the local repository's `origin` remote rather than from the checkout that an agent can rewrite.

**Architecture:** a single helper in `git.py` determines where to fetch a trusted ref: a URL as-is, a local path via its `origin`, and without `origin` the path itself marked `"local"`. The `--base-ref` stage in `accept` runs before reading `.acceptbox.toml` so config from an unverified `base` is never applied. `_prepare_ebt` fetches the ref from the same source.

**Stack:** Python `>=3.11`, standard library only, external `git`, tests with `unittest`.

**Specification:** decisions L10, L22, L26, L27 in `docs/decisions.md`. Owner choices on 2026-11-22:
1. if `--ebt-repo` or `--repo` is a local path, fetch the ref from its `origin` remote;
2. if `origin` is absent, check against the local branch and mark it in the report;
3. `--base-ref` is optional and CLI-only.

## Global constraints

- Runtime uses the standard library only; importing `witnessbox` modules is forbidden (`AGENTS.md` / `CLAUDE.md`).
- All git calls go through `run_git` (`--no-replace-objects`). Fetch receives `-c core.hooksPath=/dev/null`, already handled by `_fetch`.
- `--base-ref` cannot be set in `.acceptbox.toml`: the config is read from the same `base` being verified. Unknown key `base_ref` is already rejected by the L25 schema; no schema change is needed.
- The decision log is append-only.
- Verification: `python3 -m unittest discover -s tests`, zero skips.

## Review checklist

1. **`origin` with a relative URL** (e.g. `../upstream.git`) resolves relative to the local repository directory, not the caller's cwd. Tested in Task 1.
2. **Forged `base` with a weakened `.acceptbox.toml`.** With `--base-ref` and `base` off the main branch, expect `REJECTED` `base-not-on-ref` without reading config: `inputs.config` is `null` and the config command does not execute. Tested in Task 2.
3. **Unreachable `origin`** (network, auth) produces `gate-error` with a report and never hangs: `GIT_TERMINAL_PROMPT=0` is already set in `base_env`. No separate test needed: the `GitError → gate-error` path is already covered.
4. **Local `main` moved by an agent** does not affect the result when `origin` exists. Tested in Tasks 2 and 3.
5. **`--repo` as a URL:** the `--base-ref` ref is fetched from the URL itself, `ref_source_kind = "remote"`. Tested in Task 1 at function level.

---

### Task 1: Trusted ref source in `git.py`

**Files:**
- Modify: `src/acceptbox/git.py`: move the "URL or scp address" check from `config.py` here, add the source helper.
- Modify: `src/acceptbox/config.py`: use `is_remote_source` from `git.py` instead of local `_REMOTE_SOURCE_RE`.
- Test: `tests/test_unit_and_verdict.py`, new class `TestTrustedRefSource`.

**Interfaces:**
- Creates: `is_remote_source(value: str) -> bool`. Same regex as current `config._REMOTE_SOURCE_RE`: `[A-Za-z][A-Za-z0-9+.-]*://|[^/]+:`.
- Creates: `trusted_ref_source(source: str, env: dict[str, str]) -> tuple[str, str]`. Returns `(fetch_source, kind)` where `kind` is `"remote" | "origin" | "local"`.

- [ ] **Step 1: move `is_remote_source` to `git.py`** and replace `_REMOTE_SOURCE_RE.match(x)` in `config.py` with `is_remote_source(x)`.

- [ ] **Step 2: implement `trusted_ref_source`.**
  - `is_remote_source(source)` → `(source, "remote")`.
  - Otherwise run `run_git(Path(source), env, "remote", "get-url", "origin")`.
  - If git reports no such remote → `(source, "local")`.
  - If the `origin` URL is neither remote nor absolute, join it with the absolute path of `source`.
  - Result → `(url, "origin")`.

- [ ] **Step 3: tests.**

```python
class TestTrustedRefSource(unittest.TestCase):
    def test_kinds(self) -> None:
        env = base_env()
        self.assertEqual(trusted_ref_source("https://h/r.git", env), ("https://h/r.git", "remote"))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            self.assertEqual(trusted_ref_source(str(repo), env), (str(repo), "local"))
            _git(repo, "remote", "add", "origin", "../upstream.git")
            self.assertEqual(trusted_ref_source(str(repo), env),
                             (str(Path(tmp) / "upstream.git"), "origin"))
```

Compare normalized paths: on macOS `tmp` may differ from a resolved path due to `/private`. `os.path.normpath` and `os.path.join` without `resolve` suffice if the implementation also avoids `resolve`.

- [ ] **Step 4:** run `python3 -m unittest tests.test_unit_and_verdict tests.test_defaults_and_config`. Expected OK: config schema behaves identically.

---

### Task 2: `--base-ref` flag in `accept`

**Files:**
- Modify: `src/acceptbox/cli.py`: argument in `_add_accept_flags`, stage-1 validation, new stage between `ensure_commit(... base)` and `load_config_from_commit`, report fields.
- Modify: `src/acceptbox/verdict.py`: rejection helper.
- Test: `tests/test_defaults_and_config.py`, new class `TestBaseRef`.

**Interfaces:**
- Uses: `trusted_ref_source` (Task 1), existing `fetch_ref(repo, env, source, ref) -> str | None` and `is_ancestor(repo, env, ancestor, descendant) -> bool`.
- Creates: `verdict.base_not_on_ref(message: str) -> Verdict`, i.e. `rejected("base-not-on-ref", message)`.
- `accept.json` report fields:
  - `inputs.base_ref`: string or `null`;
  - `checks.base_ref`: `{"status": "skipped"}` without flag, otherwise `{"status": "ok" | "failed", "ref": str, "ref_source": str, "ref_source_kind": "remote" | "origin" | "local", "ref_commit": str | null}`.

- [ ] **Step 1: argument and validation.** `--base-ref` (optional, CLI-only). Validate in stage 1 via `validate_ref(args.base_ref, flag="--base-ref")`. Shared across both modes; do not add to mode-mixing checks.

- [ ] **Step 2: verification stage** immediately after `ensure_commit(clone, git_env, repo, base)` succeeds and before `load_config_from_commit`:
  - `source, kind = trusted_ref_source(repo, git_env)`;
  - `ref_commit = fetch_ref(clone, git_env, source, base_ref)`;
  - `None` → `finish(base_not_on_ref(...))`;
  - if not `is_ancestor(clone, git_env, base, ref_commit)` → `finish(base_not_on_ref(...))`;
  - populate `checks["base_ref"]` before every `finish`.

  `GitError` is already converted to `gate-error` by the existing handler.

- [ ] **Step 3: tests.** Helper function in the test module:

```python
def _add_origin(repo: Path) -> Path:
    upstream = repo.parent / "upstream.git"
    _git(repo.parent, "clone", "-q", "--bare", "--no-local", str(repo), str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    return upstream
```

Scenarios, all on `_make_command_fixture` with `.acceptbox.toml` in `base`. Move local `main` via `git update-ref refs/heads/main <sha>` (`git branch -f` fails on the checked-out branch). Pass `--head` explicitly because `HEAD` tracks `main` and moves with it.
- `test_base_on_origin_main_accepted`: `_add_origin`, then `accept --base-ref main ...`. Expected: exit `0`, `checks.base_ref.status == "ok"`, `ref_source_kind == "origin"`.
- `test_forged_base_rejected_before_config`: after `_add_origin`, create commit `forged` off `base` with `.acceptbox.toml` setting `argv = [PYTHON, "-c", "open('MARKER','w')"]`. Build `repro` and `head` on top, then `git update-ref refs/heads/main <forged>`. Expected:

```python
self.assertEqual(res.returncode, 1)
self.assertEqual(data["reason_code"], "base-not-on-ref")
self.assertIsNone(data["inputs"]["config"])
self.assertEqual(data["runs"]["repro"]["status"], "skipped")
```

- `test_without_base_ref_check_skipped`: without flag, `checks.base_ref == {"status": "skipped"}`, `inputs.base_ref is None`.
- `test_no_origin_falls_back_to_local_ref`: without `_add_origin` and with `--base-ref main`, expect exit `0` and `ref_source_kind == "local"`.

- [ ] **Step 4:** run `python3 -m unittest tests.test_defaults_and_config`. Expected OK.

---

### Task 3: `--ebt-ref` from `origin` of local `--ebt-repo`

**Files:**
- Modify: `src/acceptbox/cli.py`: `_prepare_ebt` and `inputs.ebt` in `accept` and `calibrate`.
- Test: `tests/test_defaults_and_config.py`, new method alongside `test_witnessbox_defaults_ebt_repo_head_out`.

**Interfaces:**
- Uses: `trusted_ref_source` (Task 1).
- Updates: `_prepare_ebt(...)`, passing `ref_source` from `trusted_ref_source(ebt_repo, git_env)`. Cloning and `ensure_commit(--ebt-sha)` still read from `ebt_repo`; only `fetch_ref` reads from `ref_source`.
- Report fields: add `ref_source` and `ref_source_kind` to `checks.ebt_trust` (both `ok` and `failed`) and `inputs.ebt`.

- [ ] **Step 1:** in `_prepare_ebt`, replace `fetch_ref(ebt_clone, git_env, ebt_repo, ebt_ref)` with a call using `ref_source` from `trusted_ref_source(ebt_repo, git_env)`. Update both call sites and reports.

- [ ] **Step 2: test** `test_ebt_ref_taken_from_origin_not_local_branch`:
  - take `_make_witnessbox_in_repo_fixture` and run `_add_origin(repo)`;
  - create side commit `side` off `base` changing `ebt/spec.toml` and compute `approved = pin(side)`;
  - run `git update-ref refs/heads/main <side>`, pass `--head` explicitly;
  - run `accept --mode witnessbox --ebt-ref main --ebt-sha <side> --approved-tree <approved>`.

  Expected exit `1`, `reason_code == "inputs-untrusted"`, `checks.ebt_trust.ref_source_kind == "origin"`. Without `origin` the same run would pass ancestry: this also verifies the `"local"` marker in existing tests.

- [ ] **Step 3:** run `python3 -m unittest discover -s tests`. Expected OK; all 80 existing tests pass without fixture edits because repositories without `origin` use the `"local"` fallback.

---

### Task 4: Documentation, decision L29, version 0.2.0

**Files:**
- Modify: `README.md`, `docs/onboarding.md`, `docs/decisions.md`, `pyproject.toml` (`version = "0.2.0"`), `src/acceptbox/__init__.py` (fallback version `"0.2.0"`).

- [ ] **Step 1: README.**
  - Add `[--base-ref main]` to `accept` examples for both modes.
  - Document `origin` trusted ref source and `"local"` fallback in `--base` trust boundary and `--ebt-repo` sections.
  - Add `base-not-on-ref` (exit `1`) to Exit codes.
  - Use absolute GitHub URLs for docs links (for PyPI).
  - Bump install example to `acceptbox==0.2.0`.

- [ ] **Step 2: onboarding.** Recommend `--base-ref main` and remote `origin` in quick start and witnessbox sections; document `ref_source_kind`.

- [ ] **Step 3: append L29 to the decision log.**

- [ ] **Step 4:** run `python3 -m unittest discover -s tests`. Expected OK, zero skips.

---

### Task 5: Delete merged branch `feat/l22-l24`

Execute only after owner confirmation at execution time: deleting a GitHub branch is an external action.

- [ ] **Step 1:** `git branch -d feat/l22-l24`. The branch is merged, so `-d` suffices.
- [ ] **Step 2:** `git push origin --delete feat/l22-l24`.
- [ ] **Step 3:** `git ls-remote --heads origin feat/l22-l24` outputs nothing.

## Out of scope

- `--base-ref` in `calibrate`: calibration does not issue an acceptance verdict on agent work.
- 0.2.0 release (tag, GitHub Release, PyPI): separate step with separate confirmation, following the 0.1.0 release process.
- Refactoring policy (L24) and `--prepare`: after practical usage.
