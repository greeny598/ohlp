import os
import logging
import traceback
import asyncio
import inspect
from difflib import get_close_matches
from docx import Document

from utils.document_loader import DocumentLoader
from threading import Lock
from langchain_utils.section_checker import SectionChecker
from utils.parsers import split_recommendations
import re

from utils.docx_writer import (
    build_replacements,
    replace_placeholders_in_doc,
    insert_recommendations,
    fill_comparison_table,
    save_with_timestamp,
)

from lv_parser import segment_text_semantic
from ohlp_parser import split_ohlp_sections


# Логгер для модуля генерации отчётов
logger = logging.getLogger(__name__)

# будем кэшировать текст рекомендаций
_recommendation_cache = {}
_cache_lock = Lock()


def load_cached_recommendations(rec_path: str):
    with _cache_lock:
        if rec_path not in _recommendation_cache:
            _recommendation_cache[rec_path] = DocumentLoader(
                rec_path).simple_load()
            logger.info("Загружаем рекомендации в кэш")
    logger.info("Используем кэшированный файл рекомендаций")
    return _recommendation_cache[rec_path]

# === Helper: извлекаем заголовки разделов из текста рекомендаций ===
# Функции для очистки спецсимволов и получения списка разделов
SECTION_LEAD_TRASH = r'^[\s\*<>"«»№\.\-–—\u00A0]+'
SECTION_TAIL_TRASH = r'[\s\*<>"«»\.\-–—\u00A0]+$'

def _clean_section_name(s: str) -> str:
    s = s or ''
    s = re.sub(SECTION_LEAD_TRASH, '', s.strip())
    s = re.sub(SECTION_TAIL_TRASH, '', s)
    return s.strip()

def extract_sections_from_recommendations(text: str) -> list:
    """
    Разбивает текст рекомендаций по маркеру %split% и возвращает
    список заголовков разделов. Очищает лишние спецсимволы в начале и конце строк.
    """
    parts = re.split(r'%\s*split\s*%', text, flags=re.IGNORECASE)
    sections: list[str] = []
    for part in parts:
        block = (part or '').strip()
        if not block:
            continue
        # исключаем длинное предисловие и служебные хвосты
        if block.lower().startswith('рекомендации по составлению проекта общей характеристики'):
            continue
        if block.strip().lower() == '%extra%':
            continue
        for ln in block.splitlines():
            ln = ln.strip()
            if ln:
                clean = _clean_section_name(ln)
                if clean:
                    sections.append(clean)
                break
    return sections


