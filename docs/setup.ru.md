# Настройка проекта и шаблоны

[English](setup.md) · **Русский**

В отличие от WitnessBox (который генерирует многофайловый каталог `ebt/` командой `witnessbox scaffold`), AcceptBox требует минимальный набор файлов в базовой ветке. Это руководство — аналог scaffold для AcceptBox: какие файлы нужны проекту, готовые рецепты по стекам, контракт скрипта-обёртки и готовый шаблон правил `AGENTS.md` для каждого ИИ-агента, работающего в репозитории.

Чтобы поручить настройку ИИ-агенту (с остановкой на утверждение человеком перед коммитом), передайте ему [инструкцию для агента](agent.ru.md).

## Какие файлы нужны подключённому проекту

| Файл | Обязателен? | Назначение |
|---|---|---|
| `.acceptbox.toml` | Рекомендуется | Проектные настройки (`mode`, `timeout`, `protect`, `env`, `[command]`, `[witnessbox]`). Читается строго из `base` и защищается автоматически. |
| `scripts/acceptbox-test.sh` (или аналог) | Только если чистому worktree нужна подготовка | Устанавливает зависимости / собирает проект перед тестами, соблюдая контракт кодов выхода (`0` успех, `1` провал тестов, `>= 2` ошибка среды/сборки). Добавьте его путь в `protect`. |
| `.acceptbox/AGENTS.md` (или секция в корневом `AGENTS.md`) | Рекомендуется для работы с ИИ | Правила для каждого агента, который чинит баги в этом репозитории: дисциплина коммитов `base → repro → head`, защищённые пути, команды самопроверки и формат отчёта в PR. Подключается ссылкой из корневых `AGENTS.md` и `CLAUDE.md`. |

## Рецепты по стекам

### Python (`unittest` или `pytest`)

Для `unittest` из стандартной библиотеки (установка зависимостей не нужна):

```toml
format = 1
mode = "command"
timeout = 300
protect = ["pyproject.toml"]
env = ["PYTHONPATH=src"]

[command]
tests = ["tests/"]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
```

Для `pytest` (`pytest` возвращает `0` при успехе, `1` при падении тестов и `2`–`5` при ошибках запуска/сбора/отсутствии тестов — это сразу совпадает с контрактом AcceptBox):

```toml
format = 1
mode = "command"
timeout = 300
protect = ["pyproject.toml", "uv.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
uv sync --frozen --no-progress >/dev/null || exit 2
uv run pytest
```

### TypeScript / JavaScript (`vitest` или `jest` с `bun` / `npm` / `pnpm`)

`.acceptbox.toml`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["package.json", "bun.lock", "vitest.config.ts", "scripts/acceptbox-test.sh"]

