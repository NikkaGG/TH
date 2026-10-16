# TikTok API: исследование и решение для MVP

Проверено **16 октября 2026 года**. Числа GitHub — снимок на момент проверки, не обещание стабильности. Метаданные, URL исходников, commit SHA, SHA-256 скачанных файлов и примеры открытых Issues сохранены в [evidence/snapshot.json](evidence/snapshot.json). Исходники скачаны для чтения, сторонние репозитории не запускались. Отдельно проверен опубликованный wheel TikTokApi 7.3.3: сигнатуры используемых методов совпадают с интеграцией.

## Главный технический вывод

Подтверждённого публичного API для автономной публикации заданного комментария под **произвольными чужими найденными видео** не установлено. Это вывод из изученных официальных поверхностей и исходников, а не утверждение об отсутствии любых закрытых партнёрских возможностей.

Официальный [Display API](https://developers.tiktok.com/doc/display-api-overview/) читает профиль и публичные ролики авторизованного владельца. В [публичном каталоге scopes](https://developers.tiktok.com/doc/tiktok-api-scopes/) не найдено разрешение на требуемую публикацию комментариев. [Content Posting](https://developers.tiktok.com/doc/content-posting-api-get-started/) публикует видео/фото, а не комментарии; настройка `disable_comment` не является методом написания комментария.

При этом **официальная работа с комментариями существует**. [Accounts API for Business](https://business-api.tiktok.com/gateway/docs/index?doc_id=1738864928947201&language=ENGLISH) перечисляет создание и ответы на комментарии к **своим** видео. [Официальный SDK CommentsApi](https://github.com/tiktok/tiktok-business-api-sdk/blob/f809c396520df2d7b201a9ccc5378d822b728ed3/python_sdk/business_api_client/api/comments_api.py) реализует `/open_api/v1.3/comment/post/` с `advertiser_id`, `comment_id` и `comment_type`: это ответ на рекламный комментарий. Подменять этими операциями комментирование чужих органических роликов нельзя. Login Kit и Business API также нельзя объединять предположением, что их токены взаимозаменяемы.

## 1. Официальные поверхности

| Продукт | Подтверждённая область | Ограничение для задачи | Решение |
|---|---|---|---|
| [Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/) | OAuth с согласием владельца | Нужны зарегистрированное приложение, разрешения, статический HTTPS callback; первый вход требует человека | Реальная интеграция в MVP |
| [Display API](https://developers.tiktok.com/doc/display-api-overview/) | Свой профиль, список/выбор своих публичных видео | Нет общего тематического поиска чужих видео | Реальная пагинация и обновление токенов |
| [Research API](https://developers.tiktok.com/doc/research-api-specs-query-videos/) | Поиск публичных видео; [чтение комментариев](https://developers.tiktok.com/doc/research-api-specs-query-video-comments/) | Отдельное одобрение исследования, чтение данных | Отдельный отключённый по умолчанию read-only endpoint, без импорта в лидогенерацию |
| [Accounts API / Organic](https://business-api.tiktok.com/gateway/docs/index?doc_id=1738864928947201&language=ENGLISH) | Комментарии и ответы к своим видео | Отдельные доступы Business; область owned video | Документировано, адаптер публикации в MVP не включён |
| [Marketing Comments API](https://github.com/tiktok/tiktok-business-api-sdk/blob/f809c396520df2d7b201a9ccc5378d822b728ed3/yml_files/comment_post.yml) | Ответы/модерация рекламных комментариев | Требует рекламный контекст; не общий organic comment API | Не используется для чужих роликов |
| [Discovery / Mentions](https://business-api.tiktok.com/gateway/docs/index?doc_id=1761236883355649&language=ENGLISH) | В каталоге Business есть популярные хештеги, связанные трендовые видео, музыка, поисковые тренды и mentions | Доступ, охват поиска и квоты конкретного приложения надо подтвердить отдельно; это не обещание полного keyword-search | Перспективный следующий официальный источник; не выдаётся за готовую интеграцию |
| [Content Posting](https://developers.tiktok.com/doc/content-posting-api-get-started/) | Публикация собственных видео/фото | Отдельный продукт, разрешения и аудит | За пределами сценария комментариев |

[FAQ Research](https://developers.tiktok.com/doc/research-api-faq/) прямо исключает creator, advertiser и commercial user из доступа Research Tools. Одного developer account недостаточно. Там же указаны архивная природа результатов, до 48 часов появления новых видео и до 10 дней обновления статистики. Поэтому Research не является рабочим обходом ограничений коммерческого поиска лидов и не подходит для гарантированного обнаружения свежих роликов в реальном времени.

## 2. Сравнение open-source

Последняя активность в таблице — `pushed_at` GitHub, а не время изменения метаданных `updated_at`. Столбец Issues+PR — именно комбинированный счётчик GitHub `open_issues_count`; реальные примеры Issues сохранены отдельно. Это исключает ошибочную выдачу PR за число багов.

| Оригинальный репозиторий | Stars | Последний push, UTC | Issues+PR | Лицензия | Сильные стороны | Ограничения / выбор |
|---|---:|---|---:|---|---|---|
| [davidteather/TikTok-Api](https://github.com/davidteather/TikTok-Api) | 6691 | 2026-08-24 | 150 | MIT | Async-итераторы, браузерные сессии, чтение метаданных и комментариев | Веб-методы, пустые ответы, зависимость от TikTok; выбран только как опциональный экспериментальный reader |
| [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | 20665 | 2026-10-02 | 5 | Apache-2.0 | Актуальная модульная модель platform adapter, парсеры, replay/contract tests | Большая самостоятельная платформа; live-контракты условные, права на произвольную запись не подтверждены; изучен архитектурно |
| [dfreelon/pyktok](https://github.com/dfreelon/pyktok) | 467 | 2025-10-04 | 3 | BSD-3-Clause | Простой сбор JSON/метаданных, экспорт исследовательских данных | Чтение cookies из браузера, WAF и изменяемый HTML; не выбран для сервиса с изолированными OAuth-аккаунтами |
| [Russell-Newton/TikTokPy](https://github.com/Russell-Newton/TikTokPy) | 238 | 2024-07-04 | 23 | MIT | Типизированные модели, sync/async интерфейсы | Давно без push, незакрытые проблемы загрузки/парсинга; не основной production adapter |
| [wkaisertexas/tiktok-uploader](https://github.com/wkaisertexas/tiktok-uploader) | 778 | 2026-02-12 | 18 | MIT | Изолированный браузерный upload-клиент, ленивый запуск | Загружает видео; не решает комментарии; UI-зависимость и свежие Issues о сбоях Post |
| [drawrowfly/tiktok-scraper](https://github.com/drawrowfly/tiktok-scraper) | 5218 | 2023-05-19 | 86 | MIT заявлена в package.json; GitHub license не распознана | Известный npm-парсер, тесты нормализации | Старый release, устаревшая HTTP-зависимость, mocked tests; исключён |
| [tiktok/tiktok-research-api-wrapper](https://github.com/tiktok/tiktok-research-api-wrapper) | 33 | 2024-12-04 | 3 | MIT | Официальные Python/R query-модели, pagination/search_id | Исследовательский доступ; sync requests без timeout в изученных вызовах, до 60 повторов; собственный небольшой HTTP-adapter проще контролировать |
| [tiktok/tiktok-business-api-sdk](https://github.com/tiktok/tiktok-business-api-sdk) | 229 | 2026-03-09 | 17 | MIT | Официальная схема рекламных методов, Python/JS SDK | Генерируемый широкий sync SDK; Issue о расхождении с текущей документацией; не используется как generic comment writer |
| [adinagit/tiktok-research-api-python](https://github.com/adinagit/tiktok-research-api-python) | 13 | 2023-12-25 | 0 | Явная лицензия не обнаружена | Маленький читаемый пример cursor/search_id | Скриптовые глобальные секреты, form/query сериализация, широкий except; код не копируется |

### Что показало чтение реализаций и тестов

- **TikTokApi:** [search.py](https://github.com/davidteather/TikTok-Api/blob/99630590a2e24997f69822b0541f396424f44d40/TikTokApi/api/search.py) предоставляет `users()`; примечание ограничивает поддерживаемый поиск пользователями. [Отдельный video example](https://github.com/davidteather/TikTok-Api/blob/99630590a2e24997f69822b0541f396424f44d40/examples/video_keyword_search_example.py) вызывает `make_request` с веб-путём `/api/search/item/full/`. Это существующая реализация, но не стабильный официальный endpoint. В нашем адаптере собственные границы количества/времени и контроль cursor. [Тест многостраничного поиска](https://github.com/davidteather/TikTok-Api/blob/99630590a2e24997f69822b0541f396424f44d40/tests/test_search.py) помечен skip из-за known issue. [comment.py](https://github.com/davidteather/TikTok-Api/blob/99630590a2e24997f69822b0541f396424f44d40/TikTokApi/api/comment.py) читает комментарии/ответы, метода их публикации в изученном классе нет. [Issue 1311](https://github.com/davidteather/TikTok-Api/issues/1311) сообщает о HTTP 200 с пустым телом.
- **Evil0ctal:** [adapter.py](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/4f0bed8483c35a980315d9c7b3a1d4a1119ad2b2/src/dtk/platforms/tiktok/adapter.py) отделяет таблицу запросов, client profile, risk detection и parser. [Replay tests](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/4f0bed8483c35a980315d9c7b3a1d4a1119ad2b2/tests/replay/test_tiktok_parser.py) полезны для отслеживания изменений схем. [Live tests](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/4f0bed8483c35a980315d9c7b3a1d4a1119ad2b2/tests/contract/test_live_endpoints.py) пропускаются без deployment/key/subjects; само наличие файла не подтверждает live-работоспособность.
- **Pyktok:** [pyktok.py](https://github.com/dfreelon/pyktok/blob/cb6e2eacd046fcb273fe6fcda31835dbcd5cf03f/src/pyktok/pyktok.py) извлекает встроенный JSON из HTML, может читать browser cookies и использует TikTokApi для части чтения. Это полезный исследовательский инструмент, но изоляция аккаунтов и секретов потребовала бы отдельной переделки. [Issue 83](https://github.com/dfreelon/pyktok/issues/83) касается распознавания WAF challenge.
- **TikTokPy:** [async_api.py](https://github.com/Russell-Newton/TikTokPy/blob/afb69838685fa25714a2029bafcc7972696679fd/src/tiktokapipy/async_api.py) и модели дают удобную нормализацию. [test_video.py](https://github.com/Russell-Newton/TikTokPy/blob/afb69838685fa25714a2029bafcc7972696679fd/tests/test_video.py) проверяет извлечение объекта; это не тест публикации комментариев. [PR 91](https://github.com/Russell-Newton/TikTokPy/pull/91) исправляет изменившуюся страницу профиля.
- **Uploader:** [upload.py](https://github.com/wkaisertexas/tiktok-uploader/blob/1cea92dd4c815dbd5127ee4b3a0a7b6fabbaa5f0/src/tiktok_uploader/upload.py) управляет страницей через Playwright и AuthBackend. Выбор библиотеки по слову uploader не добавляет comment API. [Issue 238](https://github.com/wkaisertexas/tiktok-uploader/issues/238) описывает молчаливый сбой публикации видео.
- **npm scraper:** [тесты](https://github.com/drawrowfly/tiktok-scraper/blob/38596d7f2a514d3f9cff4535473390490cb14e43/src/core/TikTok.test.ts) подменяют request-клиенты. [package.json](https://github.com/drawrowfly/tiktok-scraper/blob/38596d7f2a514d3f9cff4535473390490cb14e43/package.json) использует `request`/`request-promise`; переносить такую основу в новый сервис нецелесообразно.
- **Официальный Research wrapper:** [api.py](https://github.com/tiktok/tiktok-research-api-wrapper/blob/bc2ce0b326f6c36a0c1255079e463dcc1ba74dea/tiktok-research-api-python/tiktok_research_api/api.py) подтверждает query/cursor/search_id и client credentials. В изученном коде некоторые ошибки логируют body/headers, а повторение не всегда различает постоянные ошибки и transient failure. Наш адаптер возвращает только нормализованный код, ограничивает повторы фоновых чтений и не повторяет неоднозначный OAuth exchange.

### PyPI / npm: репозиторий не равен установленному пакету

| Реестр | Пакет / проверенная версия | Публикация версии, UTC |
|---|---|---|
| [PyPI](https://pypi.org/project/TikTokApi/) | TikTokApi 7.3.3 | 2026-04-01 |
| [PyPI](https://pypi.org/project/tiktokapipy/) | tiktokapipy 0.2.4.post2 | 2023-11-20 |
| [PyPI](https://pypi.org/project/pyktok/) | pyktok 0.0.31 | 2025-02-21 |
| [PyPI](https://pypi.org/project/tiktok-uploader/) | tiktok-uploader 1.2.0 | 2026-02-12 |
| [PyPI](https://pypi.org/project/TikTokResearchApi/) | TikTokResearchApi 1.0.4 | 2024-12-02 |
| [npm](https://www.npmjs.com/package/tiktok-scraper) | tiktok-scraper 1.4.36 | 2021-08-14 |

Пакеты проверены через JSON API соответствующих реестров, не только по README. Для экспериментального reader закреплены TikTokApi 7.3.3 и Playwright 1.59.0. Базовый backend не зависит от них.

## 3. Матрица доказательности

| Функция | Категория | Что подтверждено / что остаётся проверить |
|---|---|---|
| OAuth, refresh/revoke, свои публичные видео | **1. Подтверждено и доступно при разрешениях** | Официальные контракты и наш код; в этой среде нужны ключи/одобрение приложения для live-проверки |
| Мультиаккаунты, очередь, расписания, фильтр, статусы и ручной учёт | **1. Подтверждено локально** | Проверяются собственными тестами; это функции нашей системы |
| Research video/comment reads | **1. Документировано с ограниченным доступом** | Не коммерческий источник; отдельно включаемый read-only endpoint |
| Комментарии к своим видео / рекламные ответы Business | **1. Документировано, в MVP не интегрировано** | Нельзя считать работающим в нашем приложении до отдельного Business подключения и live-контракта |
| Публичный keyword-search через TikTokApi | **2. Требует проверки** | Есть исходник и адаптер; фактический результат одного запуска в [live-search.json](evidence/live-search.json). Даже один успех не доказывает устойчивость |
| Discovery/Mentions для конкретного коммерческого приложения | **2. Требует проверки** | Нужны подтверждённые продукты, права, scope/coverage и реальные примеры ответов |
| Понимание видео по изображению/звуку | **2. Расширение** | В MVP только объяснимый отбор по описанию/хештегам; ASR/vision не реализованы |
| Автокомментарий под любым чужим найденным роликом | **3. Не поддерживается выбранными публичными интеграциями** | API явно возвращает 409; нет фиктивной отправки или подмены статуса |
| Полная автономность при captcha/отозванном согласии | **3. Не гарантируется** | Работа останавливается, нужен владелец или изменение состояния внешней платформы |

## 4. Выбор компонентов и практическая альтернатива

Основа — собственные узкие HTTP-adapters к документированным операциям, а не перенос целого scraper-проекта. Из изученных проектов использованы идеи разделения адаптера/парсера, ограниченной пагинации и отдельных live-контрактов. Код сторонних проектов в runtime не скопирован. TikTokApi подключается как лицензированный опциональный пакет; сведения о лицензиях — [THIRD_PARTY_API.md](THIRD_PARTY_API.md).

Для исходного сценария сейчас практичен режим: разрешённый источник роликов → тематический отбор → готовая очередь → ручная публикация с нужного аккаунта. Импорт поддерживает данные из заранее разрешённого источника. Экспериментальный поиск не повышает права OAuth и не использует cookies подключённых владельцев.

Если нужна официальная автономная работа именно с комментариями, ближайший продуктовый сценарий — обработка входящих комментариев **на своих видео** через отдельно одобренный Accounts/Organic API. Его следует внедрять после получения соответствующего доступа, с проверкой владения видео, подтверждением comment ID и отдельным состоянием неопределённого результата. Для коммерческого обнаружения трендов следующий кандидат — Business Discovery; произвольную выдачу и запись под чужими роликами из этого не следует обещать.

Производительность оценивалась архитектурно, без выдуманных сравнительных RPS: HTTP-чтение дешевле отдельных браузеров; 10 аккаунтам достаточно двух рабочих процессов чтения, экспериментальному reader — одного из-за памяти Chromium. Реальные квоты и задержки TikTok важнее количества workers. Для замеров внешних API нужны одинаковые условия доступа; такой benchmark здесь не проводился.
