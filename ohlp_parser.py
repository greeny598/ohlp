"""
ordered_ohlp_parser.py
=======================

Упрощённый парсер ОХЛП, который сегментирует текст строго по
порядку заголовков, указанному в шаблоне. В отличие от
иерархической разбивки, каждая секция завершается перед началом
следующего заголовка из списка ``sections``. Это предотвращает
дублирование содержимого в родительских разделах и делает
результат более предсказуемым при сравнении документов.

На вход подаётся полностью извлечённый текст и список строк
``sections`` из шаблона (например, ``["4. Основные сведения",
"4.1 Показания к применению", "4.2 Режим дозирования", ...]``).

Основная функция
----------------

``split_ohlp_sections(text: str, sections: List[str], threshold: float = 0.6)``
возвращает словарь `{section_name: content}`, где ``content`` —
фрагмент текста от начала заголовка до начала следующего
заголовка из списка. Если конкретный заголовок не найден,
возвращается пустая строка.

Как это работает
----------------

1. **Удаляем оглавление**: блок «Содержание» (если есть) отбрасывается,
   чтобы номера из оглавления не воспринимались как заголовки.
2. **Ищем все возможные заголовки**: используем регулярные выражения,
   которые распознают строки вида «4.1 Заголовок» и случаи, когда
   номер находится на отдельной строке, а текст заголовка — на
   следующей строке. Функция `_find_candidate_headings` возвращает
   позицию заголовка в строке, номер, текст, уровень и
   количество строк, занимаемых заголовком.
3. **Сопоставляем заголовки с пунктами из шаблона**: для каждого
   шаблонного пункта ищем подходящий заголовок среди кандидатов:
   номер должен совпасть, а текст заголовка должен быть похож на
   название из шаблона (по методу `difflib.SequenceMatcher`).
   Если найдено несколько кандидатов, выбираем самый ранний в тексте.
4. **Определяем границы секций**: сортируем найденные позиции
   заголовков в соответствии с порядком шаблона. Для каждой
   секции ``i`` начало — это позиция её заголовка, конец —
   позиция заголовка следующей секции ``i+1``, либо конец текста
   для последней секции.
5. **Вырезаем содержимое**: удаляем строки самого заголовка (номер
   и текст) из полученного фрагмента и возвращаем оставшийся
   текст.

Пример использования::

    from ordered_ohlp_parser import split_ohlp_sections
    sections = [
        "4 Основные сведения",
        "4.1 Показания к применению",
        "4.2 Режим дозирования",
        # ...
    ]
    text = load_from_doc()  # получить текст из документа
    blocks = split_ohlp_sections(text, sections)
    print(blocks["4.1 Показания к применению"])

"""

from __future__ import annotations

import difflib
import re
from typing import Dict, List, Tuple, Optional

__all__ = ["split_ohlp_sections"]


def _strip_toc_block(text: str) -> str:
    """Удаляет блок оглавления ("Содержание") в начале текста.

    Ищет строку, начинающуюся со слова "содержание" (регистронезависимо),
    затем удаляет следующие строки, в которых идут подряд номера вида
    ``1.``, ``2.``, … до тех пор, пока нумерация не оборвётся. Возвращает
    исходный текст, если заголовок "Содержание" не найден или
    последовательность пунктов короткая.
    """
    lines = text.splitlines(True)
    hdr_idx: Optional[int] = None
    for i, ln in enumerate(lines[:100]):
        if re.search(r"(?i)^\s*содержание\b", ln):
            hdr_idx = i
            break
    if hdr_idx is None:
        return text
    expect = 1
    last_toc_line: Optional[int] = None
    for j in range(hdr_idx + 1, len(lines)):
        ln = lines[j]
        if re.match(r"^\s*$", ln):
            continue
        m = re.match(r"^\s*(\d+)[.)]\s", ln)
        if not m:
            break
        num = int(m.group(1))
        if num != expect:
            break
        last_toc_line = j
        expect += 1
        if expect > 10:
            break
    if last_toc_line is not None and expect > 3:
        del lines[hdr_idx:last_toc_line + 1]
        return "".join(lines)
    return text


