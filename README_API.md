# Lead Hunter API — рабочий MVP

Отдельный backend для управления примерно 10 своими TikTok-аккаунтами: OAuth, сбор доступных видео, тематический отбор, расписания, черновики, статусы, аудит и панель. Существующие `app/`, `start.bat` и `data/` сохранены. Новый сервис использует `tiktok_api/`, собственную БД и порт **8001**.

**Ограничение продукта:** публикация комментария под произвольным чужим роликом не поддерживается подтверждёнными публичными интеграциями. `/publish` возвращает 409 и ничего не отправляет. Черновик можно открыть в TikTok, опубликовать вручную и отметить как действие оператора. Это состояние не выдаётся за подтверждение TikTok.

Полное [исследование девяти оригинальных репозиториев](docs/API_RESEARCH.md), [архитектура и endpoints](docs/API_ARCHITECTURE.md), [отчёт проверки](docs/API_TESTING.md), [лицензии](docs/THIRD_PARTY_API.md).

## Что реализовано

- Настоящие HTTP-клиенты Login Kit Web / Display API: обмен authorization code, проверка личности, scopes, refresh rotation, отзыв доступа, постраничное чтение своих публичных видео.
- Транзакционное ограничение числа подключений, шифрование access/refresh tokens, однократный OAuth state с привязкой к браузеру.
- Кампании с темами/исключениями, словарный score, сохраняемые объяснения, импорт полных URL, дедупликация видео и комментариев между кампаниями.
- PostgreSQL, Celery, Redis, beat, сохраняемая очередь задач, leases, cancellation, bounded retries/Retry-After, роли admin/operator/viewer.
- Панель без сборки: аккаунты, поиск/расписания, импорт, комментарии, задачи. Ключ доступа хранится только в памяти вкладки.
- Опциональный **неофициальный** публичный reader через TikTokApi 7.3.3. Выключен по умолчанию; результат живой проверки приведён в отчёте. Без обещания стабильной выдачи.
- Изолированный Research read-only endpoint, также выключенный по умолчанию, только для отдельно одобренного некоммерческого исследования. Его выдача не попадает в лидогенерацию.

В API нет фиктивных аккаунтов, демонстрационных роликов или имитации успешных сетевых операций. Без одобренного TikTok app и пользовательского согласия подключённые аккаунты не появятся. Тематический отбор анализирует metadata; распознавание изображения/звука не реализовано.

## Быстрый локальный запуск на Windows

Нужен Python **3.13 или 3.14**. Для обычного MVP браузерные зависимости не нужны.

