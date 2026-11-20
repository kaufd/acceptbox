# Подключение нового проекта к AcceptBox

AcceptBox проверяет цепочку коммитов `base → repro → head` в изолированном временном клоне (не переключая рабочий checkout) и записывает машиночитаемый вердикт `accept.json`.

## 1. Установка и проверка окружения

```sh
uv tool install acceptbox==0.1.0
# Опционально (нужно только для --mode witnessbox):
uv tool install 'witnessbox>=0.10.0'

acceptbox --installation
```

`acceptbox --installation` возвращает код `0`, если работают `git` и `acceptbox`, даже когда `witnessbox` не установлен (`witnessbox.status = "missing"`).

## 2. Быстрый старт в режиме `command` (без WitnessBox)

В режиме `command` коммит `repro` добавляет репро-тест внутри путей `--tests`, а коммит `head` исправляет код, не изменяя, не переименовывая и не удаляя файлы репро-теста.

```sh
acceptbox accept \
  --mode command \
  --repo /path/to/project \
  --base <base_sha> \
  --repro <repro_sha> \
  --head <head_sha> \
  --tests tests/ \
  --protect pyproject.toml \
  --out /tmp/accept-run \
  -- python3 -m unittest discover -s tests
```

> **Важно про подготовку зависимостей (wrapper-скрипт):** каждый коммит запускается в чистом detached-worktree без кэшированных изменений рабочего дерева. Если проекту нужна установка зависимостей или сборка перед запуском тестов, оформите скрипт-обёртку после `--`. Скрипт **обязан** возвращать код выхода `>= 2` при сбое подготовки среды/зависимостей и код `1` **только** при провале самих тестов — иначе сбой среды будет ошибочно классифицирован как `red` вместо `gate-error` (код `2`).

## 3. Подключение режима `witnessbox` (EBT)

1. Убедитесь, что каталог `ebt/` утверждён владельцем и находится в основной ветке репозитория (`kaufd/witnessbox`, версия `>=0.10.0`).
2. Зафиксируйте отпечаток утверждённого дерева `ebt/`:
   ```sh
   APPROVED_TREE=$(acceptbox pin --ebt-repo /path/to/project --ebt-sha <ebt_sha>)
   ```
3. Запустите приёмку (проверяются доверие `ebt/`, ancestry, защита `ebt` и `tool/ebt_check`, красные `base` и `repro` по `--inv`, и зелёный `head`):
   ```sh
   acceptbox accept \
     --mode witnessbox \
     --repo /path/to/project \
     --base <base_sha> \
     --repro <repro_sha> \
     --head <head_sha> \
     --inv INV-1 \
     --ebt-repo /path/to/project \
     --ebt-ref main \
     --ebt-sha <ebt_sha> \
     --approved-tree "$APPROVED_TREE" \
     --out /tmp/accept-ebt-run
   ```

## 4. Калибровка чувствительности (`calibrate`)

Для проверки эталонных коммитов и мутантов (без проверки ancestry и без создания `accept.json`) используйте `acceptbox calibrate`:

```sh
acceptbox calibrate \
  --mode command \
  --repo /path/to/project \
  --green <good_sha> \
  --red <mutant_sha> \
  --out /tmp/calibrate-run \
  -- python3 -m unittest discover -s tests
```

Отчёт записывается атомарно в `<out>/calibrate.json`. Коды выхода: `0` — все ожидания совпали (`CALIBRATED`), `1` — расхождение (`FAILED`), `2` — ошибка среды/CLI (`ERROR`).
