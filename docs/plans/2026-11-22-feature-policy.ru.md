# План реализации: политика `--policy feature` и проверка фичи оркестратором

> Только на русском по решению владельца (английской версии нет, поэтому переключателя языка нет).
>
> **Для агента-исполнителя:** выполняйте задачу за задачей, отмечая шаги чекбоксами (`- [ ]`).

**Цель:** дать оркестратору простой способ проверить готовую фичу. Первая часть: `acceptbox accept --policy feature` отвечает, проходят ли тесты на `head` и не тронуты ли существующие тесты. Вторая часть, если у проекта есть UI: агент-проверяющий составляет текстовые сценарии, человек их одобряет, агент проходит их вживую и отдаёт отчёт.

**Архитектура:** `feature` — третья политика рядом с `bugfix` и `refactor`, ортогональная `--mode`. Она переиспользует ветку `refactor` (запрет `--repro`, проверка предка `base → head`, непустой дифф). Различия с `refactor`: только один прогон (`head`); `[command].tests` не защищаются целиком, вместо этого действует правило «существующие тесты не менять, хотя бы один новый тест добавить». Проверка UI в код AcceptBox не входит. Это процедура для агента в `docs/agent.md`.

**Стек:** Python `>=3.11`, только стандартная библиотека, внешний `git`, тесты на `unittest`.

**Спецификация:** обсуждение с владельцем от 2026-11-22. Решения владельца:
1. Флоу максимально простой, в том числе по коду.
2. Проверка UI опциональна.
3. Сценарии UI человек одобряет текстом («открыть X, ввести Y, увидеть Z»).
4. Окружение для UI агент донастраивает сам. Скриптами заранее его не подготовить, а отличать сбой окружения от падения теста умеет только агент.
5. Без повторных прогонов.
6. Для тестов фичи выбран вариант `--policy feature`.

Опорные решения: L24, L30, L31, а также L33 (английский вывод CLI, сейчас в индексе, не закоммичен).

## Глобальные ограничения

- Только стандартная библиотека в рантайме. Импорт модулей `witnessbox` запрещён (`AGENTS.md`).
- Все вызовы git идут через `run_git` (`--no-replace-objects`, `-c diff.renames=false --no-renames -z`).
- `--policy` задаётся только в CLI; ключ `policy` в `.acceptbox.toml` уже отклоняется схемой L25. Схему не меняем.
- Всё, что меняет смысл вердикта, печатается в строке CLI, а не только в `accept.json` (L31).
- Сообщения CLI и отчёта на английском (L33).
- Поведение `bugfix` и `refactor` не меняется: существующие тесты остаются зелёными без правок.
- Документация на двух языках в одном изменении (`AGENTS.md`). Журнал решений только дополняется: новая запись **L34** в `docs/decisions.md` и `docs/decisions.ru.md`.
- Проверка: `python3 -m unittest discover -s tests`, ноль пропусков.
- Работа строится поверх незакоммиченной в индексе работы L33. Перед стартом убедитесь, что L33 закоммичен или вы работаете поверх него. Номера строк ниже даны по состоянию с L33.

## Семантика `--policy feature` (зафиксировано)

| Стадия | `command` | `witnessbox` |
|---|---|---|
| `--repro` | запрещён → exit `2` (`--repro is not used in feature policy`) | так же |
| Предок | `base` — предок `head`, иначе `head-not-descendant` | так же |
| Защищённые пути | `.acceptbox.toml` + `protect`; `[command].tests` **не** добавляются целиком | `.acceptbox.toml` + `protect` + `ebt`, `tool/ebt_check` |
| Стадия 9 | по порядку: `feature-empty` → `existing-tests-modified` → `feature-no-tests` | только `feature-empty` |
| Прогоны | только `head`, должен быть `green` (`head-not-green`) | так же; `coverage.checked` = все инварианты области (`--inv` или все `[inv.*]`) |
| `ACCEPTED` | сообщение с оговоркой (см. ниже) | так же |

