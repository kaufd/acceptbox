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
- `version`: установленная версия (например, `"0.4.0"`)
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

- **Чтение строго из `base` (или внешнего `--config`):** по умолчанию `.acceptbox.toml` читается напрямую из объектов Git коммита `base` (`git ls-tree` + `git cat-file`), никогда из рабочего дерева или коммитов `repro`/`head` (в `calibrate` — только при переданном `--base`). Либо передайте `--config <path>` (только CLI, работает в `accept` и `calibrate`), чтобы читать ту же схему формата `1` из внешнего обычного файла вне целевого репозитория, ничего не коммитя в `main` (отсутствующий файл, симлинк, каталог и ошибки TOML дают `config-invalid`, код `2`).
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

### Значения по умолчанию и локальный авто-резолв

- `--mode`: из `.acceptbox.toml` или `command`.
- `--repo`: `.`.
- `--base`: если опущен, требуется `--base-ref <ветка>` — автоматически вычисляется `git merge-base <доверенная ветка> <head>` до чтения `.acceptbox.toml` из этого `base`.
- `--repro` (в политике `bugfix`): если опущен, автоматически выбирается единственный коммит в `base..head` с trailer `AcceptBox-Role: repro` (несколько → `repro-ambiguous`), иначе первый first-parent коммит в `base..head` (`inputs.repro_source`: `"explicit" | "trailer" | "first-commit"`). Учитывается только блок trailers коммита (разбирает `git`); строка роли в теле сообщения игнорируется. О запасном варианте `first-commit` CLI выводит предупреждение. Запрещён в политике `refactor`.
- `--head`: `HEAD` из `--repo`, резолвится в полный SHA сразу после проверки `git` (сбой даёт `gate-error` с отчётом). Явно переданный `--head` принимается только как полный 40/64-hex SHA. **Важно:** незакоммиченные правки рабочего дерева не проверяются; если `--repo` указан как URL, без `--head` через `git ls-remote` берётся удалённый `HEAD` (обычно ветка по умолчанию, а не ветка с фиксом — для URL передавайте `--head` явно).
- `--ebt-repo` (режим `witnessbox`): из `[witnessbox].ebt_repo` или `--repo`.
- `--pin-from-ref` (режим `witnessbox`, только CLI): берёт `--ebt-sha` с вершины доверенной `--ebt-ref` и читает дерево `ebt/` напрямую из её Git-объектов (нельзя совмещать с `--ebt-sha` и `--approved-tree`). Дерево закреплено, но не одобрено владельцем: в отчёте `approved_tree_sha256: null` и `pinned_from_ref: true` (хеш — в `actual_tree_sha256`), а CLI выводит предупреждение.
- `--inv` (режим `witnessbox`): опциональный. Если опущен, прогоняется вся спецификация `ebt/spec.toml`: в политике `bugfix` нарушенные на `base` инварианты (`FALSE_PROOF` / `CONFIRMED_VIOLATION`) определяются автоматически и `repro` обязан упасть хотя бы по одному из тех же инвариантов, после чего `head` проходит; в политике `refactor` вся спецификация должна быть зелёной на `base` и `head`. Явно пустой `--inv ""` по-прежнему отклоняется с `no-invariants`.
- `--timeout`: из `.acceptbox.toml` или `600` секунд.
- `--out`: атомарно создаёт новый пустой каталог в системном `TMPDIR` и печатает путь к `accept.json`. При явном `--out` каталог должен отсутствовать или быть пустым (блокировка `O_EXCL`).

### Источник доверенной ветки (`--base-ref`, `--ebt-ref` и `--strict-refs`)

