# План реализации: доверенные ветки для `--base-ref` и `--ebt-ref` (0.2.0)

> **Для исполнителя-агента:** выполнять по задачам, шаги отмечать чекбоксами (`- [ ]`).

**Цель:** закрыть оговорки о доверии из L26 и L27. Добавить опциональный флаг `--base-ref`, проверяющий, что `--base` лежит в основной ветке. Ветку для `--base-ref` и `--ebt-ref` брать из `origin` локального репозитория, а не из checkout, который может переписать агент.

**Архитектура:** одна функция в `git.py` определяет, откуда брать доверенную ветку: URL как есть, у локального пути — его `origin`, без `origin` — сам путь с пометкой `local`. Стадия `--base-ref` в `accept` идёт до чтения `.acceptbox.toml`: конфиг из непроверенного `base` не применяется. `_prepare_ebt` получает ветку из того же источника.

**Стек:** Python `>=3.11`, только стандартная библиотека, внешний `git`, тесты на `unittest`.

**Спецификация:** решения L10, L22, L26, L27 в `docs/decisions.md`. Выборы владельца от 2026-11-22:
1. если `--ebt-repo` или `--repo` — локальный путь, ветка берётся из его remote `origin`;
2. если `origin` нет, проверка идёт по локальной ветке с пометкой в отчёте;
3. `--base-ref` опциональный и задаётся только в CLI.

## Глобальные ограничения

- Runtime только на стандартной библиотеке, импорт модулей `witnessbox` запрещён (`CLAUDE.md`).
- Все вызовы git идут через `run_git` (`--no-replace-objects`). Fetch получает `-c core.hooksPath=/dev/null`, это уже делает `_fetch`.
- Сообщения, отчёты и документация — на русском, идентификаторы — на английском.
- `--base-ref` нельзя задать в `.acceptbox.toml`: конфиг читается из того же `base`, который проверяется. Неизвестный ключ `base_ref` и так отклоняется схемой L25, менять её не нужно.
- Журнал решений только дополняется.
- Проверка: `python3 -m unittest discover -s tests`, без пропусков.

## На что смотреть при ревью

1. **`origin` с относительным URL** (например, `../upstream.git`) отсчитывается от каталога локального репозитория, а не от каталога запуска. Тест в задаче 1.
2. **Поддельный `base` с ослабленным `.acceptbox.toml`.** При `--base-ref` и `base` вне основной ветки нужен REJECTED `base-not-on-ref`, причём конфиг не читается: `inputs.config` равен `null`, команда из конфига не запускается. Тест в задаче 2.
3. **Недоступный `origin`** (сеть, авторизация) даёт `gate-error` с отчётом, без зависания: `GIT_TERMINAL_PROMPT=0` уже задан в `base_env`. Отдельного теста не нужно: путь `GitError → gate-error` уже покрыт.
4. **Локальная ветка `main`, сдвинутая агентом**, не влияет на результат, если есть `origin`. Это тесты в задачах 2 и 3.
5. **`--repo` в виде URL**: ветка для `--base-ref` берётся из самого URL, `ref_source_kind = "remote"`. Тест в задаче 1, на уровне функции.

---

### Задача 1: источник доверенной ветки в `git.py`

**Файлы:**
- Изменить: `src/acceptbox/git.py`: перенести сюда проверку «URL или scp-адрес» из `config.py`, добавить функцию источника.
- Изменить: `src/acceptbox/config.py`: использовать `is_remote_source` из `git.py` вместо локального `_REMOTE_SOURCE_RE`.
- Тест: `tests/test_unit_and_verdict.py`, новый класс `TestTrustedRefSource`.

**Интерфейсы:**
- Создаёт: `is_remote_source(value: str) -> bool`. Регулярное выражение то же, что сейчас в `config._REMOTE_SOURCE_RE`: `[A-Za-z][A-Za-z0-9+.-]*://|[^/]+:`.
- Создаёт: `trusted_ref_source(source: str, env: dict[str, str]) -> tuple[str, str]`. Возвращает пару `(источник_для_fetch, kind)`, где `kind` принимает значения `"remote" | "origin" | "local"`.

- [ ] **Шаг 1: перенести `is_remote_source` в `git.py`** и заменить в `config.py` выражение `_REMOTE_SOURCE_RE.match(x)` на `is_remote_source(x)`.

- [ ] **Шаг 2: реализовать `trusted_ref_source`.**
  - `is_remote_source(source)` → `(source, "remote")`.
  - Иначе выполнить `run_git(Path(source), env, "remote", "get-url", "origin")`.
  - Если git вернул ошибку, то есть remote нет, → `(source, "local")`.
  - Если URL `origin` не удалённый и не абсолютный, склеить его с абсолютным путём `source`.
  - Результат → `(url, "origin")`.

- [ ] **Шаг 3: тесты.**