[command]
tests = ["src/**/*.test.ts", "tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
bun install --frozen-lockfile || exit 2
exec bunx vitest run
```

*(Для npm: замените `bun install --frozen-lockfile` на `npm ci || exit 2`, а `bunx vitest run` на `npx vitest run` или `npx jest`.)*

### Dart / Flutter

`.acceptbox.toml`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pubspec.yaml", "pubspec.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["test/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
flutter pub get >/dev/null || exit 2
exec flutter test --no-pub
```

### Go

`go test` возвращает код `1` и при ошибке компиляции, и при падении тестов. Сначала скомпилируйте тесты без запуска, чтобы ошибка сборки давала код `2` (`gate-error`), а не ложный `red`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["go.mod", "go.sum", "scripts/acceptbox-test.sh"]

[command]
tests = ["**/*_test.go"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
go test -run='^$' ./... >/dev/null || exit 2
exec go test ./...
```

### Rust (`cargo test`)

`cargo test` возвращает `101` при падении тестов (AcceptBox классифицировал бы это как `gate-error`, а не `red`!). Используйте обёртку, которая сначала компилирует (`exit 2` при ошибке сборки), а код падения тестов (`101`) переводит в `1`:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["Cargo.toml", "Cargo.lock", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
cargo test --no-run || exit 2
set +e
cargo test
rc=$?
if [ "$rc" -eq 0 ]; then
  exit 0
elif [ "$rc" -eq 101 ]; then
  exit 1
else
  exit 2
fi
```

### JVM (Gradle / Maven)

Для Gradle:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "scripts/acceptbox-test.sh"]

[command]
tests = ["src/test/"]
argv = ["./scripts/acceptbox-test.sh"]
```

`scripts/acceptbox-test.sh` (`chmod +x`):

```sh
#!/bin/sh
set -eu
./gradlew testClasses --quiet || exit 2
exec ./gradlew test
```

*(Для Maven запускайте `mvn -q test-compile || exit 2` перед `mvn -q test`, чтобы ошибки компиляции возвращали `2`, а не `1`.)*

### Совместный проект `command` + `witnessbox`

`.acceptbox.toml` позволяет хранить секции обоих режимов рядом без конфликта:

```toml
format = 1
mode = "command"
timeout = 600
protect = ["pyproject.toml", "scripts/acceptbox-test.sh"]

[command]
tests = ["tests/"]
argv = ["./scripts/acceptbox-test.sh"]

[witnessbox]
ebt_ref = "main"
```

Для обычных багфиксов запускайте `acceptbox accept --base-ref main` в режиме `command` (или `--policy refactor` для рефакторингов, или `--policy feature` для фич), а для приёмки по утверждённым EBT-инвариантам — `acceptbox accept --mode witnessbox --base-ref main --pin-from-ref`.

## Контракт скрипта-обёртки

Когда `[command].argv` вызывает скрипт-обёртку:

1. **Рабочий каталог:** корень чистого detached Git worktree для проверяемого коммита (`repro` или `head`). Нельзя рассчитывать на `node_modules/`, `.venv/` или `build/` из рабочего checkout разработчика.
2. **Очищенное окружение:** AcceptBox запускает команды с `shell=False` и убирает переменные окружения вне минимального allowlist и явного списка `env`. Передавайте нужные нечувствительные переменные в `env` в `.acceptbox.toml`.
3. **Строгие коды выхода:**
   - `0` → `green` (тесты запустились и прошли).
   - `1` → `red` (тесты запустились и упали на проверках).
   - `>= 2` → `gate-error` (сбой установки зависимостей, ошибка компиляции, отсутствует раннер, не найдено ни одного теста и т. д.). **Никогда** не возвращайте `1` при сбое среды или сборки — иначе AcceptBox примет сломанную среду за валидный падающий `repro`.
4. **Защита скрипта:** всегда включайте путь к скрипту-обёртке и lock-файлы в `protect = [...]` в `.acceptbox.toml`, чтобы ветка задачи не могла подменить раннер.
5. **Закоммичен и исполняем:** коммитьте скрипт с `chmod +x` (или вызывайте `["sh", "scripts/acceptbox-test.sh"]`).

## Шаблон правил для агентов проекта (`.acceptbox/AGENTS.md`)

По аналогии с `ebt/AGENTS.md` в WitnessBox скопируйте блок ниже в `.acceptbox/AGENTS.md` в вашем репозитории (при желании добавьте `.acceptbox/AGENTS.md` в `protect` в `.acceptbox.toml`) и свяжите его с корневыми инструкциями:

- В корневой `AGENTS.md`: `AcceptBox rules for this project: .acceptbox/AGENTS.md`
- В корневой `CLAUDE.md`: `@.acceptbox/AGENTS.md`

```markdown
# Правила для ИИ-агентов: AcceptBox в этом проекте

Этот проект использует AcceptBox для проверки цепочек багфиксов (`base → repro → head`) в
изолированных Git worktree. Правила ниже обязательны для каждого агента, работающего в репозитории.

## Жёсткие правила

1. **Понимайте область применимости.** Используйте `base → repro → head` (`--policy bugfix`) для
   **багфиксов** (и инвариантов, утверждённых человеком в `--mode witnessbox`). Используйте
   `--policy refactor` (`base` зелёный → `head` зелёный без правок тестов) для **рефакторингов**.
   Не фабрикуйте фиктивный `repro`-тест для новой фичи без утверждённой человеком спецификации.
2. **Строгая двухкоммитная структура багфикса (`base → repro → head`):**
   - Стартуйте от чистого коммита `base` в основной ветке (например, `main`).
   - **Коммит 1 (`repro`):** меняйте **только** тестовые файлы, попадающие под `[command].tests` в
     `.acceptbox.toml`. Не трогайте код приложения и защищённые пути. Тест должен честно
     воспроизводить баг и падать с кодом выхода `1`.
   - **Коммит 2 (`head`):** исправьте баг в коде приложения так, чтобы тесты проходили с кодом `0`.
     **Не изменяйте, не переименовывайте и не удаляйте** файлы, затронутые в `repro`.
3. **Не редактируйте защищённые файлы в ветке задачи.** Никогда не меняйте `.acceptbox.toml`,
   скрипты-обёртки тестов, lock-файлы из `protect`, а в режиме `witnessbox` — `ebt/` и
   `tool/ebt_check` в ветке багфикса: AcceptBox отклонит ветку с `protected-paths-changed`.
   Изменения `.acceptbox.toml` и тестовой инфраструктуры утверждает человек в базовой ветке.
4. **Не обходите гейт.** Не ослабляйте и не пропускайте тесты, не подделывайте `--base`, не
   двигайте локальные ветки и не переписывайте `.git/config` / `origin` ради обхода `--base-ref`,
   и не возвращайте код `1` из скрипта-обёртки при сбое среды или сборки.
5. **Относитесь к `REJECTED` / `ERROR` как к реальному сигналу.** Прочитайте `reason_code`,
   `message` и логи прогона из `accept.json`. Устраните причину (неверная разбивка коммитов, тест
   не воспроизводит баг, неполный фикс), а не обходите проверку.
6. **Отразите результат приёмки в Pull Request.** Добавьте секцию `## Acceptance`:
   - вывод `acceptbox render <путь к accept.json> --format md` (вердикт, политика, предупреждения,
     SHA `base`/`repro`/`head` и `ref_source_kind`); если `render` недоступен — SHA, вердикт с кодом
     выхода, `ref_source_kind` и все строки `acceptbox: warning:`;
   - список **Not verified** (поведение вне репро-теста, ручные/UI-сценарии или пометка, что
     локальный прогон шёл с `ref_source_kind: "local"` и ждёт проверки в CI).
   Если запустить AcceptBox локально не удалось, прямо укажите это и причину.
7. **Фичи используют `--policy feature`; никогда не изменяйте существующие тестовые файлы**
   (новые фикстуры кладите в новые файлы; если необходимо менять общий хелпер, остановитесь и
   спросите человека).
8. **Проверка фичи и UI-сценарии:** следуйте
   [docs/agent.ru.md](https://github.com/kaufd/acceptbox/blob/main/docs/agent.ru.md#7-проверка-фичи-для-оркестратора-или-агента-проверяющего),
   раздел «Проверка фичи».

## Команда локальной самопроверки

Запускайте из корня репозитория после коммита (незакоммиченные правки игнорируются):

```sh
# Багфикс (command):
acceptbox accept --base-ref main

# Рефакторинг (command):
acceptbox accept --policy refactor --base-ref main

# Фича (command):
acceptbox accept --policy feature --base-ref main

# Режим WitnessBox (опционально --inv, опционально --policy refactor|feature):
acceptbox accept --mode witnessbox --base-ref main --pin-from-ref
```

Примечание: локальный запуск — это самопроверка; окончательный доверенный вердикт выдаёт человек
или CI, запускающий AcceptBox вне checkout агента (или передающий `--repo` как URL).
```

## После настройки: проверьте работоспособность

1. **Ревью и коммит человеком:** проверьте `.acceptbox.toml`, скрипт-обёртку (если есть) и `.acceptbox/AGENTS.md`, затем закоммитьте их в `main` (и отправьте в `origin`, если настроен remote).
2. **Проверка окружения:** выполните `acceptbox --installation` (код выхода `0`).
3. **Калибровка чувствительности:** убедитесь, что конфиг работает в чистом detached-worktree и различает зелёные и красные коммиты с помощью `acceptbox calibrate` (или во временном worktree — см. стадию 4 в [инструкции для агента](agent.ru.md)):
   ```sh
   acceptbox calibrate --base "$(git rev-parse HEAD)" --green "$(git rev-parse HEAD)"
   ```
   Убедитесь, что код выхода `0` (`CALIBRATED`). Затем проверьте временный коммит с внесённым дефектом через `--red`, чтобы убедиться, что он даёт код `1` (`red`), а не `2` (`gate-error`).
