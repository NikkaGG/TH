"""Импорт сессии TikTok из обычного браузера.

Нужен потому, что Google и часто сам TikTok не дают войти в окне,
которым управляет Playwright. Пользователь входит в своём Chrome,
копирует куки, и мы сохраняем их в формате Playwright storage_state.

Понимает:
  - JSON-экспорт расширений (Cookie-Editor, EditThisCookie) и storage_state Playwright;
  - Netscape cookies.txt;
  - строку заголовка Cookie из DevTools: "sessionid=...; tt_csrf_token=...";
  - просто значение sessionid.
"""
import json
import re

from app.config import SEARCH_STATE_PATH

_SAME_SITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}
_SESSION_ALIASES = ("sessionid", "sessionid_ss", "sid_tt")  # у TikTok это одно и то же значение


def _cookie(name: str, value: str, domain: str = ".tiktok.com", path: str = "/", expires: float = -1,
            http_only: bool = False, secure: bool = True, same_site: str = "Lax") -> dict:
    if same_site == "None":
        secure = True  # иначе Chromium отклонит куку
    return {"name": name, "value": value, "domain": domain, "path": path or "/", "expires": expires,
            "httpOnly": http_only, "secure": secure, "sameSite": same_site}


def _from_json(c: dict) -> dict:
    expires = -1 if c.get("session") else (c.get("expirationDate") or c.get("expires") or -1)
    return _cookie(
        str(c["name"]), str(c.get("value", "")),
        domain=c.get("domain") or ".tiktok.com",
        path=c.get("path") or "/",
        expires=float(expires),
        http_only=bool(c.get("httpOnly")),
        secure=bool(c.get("secure", True)),
        same_site=_SAME_SITE.get(str(c.get("sameSite", "")).lower(), "Lax"),
    )


def _from_netscape(raw: str) -> list[dict]:
    cookies = []
    for line in raw.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        cookies.append(_cookie(name, value.strip(), domain, path, float(expires or 0) or -1,
                               http_only, secure.upper() == "TRUE"))
    return cookies


def _from_header(raw: str) -> list[dict]:
    raw = re.sub(r"^\s*cookie\s*:\s*", "", raw, flags=re.I)
    cookies = []
    for part in raw.split(";"):
        name, sep, value = part.partition("=")
        if sep and name.strip():
            cookies.append(_cookie(name.strip(), value.strip()))
    return cookies


def parse(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw:
        raise ValueError("Вставьте куки или значение sessionid")
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ValueError("Похоже на JSON, но он повреждён. Скопируйте экспорт целиком") from e
        items = data.get("cookies", []) if isinstance(data, dict) else data
        cookies = [_from_json(c) for c in items if isinstance(c, dict) and c.get("name")]
    elif "\t" in raw:
        cookies = _from_netscape(raw)
    elif "=" in raw:
        cookies = _from_header(raw)
    elif re.fullmatch(r"[A-Za-z0-9_\-]{16,}", raw):
        cookies = [_cookie(name, raw) for name in _SESSION_ALIASES]
    else:
        raise ValueError("Не понял формат. Вставьте значение sessionid или экспорт куки")

    cookies = [c for c in cookies if "tiktok.com" in c["domain"] and c["value"]]
    if not any(c["name"] == "sessionid" for c in cookies):
        raise ValueError("Среди куки нет sessionid. Проверьте, что вы вошли в TikTok и копируете куки с tiktok.com")
    # Если вставили только sessionid, добавляем его дубли, как это делает сам TikTok.
    names = {c["name"] for c in cookies}
    sid = next(c["value"] for c in cookies if c["name"] == "sessionid")
    cookies += [_cookie(n, sid) for n in _SESSION_ALIASES if n not in names]
    return cookies


def save(cookies: list[dict]) -> None:
    SEARCH_STATE_PATH.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear() -> bool:
    if SEARCH_STATE_PATH.exists():
        SEARCH_STATE_PATH.unlink()
        return True
    return False