```python
class TestTrustedRefSource(unittest.TestCase):
    def test_kinds(self) -> None:
        env = base_env()
        self.assertEqual(trusted_ref_source("https://h/r.git", env), ("https://h/r.git", "remote"))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            self.assertEqual(trusted_ref_source(str(repo), env), (str(repo), "local"))
            _git(repo, "remote", "add", "origin", "../upstream.git")
            self.assertEqual(trusted_ref_source(str(repo), env),
                             (str(Path(tmp) / "upstream.git"), "origin"))
```

Сравнивать нормализованные пути: на macOS `tmp` может отличаться от разрешённого пути из-за `/private`. Достаточно `os.path.normpath` и `os.path.join` без `resolve`, если реализация тоже не вызывает `resolve`.

- [ ] **Шаг 4:** выполнить `python3 -m unittest tests.test_unit_and_verdict tests.test_defaults_and_config`. Ожидается OK: схема конфига ведёт себя как раньше.

---

### Задача 2: флаг `--base-ref` в `accept`

**Файлы:**
- Изменить: `src/acceptbox/cli.py`: аргумент в `_add_accept_flags`, валидация на стадии 1, новая стадия между `ensure_commit(... base)` и `load_config_from_commit`, поля отчёта.
- Изменить: `src/acceptbox/verdict.py`: функция отказа.
- Тест: `tests/test_defaults_and_config.py`, новый класс `TestBaseRef`.

**Интерфейсы:**
- Использует: `trusted_ref_source` (задача 1), существующие `fetch_ref(repo, env, source, ref) -> str | None` и `is_ancestor(repo, env, ancestor, descendant) -> bool`.
- Создаёт: `verdict.base_not_on_ref(message: str) -> Verdict`, то есть `rejected("base-not-on-ref", message)`.
- Поля отчёта `accept.json`:
  - `inputs.base_ref`: строка или `null`;
  - `checks.base_ref`: `{"status": "skipped"}` без флага, иначе `{"status": "ok" | "failed", "ref": str, "ref_source": str, "ref_source_kind": "remote" | "origin" | "local", "ref_commit": str | null}`.

- [ ] **Шаг 1: аргумент и валидация.** `--base-ref` (необязательный, `help="ветка основной ветки, предком которой должен быть --base (только CLI)"`). На стадии 1 проверить через `validate_ref(args.base_ref, flag="--base-ref")`. Флаг общий для обоих режимов, в проверки смешивания режимов его не добавлять.

- [ ] **Шаг 2: стадия проверки** сразу после успешного `ensure_commit(clone, git_env, repo, base)` и до `load_config_from_commit`:
  - `source, kind = trusted_ref_source(repo, git_env)`;
  - `ref_commit = fetch_ref(clone, git_env, source, base_ref)`;
  - `None` → `finish(base_not_on_ref("ссылка --base-ref <ref> не найдена в <source>"))`;
  - если не `is_ancestor(clone, git_env, base, ref_commit)` → `finish(base_not_on_ref("--base <sha> не является предком --base-ref <ref>"))`;
  - перед каждым `finish` заполнить `checks["base_ref"]`.
  
  `GitError` уже превращается в `gate-error` существующим обработчиком.

- [ ] **Шаг 3: тесты.** Вспомогательная функция в файле тестов:

```python
def _add_origin(repo: Path) -> Path:
    upstream = repo.parent / "upstream.git"
    _git(repo.parent, "clone", "-q", "--bare", "--no-local", str(repo), str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    return upstream
```

Сценарии, все на `_make_command_fixture` с `.acceptbox.toml` в `base`. Локальную `main` двигать через `git update-ref refs/heads/main <sha>`: `git branch -f` для текущей ветки не работает. `--head` передавать явно, потому что `HEAD` смотрит на `main` и сдвинется вместе с ней.
- `test_base_on_origin_main_accepted`: `_add_origin`, затем `accept --base-ref main ...`. Ожидается: код `0`, `checks.base_ref.status == "ok"`, `ref_source_kind == "origin"`.
- `test_forged_base_rejected_before_config`: после `_add_origin` создать от `base` коммит `forged` с `.acceptbox.toml`, где `argv = [PYTHON, "-c", "open('MARKER','w')"]`. Поверх него сделать repro и head, затем `git update-ref refs/heads/main <forged>`. Ожидается:

```python
self.assertEqual(res.returncode, 1)
self.assertEqual(data["reason_code"], "base-not-on-ref")
self.assertIsNone(data["inputs"]["config"])
self.assertEqual(data["runs"]["repro"]["status"], "skipped")
```

- `test_without_base_ref_check_skipped`: без флага `checks.base_ref == {"status": "skipped"}`, `inputs.base_ref is None`.
- `test_no_origin_falls_back_to_local_ref`: без `_add_origin` и с `--base-ref main` ожидается код `0` и `ref_source_kind == "local"`.

- [ ] **Шаг 4:** выполнить `python3 -m unittest tests.test_defaults_and_config`. Ожидается OK.

---

### Задача 3: `--ebt-ref` из `origin` локального `--ebt-repo`

**Файлы:**
- Изменить: `src/acceptbox/cli.py`: `_prepare_ebt` и блок `inputs.ebt` в `accept` и `calibrate`.
- Тест: `tests/test_defaults_and_config.py`, новый метод рядом с `test_witnessbox_defaults_ebt_repo_head_out`.

