import re
from typing import Dict, List, Tuple
from rapidfuzz import process, fuzz

def normalize_heading(text: str) -> str:
    """
    Приводит заголовок к нижнему регистру, убирает пунктуацию и лишние пробелы.
    """
    cleaned = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned, flags=re.UNICODE).strip().lower()


def compute_section_positions(text: str, sections_raw: List[str], threshold: int = 70):
    lines = text.splitlines(keepends=True)
    plain = [ln.rstrip('\n') for ln in lines]
    offsets, cur = [], 0
    for ln in lines:
        offsets.append(cur)
        cur += len(ln)

    # находим индексы строк-заголовков
    header_idxs = [i for i, ln in enumerate(plain)
                   if re.match(r'^\s*\d+(?:\.\d+)*[.)]\s+', ln)]

    plain_norm = [normalize_heading(ln) for ln in plain]
    sections_norm = [normalize_heading(sec) for sec in sections_raw]

    starts, ends = [], []
    for sec_norm in sections_norm:
        idx = None

        # 3.1 substring: собираем *все* кандидаты, берём второй
        candidates = [i for i in header_idxs if sec_norm in plain_norm[i]]
        if len(candidates) >= 2:
            idx = candidates[1]
        elif len(candidates) == 1:
            idx = candidates[0]

        # 3.2 startswith: аналогично
        if idx is None:
            candidates = [i for i in header_idxs if plain_norm[i].startswith(sec_norm)]
            if len(candidates) >= 2:
                idx = candidates[1]
            elif len(candidates) == 1:
                idx = candidates[0]

        # 3.3 fuzzy: при необходимости тоже можно брать вторую лучшую
        if idx is None and header_idxs:
            choices = [plain_norm[i] for i in header_idxs]
            best = process.extractOne(sec_norm, choices, scorer=fuzz.partial_ratio)
            if best and best[1] >= threshold:
                rel = best[2]
                idx = header_idxs[rel]

        if idx is not None:
            starts.append(offsets[idx])
            ends.append(offsets[idx] + len(lines[idx]))
        else:
            starts.append(-1)
            ends.append(-1)

    return starts, ends

def extract_sections_from_recommendations(text: str) -> list[str]:
    """
    Возвращает список заголовков разделов из markdown‑текста рекомендаций.
    Предполагается, что рекомендации размечены маркерами %split%.
    """
    sections = []
    parts = text.split('%split%')
    # между %split% находятся блоки, в каждом первый непустой заголовок
    blocks = parts[1:-1] if len(parts) > 2 else []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        # берем первую непустую строку как заголовок
        title_line = next((ln for ln in lines if ln.strip()), '')
        # удаляем markdown и лишние пробелы
        title_clean = re.sub(r"^[#>\-*+]+\s*", "", title_line).strip()
        if title_clean:
            sections.append(title_clean)
    return sections


def _strip_markdown_line(s: str) -> str:
    # [text](url)
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    # **bold** / __bold__
    s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
    # *italic* / _italic_
    s = re.sub(r"(\*|_)(.*?)\1", r"\2", s)
    # `code`
    s = re.sub(r"`([^`]+)`", r"\1", s)
    # list markers / headers at line start
    s = re.sub(r"^[#>\-*+]+\s*", "", s)
    return s.strip()

def _extract_block_by_marker(text: str, marker: str) -> tuple[str, str | None]:
    """
    Возвращает (text_without_block, block_text_or_None).
    Если маркера два — берём между ними.
    Если маркер один — берём от него до конца.
    Маркер регистронезависимый, пробелы вокруг допускаются.
    """
    rx = re.compile(rf"%\s*{re.escape(marker)}\s*%", re.IGNORECASE)
    hits = list(rx.finditer(text))
    if len(hits) >= 2:
        a, b = hits[0], hits[1]
        block = text[a.end():b.start()].strip()
        new_text = text[:a.start()] + text[b.end():]
        return new_text, block
    elif len(hits) == 1:
        a = hits[0]
        block = text[a.end():].strip()
        new_text = text[:a.start()]
        return new_text, block
    else:
        return text, None

def split_recommendations(text: str, sections: List[str], threshold: int = 90) -> Dict[str, str]:
    """
    Ключи — ТОЛЬКО заголовки из документа (первая непустая строка после %split%).
    %intro% и %extra% вырезаются ДО сплита:
      - парой маркеров: между ними;
      - одиночный маркер: от него до конца.
    Остаточные служебные строки внутри блоков удаляются.
    """
    result: Dict[str, str] = {}

    # 1) Вырезаем intro
    work, intro = _extract_block_by_marker(text or "", "intro")
    if intro:
        result["intro"] = intro

    # 2) Вырезаем extra (включая «до конца», если маркер один)
    work, extra = _extract_block_by_marker(work, "extra")
    if extra:
        result["extra"] = extra

    # 3) Режем на блоки по %split%
    parts = re.split(r'%\s*split\s*%', work, flags=re.IGNORECASE)
    # если нет ни одного %split%, считаем весь work одним блоком
    raw_blocks = parts[1:] if len(parts) > 1 else ([work] if work.strip() else [])

    # 4) Собираем блоки
    for raw in raw_blocks:
        raw = (raw or "").strip()
        if not raw:
            continue

        lines = [ln.rstrip() for ln in raw.splitlines()]
        # Удаляем служебные строки-маркеры, если вдруг остались
        cleaned_lines = []
        for ln in lines:
            ln_stripped = ln.strip().lower()
            if ln_stripped in ("%intro%", "%extra%", "%split%"):
                continue
            cleaned_lines.append(ln)
        if not cleaned_lines:
            continue

        # Заголовок = первая непустая строка (без markdown)
        title_line = next((ln for ln in cleaned_lines if ln.strip()), "")
        title_clean = _strip_markdown_line(title_line) or "section"

        # Контент = всё после первой непустой строки
        first_idx = cleaned_lines.index(title_line)
        content = "\n".join(cleaned_lines[first_idx + 1:]).strip()

        # Ключ берём строго из документа (НЕ подменяем эталоном)
        key = title_clean

        # Если ключ уже встречался, дописываем (редкий, но безопасный кейс)
        if key in result:
            result[key] += ("\n\n" + content if content else "")
        else:
            result[key] = content

    return result


