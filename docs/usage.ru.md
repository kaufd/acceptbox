# Справочник CLI и конфигурации

[English](usage.md) · **Русский**

Эта страница описывает все команды `acceptbox`, флаги, ключи конфигурации, коды выхода, коды причин и правила записи отчётов. Для пошагового знакомства см. [подключение проекта](onboarding.ru.md), для готовых шаблонов по стекам — [настройку проекта и шаблоны](setup.ru.md).

## Проверка установки (`--installation`)

```sh
acceptbox --installation
```

Выводит JSON формата `1` со следующими полями:
- `format`: `1`
- `report_format`: `1`
- `distribution`: `"acceptbox"`
- `version`: установленная версия (например, `"0.2.0"`)
- `git`: статус (`"ok" | "missing" | "error"`), путь, версия
- `witnessbox`: путь, версия, `supported: ">=0.10.0"`, статус (`"compatible" | "missing" | "incompatible" | "error"`)

Возвращает код `0`, если доступны `git` и `acceptbox`, независимо от того, установлен ли `witnessbox`; код `2` — только при отсутствии/сбое `git` или ошибке диагностики `acceptbox`.

`acceptbox --version` печатает `acceptbox <version>`.

## Проектный конфиг (`.acceptbox.toml`)

Поместите `.acceptbox.toml` в корень репозитория и закоммитьте его в базовую ветку (например, `main`):

```toml
format = 1
mode = "command"               # опционально: "command" | "witnessbox" (по умолчанию "command")
timeout = 600                  # опционально: конечное положительное число секунд
protect = ["pyproject.toml"]   # опционально: объединяется с --protect из CLI
env = ["PYTHONPATH=src"]       # опционально: KEY или KEY=VAL (BASE, EBT_* и git local-env-vars запрещены)

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]

[witnessbox]
ebt_ref = "main"
# ebt_repo указывать не нужно, если ebt/ живёт в том же репозитории (по умолчанию --repo);
# если указан — только URL или абсолютный путь.
```

### Правила доверия, валидации и приоритета (fail-closed)

- **Чтение строго из `base`:** `.acceptbox.toml` читается напрямую из объектов Git коммита `base` (`git ls-tree` + `git cat-file`), никогда из рабочего дерева или коммитов `repro`/`head`. В `calibrate` конфиг читается только при переданном `--base`.
- **Автозащита:** `.acceptbox.toml` автоматически добавляется к защищённым путям в `base..repro` и `base..head` (как `--protect .acceptbox.toml`).
- **Граница доверия `--base`:** конфигу можно доверять ровно настолько, насколько можно доверять `--base`. Передайте `--base-ref main` (только CLI — ключ `base_ref` в `.acceptbox.toml` отклоняется), чтобы AcceptBox до чтения конфига проверил, что `--base` — предок `main` из `origin`. Без `--base-ref` значение `--base` должна задавать принимающая сторона (человек, CI или внешний оркестратор).
- **Строгая валидация схемы:** все присутствующие секции валидируются fail-closed. Неизвестные ключи, пер-задачные поля (`inv`, `ebt_sha`, `approved_tree`, `base`, `repro`, `head`, `repo`, `out`, `base_ref`), относительный локальный путь `[witnessbox].ebt_repo`, симлинки/каталоги вместо обычного файла и ошибки TOML завершаются с `ERROR` (`config-invalid`, код `2`). Блок `inputs.config` такого отчёта содержит `commit` и `sha256` отклонённого файла.
- **Разрешённые переменные среды:** `env` допускает обычные переменные (включая, например, `GIT_SSH_COMMAND` или `GIT_AUTHOR_NAME`), но запрещает `BASE`, любые `EBT_*` и переменные git-изоляции из `git rev-parse --local-env-vars`.
- **Приоритет CLI:**
  - `protect` из конфига и `--protect` из CLI суммируются (CLI не может ослабить защиту из `base`).
  - `env` сливается с приоритетом CLI по ключу; `--env KEY` без значения при отсутствии `KEY` в окружении снимает значение `KEY` из конфига.
  - `mode`, `timeout`, `tests`, `argv`, `ebt_repo` и `ebt_ref` переопределяются явными флагами CLI.