- Если репозиторий (`--repo` для `--base-ref`, `--ebt-repo` для `--ebt-ref`) задан как URL или scp-адрес, ссылка берётся напрямую из него (`ref_source_kind: "remote"`).
- Если это локальный путь, AcceptBox вызывает `git remote get-url origin` и берёт ссылку из `origin` (`ref_source_kind: "origin"`), чтобы агент не мог сдвинуть локальную ветку и подделать ancestry. Короткие имена разрешаются сначала как `refs/heads/<ref>`, затем как `refs/tags/<ref>`.
- Если у локального репозитория нет remote `origin`, проверка идёт по самому локальному репозиторию с пометкой `ref_source_kind: "local"` в отчёте (слабая проверка). Передайте `--strict-refs` (только CLI, рекомендуется в CI), чтобы fail-closed отклонять запасной путь `"local"` с кодом `ref-source-local` (код `1`). В режиме `command` `--strict-refs` требует `--base-ref`. Любой другой сбой чтения `origin` (например, `safe.directory`) даёт `gate-error`.
- **Граница доверия:** URL `origin` хранится в `.git/config` локального checkout. Сверяйте `ref_source` и `ref_source_kind` в `accept.json`, а для полной гарантии запускайте приёмку вне досягаемости агента (CI) или передавайте URL в `--repo` / `--ebt-repo`.

### Политики приёмки (`--policy bugfix` и `--policy refactor`)

- **`--policy bugfix` (по умолчанию, только CLI):** проверяет цепочку `base → repro → head` (`--repro` авто-резолвится из `base..head`, если опущен; `repro` красный, `head` зелёный; в `witnessbox` также `base` красный, а `repro` падает по тем же инвариантам).
- **`--policy refactor` (только CLI, L24/L30):** запрещает `--repro`, работает в обоих режимах (`command` и `witnessbox`), проверяет ancestry `base → head` (`head-not-descendant`) и непустоту `base..head` (`refactor-empty`), автоматически защищает `.acceptbox.toml` плюс `[command].tests` (в `command`) или `ebt`/`tool/ebt_check` (в `witnessbox`) в `base..head` и требует, чтобы **оба** коммита `base` (`base-not-green`) и `head` (`head-not-green`) прошли (`green`). **`ACCEPTED` в политике `refactor` означает «регрессий по существующим проверкам нет»; сохранение поведения не проверяется** — это сказано в самом сообщении вердикта. В режиме `witnessbox` `coverage.checked` перечисляет все инварианты области (`--inv` или все `[inv.*]` из `ebt/spec.toml`); спецификация без инвариантов — `inputs-untrusted`.

```sh
acceptbox accept --policy refactor --base-ref main
```

### Вывод CLI

Человек читает итоговую строку, а не `accept.json`, поэтому всё, что меняет смысл вердикта, выводится в ней (L31):

```text
acceptbox: ACCEPTED [policy=refactor]: регрессий по существующим проверкам нет; сохранение поведения не проверяется (policy=refactor) (отчёт: /tmp/.../accept.json)
acceptbox: внимание: ebt/ закреплён по вершине --ebt-ref 'main' (1a2b3c4d5e6f), а не по одобренному --approved-tree (--pin-from-ref)
```

- `[policy=…]` выводится после результата всегда.
- Строки `acceptbox: внимание: …` добавляются при: `--pin-from-ref` (`ebt/` закреплён, а не одобрен), `ref_source_kind: "local"` у `--base-ref` / `--ebt-ref` (слабая проверка) и автоматически выбранном repro (`first-commit`). Те же тексты сохраняются в `accept.json` → `warnings`.

### Режим `command`

Минимальный запуск (при наличии `.acceptbox.toml` в `base`):

```sh
acceptbox accept --base-ref main
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

Минимальный локальный запуск (при `[witnessbox].ebt_ref = "main"` в `.acceptbox.toml`):

```sh
acceptbox accept --mode witnessbox --base-ref main --pin-from-ref
```

Явный запуск с внешней фиксацией:

```sh
acceptbox accept \
  --mode witnessbox \
  --base <полный-sha> \
  --repro <полный-sha> \
  --ebt-ref main \
  --ebt-sha <полный-sha> \
  --approved-tree <64-hex-sha256> \
  [--inv INV-1,INV-2] \
  [--base-ref main] \
  [--strict-refs] \
  [--repo /path/to/project] \
  [--head <полный-sha>] \
  [--ebt-repo /path/to/ebt-repo] \
  [--protect path/to/protect ...] \
  [--timeout 600] \
  [--env KEY[=VAL] ...] \
  [--out /tmp/accept-ebt-run]