1. Запустите `start-api.bat` из этой папки.
2. Скрипт создаст отдельное окружение, установит закреплённые зависимости, при отсутствии `.env` сгенерирует случайные секреты, применит миграции и запустит API вместе с одним локальным worker.
3. Откройте [панель](http://127.0.0.1:8001), введите значение `LH_ADMIN_KEY` из локального `.env`. Секрет не публикуйте. Документация: [Swagger](http://127.0.0.1:8001/docs).
4. `Ctrl+C` в окне запуска останавливает оба процесса. Повторный запуск при занятом 8001 отклоняется.

Локальная БД — `lead_hunter_api.db`, не `data/`. Не запускайте несколько локальных workers. Для реального OAuth нужен HTTPS на том же host/port, что `LH_REDIRECT_URI`; HTTP-панель подходит для локальной проверки API, но не заменяет зарегистрированный callback.

Вручную, PowerShell:

```powershell
python -m venv .api-venv
.api-venv\Scripts\python -m pip install -r requirements-api.txt
.api-venv\Scripts\python tools\setup_api.py
.api-venv\Scripts\python -m alembic upgrade head
.api-venv\Scripts\python -m tiktok_api.run_local
```

`setup_api.py` откажется перезаписывать уже существующий `.env`. Не теряйте `LH_ENCRYPTION_KEYS`: без него токены в резервной копии БД не расшифровать. В Git `.env` и рабочие БД исключены.

## Docker: PostgreSQL + Redis + Celery

Нужен работающий Docker Desktop с Linux containers. `.env` должен быть создан предыдущим способом или отдельной командой `python tools/setup_api.py`.

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Compose автоматически переопределяет локальные `LH_DATABASE_URL`, `LH_REDIS_URL`, `LH_QUEUE_MODE`; `.env` остаётся пригоден для локального режима. Миграции выполняются отдельным init-сервисом до запуска API/workers. API доступен на [127.0.0.1:8001](http://127.0.0.1:8001). PostgreSQL/Redis порты наружу не опубликованы.

```powershell
docker compose logs --tail=80 api worker beat
docker compose stop
docker compose start
```

`stop/start` сохраняют контейнеры и тома. Для обновления кода используйте `docker compose up -d --build`. Новая SQLite-БД и PostgreSQL независимы: переключение режима не переносит записи. Один beat обслуживает расписания; worker по умолчанию имеет concurrency=2. Результаты и статусы хранятся в PostgreSQL, а не в Celery result backend.

## Подключение настоящего TikTok-аккаунта

1. Зарегистрируйте приложение на [TikTok for Developers](https://developers.tiktok.com/doc/login-kit-web/), подключите Login Kit Web и Display API, получите необходимые approvals для `user.info.basic` и `video.list`.
2. Настройте статический HTTPS callback, например `https://your-host.example/api/v1/oauth/callback`, и зарегистрируйте **точно этот URL** в TikTok. Не добавляйте query/fragment. Домен и доступность callback должны отвечать требованиям вашего приложения.
3. Установите `LH_CLIENT_KEY`, `LH_CLIENT_SECRET`, `LH_REDIRECT_URI` в `.env`, перезапустите сервисы. Не используйте Business/Research credentials вместо Login Kit.
4. Откройте панель именно по этому HTTPS host/port. Нажмите «Подключить через TikTok», войдите как владелец и дайте согласие. После callback панель попросит повторно ввести свой API-ключ: он не сохраняется в browser storage.
5. Повторите для каждого аккаунта. Аккаунт создаётся только после обмена кода и проверки фактического `open_id`. Новое согласие требуется при отзыве доступа/истечении refresh token.

Для разработки предусмотрен Caddy с внутренним сертификатом:

```powershell
docker compose --profile https up -d
```

Он слушает `https://localhost:8443`, соответствует примерному `LH_REDIRECT_URI` и требует доверия к локальному CA. Это **не гарантия**, что TikTok примет localhost или внутренний сертификат при регистрации приложения. Для реального подключения используйте одобренный доступный HTTPS-домен с доверенным сертификатом. В production адаптируйте `deploy/Caddyfile` к домену и входному TLS; не включайте журналы query string у OAuth callback.

## Использование API

Пример PowerShell читает секрет из файла в память, не печатает его и не включает в URL:

```powershell
$apiKeyLine = Get-Content .env | Where-Object { $_ -like 'LH_ADMIN_KEY=*' }
$apiKey = $apiKeyLine.Substring('LH_ADMIN_KEY='.Length)
$apiHeaders = @{ Authorization = "Bearer $apiKey" }
Invoke-RestMethod http://127.0.0.1:8001/api/v1/capabilities -Headers $apiHeaders
```

В Swagger нажмите **Authorize** и введите ключ роли. Создайте кампанию через `POST /api/v1/campaigns` (account_id берётся из `/accounts`):

```json
{
  "name": "Обзоры кофе",
  "account_id": "ACCOUNT_ID_FROM_OAUTH",
  "source": "display",
  "keywords": ["кофе", "coffee"],
  "exclude_keywords": ["реклама"],
  "comment_text": "Спасибо за подробный обзор!",
  "min_score": 1,
  "max_results": 100,
  "interval_minutes": 60,
  "enabled": true
}
```

`display` читает свои ролики, а не общий поиск. `import` сканирует импортированные ссылки. `experimental` доступен только после явного включения reader. `min_score` — доля найденных тем в процентах, минимум 1; любое исключение обнуляет результат. Если описание пустое, тематическое совпадение не придумывается.

Импорт через `POST /api/v1/videos/import`:

```json
{"videos": [{"url": "https://www.tiktok.com/@author/video/1234567890123456789", "description": "Описание и хештеги из разрешённого источника"}]}
```

Импортированные metadata маркируются `metadata_verified=false`, не доказывают владение видео. Shortlinks намеренно не раскрываются сервером.

Для ручного запуска кампании отправьте `POST /api/v1/campaigns/{id}/run` с `Idempotency-Key: my-unique-request-0001`. Ответ 202 содержит job ID. Повтор с тем же ключом не запускает новую работу. Статус: `GET /api/v1/jobs/{id}`; результат содержит found/matched/drafts_created и `comments_published: 0`.

Очередь — `GET /api/v1/drafts`. После фактической ручной публикации `POST /api/v1/drafts/{id}/manual-confirm` с `{"confirmed": true}`; optional `comment_id` хранится как сведения оператора. Не отправляйте подтверждение до реального ручного действия.

## Опциональный экспериментальный поиск

Локально:

```powershell
.api-venv\Scripts\python -m pip install -r requirements-api-experimental.txt
.api-venv\Scripts\python -m playwright install chromium
```

Установите `LH_EXPERIMENTAL_SEARCH_ENABLED=true` и перезапустите API/worker. `LH_EXPERIMENTAL_MS_TOKEN` необязателен и хранится только в серверной конфигурации. Это не OAuth credential и не подключение аккаунта. Reader не импортирует cookies обычного браузера и не решает captcha. При challenge, пустом/изменившемся ответе задача требует внимания, без ротации аккаунтов или непрерывного долбления TikTok.

Docker-вариант с отдельным Chromium image:

```powershell
docker compose -f compose.yaml -f compose.experimental.yaml up -d --build
```

В этом режиме один browser worker. Переключение флага не делает внутренний веб-метод официальным или стабильным. Базовый Docker image браузер не содержит.

## Research API

По умолчанию отключён. Только после отдельного одобрения некоммерческого проекта задайте `LH_RESEARCH_ENABLED=true`, `LH_RESEARCH_PROJECT`, `LH_RESEARCH_CLIENT_KEY`, `LH_RESEARCH_CLIENT_SECRET`.

`POST /api/v1/research/videos/query` принимает keyword, start_date/end_date (1..30 календарных дней), max_count≤100, optional cursor/search_id. Возвращает исходную data-страницу с отметкой назначения; результаты не создают лид-кампании/черновики. Локальный консервативный бюджет — 900 запросов за 24 часа; он не заменяет квоты конкретного Research app.

## Проверки и сопровождение

```powershell
.api-venv\Scripts\python -m pip install -r requirements-api-dev.txt
.api-venv\Scripts\python -m pytest
.api-venv\Scripts\python -m ruff check tiktok_api tests_api migrations
.api-venv\Scripts\python -m pip check
```

Тесты используют временную БД и контролируемые HTTP-ответы. Они не доказывают прохождение TikTok app review или публикацию настоящего комментария. Для отдельной live read-проверки уже подключённого аккаунта:

```powershell
.api-venv\Scripts\python -m tools.live_read_smoke ACCOUNT_ID
```

Секреты и полные upstream bodies не выводятся. `/health/ready` проверяет БД/schema и Redis в celery mode; `/metrics` защищён ключом viewer+ и отдаёт gauges статусов. Рост queued/dispatched нужно отслеживать вместе с процессами worker/beat.

Ротация шифрования: сначала `LH_ENCRYPTION_KEYS=NEW_KEY,OLD_KEY`, затем в нужном окружении `python -m tiktok_api.cli rotate-encryption`, после успешной проверки уберите OLD_KEY. Сначала сохраните резервную копию БД и ключей отдельно. Не удаляйте старый ключ, пока все нужные токены не перешифрованы.

Проект предназначен для одного доверенного рабочего пространства. Перед публичной SaaS-эксплуатацией нужны персональные identities/tenant isolation, retention, backup jobs, alerting и внешнее управление секретами. Полный перечень реализованного и оставшихся границ указан в архитектуре и отчёте.
