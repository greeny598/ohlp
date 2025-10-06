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
import re

# Паттерны для очистки названий разделов от ведущих и завершающих спецсимволов
SECTION_LEAD_TRASH = r'^[\s\*<>"«»№\.\-–—\u00A0]+'
SECTION_TAIL_TRASH = r'[\s\*<>"«»\.\-–—\u00A0]+$'

def _clean_section_name(s: str) -> str:
    """
    Очищает строку: убирает ведущие и завершающие пробелы, звёздочки,
    кавычки, дефисы и прочие спецсимволы.
    """
    s = s or ""
    s = re.sub(SECTION_LEAD_TRASH, '', s.strip())
    s = re.sub(SECTION_TAIL_TRASH, '', s)
    return s.strip()

def _first_non_empty_line(text: str) -> str:
    """
    Возвращает первую непустую строку текста.
    """
    for ln in (text or '').splitlines():
        if ln.strip():
            return ln.strip()
    return ''

def _write_fragments(paragraph, frags, default_size_pt=12):
    """
    Записывает список фрагментов в параграф, сохраняя размеры шрифта и цвет.
    frags: список кортежей (тип, текст, (r,g,b)?).
    """
    # очистим существующие run'ы
    for r in paragraph.runs:
        r.text = ''
    for kind, txt, *color in frags:
        if not txt:
            continue
        run = paragraph.add_run(txt)
        run.font.size = Pt(default_size_pt)
        if kind == 'highlight' and color:
            r, g, b = color[0]
            run.font.color.rgb = RGBColor(r, g, b)


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


def fill_comparison_table(doc: Document,
                          data: List[Dict[str, Any]],
                          table_index: int = 2) -> None:
    """
    Заполняет таблицу сравнения в отчёте. Создаёт по две строки на каждый раздел:

      1) Заголовок раздела: объединяем две первые ячейки и помещаем очищенное название
         раздела по центру. Если у таблицы есть третий столбец, его оставляем пустым.

      2) Содержимое раздела: левая колонка — референтный документ, правая колонка —
         анализируемый документ. Названия разделов выводятся жирным шрифтом, затем текст.
         Различия подсвечиваются: в референте — зелёным, в анализируемом — красным.

      Если в данных присутствует ключ "Отличия" и таблица имеет третью колонку, она
      заполняется текстом из этого поля.
    """
    table = doc.tables[table_index]
    # очищаем таблицу, оставляя только первую строку (шапку)
    while len(table.rows) > 1:
        tbl = table._tbl
        tbl.remove(tbl.tr_lst[-1])

    for entry in data:
        # Эталонное название из рекомендаций
        canonical_title_raw = entry.get('Раздел', '') or ''
        canonical_title = _clean_section_name(canonical_title_raw)

        # Содержимое по разделам
        ref_content = entry.get('Содержимое референтного документа', '') or ''
        test_content = entry.get('Содержимое тестируемого документа', '') or ''

        # названия разделов из текстов
        ref_name = _clean_section_name(_first_non_empty_line(ref_content))
        test_name = _clean_section_name(_first_non_empty_line(test_content))

        # тело без первой строки (чтобы не дублировать заголовок)
        ref_body = '\n'.join(ref_content.splitlines()[1:]) if ref_content else ''
        test_body = '\n'.join(test_content.splitlines()[1:]) if test_content else ''

        # подсветка различий (зелёный — ref, красный — test)
        ref_frags, test_frags = highlight_differences(ref_body, test_body)

        # ——— Строка заголовка ———
        heading_row = table.add_row()
        # объединяем первые две колонки, если они есть
        merged_heading = heading_row.cells[0]
        if len(heading_row.cells) > 1:
            merged_heading = merged_heading.merge(heading_row.cells[1])
        merged_heading.text = ''
        p_head = merged_heading.paragraphs[0]
        run = p_head.add_run(canonical_title or '—')
        run.bold = True
        run.font.size = Pt(12)
        p_head.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # если таблица имеет третий столбец, он остаётся пустым

        # ——— Строка содержимого ———
        content_row = table.add_row()
        # левая колонка: референт
        cell_ref = content_row.cells[0]
        # название раздела (жирным)
        para_ref_name = cell_ref.paragraphs[0]
        para_ref_name.text = ''
        run_ref_name = para_ref_name.add_run(ref_name)
        run_ref_name.bold = True
        # тело
        p_ref_body = cell_ref.add_paragraph()
        _write_fragments(p_ref_body, ref_frags)
        # правая колонка: тестируемый
        if len(content_row.cells) > 1:
            cell_test = content_row.cells[1]
            para_test_name = cell_test.paragraphs[0]
            para_test_name.text = ''
            run_test_name = para_test_name.add_run(test_name)
            run_test_name.bold = True
            p_test_body = cell_test.add_paragraph()
            _write_fragments(p_test_body, test_frags)

        # если есть третий столбец и ключ "Отличия", заполняем
        if 'Отличия' in entry and len(content_row.cells) > 2:
            content_row.cells[2].text = entry['Отличия']


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