## Фиксация дерева проверок (`pin`)

```sh
acceptbox pin --ebt-repo /path/to/project --ebt-sha "$(git -C /path/to/project rev-parse HEAD)"
```

Читает дерево `ebt/` напрямую из объектов Git коммита `--ebt-sha` (без незакоммиченных правок) и печатает 64-символьный SHA-256 в нижнем регистре. Оба флага `--ebt-repo` и `--ebt-sha` (полный 40/64-hex SHA) обязательны.

## Приёмка цепочки коммитов (`accept`)

### Значения по умолчанию

- `--mode`: из `.acceptbox.toml` или `command`.
- `--repo`: `.`.
- `--head`: `HEAD` из `--repo`, резолвится в полный SHA сразу после проверки `git` (сбой даёт `gate-error` с отчётом). Явно переданный `--head` принимается только как полный 40/64-hex SHA. **Важно:** незакоммиченные правки рабочего дерева не проверяются; если `--repo` указан как URL, без `--head` через `git ls-remote` берётся удалённый `HEAD` (обычно ветка по умолчанию, а не ветка с фиксом — для URL передавайте `--head` явно).
- `--ebt-repo` (режим `witnessbox`): из `[witnessbox].ebt_repo` или `--repo`.
- `--timeout`: из `.acceptbox.toml` или `600` секунд.
- `--out`: атомарно создаёт новый пустой каталог в системном `TMPDIR` и печатает путь к `accept.json`. При явном `--out` каталог должен отсутствовать или быть пустым (блокировка `O_EXCL`).

### Источник доверенной ветки (`--base-ref`, `--ebt-ref` и `--strict-refs`)

- Если репозиторий (`--repo` для `--base-ref`, `--ebt-repo` для `--ebt-ref`) задан как URL или scp-адрес, ссылка берётся напрямую из него (`ref_source_kind: "remote"`).
- Если это локальный путь, AcceptBox вызывает `git remote get-url origin` и берёт ссылку из `origin` (`ref_source_kind: "origin"`), чтобы агент не мог сдвинуть локальную ветку и подделать ancestry. Короткие имена разрешаются сначала как `refs/heads/<ref>`, затем как `refs/tags/<ref>`.
- Если у локального репозитория нет remote `origin`, проверка идёт по самому локальному репозиторию с пометкой `ref_source_kind: "local"` в отчёте (слабая проверка). Передайте `--strict-refs` (только CLI, рекомендуется в CI), чтобы fail-closed отклонять запасной путь `"local"` с кодом `ref-source-local` (код `1`). В режиме `command` `--strict-refs` требует `--base-ref`. Любой другой сбой чтения `origin` (например, `safe.directory`) даёт `gate-error`.
- **Граница доверия:** URL `origin` хранится в `.git/config` локального checkout. Сверяйте `ref_source` и `ref_source_kind` в `accept.json`, а для полной гарантии запускайте приёмку вне досягаемости агента (CI) или передавайте URL в `--repo` / `--ebt-repo`.

### Политики приёмки (`--policy bugfix` и `--policy refactor`)

- **`--policy bugfix` (по умолчанию, только CLI):** требует `--repro` и проверяет цепочку `base → repro → head` (`repro` красный, `head` зелёный; в `witnessbox` также `base` красный по `--inv`).
- **`--policy refactor` (только CLI, L24/L30):** запрещает `--repro`, работает в обоих режимах (`command` и `witnessbox`), проверяет ancestry `base → head` (`head-not-descendant`) и непустоту `base..head` (`refactor-empty`), автоматически защищает `.acceptbox.toml` плюс `[command].tests` (в `command`) или `ebt`/`tool/ebt_check` (в `witnessbox`) в `base..head` и требует, чтобы **оба** коммита `base` (`base-not-green`) и `head` (`head-not-green`) прошли (`green`). В `witnessbox` + `refactor` флаг `--inv` опционален (без него проверяется вся спецификация).

