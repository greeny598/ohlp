import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from docx import Document
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.shared import Cm
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
                # comment normal
                p.add_run(comment)
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
    """
    # если есть replacements, подготовим регулярное выражение для поиска ключей
    pattern = None
    if replacements:
        pattern = re.compile("|".join(re.escape(k) for k in replacements.keys()))

    for para in iter_paragraphs(doc):
        orig = para.text
        # если есть что заменять, то заменяем все вхождения в один проход
        if pattern is not None:
            new = pattern.sub(lambda m: replacements[m.group(0)], orig)
            if new != orig:
                # пробегаемся по run'ам и правим только те, где есть текст
                for run in para.runs:
                    run_text = run.text
                    replaced = pattern.sub(lambda m: replacements[m.group(0)], run_text)
                    if replaced != run_text:
                        run.text = replaced

                # если placeholder выпадал между runs и не был пойман,
                # можно на крайний случай очистить и вставить один run:
                if para.text != new:
                    for run in para.runs:
                        run.text = ""
                    para.add_run(new)

        # после замены (или если замен нет) подсветим только целевые подстроки
        # например: <!-- formula-not-decoded --> и <!-- image -->. Для этого
        # проходим по run'ам, разбиваем текст run'а по любому из target-строк
        # и создаём новые run'ы с сохранением форматирования.
        # список подстрок, требующих подсветки
        targets = ["<!-- formula-not-decoded -->", "<!-- image -->"]
        # если в абзаце нет ни одной целевой подстроки, ничего делать не нужно
        if any(t in para.text for t in targets):
            # регулярное выражение, которое выделяет любую из целевых подстрок,
            # захватывая её в результирующих частях
            pattern_highlight = re.compile("(" + "|".join(re.escape(t) for t in targets) + ")")
            new_fragments = []  # (text, formatting_dict, highlight_flag)
            for run in para.runs:
                text = run.text
                # собрать формат run'а
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
                    # разделить текст на части, включая целевые подстроки
                    parts = pattern_highlight.split(text)
                    for part in parts:
                        if not part:
                            continue
                        if part in targets:
                            new_fragments.append((part, fmt, True))
                        else:
                            new_fragments.append((part, fmt, False))
            # очистить существующие run'ы (оставим пустые, новые добавим в конец)
            for run in para.runs:
                run.text = ""
            # добавить новые run'ы с нужным форматированием
            for text, fmt, highlight in new_fragments:
                new_run = para.add_run(text)
                # восстановить форматирование
                new_run.bold = fmt['bold']
                new_run.italic = fmt['italic']
                new_run.underline = fmt['underline']
                if fmt['font_name']:
                    new_run.font.name = fmt['font_name']
                if fmt['font_size']:
                    new_run.font.size = fmt['font_size']
                # если это целевой фрагмент, подкрашиваем
                if highlight:
                    new_run.font.color.rgb = RGBColor(0, 0, 0)
                    new_run.font.highlight_color = WD_COLOR_INDEX.RED
                else:
                    # восстанавливаем цвет, если был задан
                    if fmt['color']:
                        new_run.font.color.rgb = fmt['color']
                    # восстанавливаем существующую подсветку
                    if fmt['highlight']:
                        new_run.font.highlight_color = fmt['highlight']



def _extract_from_header(doc: Document) -> Tuple[str, str]:
    import re
    # 1) найти параграф с «Листок‑вкладыш»
    paras = [p.text.strip() for p in doc.paragraphs]
    idx = next(i for i, t in enumerate(paras)
               if t.startswith("Листок‑вкладыш"))
    # 2) собрать следующие непустые строки до первого пустого
    names = []
    for line in paras[idx+1:]:
        if not line:
            break
        # отберите только строки с указанием mg
        if re.search(r'\d+\s*мг', line):
            names.append(line)
    # 3) из каждой строки взять часть до первого «,»
    drugs = [ln.split(',', 1)[0].strip() for ln in names]
    # 4) убрать дубликаты и взять первые две
    seen = []
    for d in drugs:
        if d not in seen:
            seen.append(d)
    ref = seen[0] if seen else ''
    test = seen[1] if len(seen) > 1 else seen[0] if seen else ''
    return ref, test


def extract_drug_names(doc: Document, table_index: int = 2) -> Tuple[str, str]:
    """
    Из таблицы под индексом table_index берёт названия препаратов
    в строке 2 (cells[0] и cells[1]), отсекая всё до \n и очищая.
    Возвращает (ref_name, test_name).
    """
    def clean(name: str) -> str:
        return re.sub(r'[.,;:!?()\[\]{}«»"\']+$', '', name.strip())
    table = doc.tables[table_index]
    # первая строка — заголовок, вторая — названия
    raw_ref = table.rows[1].cells[0].text.partition('\n')[2]
    raw_test = table.rows[1].cells[1].text.partition('\n')[2]
    return clean(raw_ref), clean(raw_test)


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
    Заполняет таблицу с индексом table_index данными data:
    для каждого entry в data создаёт параграфы,
    сравнивает референт и тест и подсвечивает отличия.
    """
    table = doc.tables[table_index]
    for i, entry in enumerate(data, start=1):
        row = table.rows[i]
        ref_para = row.cells[0].add_paragraph()
        test_para = row.cells[1].add_paragraph()
        ref_runs, test_runs = highlight_differences(
            entry['Содержимое референтного документа'],
            entry['Содержимое тестируемого документа']
        )
        for typ, text, *clr in ref_runs:
            run = ref_para.add_run(text)
            if typ == 'highlight':
                run.font.color.rgb = RGBColor(*clr[0])
        for typ, text, *clr in test_runs:
            run = test_para.add_run(text)
            if typ == 'highlight':
                run.font.color.rgb = RGBColor(*clr[0])
        # optional third column
        if 'Отличия' in entry and len(row.cells) > 2:
            row.cells[2].text = entry['Отличия']


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