**Интерфейсы:**
- Использует: `trusted_ref_source` (задача 1).
- Меняет: `_prepare_ebt(...) -> tuple[Path, str, str, str, str]`, дополнительно возвращаются `ref_source` и `ref_source_kind`. Клон и `ensure_commit(--ebt-sha)` по-прежнему идут из `ebt_repo`. Из `ref_source` берётся только `fetch_ref`.
- Поля отчёта: в `checks.ebt_trust` при `ok` или `failed` и в `inputs.ebt` добавить `ref_source` и `ref_source_kind`.

- [ ] **Шаг 1:** в `_prepare_ebt` заменить `fetch_ref(ebt_clone, git_env, ebt_repo, ebt_ref)` на вызов с `ref_source` из `trusted_ref_source(ebt_repo, git_env)`. Обновить оба места вызова и отчёты.

- [ ] **Шаг 2: тест** `test_ebt_ref_taken_from_origin_not_local_branch`:
  - взять `_make_witnessbox_in_repo_fixture` и выполнить `_add_origin(repo)`;
  - создать в `repo` от `base` боковой коммит `side`, меняющий `ebt/spec.toml`, и получить `approved = pin(side)`;
  - выполнить `git update-ref refs/heads/main <side>`, `--head` передать явно;
  - запустить `accept --mode witnessbox --ebt-ref main --ebt-sha <side> --approved-tree <approved>`.
  
  Ожидается код `1`, `reason_code == "inputs-untrusted"`, `checks.ebt_trust.ref_source_kind == "origin"`. Без `origin` тот же запуск проходил бы проверку предка: это проверяет пометку `local` в старых тестах.

- [ ] **Шаг 3:** выполнить `python3 -m unittest discover -s tests`. Ожидается OK, все 80 прежних тестов проходят без правок фикстур, потому что без `origin` работает запасной путь `local`.

---

### Задача 4: документация, решение L29, версия 0.2.0

**Файлы:**
- Изменить: `README.md`, `docs/onboarding.md`, `docs/decisions.md`, `pyproject.toml` (`version = "0.2.0"`), `src/acceptbox/__init__.py` (запасное значение версии `"0.2.0"`).

- [ ] **Шаг 1: README.**
  - Флаг `[--base-ref main]` в примерах `accept` обоих режимов.
  - В пунктах о границе доверия `--base` и о `--ebt-repo`: ветка берётся из `origin` локального репозитория; без `origin` проверка идёт по локальной ветке, в отчёте `ref_source_kind: "local"`.
  - В «Коды выхода» добавить `base-not-on-ref` (код `1`).
  - Строку 174 со ссылками заменить на абсолютные: `https://github.com/kaufd/acceptbox/blob/main/docs/onboarding.md` и `.../docs/decisions.md`.
  - Установка: `acceptbox==0.2.0`.

- [ ] **Шаг 2: onboarding.** В быстром старте и в разделе witnessbox рекомендовать `--base-ref main` и remote `origin` у рабочего checkout. Заменить оговорку о слабой проверке на описание `ref_source_kind`.

- [ ] **Шаг 3: L29 в конец журнала.**

```markdown
## L29 — Доверенные ветки: `--base-ref` и `--ebt-ref` из `origin` (уточняет L10, L22, L26, L27)
2026-11-22. Принято владельцем. Опциональный флаг `--base-ref <ветка>` (только CLI, не в `.acceptbox.toml`) проверяет до чтения конфига, что `--base` — предок ветки; отказ — `base-not-on-ref` (код `1`). Ветка для `--base-ref` и `--ebt-ref` берётся из доверенного источника: URL — как есть, локальный путь — из его remote `origin`; без `origin` — из самого локального репозитория с пометкой `ref_source_kind: "local"` в отчёте (слабая проверка, как в L27). Значение по умолчанию `--ebt-repo` из L22 не меняется. Пересмотреть, если: потребуется запрет запасного пути `local` или `--base-ref` в `calibrate`.
```

- [ ] **Шаг 4:** выполнить `python3 -m unittest discover -s tests`. Ожидается OK, без пропусков.

---

### Задача 5: удалить слитую ветку `feat/l22-l24`

Выполнить только после подтверждения владельца в момент исполнения: удаление ветки на GitHub — внешнее действие.

- [ ] **Шаг 1:** `git branch -d feat/l22-l24`. Ветка слита, поэтому `-d` достаточно.
- [ ] **Шаг 2:** `git push origin --delete feat/l22-l24`.
- [ ] **Шаг 3:** `git ls-remote --heads origin feat/l22-l24` ничего не выводит.

## Вне рамок плана

- `--base-ref` в `calibrate`: калибровка не выдаёт вердикт о работе агента.
- Выпуск 0.2.0 (тег, GitHub Release, PyPI): отдельный шаг с отдельным подтверждением, по образцу выпуска 0.1.0.
- Политика рефакторингов (L24) и `--prepare`: после практического использования.
