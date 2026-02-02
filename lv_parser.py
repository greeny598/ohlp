# -*- coding: utf-8 -*-
"""
semantic_segmenter.py — устойчивый семантический сегментер без LLM.

Что умеет:
- Удаляет блок «Содержание листка-вкладыша» устойчиво (учитывает лидеры ..... 3, переносы, пустые строки).
- Поддерживает заголовки, где номер "1." на одной строке, а текст заголовка — на следующей.
- Отсекает ложные заголовки (телефоны/дозировки/коды), в т.ч. случаи вроде "5.50" (точка + цифра).
- Жёстко ограничивает номера заголовков для leaflet: только 1..6.
- Режет текст между заголовками.
- Имеет fallback: если заголовок 1 потерян, берёт прелюдию до «2.» как раздел 1.
- Имеет fallback: ищет «безномерные» заголовки по смыслу, если номер пропущен.

Публичный API:
- segment_text_semantic(text: str, sections: list[str]) -> dict[str, str]
- segment_texts(test_text, ref_text, rec_text, sections, split_recommendations_func=None)
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================ НОРМАЛИЗАЦИЯ ============================

def _normalize_nbsp(s: str) -> str:
    """Заменяем неразрывные пробелы на обычные и схлопываем табы/многопробелье (не трогая переводы строк)."""
    if not s:
        return s
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s

def _clean_for_match(s: str) -> str:
    """Упрощение строки для семантических проверок."""
    s = _normalize_nbsp(s)
    s = s.lower()
    s = s.replace("®", "")
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _norm_spaces(s: str) -> str:
    return _normalize_nbsp(s or "").strip()

# ============================ ФИЛЬТРЫ МУСОРА ДЛЯ ЗАГОЛОВКОВ ============================

# Почти целиком цифры/разделители => телефон/код/доза/артефакт, не заголовок.
_DIGITISH = re.compile(r"^[\d\s\-\–—\(\)\+\/\\,\.:%№]+$")

def _looks_like_numeric_garbage(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return True

    # Короткая строка из цифр/разделителей почти наверняка телефон/код
    if len(s) <= 60 and _DIGITISH.match(s):
        return True

    # Если цифр слишком много — тоже подозрительно
    digits = sum(ch.isdigit() for ch in s)
    if digits / max(len(s), 1) > 0.55:
        return True

    return False

# ============================ УДАЛЕНИЕ ОГЛАВЛЕНИЯ (TOC) ============================

# Пункт оглавления: "1. Заголовок ..... 3" (страница опционально)
_TOC_ITEM_RE = re.compile(
    r"^\s*(?P<num>[1-6])\.\s+(?P<title>.+?)\s*(?:\.{2,}\s*\d+)?\s*$",
    re.IGNORECASE,
)

def _strip_toc_block(text: str) -> str:
    """
    Устойчиво удаляет блок оглавления листка-вкладыша:
    от строки «Содержание ...» до последнего пункта в последовательности 1..6
    (разрешает лидеры "..... 3", пустые строки, перенос заголовка на следующую строку).

    Если не удалось уверенно "съесть" пункты оглавления — удаляет хотя бы строку "Содержание...",
    чтобы не тащить её в последующую логику.
    """
    if not text:
        return text

    lines = text.splitlines(True)  # сохраняем \n
    hdr_idx: Optional[int] = None

    for i, ln in enumerate(lines):
        if re.search(r"(?i)^\s*#*\s*содержание(?:\s+листка[-–]?\s*вкладыша)?\b", ln):
            hdr_idx = i
            break

    if hdr_idx is None:
        return text

    expect = 1
    last: Optional[int] = None

    j = hdr_idx + 1
    scan_limit = min(len(lines), hdr_idx + 120)  # чтобы не улететь в бесконечность

    while j < scan_limit and expect <= 6:
        ln_raw = lines[j]
        ln = ln_raw.strip()

        if not ln:
            j += 1
            continue

        # Вариант "1." на строке, а текст пункта оглавления на следующей
        if re.match(rf"^\s*{expect}\.\s*$", ln):
            k = j + 1
            while k < scan_limit and not lines[k].strip():
                k += 1
            if k < scan_limit:
                # считаем это пунктом оглавления
                last = k
                expect += 1
                j = k + 1
                continue
            break

        m = _TOC_ITEM_RE.match(ln)
        if not m:
            break

        num = int(m.group("num"))
        if num != expect:
            break

        last = j
        expect += 1
        j += 1

    # Если нашли хотя бы 3 пункта подряд — удаляем блок оглавления целиком
    if last is not None and (expect - 1) >= 3:
        del lines[hdr_idx:last + 1]
        logger.debug("TOC removed: items 1..%s", expect - 1)
        return "".join(lines)

    # Иначе — хотя бы удаляем строку "Содержание..."
    del lines[hdr_idx:hdr_idx + 1]
    logger.debug("TOC header removed (items not confidently parsed)")
    return "".join(lines)

# ============================ ПРОСТАЯ НАРЕЗКА ПО ЗАГОЛОВКАМ (LV) ============================

# Заголовки ЛВ обычно пронумерованы 1..6 и начинаются с "N. "
# Важно: запрещаем случаи вроде "5.50" (точка + цифра) -> (?!\d)
LV_HEADING_RE = re.compile(r"^\s*(?P<num>[1-6])\s*\.(?!\d)\s*(?P<title>\S.*)$")

def _canon_section_by_num(sections: List[str], num: str) -> Optional[str]:
    """Возвращает каноническое название раздела из списка sections по номеру (1..6)."""
    if not sections:
        return None
    pref = f"{num}."
    for s in sections:
        ss = _norm_spaces(s)
        if ss.startswith(pref):
            return s
    return None

def split_leaflet_sections_simple(text: str, sections: List[str]) -> Dict[str, str]:
    """
    Простая и предсказуемая нарезка листка-вкладыша на разделы по заголовкам вида '1. ...' ... '6. ...'.
    В отличие от segment_text_semantic(), НЕ делает семантических эвристик: только номера/заголовки.

    Возвращает dict: {<канонический заголовок из sections>: <текст раздела>}.
    Если конкретный раздел не найден — ключ всё равно будет присутствовать с пустой строкой.
    """
    text = _normalize_nbsp(text or "")
    text = _strip_toc_block(text)  # убираем оглавление, чтобы пункты 1..6 не считались заголовками

    raw_lines = text.splitlines()
    lines = [_norm_spaces(ln) for ln in raw_lines]

    boundaries: List[int] = []
    headers: Dict[int, Tuple[str, str]] = {}  # idx -> (num, header_line)

    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln:
            i += 1
            continue

        m = LV_HEADING_RE.match(ln)
        if m:
            num = m.group("num")
            title = (m.group("title") or "").strip()
            if _looks_like_numeric_garbage(title):
                i += 1
                continue

            boundaries.append(i)
            headers[i] = (num, ln)
            i += 1
            continue

        # вариант: строка = '1.' (без текста), а сам заголовок в следующей строке
        m2 = re.match(r"^\s*(?P<num>[1-6])\s*\.\s*$", ln)
        if m2:
            num = m2.group("num")
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            hdr = lines[j] if j < len(lines) else ""
            if hdr and _looks_like_numeric_garbage(hdr):
                i = j + 1
                continue

            header_line = f"{num}. {hdr}" if hdr else f"{num}."
            boundaries.append(i)
            headers[i] = (num, header_line)
            i = j + 1
            continue

        i += 1

    boundaries = sorted(set(boundaries))

    if not boundaries:
        out: Dict[str, str] = {}
        if sections:
            out[sections[0]] = text.strip()
            for s in sections[1:]:
                out[s] = ""
        else:
            out["Без названия"] = text.strip()
        return out

    out: Dict[str, str] = {}
    for k, start in enumerate(boundaries):
        end = boundaries[k + 1] if k + 1 < len(boundaries) else len(lines)

        num, header_line = headers.get(start, ("", lines[start]))
        canon = _canon_section_by_num(sections, num) if num else None
        key = canon or header_line

        body = "\n".join(raw_lines[start + 1:end]).strip()
        out[key] = body

    for s in sections or []:
        if s not in out:
            out[s] = ""

    return out

# ============================ КАНДИДАТЫ ЗАГОЛОВКОВ ДЛЯ СЕМАНТИКИ ============================

HeadingCandidate = Tuple[int, int, int, str]  # (num, start, end, heading_text)

def _iter_numbered_candidates(text: str, *, max_heading_num: int = 6) -> List[HeadingCandidate]:
    """
    Находит все строки вида 'N. ...' (N=1..max_heading_num).
    - Защита от дробей/телефонов: точка НЕ должна быть частью числа => (?!\d)
    - Если после 'N.' та же строка пустая, подхватывает следующую непустую строку как текст заголовка.
    Возвращает список (num, start_idx, end_idx, heading_text).
    """
    candidates: List[HeadingCandidate] = []

    # Важно: (?!\d) предотвращает матч "5.50" как "5."
    pat = re.compile(r"(?m)^(?P<prefix>\s*#*\s*)(?P<num>\d{1,2})\.(?!\d)\s*(?P<rest>[^\n]*)")

    for m in pat.finditer(text):
        try:
            num = int(m.group("num"))
        except Exception:
            continue

        if not (1 <= num <= max_heading_num):
            continue

        start = m.start()
        rest = (m.group("rest") or "").strip()
        end = m.end()
        heading_text = rest

        # Отсекаем "цифровой мусор" (телефон/код/доза)
        if heading_text and _looks_like_numeric_garbage(heading_text):
            continue

        if not heading_text:
            # Частый кейс: "1.\nЧто из себя представляет..."
            tail = text[end:]
            nline_m = re.match(r"\s*\n?([ \t]*)(?P<nline>[^\n]+)", tail)
            if nline_m:
                heading_text = (nline_m.group("nline") or "").strip()
                end = end + nline_m.end()

                if heading_text and _looks_like_numeric_garbage(heading_text):
                    continue

        heading_text = (heading_text or "")[:300]
        candidates.append((num, start, end, heading_text))

    return candidates

# ============================ СЕМАНТИЧЕСКИЙ СКОРЕР ============================

def _score_heading(num: int, heading_text: str) -> int:
    """
    Возвращает простой "скор" соответствия заголовка ожидаемой теме.
    >=1 — подходит, 0 — отбрасываем.
    """
    s = _clean_for_match(heading_text)

    def has(*tokens: str) -> bool:
        return all(tok in s for tok in tokens)

    if num == 1:
        pts = 0
        pts += 1 if "что из себя представляет" in s or ("представля" in s and "препарат" in s) else 0
        pts += 1 if "для чего его примен" in s else 0
        return pts

    if num == 2:
        pts = 0
        pts += 1 if has("о чем", "следует", "знать") else 0
        pts += 1 if ("перед прием" in s or "перед примен" in s) else 0
        return pts

    if num == 3:
        pts = 0
        pts += 1 if ("применение препарат" in s or "прием препарат" in s) else 0
        pts += 1 if ("применение" in s or "прием" in s) else 0
        return pts

    if num == 4:
        pts = 0
        pts += 1 if ("нежелательн" in s and ("реакц" in s or "эффект" in s)) else 0
        pts += 1 if "возможн" in s else 0
        return pts

    if num == 5:
        pts = 0
        pts += 1 if "хранени" in s else 0
        pts += 1 if "препарат" in s else 0
        return pts

    if num == 6:
        pts = 0
        pts += 1 if ("содержим" in s and "упаковк" in s) else 0
        if "сведен" in s:
            pts += 1
        return pts

    return 0

# ============================ ПОИСК БЕЗНОМЕРНЫХ ХЕДЕРОВ (FALLBACK) ============================

def _plain_heading_pattern(idx: int) -> re.Pattern:
    if idx == 1:
        body = (
            r"^\s*(?:что\s+(?:из\s+себя\s+)?представля\w*\s+препарат[^\n]*"
            r"(?:для\s+чего\s+его\s+примен\w*)?)\s*$"
        )
    elif idx == 2:
        body = r"^\s*о\s+ч[её]м\s+следует\s+знать\s+перед\s+(?:при[её]мом|применением)\s+препарат\w*[^\n]*$"
    elif idx == 3:
        body = r"^\s*(?:при[её]м|применение)\s+препарат\w*[^\n]*$"
    elif idx == 4:
        body = r"^\s*возможн\w*\s+нежелательн\w*\s+реакц\w*\s*$"
    elif idx == 5:
        body = r"^\s*хранени[её]\s+препарат\w*[^\n]*$"
    else:
        body = r"^\s*содержим\w*\s+упаковк\w*[^\n]*$"
    return re.compile(body, re.IGNORECASE | re.MULTILINE)

# ============================ ОСНОВНАЯ СЕГМЕНТАЦИЯ ============================

def segment_text_semantic(raw_text: str, sections_list: List[str]) -> Dict[str, str]:
    """
    Разбивает текст на 6 разделов по смыслу.
    Возвращает {канонический_заголовок_из_sections_list: контент}.
    """
    if not raw_text:
        return {sec: "" for sec in sections_list}

    # 0) нормализуем и удаляем TOC
    text = _normalize_nbsp(raw_text)
    text = _strip_toc_block(text)

    # 1) собираем кандидатов заголовков вида "N." (жёстко 1..6)
    cands = _iter_numbered_candidates(text, max_heading_num=6)
    if not cands:
        logger.warning("No numbered headings detected at all.")
        return {sec: "" for sec in sections_list}

    # 2) для каждого номера 1..6 выбираем первое валидное вхождение по семантическому скору
    chosen: Dict[int, Tuple[int, int, str]] = {}  # num -> (start, end, heading_text)
    occupied_until = -1

    for num in range(1, min(6, len(sections_list)) + 1):
        best = None
        for (n, s, e, htxt) in cands:
            if n != num:
                continue
            if s <= occupied_until and occupied_until != -1:
                continue  # соблюдаем возрастание позиций

            score = _score_heading(num, htxt)
            if score > 0:
                best = (s, e, htxt)
                break  # берём первое валидное

        if best:
            chosen[num] = best
            occupied_until = best[1]

    if not chosen:
        logger.warning("No headings passed semantic check.")
        return {sec: "" for sec in sections_list}

    # 3) формируем контент между найденными заголовками
    ordered = sorted(((n, *v) for n, v in chosen.items()), key=lambda t: t[1])

    by_num: Dict[int, str] = {}
    for i, (n, s, e, _) in enumerate(ordered):
        nxt_s = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        chunk = text[e:nxt_s].strip()
        by_num[n] = chunk

    # 3a) fallback: если 1 не найден, но есть, например, 2 — берём прелюдию до первого найденного заголовка
    if 1 not in by_num:
        first_start = ordered[0][1] if ordered else len(text)
        prelude = text[:first_start].strip()
        if prelude:
            by_num[1] = prelude

    # 3b) fallback: если какой-то номер отсутствует — попробуем найти безномерный заголовок по смыслу
    missing = [k for k in range(1, min(6, len(sections_list)) + 1) if k not in by_num]
    if missing:
        for k in missing:
            pat = _plain_heading_pattern(k)
            m = pat.search(text)
            if m:
                next_starts = [s for (n, s, *_rest) in ordered if n > k]
                nxt = min(next_starts) if next_starts else len(text)
                chunk = text[m.end():nxt].strip()
                if chunk:
                    by_num[k] = chunk

    # 4) маппинг по каноническим ключам
    out: Dict[str, str] = {}
    for pos, sec in enumerate(sections_list, start=1):
        out[sec] = by_num.get(pos, "").strip()

    return out