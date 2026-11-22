# Автономный CI-пайплайн (GitHub Actions)

[English](ci.md) · **Русский**

AcceptBox поставляет два переиспользуемых composite GitHub Action — [`actions/setup`](../actions/setup/action.yml) и [`actions/accept`](../actions/accept/action.yml) — чтобы принимать PR с багфиксами и рефакторингами автоматически, с минимальным или нулевым участием оператора.

## Архитектура: участие человека только при изменении правил

Чтобы убрать ручное копирование SHA и действия оператора на каждом PR без ослабления безопасности, разделите репозиторий на две зоны доверия через **Branch Protection + `CODEOWNERS`**:

1. **Пути с правилами (обязательный Approve владельца в `main` через `CODEOWNERS`):**
   ```text
   /ebt/                       @owner
   /.acceptbox.toml            @owner
   /scripts/acceptbox-test.sh  @owner
   /.github/workflows/         @owner
   ```
   Человек проверяет и утверждает PR **только**, когда добавляется новый инвариант, меняется спецификация или тестовая обвязка.
2. **Автономный контур исполнения и приёмки (0 участия оператора):**
   - ИИ-агент работает в изолированной ветке/контейнере без прав пуша в `main`, делает `base → repro → head` (для багфикса) или `base → head` (для рефакторинга), проходит локальную самопроверку и открывает PR.
   - `actions/accept` запускается в GitHub Actions (вне checkout агента), автоматически определяет `policy`, `repro`, а в режиме `witnessbox` сам фиксирует `ebt/` из доверенного `origin/main`, включает `--strict-refs`, загружает `accept.json` артефактом, публикует/обновляет комментарий с вердиктом (включая хвост упавшего лога, чтобы агент мог сам исправиться) и выставляет commit status `acceptbox/accept`.
   - Когда `acceptbox/accept` стал `success` и пути `CODEOWNERS` не затронуты:
     - в **режиме `witnessbox`** (инварианты уже утверждены человеком) PR можно безопасно **автомержить**;
     - в **режиме `command`** либо просматривается только маленький дифф `base..repro`, либо добавляется независимая LLM-проверка `base..repro` перед автомержем.

## Шаблон `.github/workflows/acceptbox.yml`

Закрепляйте `kaufd/acceptbox/actions/*` по полному SHA коммита тега релиза (не используйте `@main`).

```yaml
name: AcceptBox

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]

permissions: {}

concurrency:
  group: acceptbox-${{ github.ref }}
  cancel-in-progress: true

jobs:
  accept:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      statuses: write
      pull-requests: write
      # Добавьте id-token: write и attestations: write, если включите attest: "true"
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0

      - name: Set up AcceptBox (verified against release.json)
        uses: kaufd/acceptbox/actions/setup@<полный-sha-коммита> # v0.3.0
        with:
          version: "0.3.0"
          # Опционально: укажите witnessbox-version, если проект использует --mode witnessbox:
          # witnessbox-version: "0.10.0"

      - name: Run AcceptBox acceptance
        uses: kaufd/acceptbox/actions/accept@<полный-sha-коммита> # v0.3.0
        with:
          github-token: ${{ github.token }}
          policy: auto          # bugfix по умолчанию; refactor по лейблу "refactor" или trailer
          mode: auto            # берёт из base:.acceptbox.toml или witnessbox при наличии инвариантов
          repro-strategy: first-commit
          strict-refs: "true"   # fail-closed, если источник ветки деградировал до local
```

`fetch-depth: 0` обязателен, чтобы у Git была полная история между `base` и `head`.

## Как `actions/accept` автоматически определяет параметры PR

| Параметр | Автоопределение (`resolve_pr.py`) | Явное переопределение |
|---|---|---|
| `policy` | `refactor`, если есть commit trailer `AcceptBox-Policy: refactor` или лейбл PR `refactor` / `acceptbox:refactor`; иначе `bugfix`. Конфликтующие trailers отклоняются (код `2`). | `policy: bugfix` или `policy: refactor` |
| `repro` (в `bugfix`) | 1) Единственный коммит в `base..head` с trailer `AcceptBox-Role: repro` (несколько — отказ); 2) иначе первый коммит `base..head` при `repro-strategy: first-commit`. | `repro: <40-hex-sha>` или `repro-strategy: trailer-only` |
| `inv` и `mode` | Читает `AcceptBox-Inv: INV-1,INV-2` из commit trailers или описания PR. Если инварианты найдены и `mode: auto`, включает `witnessbox`; иначе полагается на `base:.acceptbox.toml`. | `mode: command` / `witnessbox`, `inv: ...` |
| `ebt-sha` и `approved-tree` | В режиме `witnessbox`, если оба не заданы, вытягивает `origin/<base-ref>` и вычисляет `approved-tree` через `acceptbox pin` (опираясь на защиту `ebt/` в `main` через `CODEOWNERS`). | Передать оба поля `ebt-sha` и `approved-tree` явно |

### Поддерживаемые commit trailers для агентов

Агенты могут явно указывать метаданные прямо в сообщениях коммитов:

```text
test: reproduce empty cart crash

AcceptBox-Role: repro
AcceptBox-Inv: INV-CART-1
```

Для PR с рефакторингом (без `repro`-коммита; тесты и `ebt/` защищены, `base` и `head` оба зелёные):

```text
refactor: simplify discount calculation

AcceptBox-Policy: refactor
```

*(Либо повесьте на PR лейбл `refactor`.)*

## Проверка установки в `actions/setup`

`actions/setup` не доверяет непроверенной установке с PyPI:
1. Скачивает `release.json` из GitHub Release тега (`kaufd/acceptbox` и опционально `kaufd/witnessbox`).
2. Скачивает точный `.whl` с PyPI (`--no-deps --only-binary=:all:`).
3. Сверяет SHA-256 wheel-файла с `release.json` до установки (`actions/setup/verify_release.py`).
4. Устанавливает через `uv tool install --force` и сверяет вывод `--installation` с `release.json`.

## Опционально: независимый ревью `repro` для режима `command`

В режиме `witnessbox` инвариант утверждён человеком заранее, поэтому `ACCEPTED` достаточно для автомержа.
В режиме `command` `actions/accept` доказывает структуру, неизменность тестов, красный `repro` и зелёный `head`, но не оценивает смысловое качество теста. Для безопасного автомержа в `command` добавьте отдельную джобу с независимым LLM-ревьюером, которому передаются **только** текст issue и `git diff "$BASE..$REPRO"` (без `head`), чтобы отсекать тривиальные или замоканные репро-тесты.

## Границы безопасности и ограничения

- **PR из форков отклоняются по умолчанию** (`allow-forks: "false"`): PR из форков не получают токенов записи и не должны запускать недоверенный код в доверенных workflow.
- **Строгая проверка источников веток (`--strict-refs`):** включена по умолчанию в `actions/accept`. И сам AcceptBox, и `format_report.py` завершаются отказом/ошибкой, если `ref_source_kind == "local"`.
- **Не песочница ОС:** тесты выполняются в GitHub-раннере. Для двухстадийного разделения недоверенного и доверенного контуров с подписанным `gate.json` см. [режим CI в WitnessBox](https://github.com/kaufd/witnessbox/blob/main/docs/ci.ru.md).
- **Опциональная подпись (`attest`):** задайте `attest: "true"` (с правами `id-token: write` и `attestations: write`), чтобы подписывать `accept.json` через `actions/attest-build-provenance`. При сбое подписи статус коммита понижается до `error`.