```sh
acceptbox accept --policy refactor --base-ref main --strict-refs --base "$(git merge-base main HEAD)"
```

### Режим `command`

Минимальный запуск (при наличии `.acceptbox.toml` в `base`):

```sh
acceptbox accept --base-ref main --base "$(git merge-base main HEAD)" --repro <полный-sha>
```

Полный запуск с явными флагами (без `.acceptbox.toml`):

```sh
acceptbox accept \
  --mode command \
  --repo /path/to/project \
  --base <полный-sha> \
  --repro <полный-sha> \
  --head <полный-sha> \
  --tests tests/ \
  [--base-ref main] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-run] \
  -- python3 -m unittest discover -s tests
```

Команда после `--` (или `[command].argv`) запускается без shell (`shell=False`) в отдельной сессии процессов (по таймауту и после завершения убивается вся группа процессов) с очищенным окружением. Автоопределение тестовой команды не выполняется. Код `0` → `green`, код `1` → `red`, любой другой код (`>= 2`), сигнал, `OSError` или таймаут → `gate-error` (код `2`).

### Режим `witnessbox`

```sh
acceptbox accept \
  --mode witnessbox \
  --base <полный-sha> \
  --repro <полный-sha> \
  --inv INV-1,INV-2 \
  --ebt-ref main \
  --ebt-sha <полный-sha> \
  --approved-tree <64-hex-sha256> \
  [--base-ref main] \
  [--repo /path/to/project] \
  [--head <полный-sha>] \
  [--ebt-repo /path/to/ebt-repo] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-ebt-run]
```

В режиме `witnessbox` пути `ebt` и `tool/ebt_check` защищаются всегда, `base` и `repro` должны быть `red` с находками `FALSE_PROOF` или `CONFIRMED_VIOLATION` хотя бы по одному инварианту из `--inv`, а `head` — `green`.

## Калибровка чувствительности (`calibrate`)

Проверяет набор эталонных `--green` и `--red` коммитов и записывает `<out>/calibrate.json` (`accept.json` не создаётся). Проверки `ancestry`, `--protect`, диффов `base..repro` / `repro..head` и `--base-ref` **не применяются**. Флаг `--tests` не принимается. При переданном `--base <sha>` конфиг `.acceptbox.toml` читается из этого коммита (а в `witnessbox` передаётся `BASE`).

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
  --ebt-sha <полный-sha> \
  --approved-tree <64-hex-sha256> \
  [--repo /path/to/project] \
  [--ebt-repo /path/to/ebt-repo] \
  [--base <base_sha>] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/calibrate-ebt-run]
