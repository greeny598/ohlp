"""
debug_spyder.py — линейный дебаг без функций-обёрток.
Запускается как обычный скрипт (Run ▶ в Spyder) — все переменные остаются в глобальной области.
Отредактируйте пути ниже перед запуском.
"""
import nest_asyncio  # type: ignore
from langchain_utils.section_checker import SectionChecker
from utils.report_generator import (
    load_texts,
    extract_sections,
    split_into_sections,
    check_recommendations,
    load_template,
    write_recommendations_and_table,
    replace_placeholders,
    save_report,
)
import os
import asyncio
import json

# --- Пользовательские параметры (ОТРЕДАКТИРУЙТЕ ПОД СЕБЯ) ---
test_path = "D:/Work/Python_git/ohlp/test_data/ohlp_adjoprofen.pdf"
ref_path = "D:/Work/Python_git/ohlp/test_data/ohlp_deksalgin.pdf"
rec_path = "D:/Work/Python_git/ohlp/test_data/recommendations_OHLP_labeled.docx"
template_name = "report_template_ohlp.docx"
template_dir = "./templates"
output_dir = "./results"

provider = "yandex"
prefix = "DBG"
concurrency = 5
pause_between_steps = True   # False — без пауз

# --- Импорты ядра ---

# --- Настройка event loop для Spyder/Jupyter ---
nest_asyncio.apply()
loop = asyncio.get_event_loop()

print("▶ Старт линейного дебаг-запуска\n")

# 1) Загрузка исходных текстов
docs = loop.run_until_complete(load_texts(test_path, ref_path, rec_path))
print("[1/7] Тексты загружены:")
print("  test_text:", len(docs.test_text), "симв.")
print("  ref_text: ", len(docs.ref_text), "симв.")
print("  rec_text: ", len(docs.rec_text), "симв.\n")


# 2) Извлекаем список разделов из рекомендаций
sections = loop.run_until_complete(extract_sections(docs.rec_text))
print("[2/7] Разделы (первые 15):", sections[:15],
      ("…" if len(sections) > 15 else ""), "\n")


# 3) Разбиваем тексты по разделам
test_blocks, ref_blocks, recs_blocks = loop.run_until_complete(
    split_into_sections(
        doc_type=docs.loader_test.doc_type,
        test_text=docs.test_text,
        ref_text=docs.ref_text,
        rec_text=docs.rec_text,
        sections=sections,
    )
)
print("[3/7] Блоки сформированы:")
print("  test_blocks:", len(test_blocks), "разделов")
print("  ref_blocks: ", len(ref_blocks), "разделов")
print("  recs_blocks:", len(recs_blocks), "разделов\n")


# 4) Проверяем соответствие рекомендациям постатейно
checker = SectionChecker(api_provider=provider)
recommendations = loop.run_until_complete(
    check_recommendations(
        checker=checker,
        sections=sections,
        test_blocks=test_blocks,
        recs_blocks=recs_blocks,
        concurrency=concurrency,
    )
)


# 5) Загружаем DOCX-шаблон
doc = load_template(template_dir, template_name)
print("[5/7] Шаблон DOCX загружен\n")
if pause_between_steps:
    try:
        input("Нажмите Enter, чтобы продолжить…")
    except EOFError:
        pass

# 6) Вставляем рекомендации и заполняем таблицу сравнения
write_recommendations_and_table(
    doc,
    recommendations=recommendations,
    sections=sections,
    ref_blocks=ref_blocks,
    test_blocks=test_blocks,
    table_index=2,
)
print("[6/7] Рекомендации вставлены и таблица сравнения заполнена\n")
if pause_between_steps:
    try:
        input("Нажмите Enter, чтобы продолжить…")
    except EOFError:
        pass

# 7) Заменяем плейсхолдеры (имена препаратов и т.п.)
replace_placeholders(
    doc,
    drug_name_ref=docs.loader_ref.drug_name,
    drug_name_test=docs.loader_test.drug_name,
)
print("[7/7] Плейсхолдеры заменены\n")

# Сохранение отчёта
original_filename = os.path.splitext(
    os.path.basename(docs.loader_test.file_path))[0]
output_path = save_report(
    doc,
    filetype=docs.loader_test.doc_type,
    original_filename=original_filename,
    output_dir=output_dir,
    prefix=prefix,
)
print("✔ Готово. Отчёт сохранён:", output_path)

# После выполнения все ключевые переменные остаются в глобальном пространстве:
# docs, sections, test_blocks, ref_blocks, recs_blocks, checker, recommendations, doc, output_path