Определения для `command` (все пути внутри `[command].tests`, дифф `base..head`):
- `tests_modified` — любые изменения, кроме добавления (`--diff-filter=a`): правка, удаление, смена типа. Переименование при `--no-renames` распадается на удаление и добавление, поэтому тоже попадает сюда.
- `tests_added` — только добавленные файлы (`--diff-filter=A`).

Причины отказа (exit `1`):
- `feature-empty` — `diff base..head is empty: feature changes no files`.
- `existing-tests-modified` — `feature modifies existing test files: <paths>`.
- `feature-no-tests` — `feature adds no new test files under --tests`.

Сообщение `ACCEPTED`:
`head is green and existing checks are unchanged; feature correctness against the intended behaviour is not verified (policy=feature)`

## Фокус ревью

1. **Переименование существующего теста** (`git mv tests/test_app.py tests/test_app2.py`) даёт `existing-tests-modified`, а не «добавлен новый тест». Тест в задаче 2.
2. **Правка общего хелпера или фикстуры внутри `tests/`** (например, `tests/helpers.py`) — тоже `existing-tests-modified`. Это намеренная строгость: новые фикстуры кладутся в новые файлы, а изменение общих — точка остановки для человека. Тест в задаче 2, правило в документации (задача 3).
3. **Тест положен вне `[command].tests`** (например, `src/test_triple.py`) даёт `feature-no-tests`. Тест в задаче 2.
4. **В строке CLI видна оговорка** `not verified (policy=feature)` (L31). Тест в задаче 2.
5. **`witnessbox` + `feature`** работает без правил для тест-файлов, а `coverage.checked` перечисляет все инварианты спецификации. Тест в задаче 2.

---

### Задача 1: правила вердикта и фильтр диффа

**Файлы:**
- Изменить: `src/acceptbox/verdict.py`
- Изменить: `src/acceptbox/git.py:324-333` (`changed_paths`)
- Тест: `tests/test_unit_and_verdict.py`, новый класс `TestFeatureVerdict`

**Интерфейсы:**
- Создаёт: `feature_empty_verdict(*, head_changed: list[str]) -> Verdict | None`.
- Создаёт: `feature_tests_verdict(*, tests_added: list[str], tests_modified: list[str]) -> Verdict | None`. Сначала проверяет `tests_modified` (`existing-tests-modified`, пути отсортированы), затем пустой `tests_added` (`feature-no-tests`).
- Меняет: `accepted(policy)` — ветка `"feature"` с сообщением из раздела «Семантика».
- Меняет: `run_verdict(...)` — условие `if policy == "refactor":` становится `if policy != "bugfix":`. Для `feature` прогон `base` не запускается, поэтому ветка `base` там недостижима, и код подходит как есть.
- Меняет: `changed_paths(repo, env, older, newer, pathspecs=None, *, diff_filter: str | None = None) -> list[str]`. Если `diff_filter` задан, сразу после `--no-renames` добавляется `f"--diff-filter={diff_filter}"`.

- [x] **Шаг 1: реализовать функции** в `verdict.py`, тексты сообщений — из раздела «Семантика».
- [x] **Шаг 2: добавить `diff_filter`** в `changed_paths` и обновить docstring (`"a"` — всё, кроме добавлений; `"A"` — только добавления).
- [x] **Шаг 3: юнит-тесты**

```python
class TestFeatureVerdict(unittest.TestCase):
    def test_feature_rules_order_and_messages(self) -> None:
        # стиль файла: модуль импортирован как `verdict`
        self.assertEqual(verdict.feature_empty_verdict(head_changed=[]).reason_code, "feature-empty")
        self.assertIsNone(verdict.feature_empty_verdict(head_changed=["app.py"]))
        v = verdict.feature_tests_verdict(tests_added=["tests/n.py"],
                                          tests_modified=["tests/b.py", "tests/a.py"])
        self.assertEqual((v.reason_code, v.exit_code), ("existing-tests-modified", 1))
        self.assertIn("tests/a.py, tests/b.py", v.message)
        self.assertEqual(verdict.feature_tests_verdict(tests_added=[], tests_modified=[]).reason_code,
                         "feature-no-tests")
        self.assertIsNone(verdict.feature_tests_verdict(tests_added=["tests/n.py"], tests_modified=[]))

    def test_feature_accepted_message_and_head_only_runs(self) -> None:
        self.assertIn("not verified (policy=feature)", verdict.accepted("feature").message)
        green = RunOutcome("green", 0, "ok", 1, None, None)
        red = RunOutcome("red", 1, "fail", 1, None, None)
        self.assertEqual(verdict.evaluate_runs([("head", green)], policy="feature").result, "ACCEPTED")
        self.assertEqual(verdict.evaluate_runs([("head", red)], policy="feature").reason_code,
                         "head-not-green")
```

