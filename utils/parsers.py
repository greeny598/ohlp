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


def split_recommendations(text: str, sections: List[str],
                          threshold: int = 90) -> Dict[str, str]:
    """
    1) Выделяем intro/extra жёстко по паре маркеров.
    2) Сплитим по %split% и проходим по каждому блоку.
    3) Ключ = первая непустая строка блока (очищенная от Markdown).
       Если fuzzy-совпадение ≥ threshold → используем эталонное sections-имя.
       Иначе → оставляем чистый заголовок.
    4) Блоки **никогда** не пропадают — все включаются в result.
    """

    def _strip_markdown(md: str) -> str:
        s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", md)      # [text](url)
        # **bold** / __underline__
        s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
        # *italic* / _italic_
        s = re.sub(r"(\*|_)(.*?)\1", r"\2", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)                    # `code`
        # list markers / headers
        s = re.sub(r"^[#>\-*+]+\s*", "", s)
        return s.strip()

    result: Dict[str, str] = {}

    # 1) Intro
    m = re.search(r"%intro%(.*?)%intro%", text, flags=re.DOTALL)
    if m:
        result["intro"] = m.group(1).strip()

    # 2) Extra
    m = re.search(r"%extra%(.*?)%extra%", text, flags=re.DOTALL)
    if m:
        result["extra"] = m.group(1).strip()

    # 3) Split‐блоки
    parts = text.split("%split%")
    # все куски между маркерами:
    raw_blocks = parts[1:-1] if len(parts) > 2 else []

    for raw in raw_blocks:
        raw = raw.strip()
        if not raw:
            continue

        lines = raw.splitlines()
        # первая непустая строка
        title_line = next((ln for ln in lines if ln.strip()), "")
        title_clean = _strip_markdown(title_line) or "section"

        # весь остальной текст после заголовка
        idx = lines.index(title_line) if title_line in lines else -1
        content = (
            "\n".join(lines[idx + 1:]).strip() if idx >= 0 else raw
        )

        # fuzzy match
        best = process.extractOne(
            title_clean, sections, scorer=fuzz.partial_ratio)
        if best and best[1] >= threshold:
            key = best[0]
        else:
            key = title_clean

        # если этот ключ уже есть — просто доклеим
        if key in result:
            result[key] += "\n\n" + content
        else:
            result[key] = content

    return result


def split_ohlp_sections(text: str, sections: List[str], threshold: int = 70) -> Dict[str, str]:
    starts, ends = compute_section_positions(text, sections, threshold)
    result: Dict[str, str] = {}

    for i, sec in enumerate(sections):
        start = ends[i] if ends[i] >= 0 else 0
        next_start = starts[i+1] if i+1 < len(sections) and starts[i+1] >= 0 else len(text)

        raw = text[start:next_start].strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

        # нормализация: только буквы и цифры в нижнем регистре
        def normalize_cmp(s: str) -> str:
            return re.sub(r'\W+', '', s.lower())

        sec_key = normalize_cmp(sec)

        # убираем подряд **все** заголовки в начале, пока они совпадают с sec_key
        while lines and normalize_cmp(lines[0]) == sec_key:
            lines.pop(0)

        # убираем подряд идущие дубли
        deduped = []
        prev = None
        for ln in lines:
            if ln != prev:
                deduped.append(ln)
            prev = ln

        result[sec] = "\n".join(deduped)

    return result


def split_leaflet_sections(text: str, sections: List[str], threshold: int = 70) -> Dict[str, str]:
    """
    Разбивает текст листка-вкладыша на разделы по заголовкам из списка sections.

    Применяется точный мэппинг по номеру раздела (число перед ".").
    Если точный мэппинг не найден — пытается fuzzy matching заголовка.

    Args:
        text: полный текст листка-вкладыша (строки с NBSP).
        sections: список заголовков секций в формате '1. Заголовок', ...
        threshold: порог для fuzzy-matching (0-100).
    Returns:
        Словарь {оригинальный_секционный_заголовок: текст_раздела}
    """
    # Заменяем NBSP на обычные пробелы и разбиваем на строки
    lines = text.replace('\xa0', ' ').splitlines()

    # Ищем начало TOC
    toc_start = next((i for i, ln in enumerate(lines) if 'Содержание' in ln), None)
    if toc_start is None:
        raise ValueError('Не найден блок "Содержание"')

    # Определяем начало содержательной части — по первой строке с номером раздела
    toc_end = next((j for j in range(toc_start + 1, len(lines)) if re.match(r"^\s*\d+\.\s*", lines[j])), None)
    if toc_end is None:
        raise ValueError('Не удалось определить начало содержательной части')

    main_lines = lines[toc_end:]

    # Подготовка эталонных заголовков (номер и текст)
    # Маппинг номера -> раздел в sections
    num_to_section: Dict[str, str] = {}
    placeholder_norms: Dict[str, str] = {}
    for sec in sections:
        m = re.match(r"^\s*(\d+)\.\s*(.*)$", sec)
        if m:
            num, title = m.groups()
            num_to_section[num] = sec
            placeholder_norms[num] = normalize_heading(title)

    # Regex для обнаружения заголовков: номер + точка + заголовок до дефиса/тире
    heading_re = re.compile(r"^(?P<num>\d+)\.\s*(?P<title>.+?)(?=[\-–—]|$)")

    # Находим все заголовки
    headings: List[tuple] = []  # (line_index, section_name)
    for idx, ln in enumerate(main_lines):
        stripped = ln.strip()
        m = heading_re.match(stripped)
        if not m:
            continue
        num = m.group('num')
        # Пробуем точный мэппинг по номеру
        section = num_to_section.get(num)
        if section is None:
            # Фоллбэк: fuzzy по тексту заголовка
            raw_title = m.group('title').strip()
            norm_raw = normalize_heading(raw_title)
            # Сравниваем с нормами всех placeholder_norms
            best = process.extractOne(norm_raw, list(placeholder_norms.values()), scorer=fuzz.partial_ratio)
            if best and best[1] >= threshold:
                # находим соответствующий раздел по словарю
                # placeholder_norms: num->norm, so invert lookup
                for key, norm in placeholder_norms.items():
                    if norm == best[0]:
                        section = num_to_section[key]
                        break
        if section:
            headings.append((idx, section))

    if not headings:
        raise ValueError('Не найдены заголовки в содержательной части')

    # Добавляем конец документа
    indices = [idx for idx, _ in headings] + [len(main_lines)]

    # Разбиваем на блоки
    result: Dict[str, str] = {}
    for i, (idx, section) in enumerate(headings):
        start = indices[i] + 1
        end = indices[i + 1]
        block = '\n'.join(main_lines[start:end]).strip()
        result[section] = block

    return result


