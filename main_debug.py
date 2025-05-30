# %%
# Линейный скрипт для отладки в IDE (Spyder)
# Шаг 0: Укажите свои пути к PDF-файлам и параметры
# ------------------------------------------------------------------
# Путь к проверяемой инструкции:
from utils.docx_writer import fill_template
from langchain_utils.section_checker import SectionChecker
from utils.pdf_loader import extract_text_from_pdf
from datetime import datetime
import os
test_path = 'test_data/test.pdf'
# Путь к эталонной инструкции:
ref_path = 'test_data/etalon.pdf'
# Путь к файлу рекомендаций (пока не используется, можно None):
rec_path = None  # или 'path/to/your/recommendations.pdf'
# LLM-провайдер: 'openai' или 'deepseek'
provider = 'deepseek'
# Папка для сохранения отчета:
output_dir = 'results'
# ------------------------------------------------------------------

# %%
# Шаг 1: Импорт необходимых модулей

# %%
# Шаг 2: Извлечение текста из PDF
print('Шаг 2/5: Извлечение текста из PDF...')
test_text = extract_text_from_pdf(test_path)
ref_text = extract_text_from_pdf(ref_path)
print(f' - Длина тестовой инструкции: {len(test_text)} символов')
print(f' - Длина эталонной инструкции: {len(ref_text)} символов')

# %%
# Шаг 3: Инициализация SectionChecker
print('Шаг 3/5: Инициализация SectionChecker...')
checker = SectionChecker(api_provider=provider)

# %%
# Шаг 4: Сравнение инструкций через LLM
print('Шаг 4/5: Сравнение инструкций через LLM...')
diffs = checker.check_sections(ref_text, test_text)
clean = checker.clean_json_from_md(diffs)
   

# %%
# Шаг 5: Формирование и сохранение DOCX-отчета
print('Шаг 5/5: Формирование DOCX-отчета...')
# Имя препарата — из имени тестового файла без расширения
base_name = os.path.splitext(os.path.basename(test_path))[0]
info = {
    'DRUG_NAME': base_name,
    'DIFFERENCES': clean,
    'DATE': datetime.now().strftime('%d.%m.%Y г.')
}

# Создаем папку и формируем имя отчета
os.makedirs(output_dir, exist_ok=True)
timestamp = int(datetime.now().timestamp())
output_path = os.path.join(output_dir, f'report_{base_name}_{timestamp}.docx')
print(f'Сохранение отчета: {output_path}')
# Запись через шаблон
fill_template(
    template_path='templates/differences_template.docx',
    output_path=output_path,
    info=info
)
print('Отчет сохранен.')
