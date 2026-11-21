# AcceptBox

Инструмент независимой приёмки цепочки коммитов `base → repro → head` в изолированных Git worktree с атомарным машиночитаемым отчётом `accept.json` (формат 1). Runtime использует только стандартную библиотеку Python (`>=3.11`).

AcceptBox поддерживает два режима приёмки:
- **`--mode command`** (по умолчанию, L19, L22) — приёмка по обычной тестовой команде без `witnessbox`: `repro` меняет только пути внутри `--tests`, `head` не имеет права менять/переименовывать/удалять файлы репро-теста, `repro` должен завершиться кодом `1` (`red`), `head` — кодом `0` (`green`);
- **`--mode witnessbox`** — приёмка через внешний CLI [WitnessBox](https://github.com/kaufd/witnessbox) (`>=0.10.0`, без верхней границы, правила L9, L10, L17, L21): проверка доверенного дерева `ebt/`, красная `base` и красный `repro` по целевому инварианту `--inv` (`FALSE_PROOF` / `CONFIRMED_VIOLATION`), зелёный `head`.

## Область применимости (L24)

Цепочка `base → repro → head` с красным репро-коммитом предназначена для **багфиксов** (а также для инвариантов, утверждённых человеком до реализации в `--mode witnessbox`).
Для **новых фич** красный репро-тест, написанный той же моделью, не даёт полезной гарантии, так как модель может подогнать тест и реализацию друг под друга — приёмка фич строится на утверждённой человеком спецификации (D4/L10) и/или независимом ревью и проверке поведения. Политика для **рефакторингов** (`base` зелёный → `head` зелёный без `repro`) ортогональна `--mode` и остаётся отдельной задачей.

## Установка

```sh
uv tool install acceptbox==0.1.0
```

Для режима `--mode witnessbox` дополнительно установите WitnessBox (опционально):

```sh
uv tool install 'witnessbox>=0.10.0'
```

## Проверка установки (`--installation`)

```sh
acceptbox --installation
```

Выводит JSON формата `1` (`format`, `report_format`, `distribution`, `version`, блоки `git` и `witnessbox` с `supported: ">=0.10.0"`). Возвращает код `0`, если доступны `git` и `acceptbox`, независимо от того, установлен ли `witnessbox`; код `2` — только при отсутствии/сбое `git` или ошибке диагностики `acceptbox`.

## Проектный конфиг (`.acceptbox.toml`, L23, L25)

Чтобы не повторять параметры проекта при каждом запуске, добавьте в корень репозитория `.acceptbox.toml`:

```toml
format = 1
mode = "command"               # опционально: "command" | "witnessbox" (по умолчанию "command")
timeout = 600                  # опционально: положительное число секунд
protect = ["pyproject.toml"]   # опционально: объединяется с --protect из CLI
env = ["PYTHONPATH=src"]       # опционально: KEY или KEY=VAL (BASE, EBT_*, GIT_* запрещены)

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
# ebt_repo указывать не нужно, если ebt/ живёт в том же репозитории (по умолчанию --repo)
```

**Правила доверия (fail-closed):**
- `.acceptbox.toml` читается строго из объектов Git базового коммита `base` (в `calibrate` — только при переданном `--base`), а не из рабочего дерева или веток `repro`/`head`;
- `.acceptbox.toml` автоматически защищается от изменений, удаления и переименования в `base..repro` и `base..head` (как `--protect`);
- все присутствующие секции валидируются строго: неизвестные ключи, пер-задачные поля (`inv`, `ebt_sha`, `approved_tree`, `base`, `repro`, `head`), симлинки и ошибки TOML завершаются с `ERROR` (`config-invalid`, код `2`);
- `protect` из конфига и CLI суммируются (CLI не может ослабить защиту из `base`), `env` сливается с приоритетом CLI по ключу, остальные параметры переопределяются явными флагами CLI.

## Фиксация дерева проверок (`pin`)

```sh
acceptbox pin --ebt-repo /path/to/project --ebt-sha "$(git -C /path/to/project rev-parse HEAD)"
```

Читает дерево `ebt/` напрямую из объектов Git (без незакоммиченных правок) и печатает 64-символьный SHA-256. Флаг `--ebt-repo` в `pin` обязателен.

## Приёмка коммитов (`accept`)

Эргономичные значения по умолчанию (L22):
- `--mode` по умолчанию берётся из `.acceptbox.toml` или равен `command`;
- `--repo` по умолчанию `.`;
- `--head` по умолчанию `HEAD` из `--repo` с немедленным резолвом в полный SHA при старте (явно переданный `--head` по-прежнему принимается только как полный 40/64-hex SHA);
- `--ebt-repo` в `witnessbox` по умолчанию берётся из `.acceptbox.toml` или равен `--repo`;
- `--out` по умолчанию атомарно создаётся как новый пустой каталог в системном `TMPDIR`, а путь к `accept.json` выводится в итоговое сообщение.

### Режим `command`

Минимальный запуск (при наличии `.acceptbox.toml` в `base`, из корня репозитория):

```sh
acceptbox accept --base <40-hex-sha> --repro <40-hex-sha>
```

Полный запуск с явными флагами (без `.acceptbox.toml`):

```sh
acceptbox accept \
  --mode command \
  --repo /path/to/project \
  --base <40-hex-sha> \
  --repro <40-hex-sha> \
  --head <40-hex-sha> \
  --tests tests/ \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-run] \
  -- python3 -m unittest discover -s tests
```

Команда после `--` (или `[command].argv` в `.acceptbox.toml`) запускается без shell (`shell=False`) с очищенным окружением. Автоопределение тестовой команды не выполняется. Код `0` трактуется как `green`, код `1` — как `red`, любой другой код (`>= 2`), сигнал или таймаут — как `gate-error` (код `2`).

### Режим `witnessbox`

```sh
acceptbox accept \
  --mode witnessbox \
  --base <40-hex-sha> \
  --repro <40-hex-sha> \
  --inv INV-1,INV-2 \
  --ebt-ref main \
  --ebt-sha <40-hex-sha> \
  --approved-tree <64-hex-sha256> \
  [--repo /path/to/project] \
  [--head <40-hex-sha>] \
  [--ebt-repo /path/to/ebt-repo] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-ebt-run]
```

## Калибровка чувствительности (`calibrate`)

```sh
acceptbox calibrate \
  --green <sha> \
  --red <sha> \
  [--base <base_sha>] \
  [--out /tmp/calibrate-run] \
  -- python3 -m unittest discover -s tests
```

Проверяет набор `--green` / `--red` коммитов без проверки ancestry и записывает `<out>/calibrate.json` (`accept.json` не создаётся). Если передан `--base`, настройки могут подгружаться из `.acceptbox.toml` этого коммита.

## Коды выхода и отчёты

- `0` — `ACCEPTED` (для `accept`) / `CALIBRATED` (для `calibrate`);
- `1` — `REJECTED` / `FAILED` (отказ по правилам приёмки или калибровки);
- `2` — `ERROR` (ошибка аргументов CLI, непустой `--out`, некорректный `.acceptbox.toml` `config-invalid`, сбой `git`, отсутствующий/несовместимый `witnessbox`, нарушение схемы отчёта, ошибка прогона `gate-error`).

Подробнее о подключении проекта см. [docs/onboarding.md](docs/onboarding.md) и журнал решений [docs/decisions.md](docs/decisions.md).

## Разработка и сборка

```sh
python3 -m unittest discover -s tests
uv build
```
