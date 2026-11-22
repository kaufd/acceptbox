# AcceptBox example (external `--config`, zero files in target repo)

**English** · [Русский](README.ru.md)

This folder demonstrates using AcceptBox **completely locally without committing any AcceptBox files to the target repository**:

- [acceptbox.toml](acceptbox.toml) — complete external configuration template showing `mode`, `timeout`, `protect`, `env`, `[command]` (`tests`, `argv`), and `[witnessbox]` (`ebt_repo`, `ebt_ref`).
- [run_demo.py](run_demo.py) — self-contained demo script (stdlib only) that builds a clean temporary Git repository (with **no** `.acceptbox.toml` inside it) and runs AcceptBox with `--config example/acceptbox.toml` across three scenarios:
  1. Valid bugfix (`base → repro → head`) → `ACCEPTED` (exit `0`).
  2. Tampered reproduction test in `head` → `REJECTED` (`repro-tests-modified`, exit `1`).
  3. Valid refactoring (`--policy refactor`, `base` green → `head` green, tests untouched) → `ACCEPTED` (exit `0`).

## Run the demo

From the repository root:

```sh
python3 example/run_demo.py
```

## Adapting `example/acceptbox.toml` to your own project

Keep a copy outside the target repository (for example, `~/acceptbox/my-project.toml`) and run from the root of your target repository:

```sh
# Bugfix (command mode):
acceptbox accept --config ~/acceptbox/my-project.toml --base-ref main

# Refactoring (command mode):
acceptbox accept --config ~/acceptbox/my-project.toml --policy refactor --base-ref main

# WitnessBox mode (with external [witnessbox].ebt_repo set in the config):
acceptbox accept --config ~/acceptbox/my-project.toml --mode witnessbox --base-ref main --pin-from-ref
```
