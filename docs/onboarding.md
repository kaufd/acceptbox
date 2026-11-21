# Подключение нового проекта к AcceptBox

AcceptBox проверяет цепочку коммитов `base → repro → head` в изолированном временном клоне (не переключая рабочий checkout) и записывает машиночитаемый вердикт `accept.json`.

## 0. Область применимости: когда использовать `command`, а когда `witnessbox` (L24)

- **Багфиксы (`--mode command` или `--mode witnessbox`):** цепочка `base → repro → head` с красным репро-коммитом предназначена именно для багфиксов.
- **Новые фичи:** красный репро-тест, написанный той же моделью, что и реализация, не даёт полезной гарантии (модель может подогнать тест и код друг под друга). Для фич используйте утверждённую человеком спецификацию (`--mode witnessbox`, D4/L10) и/или независимое ревью и проверку поведения.
- **Рефакторинги:** политика `base` зелёный → `head` зелёный с защитой тестов без `repro` ортогональна `--mode` и является отдельной задачей.

## 1. Установка и проверка окружения

```sh
uv tool install acceptbox==0.1.0
# Опционально (нужно только для --mode witnessbox):
uv tool install 'witnessbox>=0.10.0'

acceptbox --installation
```

`acceptbox --installation` возвращает код `0`, если работают `git` и `acceptbox`, даже когда `witnessbox` не установлен (`witnessbox.status = "missing"`).

## 2. Настройка `.acceptbox.toml` (рекомендуется, L23, L25)

Добавьте и закоммитьте `.acceptbox.toml` в базовой ветке репозитория (например, `main`), чтобы не повторять параметры при каждом запуске:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pyproject.toml"]

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
```

- Конфиг читается **строго из коммита `base`**, никогда из рабочего дерева или проверяемых коммитов `repro`/`head`.
- Файл `.acceptbox.toml` автоматически защищён от изменений в `base..repro` и `base..head` (`protected-paths-changed`).
- `protect` из конфига и `--protect` из CLI суммируются; `tests`, `argv`, `mode`, `timeout`, `ebt_repo`, `ebt_ref` можно переопределить явными флагами CLI.

## 3. Быстрый старт в режиме `command` (без WitnessBox)

В режиме `command` (режим по умолчанию, L22) коммит `repro` добавляет репро-тест внутри путей `--tests`, а коммит `head` исправляет баг, не изменяя, не переименовывая и не удаляя файлы репро-теста.

Если `.acceptbox.toml` уже закоммичен в `base`, из корня репозитория достаточно выполнить:

```sh
acceptbox accept --base <base_sha> --repro <repro_sha>
```

Здесь `--repo` по умолчанию `.`, `--head` немедленно резолвится из `HEAD` текущего репозитория в полный SHA, а `--out` атомарно создаётся в `TMPDIR` (путь к `accept.json` печатается в итоговом сообщении).

Без `.acceptbox.toml` передайте `--tests` и команду после `--` явно:

```sh
acceptbox accept \
  --base <base_sha> \
  --repro <repro_sha> \
  --tests tests/ \
  --protect pyproject.toml \
  -- python3 -m unittest discover -s tests
```

> **Важно про подготовку зависимостей (wrapper-скрипт):** каждый коммит запускается в чистом detached-worktree без кэшированных изменений рабочего дерева. Если проекту нужна установка зависимостей или сборка перед запуском тестов, оформите скрипт-обёртку в `[command].argv` или после `--`. Скрипт **обязан** возвращать код выхода `>= 2` при сбое подготовки среды/зависимостей и код `1` **только** при провале самих тестов — иначе сбой среды будет ошибочно классифицирован как `red` вместо `gate-error` (код `2`).

## 4. Подключение режима `witnessbox` (EBT)

1. Убедитесь, что каталог `ebt/` утверждён владельцем и находится в основной ветке репозитория (`kaufd/witnessbox`, версия `>=0.10.0`).
2. Зафиксируйте отпечаток утверждённого дерева `ebt/` (в `pin` флаг `--ebt-repo` обязателен):
   ```sh
   APPROVED_TREE=$(acceptbox pin --ebt-repo /path/to/project --ebt-sha <ebt_sha>)
   ```
3. Запустите приёмку (проверяются доверие `ebt/`, ancestry, защита `.acceptbox.toml`, `ebt` и `tool/ebt_check`, красные `base` и `repro` по `--inv`, и зелёный `head`):
   ```sh
   acceptbox accept \
     --mode witnessbox \
     --base <base_sha> \
     --repro <repro_sha> \
     --inv INV-1 \
     --ebt-ref main \
     --ebt-sha <ebt_sha> \
     --approved-tree "$APPROVED_TREE"
   ```
   Флаги `--repo` (по умолчанию `.`), `--ebt-repo` (по умолчанию `--repo`), `--head` (по умолчанию `HEAD`) и `--out` (по умолчанию каталог в `TMPDIR`) опциональны. Если `mode = "witnessbox"` и `[witnessbox].ebt_ref` заданы в `.acceptbox.toml` в `base`, можно опустить и `--mode`, и `--ebt-ref`.

## 5. Калибровка чувствительности (`calibrate`)

Для проверки эталонных коммитов и мутантов (без проверки ancestry и без создания `accept.json`) используйте `acceptbox calibrate`:

```sh
acceptbox calibrate \
  --green <good_sha> \
  --red <mutant_sha> \
  -- python3 -m unittest discover -s tests
```

Если передать `--base <base_sha>`, `calibrate` может подгрузить проектные настройки из `.acceptbox.toml` этого коммита (без `--base` конфиг не читается).
Отчёт записывается атомарно в `<out>/calibrate.json` (при опущенном `--out` каталог создаётся в `TMPDIR`, путь печатается в итоговом сообщении). Коды выхода: `0` — все ожидания совпали (`CALIBRATED`), `1` — расхождение (`FAILED`), `2` — ошибка среды/CLI/конфига (`ERROR`).
