from docx import Document
from docx.shared import Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import os


def remove_paragraph(paragraph):
    """
    Удаляет указанный параграф из документа.
    """
    p = paragraph._element
    p.getparent().remove(p)
    paragraph._p = paragraph._element = None


def _set_table_borders(table):
    """
    Устанавливает тонкую сплошную чёрную границу для всей таблицы.
    """
    # Получаем XML-элемент таблицы
    tbl = table._element
    # Получаем или создаём элемент tblPr
    tblPr = tbl.tblPr
    if tblPr is None:
        tblPr = OxmlElement('w:tblPr')
        tbl.insert(0, tblPr)
    # Создаём элемент tblBorders
    tblBorders = OxmlElement('w:tblBorders')
    # Задаём границы для всех сторон и внутренних линий
    for border_name in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        border = OxmlElement(f'w:{border_name}')
        border.set(qn('w:val'), 'single')   # тип линии: сплошная
        border.set(qn('w:sz'), '4')          # толщина границы (в восьмых пункта)
        border.set(qn('w:space'), '0')       # отступ от содержимого
        border.set(qn('w:color'), '000000')  # цвет: чёрный
        tblBorders.append(border)
    # Добавляем tblBorders к tblPr
    tblPr.append(tblBorders)


def fill_template(template_path: str, output_path: str, info: dict) -> str:
    """
    Заполняет шаблон DOCX значениями из словаря info.
    Вставляет таблицу изменений с чёрными границами.

    Параметры:
    - template_path: путь к шаблону DOCX
    - output_path: путь для сохранения документа
    - info: словарь с ключами 'DRUG_NAME', 'DATE', 'DIFFERENCES'
    """
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Шаблон не найден: {template_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc = Document(template_path)

    # Настройка стиля Normal
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
    style.font.size = Pt(14)

    # Замена плейсхолдеров DRUG_NAME и DATE
    for para in doc.paragraphs:
        if '{DRUG_NAME}' in para.text:
            para.text = para.text.replace('{DRUG_NAME}', info.get('DRUG_NAME', ''))
            para.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        if '{DATE}' in para.text:
            para.text = para.text.replace('{DATE}', info.get('DATE', ''))
            para.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY

    # Вставка таблицы
    for para in list(doc.paragraphs):
        if '{_DIFFERENCES_}' in para.text:
            diffs = info.get('DIFFERENCES', [])
            tbl = doc.add_table(rows=1, cols=4)
            # Устанавливаем видимые границы
            _set_table_borders(tbl)

            # Заголовки
            hdr_cells = tbl.rows[0].cells
            headers = [
                'Раздел',
                'Отличия',
                'Формулировка из эталонного документа',
                'Формулировка из анализируемого документа'
            ]
            for i, title in enumerate(headers):
                cell = hdr_cells[i]
                cell.text = title
                for p in cell.paragraphs:
                    p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                    for run in p.runs:
                        run.font.name = 'Times New Roman'
                        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
                        run.font.size = Pt(12)
                        run.bold = True

            # Строки данных
            for entry in diffs:
                row_cells = tbl.add_row().cells
                values = [
                    entry.get('section', ''),
                    entry.get('difference', ''),
                    entry.get('old', ''),
                    entry.get('actual', '')
                ]
                for j, text in enumerate(values):
                    cell = row_cells[j]
                    cell.text = text
                    for p in cell.paragraphs:
                        p.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
                        for run in p.runs:
                            run.font.name = 'Times New Roman'
                            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
                            run.font.size = Pt(12)

            # Перенос таблицы на место плейсхолдера
            tbl_xml = tbl._tbl
            para._p.addnext(tbl_xml)
            remove_paragraph(para)
            break

    doc.save(output_path)
    return output_path
