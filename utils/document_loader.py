import logging
import html
import re
from pathlib import Path
from typing import Optional, Literal

from utils.docling_singletons import get_docx_converter, get_pdf_converter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Два типа документов: leaflet и ohlp
DocumentType = Literal["leaflet", "ohlp"]


class DocumentLoader:
    """
    Загружает и предобрабатывает два типа документов:
    — ohlp (общая характеристика)
    — leaflet (листок-вкладыш)
    Сохраняет:
      • doc_type: «ohlp» или «leaflet»
      • drug_name: название действующего вещества / препарата
      • text: итоговый чистый текст
    """

    def __init__(
        self,
        file_path: str,
        auto_detect_type: bool = True,
        doc_type: Optional[DocumentType] = None,
        auto_structure: bool = False,
    ):
        self.file_path = file_path
        self.auto_detect_type = auto_detect_type
        self.override_type = doc_type
        self.auto_structure = auto_structure

        self.doc_type: DocumentType = "leaflet"
        self.drug_name: str = ""
        self.text: str = ""
        self._raw_text: str = ""
        self._doc = None

    @staticmethod
    def detect_document_type(text: str) -> DocumentType:
        """
        По ключевым заголовкам решает, что перед нами:
        — ohlp (OHLP)
        — leaflet (листок-вкладыш)
        """
        ohlp_patterns = [
            r"ОБЩАЯ\s+ХАРАКТЕРИСТИКА\s+ЛЕКАРСТВЕННОГО\s+ПРЕПАРАТА",
            r"КЛИНИЧЕСКИЕ\s+ДАННЫЕ",
            r"ФАРМАКОЛОГИЧЕСКИЕ\s+СВОЙСТВА",
            r"ДЕРЖАТЕЛЬ\s+РЕГИСТРАЦИОННОГО\s+УДОСТОВЕРЕНИЯ",
        ]
        leaflet_patterns = [
            r"Листок[-\s]*вкладыш\s*–?\s*информация",
            r"Что\s+из\s+себя\s+представляет\s+препарат",
            r"Содержимое\s+упаковки",
        ]
        ohlp_score = sum(bool(re.search(p, text, re.IGNORECASE))
                         for p in ohlp_patterns)
        leaflet_score = sum(bool(re.search(p, text, re.IGNORECASE))
                            for p in leaflet_patterns)
        return "ohlp" if ohlp_score >= leaflet_score else "leaflet"

    def _get_converter(self):
        """Return a cached Docling converter based on file extension."""
        ext = Path(self.file_path).suffix.lower()
        return get_pdf_converter() if ext == ".pdf" else get_docx_converter()

    def _extract_drug_name_leaflet(self, text: str) -> str:
        """
        Извлекает название препарата из блока между 
        "листок-вкладыш" и "действующее вещество"
        """
        try:
            pattern = re.compile(
                r"(?:Листок[-\s]*вкладыш.*?\n)(.*?)(?=\n\s*Действующее вещество[:\s])",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(text)
            if match:
                block = match.group(1).strip()
                return self._normalize_drug_name(block)
        except Exception as e:
            logger.warning(
                f"[Ошибка при извлечении названия препарата из leaflet]: {e}")
        return ""

    def _extract_drug_name_ohlp(self, text: str) -> str:
        """
        Извлекает название препарата из ОХЛП между пунктами:
        1. НАИМЕНОВАНИЕ ... и 2. КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ
        """
        try:
            pattern = re.compile(
                r"1\.\s*НАИМЕНОВАНИЕ ЛЕКАРСТВЕННОГО ПРЕПАРАТА\s*(.*?)\s*2\.\s*КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(text)
            if match:
                name = match.group(1).strip()
                return self._normalize_drug_name(name)
        except Exception as e:
            logger.warning(
                f"[Ошибка при извлечении названия препарата из ОХЛП]: {e}")
        return ""

    def _clean_common(self, text: str) -> str:
        """
        Общие правила предобработки для обоих типов
        """
        text = html.unescape(text)
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"(?mi)^[Pp]age\s*\d+\s*$|^\d+\s*$", "", text)
        text = re.sub(r"(\w)\s*[-–—]\s*(\w)", r"\1-\2", text)
        text = re.sub(r"(?<![\.:!?])\n(?=[А-ЯA-Za-z0-9])", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"(https?://[^\s,]+)[\.,]?", r"\1", text)
        return text.strip()

    def _clean_ohlp(self, text: str) -> str:
        """
        Специфика OHLP
        """
        text = self._clean_common(text)
        text = re.sub(r"%\s*split\s*%", "\n%SPLIT%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"%\s*intro\s*%", "\n%INTRO%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"%\s*extra\s*%", "\n%EXTRA%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"N\s*\.\s*B\s*!+", "ВАЖНО:", text, flags=re.IGNORECASE)
        text = re.sub(r"\n{3,}", "\n\n%SECTION_BREAK%\n\n", text)
        text = re.sub(r"\n{2,}", "\n\n", text)
        return text

    def _clean_leaflet(self, text: str) -> str:
        """
        Специфика листка-вкладыша
        """
        text = self._clean_common(text)
        text = re.sub(r"%\s*split\s*%", "\n%SPLIT%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"%\s*intro\s*%", "\n%INTRO%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"%\s*extra\s*%", "\n%EXTRA%\n",
                      text, flags=re.IGNORECASE)
        text = re.sub(r"N\s*\.\s*B\s*!+", "ВАЖНО:", text, flags=re.IGNORECASE)
        text = re.sub(r"\n{3,}", "\n\n%SECTION_BREAK%\n\n", text)
        text = re.sub(r"\n{2,}", "\n\n", text)
        return text

    def load(self) -> str:
        """
        Основной метод:
        1) конвертирует PDF/DOCX в текст и сохраняет Document
        2) определяет doc_type
        3) извлекает drug_name
        4) чистит через соответствующий метод
        5) при auto_structure для leaflet — дополнительно структурирует
        """
        converter = self._get_converter()
        result = converter.convert(self.file_path)
        self._doc = result.document

        # 1) получаем текст — сначала через export_to_markdown
        try:
            raw_text = result.document.export_to_markdown()
            logger.info("[Текст получен через export_to_markdown()]")
        except Exception as e:
            logger.warning(f"[Markdown экспорт не удался: {e}]")
            raw_text = getattr(result, "text", "") or ""
            logger.info("[Текст получен через result.text]")

        self._raw_text = raw_text
        logger.info(f"[Исходный текст]:\n{raw_text[:500]}…")

        # 2) определяем тип документа
        if self.override_type:
            self.doc_type = self.override_type
        elif self.auto_detect_type:
            self.doc_type = self.detect_document_type(raw_text)
        else:
            self.doc_type = "leaflet"
        logger.info(f"Detected document type: {self.doc_type}")

        # 3) извлекаем название препарата
        if self.doc_type == "leaflet":
            self.drug_name = self._extract_drug_name_leaflet(raw_text)
        else:
            self.drug_name = self._extract_drug_name_ohlp(raw_text)
        logger.info(f"Extracted drug name: {self.drug_name!r}")

        # 4) чистим текст
        if self.doc_type == "ohlp":
            cleaned = self._clean_ohlp(raw_text)
        else:
            cleaned = self._clean_leaflet(raw_text)
            # 1) разорвать склейки «…вкладыша-1.» → «…вкладыша\n1.»
            cleaned = re.sub(r'-(?=\d+\.)', '\n', cleaned)
            # 2) обеспечить перенос перед любым N., где за точкой идёт заглавная
            cleaned = re.sub(r'(?<!\n)(\d+\.)\s*(?=[А-ЯЁ])', r'\n\1 ', cleaned)
            # 3) убрать подряд идущие пустые строки (оставляя по одной)
            cleaned = re.sub(r'\n{2,}', '\n', cleaned).strip()

        # 5) структурируем, если требуется
        if self.auto_structure and self.doc_type == "leaflet":
            logger.info("Applying leaflet structure preprocessing…")
            from .leaflet_structurer import preprocess_leaflet_structure
            cleaned = preprocess_leaflet_structure(cleaned)


        self.text = cleaned
        return self.text
    
    def _normalize_drug_name(self, text: str) -> str:
        """
        Удаляет лишние переносы строк, спецсимволы и пробелы внутри названия препарата
        """
        text = re.sub(r"\n{2,}", "\n", text)                  # двойные переводы строк → один
        text = re.sub(r"[#–—•▪]", "", text)                   # убираем спецсимволы
        text = re.sub(r"\s*-\s*", "-", text)                  # пробелы вокруг дефисов
        text = re.sub(r"[ \t]+", " ", text)                   # множественные пробелы
        return text.strip()

    def simple_load(self) -> str:
        """
        Извлекает и очищает текст из PDF, устраняя артефакты форматирования 
        и запятых. Используем для загрузки рекомендаций
        """
        try:
            converter = self._get_converter()
            result = converter.convert(self.file_path)
            raw_text = result.document.export_to_markdown()
            logger.info(f"[Исходный текст]:\n{raw_text[:1000]}")

            text = raw_text
            
            text = html.unescape(text)

            # Удаление Markdown
            text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
            text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)

            # Удаление номеров страниц
            text = re.sub(r"(?m)^[Pp]age\s*\d+\s*$", "", text)
            text = re.sub(r"(?m)^\d+\s*$", "", text)

            # Удаление пробелов между частями слов с дефисом
            text = re.sub(r"(\w)\s*-\s*(\w)", r"\1-\2", text)

            # Склейка строк, если следующая строка не начинается с заглавной буквы или маркера
            text = re.sub(r"(?<![.:!?])\n(?=[а-яa-z0-9])", " ", text)

            # Сжатие пробелов
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\s+([,.;:!?])", r"\1", text)

            # Удаление запятой после URL
            text = re.sub(r"(\bhttps?://[^\s,]+)\.,", r"\1.", text)

            # Удаление запятой в конце строки, если она не перед перечислением
            text = re.sub(r",\s*(?=\n|$)", "", text)

            # Исправление утерянной запятой между дозировкой и формой, если встречаются три слова подряд с первым числовым
            text = re.sub(r"\b(\d+)\s+(мг|г|мл|%)\s+(\w+)", r"\1 \2, \3", text)

            # Удаление лишних пробелов в кавычках
            text = re.sub(r"«\s*(.*?)\s*»", r"«\1»", text)

            # Удаление множественных пустых строк
            text = re.sub(r"\n{2,}", "\n", text)

            return text.strip()

        except Exception as e:
            logger.error(f"Ошибка при извлечении текста: {e}")
            raise
