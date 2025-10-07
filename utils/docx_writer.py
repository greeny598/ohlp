import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from docx import Document
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.shared import Cm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import RGBColor
from difflib import SequenceMatcher


def iter_paragraphs(element) -> List[Paragraph]:
    """
    Рекурсивно обходит все параграфы в переданном элементе:
    Document, _Cell (ячейка таблицы) или _Body (тело документа).
    """
    for para in element.paragraphs:
        yield para
    for table in getattr(element, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from iter_paragraphs(cell)


def insert_recommendations(doc: Document, recommendations: Dict[str, Any]) -> None:
    """
    Ищет в doc плейсхолдер {_RECOMMENDATIONS_}, очищает его и вставляет
    по одной строке для каждой записи recommendations со статусом != 'complied'.
    Каждая строка — новый абзац с:
      - отступом первой строки 1 см
      - выравниванием по ширине
      - ключ секции (жирным)
      - комментарий (с маленькой буквы, кавычки-ёлочки)
    """
    for paragraph in doc.paragraphs:
        if '{_RECOMMENDATIONS_}' in paragraph.text:
            paragraph.text = ''
            prev = paragraph

            for key, data in recommendations.items():
                print(key)
                if data.get('compliance') == 'complied':
                    continue
                comment = data.get('comments', '').strip()
                if not comment:
                    continue
                # lowercase first char
                comment = comment[0].lower() + comment[1:]
                # quotes to «»
                comment = re.sub(r'"([^\"]*)"', r'«\1»', comment)
                comment = re.sub(r"'([^']*)'", r'«\1»', comment)
                # build paragraph
                p_elm = OxmlElement('w:p')
                prev._p.addnext(p_elm)
                p = Paragraph(p_elm, prev._parent)
                fmt = p.paragraph_format
                fmt.first_line_indent = Cm(1)
                fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                # section key bold
                run_section = p.add_run(f"{key}: ")
                run_section.bold = True
                run_section.font.size = Pt(14)
                # comment normal
                run_comment = p.add_run(comment)
                run_comment.font.size = Pt(14)
                prev = p
            break


def highlight_differences(a: str, b: str,
                          color_a: Tuple[int, int, int] = (0, 128, 0),
                          color_b: Tuple[int, int, int] = (255, 0, 0)) -> Tuple[List, List]:
    """
    Сравнивает строки a и b, возвращает два списка фрагментов:
    each element is (type, text, (color,))
    where type is 'plain' or 'highlight'.
    """
    matcher = SequenceMatcher(None, a, b)
    res_a, res_b = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        ta, tb = a[i1:i2], b[j1:j2]
        if tag == 'equal':
            res_a.append(('plain', ta))
            res_b.append(('plain', tb))
        else:
            if tag in ('replace', 'delete'):
                res_a.append(('highlight', ta, color_a))
            if tag in ('replace', 'insert'):
                res_b.append(('highlight', tb, color_b))
    return res_a, res_b


def replace_placeholders_in_doc(doc: Document, replacements: dict):
    """
    Заменяет все ключи из replacements на их значения в документе,
    во всех параграфах — включая таблицы любой вложенности.

    Логика форматирования:
      - Если замена произошла в ячейке таблицы: 12 pt, полужирный.
      - Если замена произошла вне таблиц: 14 pt.
    Подсветка спец-строк (<!-- formula-not-decoded -->, <!-- image -->) сохраняется.
    """
    def _in_table(para: Paragraph) -> bool:
        # параграф находится в ячейке таблицы, если его родитель — w:tc
        try:
            return para._p.getparent().tag.endswith('}tc')
        except Exception:
            return False

    # подготовим регулярку для поиска ключей
    pattern = None
    if replacements:
        # порядок ключей не важен, экранируем для безопасности
        pattern = re.compile("|".join(re.escape(k) for k in replacements.keys()))

    for para in iter_paragraphs(doc):
        orig_text = para.text

        # 1) ЗАМЕНА ПЛЕЙСХОЛДЕРОВ
        if pattern is not None and orig_text:
            replaced_text = pattern.sub(lambda m: replacements[m.group(0)], orig_text)

            if replaced_text != orig_text:
                in_table = _in_table(para)

                # Чистим все существующие run'ы и вставляем ОДИН новый run без дублирования
                for run in para.runs:
                    run.text = ""

                new_run = para.add_run(replaced_text)
                if in_table:
                    new_run.font.size = Pt(12)
                    new_run.bold = True
                else:
                    new_run.font.size = Pt(14)

        # 2) ПОДСВЕТКА спец-строк (выполняется и если замен не было)
        # Проходим по run'ам, разбиваем по таргетам, заново собираем с сохранением формата.
        targets = ["<!-- formula-not-decoded -->", "<!-- image -->"]
        if any(t in para.text for t in targets):
            pattern_highlight = re.compile("(" + "|".join(re.escape(t) for t in targets) + ")")
            new_fragments = []  # (text, formatting_dict, highlight_flag)

            for run in para.runs:
                text = run.text
                fmt = {
                    'bold': run.bold,
                    'italic': run.italic,
                    'underline': run.underline,
                    'font_name': run.font.name,
                    'font_size': run.font.size,
                    'color': run.font.color.rgb if run.font.color and run.font.color.rgb else None,
                    'highlight': run.font.highlight_color
                }
                if text:
                    parts = pattern_highlight.split(text)
                    for part in parts:
                        if not part:
                            continue
                        if part in targets:
                            new_fragments.append((part, fmt, True))
                        else:
                            new_fragments.append((part, fmt, False))

            # очистка и пересборка
            for run in para.runs:
                run.text = ""

            for text, fmt, highlight in new_fragments:
                new_run = para.add_run(text)
                new_run.bold = fmt['bold']
                new_run.italic = fmt['italic']
                new_run.underline = fmt['underline']
                if fmt['font_name']:
                    new_run.font.name = fmt['font_name']
                if fmt['font_size']:
                    new_run.font.size = fmt['font_size']
                if highlight:
                    new_run.font.color.rgb = RGBColor(0, 0, 0)
                    new_run.font.highlight_color = WD_COLOR_INDEX.RED
                else:
                    if fmt['color']:
                        new_run.font.color.rgb = fmt['color']
                    if fmt['highlight']:
                        new_run.font.highlight_color = fmt['highlight']



def build_replacements(ref_name: str, test_name: str) -> Dict[str, str]:
    """
    Формирует словарь для replace_placeholders_in_doc:
      {{'_REF_NAME_': ref_name, '_TEST_NAME_': test_name, '_DATE_': today}}
    """
    today = datetime.now().strftime("%d.%m.%Y") + " г."
    return {
        "{_REF_NAME_}": ref_name,
        "{_TEST_NAME_}": test_name,
        "{_DATE_}": today,
    }


# --- вспомогательные функции для таблицы сравнения ---
def _clean_section_name(s: str) -> str:
    """
    Очищает строку: убирает ведущие и завершающие пробелы, звёздочки,
    кавычки, дефисы и прочие спецсимволы. Используется для названий разделов.
    """
    if not s:
        return ""
    # символы в начале
    s = re.sub(r'^[\s\*<>"«»№\.\-–—\u00A0]+', '', s.strip())
    # символы в конце
    s = re.sub(r'[\s\*<>"«»\.\-–—\u00A0]+$', '', s)
    return s.strip()


def _first_non_empty_line(text: str) -> str:
    """
    Возвращает первую непустую строку текста.
    """
    for ln in (text or '').splitlines():
        if ln.strip():
            return ln.strip()
    return ''


def _remove_first_line(text: str) -> str:
    """
    Возвращает текст без первой непустой строки (используется, чтобы
    убрать заголовок раздела из текста и не дублировать его в таблице).
    """
    if not text:
        return ""
    lines = text.splitlines()
    found = False
    new_lines: List[str] = []
    for ln in lines:
        if not found and ln.strip():
            found = True
            continue
        new_lines.append(ln)
    return "\n".join(new_lines).lstrip()


def _write_fragments(paragraph, frags: List[Tuple[str, str, Tuple[int, int, int]]], default_size_pt: int = 12) -> None:
    """
    Записывает список фрагментов (тип, текст, (r,g,b)) в параграф.
    typ может быть 'plain' или 'highlight'.
    Цвет применяется только для 'highlight'. Шрифт устанавливается стандартным
    размером и не выделяется полужирным.
    """
    # удалить существующие run'ы
    for run in paragraph.runs:
        run.text = ''
    for item in frags:
        if not item:
            continue
        kind = item[0]
        txt = item[1]
        color = item[2] if len(item) > 2 else None
        if not txt:
            continue
        run = paragraph.add_run(txt)
        run.font.size = Pt(default_size_pt)
        # не выделяем текст тела жирным
        run.bold = False
        if kind == 'highlight' and color:
            r, g, b = color
            run.font.color.rgb = RGBColor(r, g, b)


def fill_comparison_table(doc: Document,
                          data: List[Dict[str, Any]],
                          table_index: int = 2) -> None:
    """
    Заполняет таблицу сравнения данными data. Для каждого entry формируется три строки:

      1. Первая строка: содержит эталонное название раздела (из рекомендаций). Все
         ячейки строки объединяются, текст центрируется и выделяется полужирным.
      2. Вторая строка: содержит названия разделов, извлечённые из референтной и
         анализируемой инструкций. Названия выводятся полужирным в своих столбцах.
         Если у таблицы есть третий столбец, он оставляется пустым.
      3. Третья строка: содержит текст логических блоков для референтного и
         анализируемого документов. Различия подсвечиваются (зелёный —
         референт, красный — анализируемый). Текст не выделяется полужирным.

      Если в entry присутствует ключ "Отличия" и таблица имеет третий столбец,
      этот текст помещается в третий столбец третьей строки.
    """
    table = doc.tables[table_index]
    # очищаем таблицу, оставляя только первую строку (шапку)
    while len(table.rows) > 1:
        tbl = table._tbl
        tbl.remove(tbl.tr_lst[-1])

    for entry in data:
        canonical_raw = entry.get('Раздел', '') or ''
        canonical_title = _clean_section_name(canonical_raw)
        ref_content = entry.get('Содержимое референтного документа', '') or ''
        test_content = entry.get('Содержимое тестируемого документа', '') or ''

        # извлекаем названия разделов из документов и очищаем их
        ref_name = _clean_section_name(_first_non_empty_line(ref_content))
        test_name = _clean_section_name(_first_non_empty_line(test_content))

        # убираем первую строку из содержимого (чтобы не дублировать в теле)
        ref_body = _remove_first_line(ref_content)
        test_body = _remove_first_line(test_content)

        # подготовим фрагменты для подсветки различий
        ref_frags, test_frags = highlight_differences(ref_body, test_body)

        # 1) строка с эталонным названием (объединяем все ячейки)
        header_row = table.add_row()
        # начальная ячейка
        merged_cell = header_row.cells[0]
        # если больше одной ячейки — объединяем их
        for idx in range(1, len(header_row.cells)):
            merged_cell = merged_cell.merge(header_row.cells[idx])
        merged_cell.text = ''
        p = merged_cell.paragraphs[0]
        run = p.add_run(canonical_title or '—')
        run.bold = True
        run.font.size = Pt(12)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 2) строка с названиями из инструкций
        name_row = table.add_row()
        # заполняем первую и вторую ячейки названиями (полужирный)
        if len(name_row.cells) > 0:
            cell_ref_name = name_row.cells[0]
            cell_ref_name.text = ''
            p_rn = cell_ref_name.paragraphs[0]
            run_r = p_rn.add_run(ref_name or '')
            run_r.bold = True
            run_r.font.size = Pt(12)
        if len(name_row.cells) > 1:
            cell_test_name = name_row.cells[1]
            cell_test_name.text = ''
            p_tn = cell_test_name.paragraphs[0]
            run_t = p_tn.add_run(test_name or '')
            run_t.bold = True
            run_t.font.size = Pt(12)
        # оставшиеся ячейки (например, третью) оставляем пустыми

        # 3) строка с содержимым раздела
        body_row = table.add_row()
        # референтная колонка
        if len(body_row.cells) > 0:
            cell_ref_body = body_row.cells[0]
            cell_ref_body.text = ''
            p_ref = cell_ref_body.paragraphs[0]
            _write_fragments(p_ref, ref_frags)
        # анализируемая колонка
        if len(body_row.cells) > 1:
            cell_test_body = body_row.cells[1]
            cell_test_body.text = ''
            p_test = cell_test_body.paragraphs[0]
            _write_fragments(p_test, test_frags)
        # если есть третий столбец и поле "Отличия"
        if 'Отличия' in entry and len(body_row.cells) > 2:
            body_row.cells[2].text = entry['Отличия']


def save_with_timestamp(doc: Document,
                        filetype: str,
                        original_filename: str,
                        output_dir: str = "results",
                        prefix: str = "report") -> str:
    """
    Сохраняет doc в папке output_dir с названием
    prefix_DD_MM_YY(HH_MM_SS).docx, возвращает путь.
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%d.%m.%y_(%H-%M-%S)")
    filename = f"{prefix}_{filetype}_{original_filename}_{ts}.docx"
    path = os.path.join(output_dir, filename)
    doc.save(path)
    return path
