"""Playwright-парсер поиска TikTok и модуль автоматической отправки комментариев.

Отдельная браузерная сессия и прокси для каждого аккаунта. Подключение выполняется
во вкладке «Аккаунты», сессии сохраняются в data/session/accounts/.

CLI для отладки без веб-интерфейса:
    python -m app.workers.tiktok_search login
    python -m app.workers.tiktok_search check
    python -m app.workers.tiktok_search search "#coffee" "кофейня москва" [--visible]
    python -m app.workers.tiktok_search post "https://www.tiktok.com/@user/video/123" "Привет фулл тут gjrjgi" [--visible]
"""
import asyncio
import json
import random
import re
import sys
import uuid
from ipaddress import ip_address
from pathlib import Path
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

from playwright.async_api import BrowserContext, Page, Response, async_playwright

from app.services import account_sessions
from app.services import settings as app_settings
from app.workers.proxy_relay import browser_proxy_url

TIKTOK = "https://www.tiktok.com"
_VIDEO_HREF_RE = re.compile(r"/@([^/?#]+)/video/(\d+)")
# Разметка капчи меняется, поэтому ищем любой видимый элемент с "captcha" в id или классе.
_CAPTCHA_SELECTOR = '[id*="captcha"]:visible, [class*="captcha"]:visible'
CAPTCHA_WAIT_S = 180  # сколько ждать, пока человек пройдёт капчу в видимом окне
_API_MARKERS = ("/api/search/", "/api/challenge/item_list")


class SessionError(RuntimeError):
    pass


class CaptchaError(RuntimeError):
    pass


class BlockedError(CaptchaError):
    """TikTok показал «Страница недоступна» вместо выдачи. Как и капча, останавливает поиск целиком."""


class EmptyResultError(RuntimeError):
    pass


@dataclass
class FoundVideo:
    video_id: str
    url: str
    author: str | None
    description: str | None = None
    views: int | None = None


def proxy_settings(proxy_url: str | None) -> dict | None:
    """'http://user:pass@host:port' / 'socks5://host:port' → формат Playwright."""
    if not proxy_url:
        return None
    p = urlparse(proxy_url)
    if not p.scheme or not p.hostname or not p.port:
        raise ValueError("Некорректный прокси: укажите протокол, адрес и порт")
    settings = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        settings["username"] = unquote(p.username)
        settings["password"] = unquote(p.password or "")
    return settings


@asynccontextmanager
async def open_context(
    headless: bool = True, use_state: bool = True, account_id: int | None = None,
    proxy_url: str | None = None, state_path: Path | None = None, use_system_chrome: bool | None = None,
) -> AsyncIterator[BrowserContext]:
    browser_dir = None
    if proxy_url is None:
        profile = account_sessions.profile(account_id)
        proxy_url, state_path, use_system_chrome = profile.proxy_url, profile.state_path, profile.use_system_chrome
        browser_dir = profile.browser_dir if use_state else None
    if not proxy_url:
        raise ValueError("Для работы аккаунта обязателен прокси")
    async with browser_proxy_url(proxy_url) as browser_proxy, async_playwright() as pw:
        channel = "chrome" if use_system_chrome else None
        if browser_dir:
            browser_dir.mkdir(parents=True, exist_ok=True)
            marker = browser_dir / "session-imported"
            ctx = await pw.chromium.launch_persistent_context(
                str(browser_dir), headless=headless, proxy=proxy_settings(browser_proxy),
                channel=channel, locale="ru-RU", viewport={"width":1366,"height":860},
            )
            try:
                if not marker.exists():
                    await ctx.set_storage_state(state_path)
                    marker.write_text("1", encoding="ascii")
                ctx.set_default_timeout(30_000)
                yield ctx
            finally:
                try:
                    await asyncio.wait_for(ctx.close(), 10)
                except Exception:
                    pass
            return
        try:
            browser = await pw.chromium.launch(
                headless=headless, proxy=proxy_settings(browser_proxy), channel=channel
            )
        except Exception as e:
            if channel and "is not found" in str(e):
                raise RuntimeError(
                    "Google Chrome не найден. Установите его или выберите встроенный Chromium "
                    "в окне подключения аккаунта"
                ) from e
            raise
        try:
            state = str(state_path) if use_state and state_path and state_path.exists() else None
            ctx = await browser.new_context(
                storage_state=state, locale="ru-RU", viewport={"width": 1366, "height": 860}
            )
            ctx.set_default_timeout(30_000)
            yield ctx
        finally:
            try:
                await asyncio.wait_for(browser.close(), 10)
            except Exception:
                # Cleanup must not hide a confirmed result or a pre-click error.
                pass