- [x] **Шаг 4: запустить**

Run: `python3 -m unittest tests.test_unit_and_verdict -v`
Expected: все тесты PASS, включая существующие для `refactor`.

---

### Задача 2: подключение политики в `accept`

**Файлы:**
- Изменить: `src/acceptbox/cli.py`
- Изменить: `src/acceptbox/render.py:205-206` (`preferred_map`)
- Тест: `tests/test_policy_and_strict_refs.py`, новые классы `TestFeaturePolicyCommand` и `TestFeaturePolicyWitnessbox`

**Интерфейсы:**
- Потребляет: всё из задачи 1.
- Создаёт: в `accept.json` проверку `checks["feature"] = {"status": "ok" | "failed", "changed_base_head": [...], "tests_added": [...], "tests_modified": [...]}`. В `witnessbox` — только `status` и `changed_base_head`.
- Создаёт: константу `FEATURE_SKIP_REASON = "not run in feature policy"` (причина для пропущенных `base` и `repro`).

Правки в `cli.py` (по месту, номера строк — ориентир):

- [x] **Шаг 1: argparse** (`:1215-1217`): `choices=("bugfix", "refactor", "feature")`, в help добавить `feature (head green, existing tests unchanged, new tests required; CLI-only, L34)`.
- [x] **Шаг 2: стадия 1** (`:286-293`): ветку `else` оставить общей для `refactor` и `feature`, сообщение — `f"--repro is not used in {policy} policy"`.
- [x] **Шаг 3: начальные `runs` и `checks`** (`:361-369`) и их повтор после загрузки конфига (`:526-536`):
  - при `policy == "feature"` прогоны `base` и `repro` пропускаются с причиной `FEATURE_SKIP_REASON`;
  - `checks["repro_tests"]` пропускается с причиной `f"policy={policy}"` и для `refactor`, и для `feature`.
- [x] **Шаг 4: `finish()`** (`:375`, `:428`): область инвариантов и `all_green` считаются одинаково для `refactor` и `feature`: `policy in ("refactor", "feature")`. Docstring у `coverage(...)` в `verdict.py` обновить: «refactor/feature принят — все инварианты области прошли».
- [x] **Шаг 5: загрузка `spec_invs`** (`:669`): `policy in ("refactor", "feature") and inv_list is None`.
- [x] **Шаг 6: защищённые пути** (`:720-724`): `[command].tests` добавляются только при `policy == "refactor"`. Для `feature` код уже даёт `[]`, но поясняющий комментарий нужно обновить.
- [x] **Шаг 7: стадия 9** (`:747-776`): новая ветка `elif policy == "feature":`
  - `head_changed = changed_paths(clone, git_env, base, head)` → `feature_empty_verdict`;
  - только в `command`: `tests_modified = changed_paths(..., tests, diff_filter="a")`, `tests_added = changed_paths(..., tests, diff_filter="A")` → `feature_tests_verdict`;
  - записать `checks["feature"]`; при отказе — `return finish(verdict)`.
- [x] **Шаг 8: стадия 10** (`:778-782`): `order = ("head",)` при `policy == "feature"`.
- [x] **Шаг 9: `render.py`**: `"feature": ("changed_base_head", "tests_added", "tests_modified")` в `preferred_map`.
- [x] **Шаг 10: фикстура и тесты.** Фикстура `_make_command_feature_fixture(tmp: Path) -> dict` строится по образцу `_make_command_refactor_fixture`:
  - `base`: `app.py` с `double`, `tests/test_app.py`, `.acceptbox.toml`;
  - `head`: в `app.py` добавлен `triple`, плюс новый `tests/test_triple.py`;
  - возвращает `{"repo", "base", "head"}`.

