# Сторонние компоненты и происхождение кода

Новый runtime `tiktok_api/` написан самостоятельно. Код существующего приложения `app/` в него не перенесён. Материалы в `docs/evidence/` — исследовательские снимки, не импортируемые модули; сохранены обнаруженные LICENSE-файлы. Для проектов с неуточнённой лицензией полные исходники в результате не оставлены: сохранены metadata, ссылки, SHA и наши аналитические выводы. SHA/источник каждого образца находятся в snapshot.json. Не удаляйте относящиеся к распространяемым образцам лицензии.

| Компонент | Использование | Лицензия / первичный источник |
|---|---|---|
| FastAPI | HTTP API | MIT, [репозиторий](https://github.com/fastapi/fastapi) |
| Starlette | ASGI под FastAPI | BSD-3-Clause, [репозиторий](https://github.com/Kludex/starlette) |
| Pydantic / pydantic-settings | Контракты и настройки | MIT, [репозиторий](https://github.com/pydantic/pydantic) |
| SQLAlchemy / Alembic | ORM / миграции | MIT, [репозиторий](https://github.com/sqlalchemy/sqlalchemy) |
| HTTPX | Официальные HTTP вызовы | BSD-3-Clause, [репозиторий](https://github.com/encode/httpx) |
| cryptography | Fernet/MultiFernet | Apache-2.0 или BSD-3-Clause, [лицензии](https://github.com/pyca/cryptography) |
| Celery | Фоновые workers | BSD-3-Clause, [репозиторий](https://github.com/celery/celery) |
| redis-py | Брокер/лимиты | MIT, [репозиторий](https://github.com/redis/redis-py) |
| psycopg | PostgreSQL driver | LGPL-3.0, [репозиторий](https://github.com/psycopg/psycopg) |
| TikTokApi 7.3.3 | Только опциональный public search | MIT, [репозиторий](https://github.com/davidteather/TikTok-Api) |
| Playwright 1.59.0 | Только опциональный search / browser QA | Apache-2.0, [репозиторий](https://github.com/microsoft/playwright-python) |

Redis server в Compose закреплён на ветке **7.4**, для которой необходимо учитывать условия лицензирования самой серверной поставки отдельно от MIT-клиента redis-py: [официальная информация Redis](https://redis.io/legal/licenses/). Это не заявление о том, что любая версия Redis server имеет MIT-лицензию. При требовании только permissive OSS можно заменить Redis 7.4 на совместимый Valkey после отдельных integration tests; замена здесь не проверялась.

Для транзитивных Python-зависимостей источником установленных лицензий являются metadata и license-файлы соответствующих wheel. Версии фиксируются в `requirements-api*.txt`. MIT- и Apache-образцы исходников сохранены с notices; код проекта без обнаруженной лицензии adinagit не переносился в runtime. Ни отсутствие известной уязвимости, ни лицензия не означают разрешение TikTok на конкретный сценарий использования.
