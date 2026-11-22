# AcceptBox development rules

These rules apply to every coding agent working on this repository (Claude Code loads them through `CLAUDE.md`). They govern developing AcceptBox itself; an agent connecting AcceptBox to another project or working inside a connected project follows [docs/agent.md](docs/agent.md).

## Structure and dependencies

- `src/acceptbox/` is the only runtime package; the console entry point is `acceptbox`.
- The runtime uses **only the Python standard library (`>=3.11`)** and has zero third-party runtime dependencies (L14). `hatchling` is used strictly as a build backend in `pyproject.toml`.
- Importing any internal `witnessbox` module is strictly forbidden. Interaction with `witnessbox` is allowed only via its external CLI (`witnessbox --installation`, `witnessbox gate ...`) with fail-closed JSON schema validation.
- Runners `command.py` and `witnessbox.py` are flat modules (no `adapters/` subpackage) implementing a uniform `run_commit(...) -> RunOutcome` contract. `.acceptbox.toml` is parsed in flat module `config.py` using stdlib `tomllib`.

## Specification and decisions

- In a connecting project, a human approves the EBT specification (`spec.toml`); an agent never edits `spec.toml` without human approval (WitnessBox decision D4).
- Threat model ([L31](docs/decisions.md)): the agent under test errs, hallucinates, or cuts corners but does not deliberately attack the host; design checks against accidental failure modes. Anything that changes the meaning of a verdict must appear in the CLI summary output, not only in `accept.json`.
- The decision log in [docs/decisions.md](docs/decisions.md) and [docs/decisions.ru.md](docs/decisions.ru.md) is append-only: when something changes, add a new entry with date, reason, and status to **both** files in one change; never rewrite earlier entries.

## Language, documentation, and verification

- **Documentation is bilingual:** English is the primary version (`README.md`, `docs/<name>.md`), and Russian sits next to it (`README.ru.md`, `docs/<name>.ru.md`). Every documentation page starts with the language switcher right below the title (`**English** · [Русский](...)` or `[English](...) · **Русский**`). Links inside English docs point to `.md`; links inside Russian docs point to `.ru.md`; links in `README.md` use absolute GitHub URLs so they work on PyPI. Update both languages in the same change.
- **Pull requests and this file** are written in English; code identifiers and all CLI/report output messages in `src/acceptbox/` are in English ([L33](docs/decisions.md)).
- All tests must run without depending on personal directories or external repositories:
  ```sh
  python3 -m unittest discover -s tests
  ```
- All Git, environment, `.acceptbox.toml`, and JSON-contract checks must remain fail-closed: strip `git rev-parse --local-env-vars`, pass `--no-replace-objects`, `-c core.hooksPath=/dev/null`, `-c diff.renames=false --no-renames -z`, validate `origin` URLs and pass `--` before fetch sources, read `.acceptbox.toml` strictly from Git objects of `base` (or, only when the CLI passes `--config`, from that external regular file opened with `O_NOFOLLOW`) with auto-protection across `base..repro` and `base..head`, acquire `--out` atomically (`O_EXCL`), and write `accept.json` / `calibrate.json` atomically.