```python
class TestFeaturePolicyCommand(unittest.TestCase):
    def test_feature_command_happy_path(self):
        # accept --policy feature --base B --head H --out OUT → exit 0
        # data["policy"] == "feature"; runs: base/repro "skipped", head "green"
        # checks["feature"] == {"status": "ok", "changed_base_head": [...],
        #                       "tests_added": ["tests/test_triple.py"], "tests_modified": []}
        # checks["protected_paths"]["specs"] не содержит "tests/"
        # "not verified (policy=feature)" in res.stdout

    def test_feature_command_rejects_modified_renamed_deleted_or_helper_tests(self):
        # от base три head: правка tests/test_app.py; git mv в tests/test_app2.py;
        # удаление tests/test_app.py. Четвёртый случай: отдельный коммит добавляет
        # tests/helpers.py и служит base, head правит helpers.py.
        # каждый вместе с новым tests/test_triple.py → exit 1, "existing-tests-modified",
        # изменённый путь в checks["feature"]["tests_modified"]

    def test_feature_command_rejects_no_tests_test_outside_tests_and_empty_diff(self):
        # head меняет только app.py → "feature-no-tests"
        # head добавляет src/test_triple.py → "feature-no-tests"
        # --head == --base → "feature-empty"

    def test_feature_command_rejects_red_head_and_repro_flag(self):
        # новый тест падает → exit 1, "head-not-green"
        # --repro <sha> вместе с --policy feature → exit 2

    def test_feature_one_liner_with_base_ref(self):
        # _add_origin(repo); accept --policy feature --base-ref main --head H → exit 0, ACCEPTED

class TestFeaturePolicyWitnessbox(unittest.TestCase):
    def test_witnessbox_feature_runs_head_only_and_covers_spec(self):
        # _make_witnessbox_refactor_fixture(tmp, base_state="red", head_state="green")
        # (base красный — feature его не запускает, это и проверяем)
        # --policy feature --mode witnessbox ... → exit 0
        # runs["base"]["status"] == "skipped"; coverage.checked == ["INV-1"];
        # "tests_added" not in checks["feature"]
```

- [x] **Шаг 11: запустить**

Run: `python3 -m unittest tests.test_policy_and_strict_refs -v`
Expected: PASS, включая все существующие тесты `refactor` и `bugfix`.

Run: `python3 -m unittest discover -s tests`
Expected: `OK`, ноль пропусков.

---

### Задача 3: документация, процедура проверки фичи, решение L34, версия

**Файлы:**
- Изменить: `docs/decisions.md`, `docs/decisions.ru.md` — запись L34.
- Изменить: `docs/usage.md`, `docs/usage.ru.md` — раздел политик, таблица кодов причин, пример строки CLI.
- Изменить: `docs/agent.md`, `docs/agent.ru.md` — разделы 1 и 5, новый раздел «Проверка фичи».
- Изменить: `docs/setup.md`, `docs/setup.ru.md` — шаблон `.acceptbox/AGENTS.md`.
- Изменить: `README.md`, `README.ru.md` — пункт про фичи в «When to use».
- Изменить: `pyproject.toml`, `src/acceptbox/__init__.py` — версия `0.5.0`, если L33 её ещё не поднял.

- [x] **Шаг 1: L34 в обоих журналах.** Заголовок: `L34 — Feature policy (--policy feature) and orchestrated feature verification (extends L24, L30, L31)`, дата 2026-11-22, `Accepted by owner.` Пункты:
  - семантика из таблицы выше и три новых кода причин;
  - почему только `head`: владелец требует без лишних прогонов, а прогон `base` для фичи ничего не доказывает;
  - почему новый тест обязателен, а существующие менять нельзя: L31 — агент срезает углы, проще всего «починить» старый тест;
  - смысл `ACCEPTED` и оговорка в CLI;
  - UI-проверка — процедура для агента, не код AcceptBox. Её результат — суждение агента-проверяющего со скриншотами, недетерминированное;
  - `Revisit if:` одобренные сценарии понадобится хранить в репозитории как регрессию.