def generate_report(
    test_path: str,
    ref_path: str,
    rec_path: str,
    template_name: str,
    template_dir: str,
    output_dir: str,
    provider: str,
    prefix: str
) -> str:
    """
    Генерация отчёта на основе тестовой инструкции,
    эталонной инструкции и рекомендаций.
    Возвращает путь к сгенерированному отчёту .docx.

    template_dir: путь к папке с шаблонами
    output_dir: путь к папке для сохранения отчётов
    provider: имя провайдера для SectionChecker
    prefix: префикс имени файла отчёта
    """
    logger.info("=== Начало генерации отчёта ===")
    try:
        # 1) Извлечение текстов
        logger.info("1) Извлечение текстов из документов…")
        loader_test = DocumentLoader(test_path)
        test_text = loader_test.load()
        logger.info(f"  → TEST ({test_path}): {len(test_text)} chars, "
                    f"type={loader_test.doc_type},\
                        drug={loader_test.drug_name!r}")

        loader_ref = DocumentLoader(ref_path)
        ref_text = loader_ref.load()
        logger.info(f"  → REF  ({ref_path}):  {len(ref_text)} chars, "
                    f"type={loader_ref.doc_type},\
                        drug={loader_ref.drug_name!r}")

        # используем кэшированные рекомендации
        rec_text = load_cached_recommendations(rec_path)
        logger.info(f"  → REC  ({rec_path}):  {len(rec_text)} chars")

        # 2) Инициализация SectionChecker
        logger.info(
            f"2) Инициализация SectionChecker с провайдером: {provider}")
        checker = SectionChecker(api_provider=provider)

        # 3) Извлечение списка разделов из файла рекомендаций и загрузка шаблона
        logger.info("3) Извлечение списка разделов из файла рекомендаций…")
        sections = extract_sections_from_recommendations(rec_text)
        logger.info(f"  → {len(sections)} разделов (из рекомендаций): {sections}")
        # Загрузка шаблона отчёта
        template_path = os.path.join(template_dir, template_name)
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Шаблон не найден: {template_path}")
        doc = Document(template_path)
        # Предупреждаем, если в шаблоне мало таблиц
        if len(doc.tables) < 3:
            logger.warning("В шаблоне недостаточно таблиц; некоторые данные могут быть отображены некорректно")

        # 4) Разбиение на секции
        logger.info("4) Разбиение текстов по разделам…")
        if loader_test.doc_type == "leaflet":
          test_blocks = segment_text_semantic(test_text, sections)
          ref_blocks = segment_text_semantic(ref_text, sections)
        else:
           test_blocks = split_ohlp_sections(test_text, sections)
           ref_blocks = split_ohlp_sections(ref_text, sections)
        recs_blocks = split_recommendations(rec_text, sections)

        # 5) Проверка рекомендаций
        logger.info("5) Проверка рекомендаций для каждого раздела…")
        recommendations = {}
        for idx, sec in enumerate(sections, start=1):
            logger.info(f"  {idx}/{len(sections)}: раздел '{sec}'…")
            try:
                actual_text = test_blocks.get(sec, "")
                matches = get_close_matches(
                    sec, recs_blocks.keys(), n=1, cutoff=0.7)
                if not matches:
                    logger.warning(" – рекомендации не найдены, пропускаем")
                    continue
                rec_part = recs_blocks[matches[0]]
                if not rec_part.strip():
                    logger.debug(" – рекомендации пусты, пропускаем")
                    continue
                result = checker.check_recommends(rec_part, actual_text)
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                logger.info(
                    f"    → recommendation (first 100): {str(result)[:100]}...")
                recommendations[sec] = result
            except Exception as e:
                logger.error(
                    f"Ошибка при проверке рекомендаций для раздела '{sec}': {e}")
                logger.debug(traceback.format_exc())
                recommendations[sec] = f"Ошибка: {e}"

        # 6) Вставка рекомендаций и заполнение таблицы
        logger.info("6) Вставка рекомендаций и заполнение таблицы…")
        try:
            insert_recommendations(doc, recommendations)
        except Exception as e:
            logger.error(f"Ошибка вставки рекомендаций: {e}")
            logger.debug(traceback.format_exc())

        compare_data = [
            {
                "Раздел": sec,
                "Содержимое референтного документа": ref_blocks.get(sec, ""),
                "Содержимое тестируемого документа": test_blocks.get(sec, ""),
            }
            for sec in sections
        ]
        try:
            fill_comparison_table(
            doc,
            compare_data,
            table_index=2)
        except Exception as e:
            logger.error(f"Ошибка заполнения таблицы сравнения: {e}")
            logger.debug(traceback.format_exc())

        # 7) Замена плейсхолдеров
        logger.info("7) Замена плейсхолдеров (имена, даты…)…")
        try:
            replacements = build_replacements(
                loader_ref.drug_name, loader_test.drug_name)
            replace_placeholders_in_doc(doc, replacements)
        except Exception as e:
            logger.error(f"Ошибка замены плейсхолдеров: {e}")
            logger.debug(traceback.format_exc())

        # 8) Сохранение отчёта
        logger.info("8) Сохранение итогового документа…")

        original_filename = os.path.splitext(
            os.path.basename(loader_test.file_path))[0]

        output_path = save_with_timestamp(
            doc,
            filetype=loader_test.doc_type,
            original_filename=original_filename,
            output_dir=output_dir,
            prefix=prefix
        )
        logger.info(f"Отчёт сохранён: {output_path}")
        logger.info("=== Генерация отчёта завершена ===")
        return output_path

    except Exception as e:
        logger.critical(f"Невозможно сгенерировать отчёт: {e}")
        logger.debug(traceback.format_exc())
        raise

