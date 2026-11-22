# Autonomous CI pipeline (GitHub Actions)

**English** · [Русский](ci.ru.md)

AcceptBox ships two reusable composite GitHub Actions — [`actions/setup`](../actions/setup/action.yml) and [`actions/accept`](../actions/accept/action.yml) — so bugfix and refactoring pull requests can be accepted automatically with minimal or zero human operator involvement.

## Architecture: human approval only on rule changes

To eliminate manual SHA copying and per-PR operator steps without weakening security, split the repository into two trust zones using **Branch Protection + `CODEOWNERS`**:

1. **Human-approved rule paths (`CODEOWNERS` required on `main`):**
   ```text
   /ebt/                       @owner
   /.acceptbox.toml            @owner
   /scripts/acceptbox-test.sh  @owner
   /.github/workflows/         @owner
   ```
   A human reviews and approves pull requests **only** when a new invariant, spec edit, or test-runner change is introduced.
2. **Autonomous execution & acceptance loop (zero operator):**
   - An AI coding agent works in an isolated branch/container with no push rights to `main`, creates `base → repro → head` (for bugfixes) or `base → head` (for refactorings), self-checks locally, and opens a PR.
   - `actions/accept` runs in GitHub Actions (outside the agent's checkout), automatically resolves `policy`, `repro`, and (for `witnessbox` mode) pins `ebt/` from trusted `origin/main`, enforces `--strict-refs`, uploads `accept.json`, posts/updates a sticky PR verdict comment (with failing log tails so an agent can self-correct), and sets the `acceptbox/accept` commit status.
   - When `acceptbox/accept` is `success` and no `CODEOWNERS` paths were modified:
     - in **`witnessbox` mode** (human-approved invariants), the PR can be **auto-merged** safely;
     - in **`command` mode**, either review only the small `base..repro` diff or add an independent LLM check on `base..repro` before auto-merge.

## Template `.github/workflows/acceptbox.yml`

Pin `kaufd/acceptbox/actions/*` to the full commit SHA of the release tag (never `@main`).

```yaml
name: AcceptBox

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]

permissions: {}

concurrency:
  group: acceptbox-${{ github.ref }}
  cancel-in-progress: true

jobs:
  accept:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      statuses: write
      pull-requests: write
      # Add id-token: write and attestations: write if you set attest: "true"
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0

      - name: Set up AcceptBox (verified against release.json)
        uses: kaufd/acceptbox/actions/setup@<full-commit-sha> # v0.3.0
        with:
          version: "0.3.0"
          # Optional: add witnessbox-version if the project uses --mode witnessbox:
          # witnessbox-version: "0.10.0"

      - name: Run AcceptBox acceptance
        uses: kaufd/acceptbox/actions/accept@<full-commit-sha> # v0.3.0
        with:
          github-token: ${{ github.token }}
          policy: auto          # bugfix by default; refactor on label "refactor" or trailer
          mode: auto            # uses base:.acceptbox.toml, or witnessbox if invariants are set
          repro-strategy: first-commit
          strict-refs: "true"   # fail-closed if trusted ref source degrades to local
```

`fetch-depth: 0` is required so Git has full ancestry between `base` and `head`.

## How `actions/accept` resolves PR parameters automatically

| Parameter | Automatic resolution (`resolve_pr.py`) | Override |
|---|---|---|
| `policy` | `refactor` if commit trailer `AcceptBox-Policy: refactor` or PR label `refactor` / `acceptbox:refactor` is present; otherwise `bugfix`. Conflicting trailers fail closed (exit `2`). | `policy: bugfix` or `policy: refactor` |
| `repro` (in `bugfix`) | 1) Single commit in `base..head` with trailer `AcceptBox-Role: repro` (multiple trailers fail closed); 2) otherwise the first commit in `base..head` when `repro-strategy: first-commit`. | `repro: <40-hex-sha>` or `repro-strategy: trailer-only` |
| `inv` & `mode` | Reads `AcceptBox-Inv: INV-1,INV-2` from commit trailers or PR body. If invariants are found and `mode: auto`, switches to `witnessbox`; otherwise defers to `base:.acceptbox.toml`. | `mode: command` / `witnessbox`, `inv: ...` |
| `ebt-sha` & `approved-tree` | In `witnessbox` mode, when both are omitted, fetches `origin/<base-ref>` and computes `approved-tree` via `acceptbox pin` (relying on `CODEOWNERS` protecting `ebt/` on `main`). | Pass both `ebt-sha` and `approved-tree` explicitly |

### Commit trailers supported by agents

Agents can declare intent right in commit messages:

```text
test: reproduce empty cart crash

AcceptBox-Role: repro
AcceptBox-Inv: INV-CART-1
```

For a refactoring PR (no `repro` commit; tests and `ebt/` stay immutable while `base` and `head` both pass):

```text
refactor: simplify discount calculation

AcceptBox-Policy: refactor
```

*(Alternatively, label the PR `refactor`.)*

## `actions/setup` verification

`actions/setup` never trusts an unverified package install:
1. Downloads `release.json` from the tagged GitHub Release (`kaufd/acceptbox` and optionally `kaufd/witnessbox`).
2. Downloads the exact `.whl` from PyPI (`--no-deps --only-binary=:all:`).
3. Verifies the wheel's SHA-256 against `release.json` before installing (`actions/setup/verify_release.py`).
4. Installs via `uv tool install --force` and verifies `--installation` JSON against `release.json`.

## Optional: independent `repro` review for `command` mode

In `witnessbox` mode, the invariant is human-approved, so `ACCEPTED` is sufficient for auto-merge.
In `command` mode, `actions/accept` proves structure, test immutability, red `repro`, and green `head`, but does not judge semantic test quality. If you want zero-operator auto-merge in `command` mode too, add a separate job that runs an independent LLM reviewer given **only** the issue description and `git diff "$BASE..$REPRO"` (never `head`) to reject trivial or mocked reproduction tests.

## Security boundaries and limits

- **Fork PRs are rejected by default** (`allow-forks: "false"`): fork PRs do not receive write tokens and should not run untrusted code in trusted workflows.
- **Strict trusted refs (`--strict-refs`):** enabled by default in `actions/accept`. Both AcceptBox itself and `format_report.py` fail closed if `ref_source_kind == "local"`.
- **Not an OS sandbox:** tests execute inside the GitHub-hosted runner. For WitnessBox's two-stage untrusted/trusted separation with signed `gate.json` replay, see [WitnessBox CI mode](https://github.com/kaufd/witnessbox/blob/main/docs/ci.md).
- **Optional attestation:** set `attest: "true"` (with `id-token: write` and `attestations: write`) to sign `accept.json` via `actions/attest-build-provenance`. If attestation fails, the final commit status is downgraded to `error`.