- [x] **Шаг 2: `usage`.**
  - Заголовок раздела: `### Policies (--policy bugfix, refactor, feature)`, пункт про `feature` — по таблице семантики.
  - Три строки в таблицу кодов `REJECTED` с колонкой `accept (feature)`.
  - В «CLI output» — пример строки `ACCEPTED [policy=feature]`.
  - `head-not-descendant`: дописать `feature` в список политик.
- [x] **Шаг 3: `agent`, раздел 1.** Пункт «New feature without approved EBT spec» заменить на «New feature? → `--policy feature`: one or more commits with code plus new test files; never edit existing tests; `ACCEPTED` proves tests pass and existing tests are intact, not that the feature matches intent». В раздел 5 — три строки с подсказками:
  - `feature-empty` → «commit the feature»;
  - `existing-tests-modified` → «revert edits to existing tests; put new fixtures in new files; if a shared test helper must change, stop and ask the human»;
  - `feature-no-tests` → «add test files under `[command].tests`».
- [x] **Шаг 4: `agent`, новый раздел «Проверка фичи (для оркестратора или агента-проверяющего)»** после раздела 6. Содержание фиксировано:
  1. Проверяет **не** тот агент, который делал фичу.
  2. Запустить `acceptbox accept --policy feature --base-ref main`, результат — вердикт и путь к `accept.json`.
  3. Если в проекте есть UI: по описанию задачи и диффу составить нумерованный список сценариев, по одной строке «открыть X → сделать Y → увидеть Z». Сценарии должны покрывать то, что просил человек, а не только то, что сделано в коде.
  4. **Точка остановки:** показать список человеку и ждать одобрения или правок. Без одобрения UI не проверять.
  5. Окружение донастраивать самому (браузер, симулятор, сервер, данные) и записывать каждый шаг. Файлы репозитория не менять: если это нужно, остановиться и сообщить.
  6. Пройти каждый одобренный сценарий в браузере или симуляторе, на каждый — скриншот.
  7. Отчёт по шаблону:
     - строка вердикта `accept`;
     - таблица `# | Сценарий | Результат (pass/fail/blocked) | Наблюдение | Скриншот`;
     - список шагов настройки окружения;
     - явная фраза: «UI-результат — суждение агента, не детерминированная проверка».

  `blocked` — сценарий не удалось пройти из-за окружения. Это не `pass`.
- [x] **Шаг 5: `setup`, шаблон `.acceptbox/AGENTS.md`.** Два правила: «features use `--policy feature`; never modify existing test files» и «feature verification and UI scenarios: follow docs/agent.md, section "Feature verification"». Ссылка ведёт на GitHub-URL `docs/agent.md`, как остальные ссылки шаблона.
- [x] **Шаг 6: `README`.** Пункт «New features without an approved spec» переписать: `--policy feature` проверяет, что `head` зелёный, существующие тесты не тронуты и добавлены новые; соответствие замыслу не проверяется. Нужна одобренная спецификация (`witnessbox`) и/или проверка сценариев (ссылка на раздел agent.md). Ссылки абсолютные (GitHub URL).
- [x] **Шаг 7: версия `0.5.0`** в `pyproject.toml` и в запасной `__version__`, если L33 не поднял её сам.
- [x] **Шаг 8: проверить**

Run: `python3 -m unittest discover -s tests`
Expected: `OK`, ноль пропусков.

Run: `grep -c "feature-no-tests" docs/usage.md docs/usage.ru.md docs/agent.md docs/agent.ru.md`
Expected: в каждом файле ≥ 1.

Run: `grep -n "^## L34" docs/decisions.md docs/decisions.ru.md`
Expected: по одной строке в каждом файле.

---

## Вне рамок плана

- Подсказки по `reason_code` в CLI и `accept.json` (идея из разбора CodeRabbit) — отдельная задача; раздел 5 `agent.md` уже частично их содержит.
- `acceptbox ui`, секция `[ui]`, артефакты UI в `accept.json`, хранение одобренных сценариев в репозитории — отложено решением владельца.
- Прогон `base` для `feature` и проверка «новые тесты падают на `base`» — не делаем (без лишних прогонов; L24 — для фич это слабая гарантия).