```

В режиме `witnessbox` пути `ebt` и `tool/ebt_check` защищаются всегда. В политике `bugfix` коммиты `base` и `repro` должны быть `red` с находками `FALSE_PROOF` или `CONFIRMED_VIOLATION` хотя бы по одному целевому инварианту (из `--inv`, либо определённому автоматически по `base`), а `head` — `green`.

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

## Отображение отчётов (`render`)

Преобразует отчёт `accept.json` или `calibrate.json` формата `1` в читаемый Markdown (для комментариев PR и сводок CI) или автономную HTML5-страницу (только встроенный CSS, без внешних скриптов, CDN и шрифтов):

```sh
# Вывести Markdown в stdout
acceptbox render /tmp/accept-run/accept.json --format md

# Атомарно записать автономный HTML5-отчёт
acceptbox render /tmp/accept-run/accept.json --format html --out /tmp/accept-run/report.html
```

- **Чистая функция от JSON-отчёта:** `render` не запускает `git` или подпроцессы и не читает внешние логи прогонов с диска.
- **Видимость смысла вердикта (L31):** верхний баннер показывает `result`, `[policy=…]` (для `accept`), `mode`, `reason_code` и `message`; все `warnings` выводятся заметно сразу под баннером и никогда не сворачиваются.
- **Строгое экранирование:** все динамические значения (пути, сообщения, предупреждения, причины, коммиты, инварианты) экранируются через `html.escape(..., quote=True)` в HTML и с экранированием `|` и переводов строк в таблицах Markdown.
- **Атомарная запись и коды выхода:** без `--out` вывод идёт в `stdout`; с `--out <path>` файл пишется атомарно через временный файл в том же каталоге + `fsync` + `os.replace`. Код выхода `0` при успехе и `2` (в `stderr`, без создания `--out`) при отсутствующем/битом JSON, `format != 1`, отсутствующем родительском каталоге или лишних аргументах после `--`.

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
| `head-not-descendant` | `accept` (`refactor`; `bugfix` с авто-резолвом `--repro`) | `base` не является предком `head` |
| `protected-paths-changed` | `accept` | Защищённый путь (`.acceptbox.toml`, `protect`, `[command].tests` в `refactor` или `ebt`/`tool/ebt_check` в `witnessbox`) изменён |
| `repro-empty` | `accept` (`bugfix` + `command`) | Дифф `base..repro` пуст (или нет коммитов в `base..head` при авто-резолве) |
| `repro-ambiguous` | `accept` (`bugfix`, авто-`repro`) | Несколько коммитов в `base..head` помечены `AcceptBox-Role: repro` |
| `refactor-empty` | `accept` (`refactor`) | Дифф `base..head` пуст |
| `repro-outside-tests` | `accept` (`bugfix` + `command`) | `base..repro` меняет пути вне `--tests` |
| `repro-tests-modified` | `accept` (`bugfix` + `command`) | `repro..head` изменяет, переименовывает или удаляет файлы из `base..repro` |
| `base-green` / `base-report-invalid` / `base-red-wrong-reason` | `accept` (`bugfix` + `witnessbox`) | `base` не упал по ожидаемому инварианту |
| `base-not-green` | `accept` (`refactor`) | `base` не зелёный |
| `repro-green` / `repro-report-invalid` / `repro-red-wrong-reason` | `accept` (`bugfix`) | `repro` зелёный или упал не по нужной причине |
| `head-not-green` | `accept` | `head` не зелёный |
| `mismatch` | `calibrate` | Хотя бы один коммит `--green` / `--red` дал неожиданный исход |

### Коды `ERROR` (код `2`) и правило записи отчёта

- **Отчёт не создаётся:** ошибки синтаксиса CLI, `out-not-empty`, а также нехватка обязательных аргументов (`--tests`, команды после `--`, `--ebt-ref`, `--ebt-sha` / `--approved-tree` без `--pin-from-ref`) или смешивание флагов режимов, обнаруженные после чтения `base:.acceptbox.toml`. Если пустой каталог `--out` был создан текущим запуском, он удаляется.
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