async def _has_session(ctx: BrowserContext) -> bool:
    return any(c["name"] == "sessionid" and c["value"] for c in await ctx.cookies(TIKTOK))


# ---------- Сессия ----------

class ConnectionCancelled(RuntimeError):
    pass


async def connect_with_session(proxy_url: str, state_path: Path, chrome: bool,
                               on_stage: Callable, should_stop: Callable) -> str:
    """Проверка импортированной сессии в отдельном браузере через заданный прокси."""
    if not proxy_url or not state_path.exists():
        raise ValueError("Для подключения нужны прокси и sessionid")
    if should_stop():
        raise ConnectionCancelled()
    on_stage("Проверяем прокси…")
    async with open_context(headless=True, use_state=True, proxy_url=proxy_url,
                            state_path=state_path, use_system_chrome=chrome) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto("https://api.ipify.org?format=json", timeout=30_000)
            ip_address(json.loads(await page.inner_text("body"))["ip"])
        except Exception as e:
            raise RuntimeError(_explain_proxy_error(str(e), proxy_url)) from None
        if should_stop():
            raise ConnectionCancelled()
        on_stage("Проверяем sessionid через прокси…")
        try:
            await page.goto(TIKTOK, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            raise SessionError("Не удалось открыть TikTok через этот прокси. Проверьте подключение") from None
        if should_stop():
            raise ConnectionCancelled()
        if not await _has_session(ctx):
            raise SessionError("TikTok не принял sessionid. Скопируйте свежее значение из своего браузера")
        info = await _account_info(ctx)
        username = ((info or {}).get("data") or {}).get("username")
        if not info or info.get("message") == "error" or not isinstance(username, str) or not username.strip():
            raise SessionError("TikTok не подтвердил вход. Убедитесь, что вы вошли в нужный аккаунт, "
                               "и скопируйте свежее значение sessionid")
        if should_stop():
            raise ConnectionCancelled()
        on_stage("Аккаунт подтверждён. Сохраняем подключение…")
        await ctx.storage_state(path=str(state_path))
        return username.strip().lstrip("@").lower()


async def connect_account(proxy_url: str, state_path: Path, chrome: bool,
                          on_stage: Callable, should_stop: Callable, timeout_s: int = 300) -> str:
    """Новый изолированный браузер. Прокси обязателен, прямого сетевого fallback нет."""
    if not proxy_url:
        raise ValueError("Укажите прокси перед входом")
    on_stage("Проверяем прокси…")
    async with open_context(headless=False, use_state=False, proxy_url=proxy_url,
                            state_path=state_path, use_system_chrome=chrome) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto("https://api.ipify.org?format=json", timeout=30_000)
            ip_address(json.loads(await page.inner_text("body"))["ip"])
        except Exception as e:
            raise RuntimeError(_explain_proxy_error(str(e), proxy_url)) from None
        if should_stop():
            raise ConnectionCancelled()
        on_stage("Прокси работает. Открываем TikTok…")
        try:
            await page.goto(f"{TIKTOK}/login", wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            raise RuntimeError("Через этот прокси не удалось открыть TikTok. Проверьте адрес и доступ к сайту") from None
        on_stage("Войдите в TikTok в открывшемся окне. Ожидаем авторизацию…")
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if should_stop():
                raise ConnectionCancelled()
            if page.is_closed():
                raise SessionError("Окно закрыто до завершения входа. Аккаунт не добавлен")
            if await _has_session(ctx):
                info = await _account_info(ctx)
                username = ((info or {}).get("data") or {}).get("username")
                if info and info.get("message") != "error" and isinstance(username, str) and username.strip():
                    on_stage("Проверяем аккаунт и сохраняем подключение…")
                    await ctx.storage_state(path=str(state_path))
                    return username.strip().lstrip("@").lower()
            await asyncio.sleep(1)
    raise SessionError("Время входа истекло. Аккаунт не добавлен, начните подключение заново")

async def login_interactive(timeout_s: int = 300) -> str:
    raise SessionError("Подключайте аккаунт в панели: «Аккаунты» → «Добавить аккаунт». Сначала нужен прокси")


async def _account_info(ctx: BrowserContext) -> dict | None:
    """Ответ TikTok об аккаунте. Кука sessionid может лежать в браузере и после того,
    как сессия умерла, поэтому спрашиваем сервер."""
    try:
        r = await ctx.request.get(f"{TIKTOK}/passport/web/account/info/?aid=1459", timeout=20_000)
        return await r.json()
    except Exception:
        return None


async def check_session(account_id: int | None = None) -> tuple[bool, str, str | None]:
    """(сессия действует, текст для UI, ник аккаунта, если TikTok его отдал)."""
    profile = account_sessions.profile(account_id)
    async with open_context(account_id=profile.account_id) as ctx:
        page = await ctx.new_page()
        await page.goto(TIKTOK, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if not await _has_session(ctx):
            return False, "Сессия истекла: войдите заново", None

        info = await _account_info(ctx)
        if info is None:
            raise RuntimeError("TikTok не ответил на проверку аккаунта. Повторите проверку позже")
        if info and info.get("message") == "error":
            reason = (info.get("data") or {}).get("description") or "сессия недействительна"
            return False, f"TikTok не принял сессию ({reason}). Подключите аккаунт заново через прокси", None

        username = ((info or {}).get("data") or {}).get("username")
        if not username or username.lower() != profile.username.lower():
            return False, "TikTok не подтвердил ожидаемый аккаунт. Подключите его заново", None
        await ctx.storage_state(path=str(profile.state_path))
        return True, f"Сессия активна: @{username}", username


def _direct_proxy_ip(proxy: str) -> str | None:
    """IP через прокси без браузера (только HTTP-прокси). None, если не получилось."""
    if not proxy.startswith(("http://", "https://")):
        return None
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        with opener.open("https://api.ipify.org", timeout=20) as r:
            return r.read().decode().strip() or None
    except Exception:
        return None


def _explain_proxy_error(err: str, proxy: str | None) -> str:
    """Коды ошибок Chromium → понятный текст."""
    host = urlparse(proxy).netloc.rsplit("@", 1)[-1] if proxy else "прокси"
    if "ERR_PROXY_CONNECTION_FAILED" in err or "ERR_CONNECTION_REFUSED" in err or "ERR_CONNECTION_TIMED_OUT" in err:
        return (f"Компьютер не может подключиться к серверу прокси {host}. Логин и пароль тут ни при чём: "
                "соединение не доходит до прокси. Проверьте, не мешают ли VPN, антивирус или провайдер, "
                "или попросите у продавца другой сервер или порт.")
    if "ERR_TUNNEL_CONNECTION_FAILED" in err or "407" in err or "ERR_PROXY_AUTH" in err:
        return "Прокси отклонил подключение: проверьте логин и пароль или не закончился ли тариф."
    if "ERR_NAME_NOT_RESOLVED" in err:
        return f"Адрес прокси {host} не найден. Проверьте, нет ли опечатки."
    if "ERR_SOCKS_CONNECTION_FAILED" in err:
        return "SOCKS5-прокси не отвечает. Проверьте тип прокси: возможно, это HTTP."
    return f"Прокси не отвечает: {err.splitlines()[0]}"


async def test_proxy() -> str:
    """Через какой IP ходит парсер и открывается ли TikTok."""
    profile = account_sessions.profile()
    proxy = profile.proxy_url
    async with open_context(use_state=False, account_id=profile.account_id) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto("https://api.ipify.org?format=json", timeout=30_000)
            ip = json.loads(await page.inner_text("body"))["ip"]
        except Exception as e:
            msg = _explain_proxy_error(str(e), proxy)
            # Проверяем прокси без браузера: если так он работает, браузер блокирует что-то на компьютере.
            direct_ip = await asyncio.to_thread(_direct_proxy_ip, proxy) if proxy else None
            if direct_ip:
                msg = (f"Прокси рабочий (напрямую отвечает с IP {direct_ip}), но браузер панели не может к нему "
                       "подключиться. Обычно это антивирус или фаервол. Включите ниже галочку "
                       "«Использовать установленный Google Chrome» или добавьте в исключения антивируса "
                       r"папку %LOCALAPPDATA%\ms-playwright")
            raise RuntimeError(msg) from e
        try:
            resp = await page.goto(TIKTOK, wait_until="domcontentloaded", timeout=45_000)
            tiktok = "открывается" if resp and resp.ok else f"ответ {resp.status if resp else 'пустой'}"
        except Exception:
            tiktok = "не открывается"
    via = f"через прокси {app_settings.mask_proxy(proxy)}" if proxy else "без прокси"
    browser = "Google Chrome" if profile.use_system_chrome else "встроенный браузер"
    return f"IP {ip} ({via}, {browser}), TikTok {tiktok}"


# ---------- Поиск ----------

def search_url(query: str) -> str:
    q = query.strip()
    if q.startswith("#"):
        return f"{TIKTOK}/tag/{quote(q[1:])}"
    return f"{TIKTOK}/search/video?q={quote(q)}"


def _parse_item(item: dict) -> FoundVideo | None:
    vid = str(item.get("id") or "")
    author = (item.get("author") or {}).get("uniqueId")
    if not vid.isdigit() or not author:
        return None
    return FoundVideo(
        video_id=vid,
        url=f"{TIKTOK}/@{author}/video/{vid}",
        author=author,
        description=item.get("desc") or None,
        views=(item.get("stats") or {}).get("playCount"),
    )


def _extract_items(data: dict) -> list[dict]:
    items = list(data.get("item_list") or data.get("itemList") or [])
    for row in data.get("data") or []:  # /api/search/...: [{"type": 1, "item": {...}}]
        if isinstance(row, dict) and isinstance(row.get("item"), dict):
            items.append(row["item"])
    return items


async def _raise_if_blocked(page: Page, visible: bool) -> None:
    if "/login" in page.url:
        raise SessionError("TikTok требует вход. Подключите аккаунт заново во вкладке «Аккаунты»")
    captcha = page.locator(_CAPTCHA_SELECTOR).or_(page.get_by_text(re.compile(
        r"Передвиньте ползунок, чтобы совместить пазл|Перетащите ползунок|Drag the slider to fit the puzzle|Rotate.*(?:image|puzzle)", re.I,
    )).and_(page.locator(":visible")))
    if not await captcha.count():
        return
    if not visible:
        raise CaptchaError("TikTok показал капчу. Запустите задачу в режиме «видимое окно» и пройдите её вручную")
    from app.services.logs import log_event
    log_event("tasks", "TikTok показал капчу. Пройдите её в открытом окне Chrome; ожидаем до трёх минут")
    try:
        await captcha.first.wait_for(state="hidden", timeout=CAPTCHA_WAIT_S * 1000)
    except Exception as e:
        raise CaptchaError(f"Капча не пройдена за {CAPTCHA_WAIT_S} с") from e
    await page.wait_for_timeout(2000)


async def _wait_search_ready(page: Page, found: dict[str, FoundVideo], visible: bool,
                             timeout_s: float = 90) -> None:
    """Hydration through a proxy can take longer than the pagination idle limit."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        checking_at = asyncio.get_running_loop().time()
        await _raise_if_blocked(page, visible)
        await _recover_error_page(page, visible)
        # Manual challenges have their own timeout, separate from page loading.
        deadline += asyncio.get_running_loop().time() - checking_at
        if found or await page.locator('a[href*="/video/"]').count():
            return
        if await page.get_by_text(re.compile(r"Ничего не найдено|No results found", re.I)).first.is_visible():
            return
        await page.wait_for_timeout(1000)
    raise EmptyResultError(
        "TikTok не загрузил выдачу за 90 секунд. "
        "Проверьте загрузку TikTok через прокси и повторите поиск в видимом окне"
    )


_ERROR_PAGE_RE = re.compile(r"Страница недоступна|Что-то пошло не так|Page not available|Something went wrong", re.I)
_RETRY_SELECTOR = (
    'button:has-text("Попробовать"), button:has-text("Обновить"), '
    'button:has-text("Try again"), button:has-text("Refresh")'
)


async def _error_page_shown(page: Page) -> bool:
    try:
        return await page.get_by_text(_ERROR_PAGE_RE).first.is_visible()
    except Exception:
        return False


async def _recover_error_page(page: Page, visible: bool) -> None:
    """TikTok иногда отвечает «Страница недоступна». Дважды жмём «Попробовать ещё раз»;
    в видимом окне даём человеку разобраться самому, как с капчей."""
    if not await _error_page_shown(page):
        return
    for _ in range(2):
        try:
            await page.locator(_RETRY_SELECTOR).first.click(timeout=5000)
        except Exception:
            await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(random.uniform(3000, 5000))
        if not await _error_page_shown(page):
            return
    if visible:
        try:
            await page.locator('a[href*="/video/"]').first.wait_for(timeout=CAPTCHA_WAIT_S * 1000)
            return
        except Exception:
            pass
    raise BlockedError(
        "TikTok показал ошибку загрузки страницы поиска. "
        "По этому экрану нельзя определить, истёк ли вход или ограничен ли IP. "
        "Запустите поиск в видимом окне: проверьте загрузку страницы и пройдите капчу, если она появится."
    )


async def _collect_dom(page: Page, found: dict[str, FoundVideo]) -> None:
    hrefs = await page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)")
    for href in hrefs:
        if m := _VIDEO_HREF_RE.search(href):
            author, vid = m.groups()
            found.setdefault(vid, FoundVideo(video_id=vid, url=f"{TIKTOK}/@{author}/video/{vid}", author=author))


_LOAD_MORE_SELECTOR = (
    '[data-e2e="search-load-more"], button:has-text("Загрузить ещё"), '
    'button:has-text("Загрузить еще"), button:has-text("Load more")'
)
_MAX_STALE = 5  # сколько подгрузок подряд без новых роликов терпим, прежде чем остановиться


async def _load_more(page: Page) -> None:
    """Следующая порция выдачи. На странице поиска TikTok подгружает ролики кнопкой
    «Загрузить ещё», на странице хэштега прокруткой, поэтому пробуем оба способа."""
    btn = page.locator(_LOAD_MORE_SELECTOR).first
    try:
        if await btn.is_visible():
            await btn.scroll_into_view_if_needed(timeout=3000)
            await btn.click(timeout=5000)
            return
    except Exception:
        pass
    try:
        await page.locator('a[href*="/video/"]').last.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    # Колесо крутит тот блок, над которым курсор, поэтому ставим курсор в центр выдачи.
    vp = page.viewport_size or {"width": 1366, "height": 860}
    await page.mouse.move(vp["width"] / 2, vp["height"] / 2)
    await page.mouse.wheel(0, 3000)


async def search_videos(ctx: BrowserContext, query: str, limit: int = 30, visible: bool = False) -> list[FoundVideo]:
    found: dict[str, FoundVideo] = {}

    async def on_response(resp: Response) -> None:
        if not any(m in resp.url for m in _API_MARKERS):
            return
        try:
            data = await resp.json()
        except Exception:
            return
        for item in _extract_items(data):
            if v := _parse_item(item):
                found[v.video_id] = v

    page = await ctx.new_page()
    page.on("response", on_response)
    try:
        if visible:
            await page.bring_to_front()
        await page.goto(search_url(query), wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(random.uniform(2000, 3500))
        await _raise_if_blocked(page, visible)
        await _recover_error_page(page, visible)
        await _wait_search_ready(page, found, visible)

        stale = 0
        while len(found) < limit and stale < _MAX_STALE:
            before = len(found)
            await _collect_dom(page, found)
            await _load_more(page)
            await page.wait_for_timeout(random.uniform(2000, 3500))  # ждём подгрузку выдачи
            await _raise_if_blocked(page, visible)
            await _recover_error_page(page, visible)
            await _collect_dom(page, found)
            stale = stale + 1 if len(found) == before else 0
        if not found:
            raise EmptyResultError(
                "0 роликов: TikTok не отдал выдачу. Проверьте подключение во вкладке «Аккаунты» "
                "или запустите поиск в видимом окне"
            )
        return list(found.values())[:limit]
    finally:
        await page.close()


async def search_many(
    queries: list[str],
    limit: int,
    on_results: Callable[[str, list[FoundVideo]], None],
    on_error: Callable[[str, Exception], None],
    visible: bool = False,
    account_id: int | None = None,
) -> None:
    profile = account_sessions.profile(account_id)
    async with open_context(headless=not visible, account_id=profile.account_id) as ctx:
        for i, query in enumerate(queries):
            if i:
                await asyncio.sleep(random.uniform(3, 6))
            try:
                videos = await search_videos(ctx, query, limit, visible)
                # Keep cookies acquired after a manual challenge for the next task.
                await ctx.storage_state(path=str(profile.state_path))
                on_results(query, videos)
            except (SessionError, CaptchaError):
                raise
            except Exception as e:
                on_error(query, e)


# ---------- Автоматическая отправка комментария ----------

class PostUncertainError(RuntimeError):
    """Клик мог отправить комментарий. Повтор возможен только после ручной проверки."""


class CommentRejectedError(RuntimeError):
    """TikTok явно отказал в публикации."""


def _publication_response(resp: Response, video_id: str, text: str) -> bool:
    """Принимаем только ответ на публикацию именно текущего текста под текущим видео."""
    parsed = urlparse(resp.url)
    if not (parsed.hostname or "").endswith(".tiktok.com") or parsed.path.rstrip("/") != "/api/comment/publish":
        return False
    if resp.request.method != "POST":
        return False
    fields = parse_qs(parsed.query)
    body = resp.request.post_data or ""
    try:
        extra = json.loads(body)
        if isinstance(extra, dict):
            fields.update({k: [str(v)] for k, v in extra.items()})
    except (ValueError, TypeError):
        fields.update(parse_qs(body))
    # Match the video here; validate the returned text and author below. TikTok
    # may normalize request text or reject it, and that response must be read.
    return fields.get("aweme_id") == [video_id]


def _confirmed_comment(data: dict, video_id: str, text: str, username: str) -> str:
    if not isinstance(data, dict):
        raise PostUncertainError("TikTok вернул непонятный ответ. Проверьте публикацию вручную")
    if isinstance(data.get("status_code"), int) and data["status_code"] != 0:
        raise CommentRejectedError(f"TikTok отклонил комментарий (код {data['status_code']}). Автоотправка остановлена")
    comment = data.get("comment") or {}
    user = comment.get("user") or {}
    author = user.get("unique_id") or user.get("uniqueId") or ""
    cid = str(comment.get("cid") or "")
    if (data.get("status_code") != 0 or not cid.isdigit() or comment.get("text") != text
            or str(comment.get("aweme_id", video_id)) != video_id or author.lower() != username.lower()):
        raise PostUncertainError("TikTok не подтвердил текст и автора опубликованного комментария. "
                                 "Проверьте ролик вручную перед повтором")
    return cid


_COMMENT_INPUT = (
    '[data-e2e="comment-input"][contenteditable="true"]:visible, '
    '[data-e2e="comment-input"] [contenteditable="true"]:visible, '
    '[data-e2e="comment-input"] textarea:visible, '
    'textarea[data-e2e="comment-input"]:visible'
)


async def _prepare_comment_form(page: Page, visible: bool, timeout_s: float = 90):
    deadline = asyncio.get_running_loop().time() + timeout_s
    field = page.locator(_COMMENT_INPUT).first
    cookie_buttons = page.get_by_role("button", name=re.compile(
        r"^Отклонить использование дополнительных файлов cookie$|^Decline optional cookies$|^Reject optional cookies$",
        re.I,
    ))
    comments_tab = page.get_by_text(re.compile(r"^Комментарии(?:\s*\(.*\))?$|^Comments(?:\s*\(.*\))?$", re.I)).and_(page.locator(":visible")).first
    comments_icon = page.get_by_role("button", name=re.compile(r"comment|коммент", re.I)).first
    comments_link = page.locator('[data-e2e="comment-icon"]:visible, [data-e2e="browse-comment"]:visible').first
    opened_comments = False
    while asyncio.get_running_loop().time() < deadline:
        checking_at = asyncio.get_running_loop().time()
        await _raise_if_blocked(page, visible)
        deadline += asyncio.get_running_loop().time() - checking_at
        if await _error_page_shown(page):
            raise BlockedError("TikTok не открыл ролик. Публикация не начата")
        comment_status = await page.evaluate("""() => {
            const element = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
            if (!element) return null;
            try {
                const scope = JSON.parse(element.textContent).__DEFAULT_SCOPE__ || {};
                return scope['webapp.video-detail']?.itemInfo?.itemStruct?.itemCommentStatus ?? null;
            } catch { return null; }
        }""")
        if comment_status == 3:
            raise CommentRejectedError("На этом ролике TikTok отключил комментарии. Публикация не начата; выберите другой ролик")
        if await cookie_buttons.first.is_visible():
            await cookie_buttons.first.click(timeout=5000)
        if await field.is_visible():
            return field
        if await comments_tab.is_visible():
            await comments_tab.click(timeout=5000)
        elif not opened_comments:
            if await comments_icon.is_visible():
                await comments_icon.click(timeout=5000)
                opened_comments = True
            elif await comments_link.is_visible():
                await comments_link.click(timeout=5000)
                opened_comments = True
        if await page.get_by_text(re.compile(r"Комментарии отключены|Comments (?:are )?turned off|Comments disabled", re.I)).first.is_visible():
            raise CommentRejectedError("Автор отключил комментарии к этому ролику. Публикация не начата")
        await page.wait_for_timeout(1000)
    raise RuntimeError("TikTok не показал поле комментария за 90 секунд. Публикация не начата")


async def post_comment_on_video(
    ctx: BrowserContext, video_url: str, comment_text: str, visible: bool = False,
    expected_username: str | None = None, before_post: Callable | None = None,
    on_stage: Callable | None = None,
) -> str:
    """Один клик без повторов. Возвращает ID только при подтверждении TikTok."""
    parsed = urlparse(video_url)
    match = _VIDEO_HREF_RE.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "www.tiktok.com" or not match:
        raise ValueError("Для автоотправки нужна прямая ссылка https://www.tiktok.com/@автор/video/ID")
    if not comment_text.strip():
        raise ValueError("Текст комментария пуст")
    video_id = match.group(2)
    page = await ctx.new_page()
    attempted = False
    stage = on_stage or (lambda message: None)
    diagnostics = {"video_id": video_id, "requests": [], "responses": []}
    response_jobs = []
    comments_ready = asyncio.Event()

    async def observe_comments(response):
        if response.request.method != "GET" or urlparse(response.url).path.rstrip("/") != "/api/comment/list":
            return
        try:
            if parse_qs(urlparse(response.url).query).get("aweme_id") != [video_id]:
                return
            data = await response.json()
            if response.ok and data.get("status_code") == 0 and isinstance(data.get("comments"), list):
                comments_ready.set()
        except Exception:
            pass

    def observe_request(request):
        if "/api/comment/publish" not in urlparse(request.url).path:
            return
        fields = parse_qs(urlparse(request.url).query)
        body = request.post_data or ""
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                fields.update({k: [str(v)] for k, v in payload.items()})
        except (ValueError, TypeError):
            fields.update(parse_qs(body))
        diagnostics["requests"].append({
            "path": urlparse(request.url).path, "method": request.method,
            "field_names": sorted(key for key in fields if re.fullmatch(r"[A-Za-z0-9_]{1,64}", key)),
            "video_matches": fields.get("aweme_id") == [video_id],
            "text_matches": fields.get("text") == [comment_text],
        })

    async def observe_response(response):
        if "/api/comment/publish" not in urlparse(response.url).path:
            return
        summary = {"path": urlparse(response.url).path, "http": response.status,
                   "content_type": response.headers.get("content-type"),
                   "content_length": response.headers.get("content-length")}
        diagnostics["responses"].append(summary)
        try:
            summary["bytes"] = len(await response.body())
            data = await response.json()
            summary["status_code"] = data.get("status_code")
            comment = data.get("comment") or {}
            user = comment.get("user") or {}
            summary.update({"has_comment_id": bool(comment.get("cid")),
                            "text_matches": comment.get("text") == comment_text,
                            "author_matches": (user.get("unique_id") or user.get("uniqueId") or "").lower() == (expected_username or "").lower()})
        except Exception as exc:
            summary["decode_error"] = type(exc).__name__

    page.on("request", observe_request)
    page.on("response", lambda response: response_jobs.append(asyncio.create_task(observe_comments(response)))
            if urlparse(response.url).path.rstrip("/") == "/api/comment/list" else None)
    page.on("response", lambda response: response_jobs.append(asyncio.create_task(observe_response(response)))
            if "/api/comment/publish" in urlparse(response.url).path else None)

    async def save_diagnostics(failure_type):
        from app.config import DATA_DIR
        from app.services.logs import log_event
        diagnostics["failure_type"] = failure_type
        try:
            directory = DATA_DIR / "diagnostics"
            directory.mkdir(parents=True, exist_ok=True)
            name = f"comment-{video_id}-{uuid.uuid4().hex[:8]}"
            (directory / f"{name}.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.screenshot(path=str(directory / f"{name}.png"), timeout=3000)
            log_event("drafts", f"Диагностика публикации сохранена: {name}; запросов: {len(diagnostics['requests'])}, ответов: {len(diagnostics['responses'])}")
        except Exception:
            pass
    try:
        if visible:
            await page.bring_to_front()
        stage("Загружаем ролик через прокси")
        await page.goto(video_url, wait_until="domcontentloaded", timeout=45_000)
        stage("Открываем комментарии и ждём поле ввода")
        field = await _prepare_comment_form(page, visible)
        stage("Проверяем загрузку комментариев перед отправкой")
        deadline = asyncio.get_running_loop().time() + 45
        while not comments_ready.is_set() and asyncio.get_running_loop().time() < deadline:
            checking_at = asyncio.get_running_loop().time()
            stage("Ждём загрузку комментариев; если появилась капча, пройдите её в окне Chrome")
            await _raise_if_blocked(page, visible)
            deadline += asyncio.get_running_loop().time() - checking_at
            try:
                await asyncio.wait_for(comments_ready.wait(), .5)
            except asyncio.TimeoutError:
                pass
        if not comments_ready.is_set():
            await save_diagnostics("CommentsNotReady")
            raise SessionError(
                "TikTok не загрузил комментарии. Отправка не начата, черновик сохранён. "
                "Проверьте TikTok в браузере аккаунта: может потребоваться проверка или повторный вход"
            )
        await field.fill("", timeout=15_000)
        await field.press_sequentially(comment_text, delay=20, timeout=15_000)
        button = page.locator('[data-e2e="comment-post"]:visible, [data-e2e="comment-post-button"]:visible').first
        await button.click(trial=True, timeout=10_000)
        # Проверяем фактического пользователя в том же контексте прямо перед публикацией.
        stage("Проверяем аккаунт перед публикацией")
        info = await _account_info(ctx)
        username = ((info or {}).get("data") or {}).get("username")
        if not info or info.get("message") == "error" or not username:
            raise SessionError("TikTok не подтвердил аккаунт. Проверьте вход во вкладке «Аккаунты»")
        if expected_username and username.lower() != expected_username.lower():
            raise SessionError("В браузерной сессии другой TikTok-аккаунт. Автоотправка остановлена")
        await _raise_if_blocked(page, visible)
        if before_post:
            before_post()
        stage("Отправляем комментарий и ждём подтверждение TikTok")
        async with page.expect_response(
            lambda resp: _publication_response(resp, video_id, comment_text), timeout=25_000,
        ) as pending:
            attempted = True
            await button.click(timeout=10_000)
        response = await pending.value
        if not response.ok:
            raise PostUncertainError("Ответ TikTok не получен успешно. Проверьте публикацию вручную")
        body = b"" if response.headers.get("content-length") == "0" else await response.body()
        if not body.strip():
            raise PostUncertainError(
                "TikTok вернул пустой ответ на отправку комментария (HTTP 200). "
                "Публикация не подтверждена: проверьте её в TikTok перед повторной отправкой"
            )
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise PostUncertainError("TikTok вернул нечитаемый ответ на публикацию. Проверьте комментарий в TikTok перед повторной отправкой") from None
        return _confirmed_comment(payload, video_id, comment_text, username)
    except CommentRejectedError:
        raise
    except PostUncertainError as e:
        await save_diagnostics(type(e).__name__)
        raise
    except Exception as e:
        if attempted:
            await save_diagnostics(type(e).__name__)
            raise PostUncertainError("Не удалось подтвердить публикацию после нажатия кнопки. "
                                     "Проверьте ролик вручную; автоматического повтора не будет") from e
        raise
    finally:
        for job in response_jobs:
            if not job.done():
                job.cancel()
        if response_jobs:
            await asyncio.gather(*response_jobs, return_exceptions=True)
        try:
            await asyncio.wait_for(page.close(), 5)
        except Exception:
            pass


# ---------- CLI ----------

async def _cli(argv: list[str]) -> None:
    cmd, *args = argv or ["help"]
    if cmd == "login":
        print(await login_interactive())
    elif cmd == "check":
        print((await check_session())[1])  # noqa
    elif cmd == "search" and args:
        await search_many(
            [a for a in args if a != "--visible"], 20, visible="--visible" in args,
            on_results=lambda q, vs: [print(q, v.url, v.views, (v.description or "")[:60]) for v in vs],
            on_error=lambda q, e: print(f"{q}: ошибка {e}"),
        )
    elif cmd == "post" and len(args) >= 2:
        url = args[0]
        text = " ".join([a for a in args[1:] if a != "--visible"])
        visible = "--visible" in args
        async with open_context(headless=not visible) as ctx:
            comment_id = await post_comment_on_video(ctx, url, text, visible=visible)
            print(f"TikTok подтвердил комментарий {comment_id}")
    else:
        print(__doc__)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(_cli(sys.argv[1:]))
