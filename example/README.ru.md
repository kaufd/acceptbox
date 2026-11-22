# Пример AcceptBox (внешний `--config`, без файлов в целевом репозитории)

[English](README.md) · **Русский**

Эта папка показывает, как использовать AcceptBox **полностью локально, не коммитя ни одного служебного файла в целевой репозиторий**:

- [acceptbox.toml](acceptbox.toml) — полный шаблон внешней конфигурации со всеми проектными настройками: `mode`, `timeout`, `protect`, `env`, `[command]` (`tests`, `argv`) и `[witnessbox]` (`ebt_repo`, `ebt_ref`).
- [run_demo.py](run_demo.py) — самодостаточный демо-скрипт (только стандартная библиотека Python), который создаёт чистый временный Git-репозиторий (без `.acceptbox.toml` внутри) и запускает AcceptBox с `--config example/acceptbox.toml` на трёх сценариях:
  1. Корректный багфикс (`base → repro → head`) → `ACCEPTED` (код `0`).
  2. Попытка подправить репро-тест в `head` → `REJECTED` (`repro-tests-modified`, код `1`).
  3. Корректный рефакторинг (`--policy refactor`, `base` зелёный → `head` зелёный, тесты не тронуты) → `ACCEPTED` (код `0`).

## Запуск демонстрации

Из корня репозитория:

```sh
python3 example/run_demo.py
```

## Использование `example/acceptbox.toml` со своим проектом

Положите копию файла **вне** целевого репозитория (например, `~/acceptbox/my-project.toml`) и запускайте из корня вашего проекта:

```sh
# Багфикс (режим command):
acceptbox accept --config ~/acceptbox/my-project.toml --base-ref main

# Рефакторинг (режим command):
acceptbox accept --config ~/acceptbox/my-project.toml --policy refactor --base-ref main

# Режим WitnessBox (с внешним [witnessbox].ebt_repo в конфиге):
acceptbox accept --config ~/acceptbox/my-project.toml --mode witnessbox --base-ref main --pin-from-ref
```