def _normalize_text(s: str) -> str:
    """Нормализует строку: приводит к нижнему регистру, убирает
    пунктуацию и лишние пробелы. Это помогает при сравнении текстов
    заголовков.
    """
    if not s:
        return ""
    s = s.lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s, flags=re.UNICODE).strip()
    return s


def _extract_template_info(sections: List[str]) -> Tuple[Dict[int, str], Dict[int, str], Dict[int, str]]:
    """Извлекает номера и заголовки из списка шаблонных секций.

    Возвращает три словаря:

    * ``idx_to_num``: индекс → номер раздела (``4.1``), сохраняет
      порядок следования в оригинальном списке.
    * ``idx_to_title_norm``: индекс → нормализованный текст
      заголовка (без номера).
    * ``num_to_indices``: номер → список индексов, в которых этот
      номер встречается (обычно один, но возможно несколько одинаковых
      номеров в шаблоне).
    """
    idx_to_num: Dict[int, str] = {}
    idx_to_title_norm: Dict[int, str] = {}
    num_to_indices: Dict[str, List[int]] = {}
    pattern = re.compile(r"^\s*(\d+(?:\.\d+)*)(?:[.)])?\s*(.*)$")
    for idx, sec in enumerate(sections):
        m = pattern.match(sec)
        if not m:
            continue
        num = m.group(1)
        title = m.group(2).strip()
        idx_to_num[idx] = num
        idx_to_title_norm[idx] = _normalize_text(title)
        num_to_indices.setdefault(num, []).append(idx)
    return idx_to_num, idx_to_title_norm, num_to_indices


def _find_candidate_headings(text: str) -> List[Tuple[int, str, str, int, int]]:
    """Находит потенциальные заголовки в тексте.

    Возвращает список кортежей
    ``(pos, num, title, level, lines_count)``, где:

    * ``pos`` — смещение (в байтах) начала заголовка в исходном тексте;
    * ``num`` — номер заголовка (например, ``'4.1'``);
    * ``title`` — текст заголовка без номера;
    * ``level`` — уровень заголовка (количество точек + 1);
    * ``lines_count`` — сколько строк занимает заголовок (1, если
      номер и заголовок находятся на одной строке; >1, если текст
      заголовка начинается на следующей строке).
    """
    candidates: List[Tuple[int, str, str, int, int]] = []
    lines = text.splitlines(True)
    offset = 0
    num_only_re = re.compile(r"^\s*(\d+(?:\.\d+)*)(?:[.)])?\s*$")
    num_with_title_re = re.compile(r"^\s*(\d+(?:\.\d+)*)(?:[.)])?\s+([^\s].*)$")
    i = 0
    while i < len(lines):
        line = lines[i]
        m_with_title = num_with_title_re.match(line)
        if m_with_title:
            num = m_with_title.group(1)
            title = m_with_title.group(2).strip()
            level = num.count('.') + 1
            candidates.append((offset, num, title, level, 1))
            offset += len(line)
            i += 1
            continue
        m_only = num_only_re.match(line)
        if m_only:
            num = m_only.group(1)
            j = i + 1
            title = ""
            while j < len(lines):
                next_line = lines[j]
                if re.match(r"^\s*$", next_line):
                    j += 1
                    continue
                # если след. строка также номер, то это другой пункт
                if num_with_title_re.match(next_line) or num_only_re.match(next_line):
                    break
                title = next_line.strip()
                break
            if title:
                level = num.count('.') + 1
                lines_count = j - i + 1
                candidates.append((offset, num, title, level, lines_count))
            offset += len(line)
            i += 1
            continue
        offset += len(line)
        i += 1
    return candidates


def _similarity(a: str, b: str) -> float:
    """Вычисляет коэффициент похожести двух строк (0–1) с
    использованием `difflib.SequenceMatcher`. """
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()



