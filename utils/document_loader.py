"""
Модуль извлечения и очистки текста из PDF с помощью Docling DocumentConverter.
"""
import logging
import re

from docling.document_converter import DocumentConverter

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_text_from_document(file_path: str) -> str:
    """
    Загружает документ через Docling DocumentConverter и возвращает очищенный текст.

    Процесс:
    1. Конвертация в документ Docling.
    2. Экспорт контента в Markdown.
    3. Удаление синтаксиса Markdown (заголовков, списков).
    4. Удаление номеров страниц.
    5. Нормализация пробелов и переносов строк.

    :param file_path: путь к файлу
    :return: очищенный текст документа
    """
    try:
        logger.info(f"Конвертация документа через Docling: {file_path}")
        converter = DocumentConverter()
        result = converter.convert(file_path)
        # Экспорт в Markdown
        md_content = result.document.export_to_markdown()
        text = md_content

        # Удаляем базовый Markdown-синтаксис:
        # 1) Удаляем заголовки Markdown: строки, начинающиеся с одного или нескольких '# ' (например, '# Заголовок')
        #    после удаления '# ' остается только текст заголовка
        # text = re.sub(r"^#+\s+", "", md_content, flags=re.MULTILINE)
        # 2) Удаляем маркеры списков Markdown: строки, начинающиеся с '-', '*' или '+' и пробела
        #    (например, '- пункт' или '* пункт' -> 'пункт')
        text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)

        # Удаляем строки с номерами страниц (только цифры или 'Page X')
        text = re.sub(r"(?m)^[Pp]age\s*\d+\s*$", "", text)
        text = re.sub(r"(?m)^\d+\s*$", "", text)

        # Нормализация пробелов и переводов строк:
        # Убираем лишние пробелы и табуляции внутри строк
        text = re.sub(r"[ \t]+", " ", text)
        # Сокращаем более двух подряд переводов строк до двух
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text
    except Exception as e:
        logger.error(f"Ошибка при извлечении текста: {e}")
        raise