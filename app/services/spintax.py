"""Spintax для черновиков ответов.

    {Привет|Добрый день}, [author]! {Классный|Интересный} ролик.

{a|b|c}: случайный вариант, вложенность поддерживается: {a|{b|c}}.
[name]: подстановка переменной. Неизвестная переменная остаётся как есть,
чтобы её было видно в черновике.
"""
import random
import re

_INNER_RE = re.compile(r"\{([^{}]*)\}")
_VAR_RE = re.compile(r"\[([a-zA-Z_][a-zA-Z0-9_]*)\]")


def validate(text: str) -> None:
    depth = 0
    for ch in text:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            raise ValueError("Лишняя закрывающая скобка '}'")
    if depth:
        raise ValueError("Не закрыта скобка '{'")


def spin(text: str, variables: dict[str, object] | None = None, rng: random.Random | None = None) -> str:
    validate(text)
    rng = rng or random
    # Раскрываем самые внутренние группы, пока они есть.
    while True:
        text, n = _INNER_RE.subn(lambda m: rng.choice(m.group(1).split("|")), text)
        if not n:
            break
    variables = variables or {}
    text = _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def count_variants(text: str) -> int:
    """Число уникальных комбинаций (без учёта совпадающих строк)."""
    validate(text)

    def _count(s: str) -> int:
        total, i = 1, 0
        while i < len(s):
            if s[i] == "{":
                depth, j = 1, i + 1
                while depth:
                    depth += (s[j] == "{") - (s[j] == "}")
                    j += 1
                total *= sum(_count(opt) for opt in _split_top(s[i + 1 : j - 1]))
                i = j
            else:
                i += 1
        return total

    return _count(text)


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        depth += (ch == "{") - (ch == "}")
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def video_variables(video) -> dict[str, object]:
    """Переменные, доступные в шаблоне для конкретного ролика."""
    return {"author": f"@{video.author}" if video.author else "", "query": video.query}
