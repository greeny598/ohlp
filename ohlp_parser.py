import re
from typing import Dict, List, Tuple, Optional

__all__ = ["split_ohlp_sections"]

# Строгий заголовок ОХЛП: 1. ..., 4.1. ..., 6.2.3. ...
HEADING_RE = re.compile(
    r"(?m)^(?P<header>\s*(?P<num>[1-9](?:\.\d+)*)\.\s+(?P<title>[A-Za-zА-Яа-яЁё][^\n\r]*))\s*$"
)

WS_RE = re.compile(r"[\t\u00A0\u2007\u202F]+")
MULTINL_RE = re.compile(r"\n{3,}")


def _normalize_text(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    # заменяем экзотические пробелы, но НЕ трогаем \n
    s = WS_RE.sub(" ", s)
    # 3+ пустых строк → 2 (это не мешает заголовкам)
    s = MULTINL_RE.sub("\n\n", s)
    return s


def _collapse_wrapped_headings(text: str, sections: Optional[List[str]]) -> str:
    """
    Склеивает многострочные заголовки на основе эталонных titles из `sections`.

    Идея:
      - для каждого эталонного заголовка строим паттерн с \s+ между токенами,
        который допускает переносы строк;
      - если находим совпадение в тексте, внутри него схлопываем все
        последовательности whitespace (вкл. \n) в одиночный пробел.
    """
    if not sections:
        return text

    for sec in sections:
        if not sec:
            continue

        # Нормализуем эталон: схлопываем пробелы
        norm = " ".join(sec.split())
        if not norm:
            continue

        tokens = norm.split(" ")

        # Паттерн:
        #   ^\s*4\.7\.\s+Влияние\s+на\s+способность ...
        # \s+ внутри позволяет ловить разрывы строки.
        pattern = r"(?m)^\s*" + r"\s+".join(re.escape(tok) for tok in tokens)

        def repl(m: re.Match) -> str:
            # Схлопываем все пробелы/переносы внутрь матча в одиночные пробелы
            return re.sub(r"\s+", " ", m.group(0)).strip()

        text = re.sub(pattern, repl, text)

    return text


def _collect_candidates(text: str) -> List[Tuple[int, int, str, str, str]]:
    out: List[Tuple[int, int, str, str, str]] = []
    for m in HEADING_RE.finditer(text):
        header = m.group("header")
        num = m.group("num")
        title = m.group("title")
        start, end = m.span()
        out.append((start, end, header, num, title))
    out.sort(key=lambda x: x[0])
    return out


def split_ohlp_sections(
    text: str,
    sections: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Возвращает dict: {<оригинальный заголовок>: <тело>}

    - Заголовком считается строка, начинающаяся с ^<цифры/подцифры>. <пробел> ...
      (1. ..., 4.1. ..., 7.1. ...).
    - Чужие числовые строки (типа "3 года (пластиковые шприцы).") не подходят,
      потому что у них нет вида "N." в начале строки.
    - Если заголовок был разорван на несколько строк, а его эталон есть в `sections`,
      он сначала склеивается в одну строку.
    """
    raw = _normalize_text(text)
    raw = _collapse_wrapped_headings(raw, sections)

    cands = _collect_candidates(raw)
    result: Dict[str, str] = {}

    for idx, (start, end, header, num, title) in enumerate(cands):
        next_start = cands[idx + 1][0] if idx + 1 < len(cands) else len(raw)
        body = raw[end:next_start].strip("\n ")

        key = header.strip()  # максимально близко к оригиналу (после _normalize_text)

        result[key] = body

    return result