def _jaccard(a: str, b: str) -> float:
    A, B = set(a.split()), set(b.split())
    return len(A & B) / len(A | B) if A and B else 0.0

def _containment(a: str, b: str) -> float:
    A, B = set(a.split()), set(b.split())
    return len(A & B) / len(A) if A else 0.0

def _hybrid_score(tmpl_norm: str, cand_norm: str) -> float:
    """Комбинированная метрика схожести заголовков."""
    sm = difflib.SequenceMatcher(None, cand_norm, tmpl_norm).ratio()
    jac = _jaccard(cand_norm, tmpl_norm)
    cont = _containment(cand_norm, tmpl_norm)
    return 0.5 * cont + 0.3 * sm + 0.2 * jac


def split_ohlp_sections(
    text: str,
    sections: List[str],
    threshold: float = 0.6,
) -> Dict[str, str]:
    """Разбивает текст на секции в соответствии с порядком шаблона.

    :param text: уже извлечённый текст документа.
    :param sections: список заголовков секций из шаблона.
    :param threshold: порог схожести (0–1) при сопоставлении
        заголовков (чем выше, тем строже).
    :returns: словарь ``{section: content}``.
    """
    # если текста нет, вернуть пустые разделы
    if not text:
        return {sec: "" for sec in sections}
    # убираем TOC
    clean_text = _strip_toc_block(text)
    # извлекаем шаблонную информацию
    idx_to_num, idx_to_title_norm, num_to_indices = _extract_template_info(sections)
    # ищем кандидаты в тексте
    candidates = _find_candidate_headings(clean_text)
    # подготавливаем индекс кандидатов и монотонное сопоставление с fallback по номеру
    cand_by_num: Dict[str, List[Tuple[int, str, str, int, int]]] = {}
    for c in candidates:
        cand_by_num.setdefault(c[1], []).append(c)
    for arr in cand_by_num.values():
        arr.sort(key=lambda x: x[0])

    section_pos: Dict[int, Tuple[int, int]] = {}
    prev_pos = -1
    for idx, sec in enumerate(sections):
        num = idx_to_num.get(idx)
        if not num:
            continue
        arr = [c for c in cand_by_num.get(num, []) if c[0] > prev_pos]
        if not arr:
            continue
        tmpl_norm = idx_to_title_norm.get(idx, "")
        best = None
        best_score = -1.0
        for c in arr:
            cand_norm = _normalize_text(c[2])
            s = _hybrid_score(tmpl_norm, cand_norm)
            if s > best_score:
                best_score = s; best = c
        # fallback: только для подпунктов (чтобы не ловить «2 года» и т.п.)
        chosen = best if best_score >= threshold else (arr[0] if '.' in num else None)
        if chosen is None:
            continue
        section_pos[idx] = (chosen[0], chosen[4])
        prev_pos = chosen[0]
    # формируем список стартов по порядку секций
    starts: List[Tuple[int, Optional[int], int]] = []
    for idx, sec in enumerate(sections):
        if idx in section_pos:
            pos, lines_count = section_pos[idx]
            starts.append((idx, pos, lines_count))
        else:
            starts.append((idx, None, 0))
    # готовим результат
    result: Dict[str, str] = {}
    text_len = len(clean_text)
    for i, (idx, pos, lines_count) in enumerate(starts):
        sec = sections[idx]
        # если раздел не найден, пусто
        if pos is None:
            result[sec] = ""
            continue
        # ищем конец: следующий раздел, у которого pos != None
        end: int = text_len
        for j in range(i + 1, len(starts)):
            _, pos_j, _ = starts[j]
            if pos_j is not None and pos_j > pos:
                end = pos_j
                break
        # извлекаем фрагмент
        segment = clean_text[pos:end]
        lines = segment.splitlines()
        # удаляем строки заголовка (их количество lines_count)
        content_lines = lines[lines_count:] if len(lines) > lines_count else []
        content = "\n".join(content_lines).strip()
        result[sec] = content
    return result
