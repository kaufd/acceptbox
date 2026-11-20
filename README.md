# AcceptBox

Инструмент независимой приёмки цепочки коммитов `base → repro → head` в изолированных Git worktree с атомарным машиночитаемым отчётом `accept.json` (формат 1). Runtime использует только стандартную библиотеку Python (`>=3.11`).

AcceptBox поддерживает два режима приёмки:
- **`--mode command`** — приёмка по обычной тестовой команде без `witnessbox` (правило L19): `repro` меняет только пути внутри `--tests`, `head` не имеет права менять/переименовывать/удалять файлы репро-теста, `repro` должен завершиться кодом `1` (`red`), `head` — кодом `0` (`green`);
- **`--mode witnessbox`** — приёмка через внешний CLI [WitnessBox](https://github.com/kaufd/witnessbox) (`>=0.10.0`, без верхней границы, правила L9, L10, L17, L21): проверка доверенного дерева `ebt/`, красная `base` и красный `repro` по целевому инварианту `--inv` (`FALSE_PROOF` / `CONFIRMED_VIOLATION`), зелёный `head`.

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

## Фиксация дерева проверок (`pin`)

```sh
acceptbox pin --ebt-repo /path/to/project --ebt-sha "$(git -C /path/to/project rev-parse HEAD)"
```

Читает дерево `ebt/` напрямую из объектов Git (без незакоммиченных правок) и печатает 64-символьный SHA-256.

## Приёмка коммитов (`accept`)

### Режим `command`

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
  --out /tmp/accept-run \
  -- python3 -m unittest discover -s tests
```

Команда после `--` запускается без shell (`shell=False`) с очищенным окружением. Код `0` трактуется как `green`, код `1` — как `red`, любой другой код (`>= 2`), сигнал или таймаут — как `gate-error` (код `2`).

### Режим `witnessbox`

```sh
acceptbox accept \
  --mode witnessbox \
  --repo /path/to/project \
  --base <40-hex-sha> \
  --repro <40-hex-sha> \
  --head <40-hex-sha> \
  --inv INV-1,INV-2 \
  --ebt-repo /path/to/project \
  --ebt-ref main \
  --ebt-sha <40-hex-sha> \
  --approved-tree <64-hex-sha256> \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  --out /tmp/accept-ebt-run
```

## Калибровка чувствительности (`calibrate`)

```sh
acceptbox calibrate \
  --mode command \
  --repo /path/to/project \
  --green <sha> \
  --red <sha> \
  --out /tmp/calibrate-run \
  -- python3 -m unittest discover -s tests
```

Проверяет набор `--green` / `--red` коммитов без проверки ancestry и записывает `<out>/calibrate.json` (`accept.json` не создаётся).

## Коды выхода и отчёты

- `0` — `ACCEPTED` (для `accept`) / `CALIBRATED` (для `calibrate`);
- `1` — `REJECTED` / `FAILED` (отказ по правилам приёмки или калибровки);
- `2` — `ERROR` (ошибка аргументов CLI, непустой `--out`, сбой `git`, отсутствующий/несовместимый `witnessbox`, нарушение схемы отчёта, ошибка прогона `gate-error`).

Подробнее о подключении проекта см. [docs/onboarding.md](docs/onboarding.md) и журнал решений [docs/decisions.md](docs/decisions.md).

## Разработка и сборка

```sh
python3 -m unittest discover -s tests
uv build
```