async def generate_report_async(
    test_path: str,
    ref_path: str,
    rec_path: str,
    template_name: str,
    template_dir: str,
    output_dir: str,
    provider: str,
    prefix: str
) -> str:
    """
    Асинхронная генерация отчёта на основе тестовой инструкции,
    эталонной инструкции и рекомендаций.
    Возвращает путь к сгенерированному отчёту .docx.
    """
    logger.info("=== [async] Начало генерации отчёта ===")
    try:
        # 1) Извлечение текстов (параллельно)
        logger.info("1) [async] Извлечение текстов из документов…")
        loader_test = DocumentLoader(test_path)
        loader_ref = DocumentLoader(ref_path)

        test_text, ref_text = await asyncio.gather(
            asyncio.to_thread(loader_test.load),
            asyncio.to_thread(loader_ref.load)
        )
        logger.info(f"  → TEST ({test_path}): {len(test_text)} chars, "
                    f"type={loader_test.doc_type}, drug={loader_test.drug_name!r}")
        logger.info(f"  → REF  ({ref_path}):  {len(ref_text)} chars, "
                    f"type={loader_ref.doc_type}, drug={loader_ref.drug_name!r}")

        # используем кэшированные рекомендации (в отдельном потоке)
        rec_text = await asyncio.to_thread(load_cached_recommendations, rec_path)
        logger.info(f"  → REC  ({rec_path}):  {len(rec_text)} chars")

        # 2) Инициализация SectionChecker
        logger.info(f"2) [async] Инициализация SectionChecker с провайдером: {provider}")
        checker = SectionChecker(api_provider=provider)

        # 3) [async] Извлечение списка разделов из файла рекомендаций и загрузка шаблона
        logger.info("3) [async] Извлечение списка разделов из файла рекомендаций…")
        sections = extract_sections_from_recommendations(rec_text)
        logger.info(f"  → {len(sections)} разделов (из рекомендаций): {sections}")
        # Загрузка шаблона отчёта
        template_path = os.path.join(template_dir, template_name)
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Шаблон не найден: {template_path}")
        doc = Document(template_path)
        if len(doc.tables) < 3:
            logger.warning("[async] В шаблоне недостаточно таблиц; некоторые данные могут быть отображены некорректно")

        # 4) Разбиение на секции (параллельно в пуле потоков)
        logger.info("4) [async] Разбиение текстов по разделам…")
        if loader_test.doc_type == "leaflet":
            test_blocks, ref_blocks = await asyncio.gather(
                asyncio.to_thread(segment_text_semantic, test_text, sections),
                asyncio.to_thread(segment_text_semantic, ref_text, sections),
            )
        else:
            test_blocks, ref_blocks = await asyncio.gather(
                asyncio.to_thread(split_ohlp_sections, test_text, sections),
                asyncio.to_thread(split_ohlp_sections, ref_text, sections),
            )
        recs_blocks = await asyncio.to_thread(split_recommendations, rec_text, sections)

        # 5) Проверка рекомендаций (параллельно с ограничением семафором)
        logger.info("5) [async] Проверка рекомендаций для каждого раздела…")
        sem = asyncio.Semaphore(5)  # настройте под лимиты провайдера/ресурсов

        async def check_one(sec: str):
            logger.info(f"  → раздел '{sec}'…")
            actual_text = test_blocks.get(sec, "")
            matches = get_close_matches(sec, recs_blocks.keys(), n=1, cutoff=0.7)
            if not matches:
                logger.warning(f"    – рекомендации не найдены для '{sec}', пропускаем")
                return sec, {'compliance': 'not_complied', 'comments': ''}

            rec_part = recs_blocks[matches[0]]
            if not rec_part.strip():
                logger.debug(f"    – рекомендации пусты для '{sec}', пропускаем")
                return sec, {'compliance': 'not_complied', 'comments': ''}

            async with sem:
                try:
                    result = await checker.check_recommends_async(rec_part, actual_text)
                    logger.info(f"    → recommendation (first 100): {str(result)[:100]}…")
                    return sec, result
                except Exception as e:
                    logger.error(f"Ошибка при проверке рекомендаций для '{sec}': {e}")
                    logger.debug(traceback.format_exc())
                    return sec, {'compliance': 'not_complied', 'comments': f'Ошибка: {e}'}

        results = await asyncio.gather(*(check_one(sec) for sec in sections))
        recommendations = {sec: rec for sec, rec in results}

        # 6) Вставка рекомендаций и заполнение таблицы
        logger.info("6) [async] Вставка рекомендаций и заполнение таблицы…")
        try:
            insert_recommendations(doc, recommendations)
        except Exception as e:
            logger.error(f"Ошибка вставки рекомендаций: {e}")
            logger.debug(traceback.format_exc())

        compare_data = [
            {
                "Раздел": sec,
                "Содержимое референтного документа": ref_blocks.get(sec, ""),
                "Содержимое тестируемого документа": test_blocks.get(sec, ""),
            }
            for sec in sections
        ]
        try:
            fill_comparison_table(doc, compare_data, table_index=2)
        except Exception as e:
            logger.error(f"Ошибка заполнения таблицы сравнения: {e}")
            logger.debug(traceback.format_exc())

        # 7) Замена плейсхолдеров
        logger.info("7) [async] Замена плейсхолдеров (имена, даты…)…")
        try:
            replacements = build_replacements(loader_ref.drug_name, loader_test.drug_name)
            replace_placeholders_in_doc(doc, replacements)
        except Exception as e:
            logger.error(f"Ошибка замены плейсхолдеров: {e}")
            logger.debug(traceback.format_exc())

        # 8) Сохранение отчёта
        logger.info("8) [async] Сохранение итогового документа…")
        original_filename = os.path.splitext(os.path.basename(loader_test.file_path))[0]
        output_path = save_with_timestamp(
            doc,
            filetype=loader_test.doc_type,
            original_filename=original_filename,
            output_dir=output_dir,
            prefix=prefix
        )
        logger.info(f"[async] Отчёт сохранён: {output_path}")
        logger.info("=== [async] Генерация отчёта завершена ===")
        return output_path

    except Exception as e:
        logger.critical(f"[async] Невозможно сгенерировать отчёт: {e}")
        logger.debug(traceback.format_exc())
        raise