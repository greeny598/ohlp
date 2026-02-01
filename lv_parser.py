# -*- coding: utf-8 -*-
"""
semantic_segmenter.py — устойчивый семантический сегментер без LLM.

Что умеет:
- Удаляет блок «Содержание листка-вкладыша» аккуратно (только пункты 1..6 подряд).
- Поддерживает заголовки, где номер "1." на одной строке, а текст заголовка — на следующей.
- Классифицирует заголовки по смыслу и режет текст между ними.
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

# ============================ ПРОСТАЯ НАРЕЗКА ПО ЗАГОЛОВКАМ (LV) ============================

# Заголовки ЛВ обычно пронумерованы 1..6 и начинаются с "N. "
# Учитываем NBSP между номером и текстом.
LV_HEADING_RE = re.compile(r"^\s*(?P<num>[1-6])\s*\.\s*(?P<title>\S.*)$")

def _norm_spaces(s: str) -> str:
    return _normalize_nbsp(s or "").strip()

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
    text = _strip_toc_block(text)  # убираем оглавление «Содержание...», чтобы пункты 1..6 не считались заголовками
    raw_lines = text.splitlines()
    lines = [_norm_spaces(ln) for ln in raw_lines]

    # границы (индексы строк), где начинается очередной раздел
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
            boundaries.append(i)
            headers[i] = (num, ln)
            i += 1
            continue

        # вариант: строка = '1.' (без текста), а сам заголовок в следующей строке
        m2 = re.match(r"^\s*(?P<num>[1-6])\s*\.\s*$", ln)
        if m2:
            num = m2.group("num")
            # ищем следующую непустую строку как заголовок
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            header_line = f"{num}. {lines[j]}" if j < len(lines) else f"{num}."
            boundaries.append(i)
            headers[i] = (num, header_line)
            i = j + 1
            continue

        i += 1

    boundaries = sorted(set(boundaries))

    # Если ничего не нашли — fallback: вернуть весь текст одним блоком (под первым разделом, если есть)
    if not boundaries:
        out: Dict[str, str] = {}
        if sections:
            out[sections[0]] = text.strip()
            for s in sections[1:]:
                out[s] = ""
        else:
            out["Без названия"] = text.strip()
        return out

    # собираем куски
    out: Dict[str, str] = {}
    for k, start in enumerate(boundaries):
        end = boundaries[k + 1] if k + 1 < len(boundaries) else len(lines)

        num, header_line = headers.get(start, ("", lines[start]))
        canon = _canon_section_by_num(sections, num) if num else None
        key = canon or header_line

        # тело — оригинальные строки (raw_lines) между start+1..end-1, чтобы не терять форматирование строк
        body = "\n".join(raw_lines[start + 1:end]).strip()
        out[key] = body

    # гарантируем присутствие всех канонических разделов из recommendations
    for s in sections or []:
        if s not in out:
            out[s] = ""

    return out


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

# ============================ УДАЛЕНИЕ ОГЛАВЛЕНИЯ ============================

def _strip_toc_block(text: str) -> str:
    """
    Удаляем ТОЛЬКО оглавление: от строки «Содержание…» до последнего пункта в последовательности 1..6,
    где каждый пункт находится на СВОЕЙ строке (номер и текст заголовка в одной строке).
    Останавливаемся, если нумерация прерывается или внезапно снова появляется «1.» (это уже тело).
    """
    if not text:
        return text

    lines = text.splitlines(True)  # сохраняем \n
    hdr_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if re.search(r"(?i)^\s*#*\s*содержание(?:\s+листка[-–]вкладыша)?\b", ln):
            hdr_idx = i
            break
    if hdr_idx is None:
        return text

    expect = 1
    last_toc_line: Optional[int] = None
    j = hdr_idx + 1
    while j < len(lines) and expect <= 6:
        ln = lines[j]
        if re.match(r"^\s*$", ln):
            # пустые строки допускаются внутри TOC
            j += 1
            continue
        m = re.match(r"^\s*(\d+)\.\s+\S", ln)  # номер + хотя бы одно непустое слово
        if not m:
            break
        num = int(m.group(1))
        if num != expect:
            # если внезапно пошёл «1.» снова — это уже тело
            break
        last_toc_line = j
        expect += 1
        j += 1

    if last_toc_line is not None and (expect - 1) >= 3:
        del lines[hdr_idx:last_toc_line + 1]
        logger.debug("TOC removed: items %s..%s", 1, expect - 1)
        return "".join(lines)
    return text

# ============================ КАНДИДАТЫ ЗАГОЛОВКОВ ============================

HeadingCandidate = Tuple[int, int, int, str]  # (num, start, end, heading_text)

def _iter_numbered_candidates(text: str) -> List[HeadingCandidate]:
    """
    Находит все строки вида 'N. ...' (N=1..6). Если после 'N.' та же строка пустая,
    подхватывает следующую непустую строку как текст заголовка.
    Возвращает список (num, start_idx, end_idx, heading_text).
    """
    candidates: List[HeadingCandidate] = []
    for m in re.finditer(r"(?m)^(?P<prefix>\s*#*\s*)(?P<num>\d{1,2})\.\s*(?P<rest>[^\n]*)", text):
        try:
            num = int(m.group("num"))
        except Exception:
            continue
        if not (1 <= num <= 6):
            continue

        start = m.start()
        rest = (m.group("rest") or "").strip()
        end = m.end()
        heading_text = rest

        if not heading_text:
            # Частый кейс: "1.\nЧто из себя представляет..."
            tail = text[end:]
            nline_m = re.match(r"\s*\n?([ \t]*)(?P<nline>[^\n]+)", tail)
            if nline_m:
                heading_text = (nline_m.group("nline") or "").strip()
                end = end + nline_m.end()

        heading_text = heading_text[:300]
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
        body = (r"^\s*(?:что\s+(?:из\s+себя\s+)?представля\w*\s+препарат[^\n]*"
                r"(?:для\s+чего\s+его\s+примен\w*)?)\s*$")
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

    # 1) собираем кандидатов заголовков вида "N."
    cands = _iter_numbered_candidates(text)
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
                break  # берём первое валидное после TOC
        if best:
            chosen[num] = best
            occupied_until = best[1]

    # 2a) если совсем ничего не нашли — выходим пустыми
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

    # 3a) Fallback: если 1 не найден, но есть, например, 2 — берём прелюдию до первого найденного заголовка
    if 1 not in by_num:
        first_start = ordered[0][1] if ordered else len(text)
        prelude = text[:first_start].strip()
        if prelude:
            by_num[1] = prelude

    # 3б) Fallback: если какой-то номер отсутствует — попробуем найти безномерный заголовок по смыслу
    missing = [k for k in range(1, min(6, len(sections_list)) + 1) if k not in by_num]
    if missing:
        for k in missing:
            pat = _plain_heading_pattern(k)
            m = pat.search(text)
            if m:
                # граница следующего найденного номерного заголовка (если он есть)
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
