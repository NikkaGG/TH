# Проверка MVP — 16 октября 2026 года

Результаты ниже относятся к конкретной рабочей папке и конфигурации этой проверки. Наличие тестов не доказывает доступность всех продуктов TikTok для конкретного приложения.

## Автоматические проверки backend

**29 тестов** в `tests_api/`: временные SQLite-БД, настоящий FastAPI ASGI, HTTPX MockTransport для документированных внешних контрактов. Реальная сеть TikTok блокируется в этих тестах.

Проверяются:

- роли admin/operator/viewer, отсутствие access/refresh tokens в ответах и аудите;
- OAuth state: expiry, browser binding, одноразовое потребление, проверка open_id;
- зашифрованное хранение, MultiFernet rotation, срок и ротация refresh token;
- ограничение количества аккаунтов, локальная блокировка при ошибке revoke;
- pagination Display, form encoding OAuth, пустой HTTP 200, malformed data, 401/403/429/5xx;
- импорт/валидация URL, запрещённые хосты/порты/userinfo, ограничения размера тела;
- Unicode/фразы/исключения и отсутствие ложного совпадения пустого хештега;
- создание кампании, идемпотентность, обнаружение совпадений, черновики, дедупликация;
- broker failure, повтор только чтения, Retry-After, максимум попыток, expired worker lease, отмена;
- явный отказ `/publish`, ручное подтверждение без фальшивого TikTok evidence;
- миграция upgrade → повторный upgrade → downgrade → upgrade на временной БД;
- компиляция PostgreSQL DDL, rate limits, security headers, строгие входные модели.

Также выполняются Ruff и `pip check`. Основные зависимости закреплены в requirements-api.txt. В текущем Starlette 1.8.0 TestClient сообщает deprecation warning для использования HTTPX 0.28 вместо httpx2; тесты проходят. Runtime официальный клиент использует HTTPX, и этот warning касается тестового клиента, не отказа интеграции. Переход тестового клиента на httpx2 — отдельное обновление зависимости, не скрытая ошибка отправки.

## Настоящая контейнерная интеграция

Базовые Docker images **реально собраны** на Linux Python 3.13. Запущены PostgreSQL 17, Redis 7.4, FastAPI, Celery prefork worker и beat. Ready endpoint возвращает 200; Celery inspect ping получает pong; `pip check` внутри image не находит конфликтов.

Дополнительно `caddy validate` успешно проверил конфигурацию опционального HTTPS-прокси. Это проверка конфигурации, не подтверждение принятия локального callback приложением TikTok. Идентификаторы собранных образов сохранены в [docker-images.json](evidence/docker-images.json), версии и заявленные license metadata пакетов — в [dependencies.json](evidence/dependencies.json).

В **отдельном временном Compose project**, с собственными PostgreSQL/Redis volumes, проведён `tools/integration_docker.py`:

| Проверка | Результат |
|---|---|
| Синтетические аккаунты исключительно в QA-БД | 10 |
| Кампании, по две на аккаунт | 20 |
| Фоновые задачи через настоящий broker/beat/workers | 20 завершились успешно |
| Идемпотентный повтор HTTP запуска | Тот же job ID |
| Дедупликация черновиков между параллельными кампаниями | 10 уникальных черновиков |
| Тематическое исключение advertisement | Исключённый ролик не создал черновик |
| Публикаций комментариев | **0** |
| Попытка `/publish` | HTTP 409 |
| Ручная отметка | `verified_by_tiktok=false` |
| Live TikTok calls | **0** |
| Основная рабочая БД | Тестовые аккаунты/ролики не добавлялись |
| Временные тестовые контейнеры/тома | Удалены после проверки |

Проверяемый машинный результат — [docker-integration.json](evidence/docker-integration.json). Этот тест подтверждает взаимодействие наших реальных инфраструктурных компонентов; его fixture-аккаунты не являются подключением настоящих TikTok-владельцев.

## Панель в настоящем Chromium

Проверены неправильный/правильный API-ключ, все четыре раздела, выход, очистка поля ключа, отсутствие ошибок JavaScript и горизонтального overflow на ширине 390 px. Никаких fake-роликов в рабочую панель для скриншота не добавлялось. Повторная проверка выполнялась уже против Docker backend.

- [Машинный результат](evidence/ui-smoke.json)
- [Desktop screenshot](evidence/api-panel-desktop.png)
- [Mobile screenshot](evidence/api-panel-mobile.png)
- [Сохранённая схема OpenAPI](evidence/openapi.json)

## Живой эксперимент TikTokApi

Выполнен read-only запрос `coffee`, максимум 3 ролика, TikTokApi **7.3.3**, Playwright **1.59.0**, новый Chromium без cookies подключённых владельцев. Последний результат: **ошибка `experimental_empty_response`, около 14 секунд, 0 операций записи**.

Доказательство — [live-search.json](evidence/live-search.json). Это означает, что выбранный экспериментальный путь **не прошёл live-проверку в данной среде**. Причина нормализована по типу `EmptyResponseException`; одного этого недостаточно, чтобы доказать конкретную причину WAF/регион/сессию. Сырые body/headers и секреты не сохранялись. Нельзя заменять этот результат утверждением «поиск работает», даже если upstream README показывает пример.

## Что не проверено вживую

- Реальная OAuth-авторизация/refresh/revoke конкретного владельца и Display выдача: ключи одобренного TikTok приложения в этой сессии отсутствуют. Реальный клиент реализован; контрактные тесты не заменяют live admission.
- Research: нет отдельного разрешения исследовательского проекта; для коммерческого лидогенератора он не предназначен.
- Business own-video comments/Discovery/Mentions: отдельный доступ не предоставлен, эти adapters не заявлены интегрированными.
- Публикация реального комментария: не выполнялась. Для произвольного чужого ролика неподдерживаемая операция явно отключена.
- Опциональный Chromium Docker image: конфигурация проверена на синтаксис; базовый runtime собран и проверен полностью, browser image отдельно в этой сессии не собирался.
- Нагрузочный benchmark внешнего TikTok, устойчивость к изменениям сайта и production security audit: не проводились.

## Воспроизведение

Из корня проекта после установки dev-зависимостей:

```powershell
.api-venv\Scripts\python -m pytest
.api-venv\Scripts\python -m ruff check tiktok_api tests_api migrations
.api-venv\Scripts\python -m pip check
docker compose build
.api-venv\Scripts\python -m tools.integration_docker
.api-venv\Scripts\python -m tools.smoke_api_ui
```

Контейнерная QA-проверка требует свободного localhost:18001; основная панель использует 8001. QA-скрипт создаёт случайное имя проекта и очищает только созданные им тестовые контейнеры/тома. UI smoke требует отдельно установленного Playwright и Chromium. `python -m tools.probe_experimental` выполняет отдельный живой публичный read-запрос; он не входит в обычные тесты и не публикует комментарии.
