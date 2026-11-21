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

## Проектный конфиг (`.acceptbox.toml`, L23, L25, L26)

Чтобы не повторять параметры проекта при каждом запуске, добавьте в корень репозитория `.acceptbox.toml`:

```toml
format = 1
mode = "command"               # опционально: "command" | "witnessbox" (по умолчанию "command")
timeout = 600                  # опционально: положительное число секунд
protect = ["pyproject.toml"]   # опционально: объединяется с --protect из CLI
env = ["PYTHONPATH=src"]       # опционально: KEY или KEY=VAL (BASE, EBT_* и git local-env-vars запрещены)

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
# ebt_repo указывать не нужно, если ebt/ живёт в том же репозитории (по умолчанию --repo);
# если указан — только URL или абсолютный путь (L27)
```

**Правила доверия (fail-closed):**
- `.acceptbox.toml` читается строго из объектов Git базового коммита `base` (в `calibrate` — только при переданном `--base`), а не из рабочего дерева или веток `repro`/`head`;
- `.acceptbox.toml` автоматически защищается от изменений, удаления и переименования в `base..repro` и `base..head` (как `--protect`);
- **граница доверия `--base` (L26):** конфигу можно доверять ровно настолько, насколько можно доверять `--base`. AcceptBox не проверяет принадлежность `--base` основной ветке — `--base` должна задавать принимающая сторона (человек, CI или внешний оркестратор, например через `git merge-base main HEAD`), а не проверяемый агент;
- все присутствующие секции валидируются строго: неизвестные ключи, пер-задачные поля (`inv`, `ebt_sha`, `approved_tree`, `base`, `repro`, `head`, `repo`, `out`), относительный локальный путь `[witnessbox].ebt_repo`, симлинки и ошибки TOML завершаются с `ERROR` (`config-invalid`, код `2`); блок `inputs.config` такого отчёта содержит `commit` и `sha256` отклонённого файла;
- `env` допускает обычные переменные (включая `GIT_SSH_COMMAND` или `GIT_AUTHOR_NAME`), но запрещает `BASE`, любые `EBT_*` и переменные git-изоляции из `git rev-parse --local-env-vars`;
- `protect` из конфига и CLI суммируются (CLI не может ослабить защиту из `base`), `env` сливается с приоритетом CLI по ключу (`--env KEY` без значения при отсутствии `KEY` в окружении снимает значение из конфига), остальные параметры переопределяются явными флагами CLI.

## Фиксация дерева проверок (`pin`)

```sh
acceptbox pin --ebt-repo /path/to/project --ebt-sha "$(git -C /path/to/project rev-parse HEAD)"
```

Читает дерево `ebt/` напрямую из объектов Git (без незакоммиченных правок) и печатает 64-символьный SHA-256. Флаг `--ebt-repo` в `pin` обязателен.

## Приёмка коммитов (`accept`)

Эргономичные значения по умолчанию (L22):
- `--mode` по умолчанию берётся из `.acceptbox.toml` или равен `command`;
- `--repo` по умолчанию `.`;
- `--head` по умолчанию `HEAD` из `--repo`; резолв в полный SHA выполняется сразу после проверки `git`, его сбой даёт `gate-error` с отчётом (явно переданный `--head` по-прежнему принимается только как полный 40/64-hex SHA). **Важно:** незакоммиченные правки рабочего дерева не проверяются, а если `--repo` указан как URL, через `git ls-remote` берётся удалённый `HEAD` (обычно ветка по умолчанию, а не ветка с фиксом — для URL передавайте `--head` явно);
- `--ebt-repo` в `witnessbox` по умолчанию берётся из `.acceptbox.toml` или равен `--repo`. Если это локальный checkout, проверка «`--ebt-sha` — предок `--ebt-ref`» идёт по локальной ветке, которую может сдвинуть любой, кто пишет в этот checkout: опорой доверия остаётся только `--approved-tree`. Для полной проверки L10 передайте в `--ebt-repo` URL основного репозитория (L27);
- `--out` по умолчанию атомарно создаётся как новый пустой каталог в системном `TMPDIR`, а путь к `accept.json` выводится в итоговое сообщение.

### Режим `command`

Минимальный запуск (при наличии `.acceptbox.toml` в `base`, из корня репозитория):

```sh
acceptbox accept --base "$(git merge-base main HEAD)" --repro <40-hex-sha>
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

Режим `command`:

```sh
acceptbox calibrate \
  --green <sha> \
  --red <sha> \
  [--repo /path/to/project] \
  [--base <base_sha>] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/calibrate-run] \
  -- python3 -m unittest discover -s tests
```

Режим `witnessbox`:

```sh
acceptbox calibrate \
  --mode witnessbox \
  --green <sha> \
  --red <sha> \
  --inv INV-1 \
  --ebt-ref main \
  --ebt-sha <40-hex-sha> \
  --approved-tree <64-hex-sha256> \
  [--repo /path/to/project] \
  [--ebt-repo /path/to/ebt-repo] \
  [--base <base_sha>] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/calibrate-ebt-run]
```

Калибровка проверяет набор `--green` / `--red` коммитов и записывает `<out>/calibrate.json` (`accept.json` не создаётся). Проверки `ancestry`, `--protect` и диффа `base..repro` / `repro..head` в калибровке **не применяются**. Если передан `--base`, настройки могут подгружаться из `.acceptbox.toml` этого коммита (и в `witnessbox` передаётся `BASE`).

## Коды выхода и отчёты

- `0` — `ACCEPTED` (для `accept`) / `CALIBRATED` (для `calibrate`);
- `1` — `REJECTED` / `FAILED` (отказ по правилам приёмки или калибровки);
- `2` — `ERROR` (ошибка аргументов CLI, непустой `--out`, некорректный `.acceptbox.toml` `config-invalid`, сбой `git`, отсутствующий/несовместимый `witnessbox`, нарушение схемы отчёта, ошибка прогона `gate-error`).

**Правило записи отчёта при коде `2` (L26):**
- `accept.json` / `calibrate.json` **не создаётся** при ошибках синтаксиса CLI, при `out-not-empty`, а также при нехватке обязательных аргументов (`--tests`, команды после `--`, `--inv`, `--ebt-ref`, `--ebt-sha`, `--approved-tree`) или смешивании флагов режимов, обнаруженных после чтения `base:.acceptbox.toml`; если пустой каталог `--out` был создан текущим запуском, он удаляется;
- во всех остальных случаях кода `2` после захвата `--out` (включая `config-invalid`, `witnessbox-missing`, `witnessbox-incompatible`, `gate-error`) отчёт записывается атомарно, а путь к нему печатается в `stderr`.

Подробнее о подключении проекта см. [docs/onboarding.md](docs/onboarding.md) и журнал решений [docs/decisions.md](docs/decisions.md).

## Разработка и сборка

```sh
python3 -m unittest discover -s tests
uv build
```