```

## Коды выхода, коды причин и правила записи отчётов

| Код выхода | Результат `accept` | Результат `calibrate` | Значение |
|---|---|---|---|
| `0` | `ACCEPTED` | `CALIBRATED` | Все проверки / ожидания совпали |
| `1` | `REJECTED` | `FAILED` | Отказ по правилам приёмки или калибровки |
| `2` | `ERROR` | `ERROR` | Ошибка CLI, конфига, `git`, `witnessbox` или окружения |

### Коды причин `REJECTED` / `FAILED` (код `1`)

| Код причины | Режим / команда | Значение |
|---|---|---|
| `base-not-on-ref` | `accept` (с `--base-ref`) | `--base-ref` не найдена в доверенном источнике или `--base` не является её предком |
| `ref-source-local` | `accept` (с `--strict-refs`) | Источник доверенной ветки деградировал до `"local"` при включённом `--strict-refs` |
| `no-invariants` | `witnessbox` | `--inv` не содержит ни одного инварианта |
| `inputs-untrusted` | `witnessbox` | `--ebt-ref` не найдена, `--ebt-sha` не предок `--ebt-ref`, хеш `ebt/` ≠ `--approved-tree` или нет `ebt/spec.toml` |
| `repro-not-descendant` | `accept` (`bugfix`) | `base` не является предком `repro` |
| `repro-not-ancestor` | `accept` (`bugfix`) | `repro` не является предком `head` |
| `head-not-descendant` | `accept` (`refactor`) | `base` не является предком `head` |
| `protected-paths-changed` | `accept` | Защищённый путь (`.acceptbox.toml`, `protect`, `[command].tests` в `refactor` или `ebt`/`tool/ebt_check` в `witnessbox`) изменён |
| `repro-empty` | `accept` (`bugfix` + `command`) | Дифф `base..repro` пуст |
| `refactor-empty` | `accept` (`refactor`) | Дифф `base..head` пуст |
| `repro-outside-tests` | `accept` (`bugfix` + `command`) | `base..repro` меняет пути вне `--tests` |
| `repro-tests-modified` | `accept` (`bugfix` + `command`) | `repro..head` изменяет, переименовывает или удаляет файлы из `base..repro` |
| `base-green` / `base-report-invalid` / `base-red-wrong-reason` | `accept` (`bugfix` + `witnessbox`) | `base` не упал по ожидаемому инварианту |
| `base-not-green` | `accept` (`refactor`) | `base` не зелёный |
| `repro-green` / `repro-report-invalid` / `repro-red-wrong-reason` | `accept` (`bugfix`) | `repro` зелёный или упал не по нужной причине |
| `head-not-green` | `accept` | `head` не зелёный |
| `mismatch` | `calibrate` | Хотя бы один коммит `--green` / `--red` дал неожиданный исход |

### Коды `ERROR` (код `2`) и правило записи отчёта

- **Отчёт не создаётся:** ошибки синтаксиса CLI, `out-not-empty`, а также нехватка обязательных аргументов (`--tests`, команды после `--`, `--inv`, `--ebt-ref`, `--ebt-sha`, `--approved-tree`) или смешивание флагов режимов, обнаруженные после чтения `base:.acceptbox.toml`. Если пустой каталог `--out` был создан текущим запуском, он удаляется.
- **Отчёт записывается атомарно** (путь печатается в `stderr`) во всех остальных случаях кода `2` после захвата `--out`: `config-invalid`, `witnessbox-missing`, `witnessbox-incompatible`, `gate-error`.

## Разработка, сборка и выпуск релиза

```sh
python3 -m unittest discover -s tests
python3 tests/release_check.py
```

`tests/release_check.py` собирает sdist и wheel из него, проверяет отсутствие сторонних runtime-импортов и совпадение версий, прогоняет весь набор тестов из распакованного sdist (без пропусков), сверяет побайтовую идентичность пересобранного wheel, проверяет установленный wheel в чистом venv и записывает `build/release/<version>/release.json` и `SHA256SUMS`.

### Выпуск релиза (GitHub Actions)

1. Обновите `version` в `pyproject.toml` и запасное значение `__version__` в `src/acceptbox/__init__.py`, влейте в `main`.
2. Поставьте и отправьте тег: `git tag -a v<version> -m "AcceptBox <version>" && git push origin v<version>`.
   Workflow [Release](../.github/workflows/release.yml) выполнит `tests/release_check.py` и создаст GitHub **prerelease** с прикреплёнными wheel, sdist, `release.json` и `SHA256SUMS` (при необходимости его можно запустить вручную: `gh workflow run release.yml -f tag=v<version>`).
3. Опубликуйте проверенные артефакты на PyPI (Trusted Publishing по OIDC; workflow ничего не собирает, а сверяет SHA-256 с `release.json`):
   ```sh
   gh workflow run publish.yml -f tag=v<version>
   ```
4. Проверьте установку с PyPI (`uv tool install acceptbox==<version> && acceptbox --installation`) и переведите GitHub Release из prerelease в стабильный:
   ```sh
   gh release edit v<version> --prerelease=false --latest
   ```
