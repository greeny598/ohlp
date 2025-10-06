import logging
import html
import re
import unicodedata
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
        encoding: str = "utf-8"  # ← Добавляем параметр
    ):
        self.file_path = file_path
        self.auto_detect_type = auto_detect_type
        self.override_type = doc_type
        self.auto_structure = auto_structure
        self.encoding = encoding  # ← Сохраняем кодировку

        self.doc_type: DocumentType = "leaflet"
        self.drug_name: str = ""
        self.text: str = ""
        self._raw_text: str = ""
        self._doc = None

    @staticmethod
    def detect_document_type(text: str) -> Literal["leaflet", "ohlp"]:
        """
        Надёжное определение типа документа по уникальным заголовкам/структурам.

        Листок-вкладыш (leaflet): 
          • "Листок-вкладыш … информация для пациента" и/или 
          • "Содержание листка-вкладыша" и/или 
          • >=3 из разделов 1–6 для пациента:
            1. Что из себя представляет … и для чего его применяют
            2. О чём следует знать …
            3. Применение/Как принимать
            4. Возможные нежелательные реакции
            5. Хранение (препарата)
            6. Содержимое упаковки и прочие сведения

        ОХЛП (ohlp):
          • "ОБЩАЯ ХАРАКТЕРИСТИКА ЛЕКАРСТВЕННОГО ПРЕПАРАТА" И 
          • наличие разделов "1. НАИМЕНОВАНИЕ …" и "2. КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ"
            (или ≥3 характерных раздела 1–6, включая 1 и 2).
        """

        # Нормализация
        t = unicodedata.normalize("NFKC", text or "")
        t = t.replace("\u00A0", " ")
        tl = t.lower()
        dash = r"[\-\u2010\u2011\u2012\u2013\u2014\u2212\u2043]"

        # --- LEAFLET: заголовки/оглавление ---
        lv_heading = re.search(
            rf"листок{dash}?\s*вкладыш.*?информация\s+для\s+пациента", tl, re.DOTALL
        )
        lv_contents = re.search(
            rf"содержание\s+листка{dash}?\s*вкладыша", tl
        )

        # LEAFLET: типовая 1–6 структура (допускаем вариации формулировок)
        lv_sections_patterns = [
            r"\b1\.\s*(что\s+(?:из\s+себя\s+представляет|такое).{0,120}?и\s+для\s+чего\s+его\s+применя[юе]т)",
            r"\b2\.\s*(о\s*ч[её]м\s+следует\s+знать|что\s+нужно\s+знать)",
            r"\b3\.\s*(применени[ея]|как\s+принима[тьте]|как\s+использовать)",
            r"\b4\.\s*возможн[ыо]е?\s+нежелательн[ыо]е?\s+реакц",
            r"\b5\.\s*хранени[ея](?:\s+препарата)?",
            r"\b6\.\s*содержим[оа]е\s+упаковк[иы].*?проч[иы]е?\s+сведен",
        ]
        lv_sections_found = sum(bool(re.search(p, tl, re.DOTALL)) for p in lv_sections_patterns)

        # --- OHLP: «шапка» и нумерованные разделы 1–6 ---
        o_head = re.search(r"\bобщая\s+характеристика\s+лекарственн[оы]го\s+препарат[аи]\b", tl)
        o_s1 = re.search(r"\b1\.\s*наименовани[ея]\s+лекарственн[оы]го\s+препарат[аи]\b", tl)
        o_s2 = re.search(r"\b2\.\s*качественн[ыий]\s+и\s+количественн[ыий]\s+состав\b", tl)
        o_s3 = re.search(r"\b3\.\s*лекарственн[аояы]\s+форма\b", tl)
        o_s4 = re.search(r"\b4\.\s*клиническ[иеих]\s+данн[ыеых]\b", tl)
        o_s5 = re.search(r"\b5\.\s*фармакологическ[иеих]\s+свойств[ао]\b", tl)
        o_s6 = re.search(r"\b6\.\s*фармацевтическ[иеих]\s+свойств[ао]\b", tl)
        ohlp_sections_found = sum(bool(x) for x in (o_s1, o_s2, o_s3, o_s4, o_s5, o_s6))

        # --- Решение (жёсткие правила → потом мягкий скоринг) ---
        # 1) Явные признаки листка-вкладыша
        if lv_heading or lv_contents or lv_sections_found >= 3:
            return "leaflet"

        # 2) Явные признаки ОХЛП
        if (o_head and o_s1 and o_s2) or (o_s1 and o_s2 and ohlp_sections_found >= 3):
            return "ohlp"

        # 3) Тай-брейк: если упомянут «листок-вкладыш», предпочитаем leaflet
        if re.search(rf"листок{dash}?\s*вкладыш", tl):
            return "leaflet"

        # 4) Мягкий скоринг (без «держателя РУ», т.к. встречается в обоих типах)
        lv_score = (1 if lv_heading else 0) + (1 if lv_contents else 0) + min(lv_sections_found, 3)
        ohlp_score = (1 if o_head else 0) + (2 if (o_s1 and o_s2) else 0) + min(ohlp_sections_found, 3)

        return "leaflet" if lv_score >= ohlp_score else "ohlp"


    def _get_converter(self):
        """Return a cached Docling converter based on file extension."""
        ext = Path(self.file_path).suffix.lower()
        if ext == ".pdf":
            return get_pdf_converter() 
        else:
            return get_docx_converter()

    
    def _extract_brand_from_line(self, line: str) -> str:
        """
        Возвращает только наименование (бренд) из первой строки заголовка,
        обрезая дозировку/концентрацию/процент и т.п.
        Логика: идём слева направо и собираем токены, пока не встретим цифры.
        """
        # подстраховка: восстановим ®/™ и нормализуем пробелы
        line = self._restore_trademarks(line or "")
        line = re.sub(r"\s+", " ", line.strip())

        if not line:
            return ""

        brand_tokens = []
        for tok in line.split():
            # очистим крайние знаки препинания, но оставим внутренние дефисы
            tok_clean = tok.strip(",.;:()[]{}")

            # отдельный токен с ®/™ — приклеиваем к предыдущему
            if tok_clean in {"®", "™"}:
                if brand_tokens:
                    brand_tokens[-1] = brand_tokens[-1] + " " + tok_clean
                continue

            # как только встречаем цифры (в т.ч. в склеенном виде "10мг/мл" или "0,25")
            # — останавливаемся: дальше пошла дозировка/концентрация/процент
            if re.search(r"\d", tok_clean):
                break

            brand_tokens.append(tok_clean)

        brand = " ".join(brand_tokens).strip(" ,.;:")

        # Фоллбек: если по какой-то причине ничего не собрали — обрежем по первой запятой
        if not brand:
            brand = re.split(r"\s*,\s*", line, maxsplit=1)[0].strip(" ,.;:")

        # финальная нормализация (дефисы/пробелы и т.п.)
        return self._normalize_drug_name(brand)

    
    
    def _extract_drug_name_leaflet(self, text: str) -> str:
        """
        Извлекает название препарата из блока между
        «Листок-вкладыш … информация для пациента» и «Действующее вещество».
        Возвращает только наименование (без дозировки, формы и т.п.).
        """
        try:
            pattern = re.compile(
                r"(?:Листок[-\s]*вкладыш.*?\n)(.*?)(?=\n\s*Действующее\s+вещество\s*:?)",
                re.IGNORECASE | re.DOTALL,
            )
            m = pattern.search(text)
            if not m:
                return ""

            block = (m.group(1) or "").strip()
            first_line = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")

            if not first_line:
                return ""

            return self._extract_brand_from_line(first_line)

        except Exception as e:
            logger.warning(f"[Ошибка при извлечении названия препарата из leaflet]: {e}")
            return ""


    def _extract_drug_name_ohlp(self, text: str) -> str:
        """
        Извлекает название препарата из ОХЛП между пунктами:
        1. НАИМЕНОВАНИЕ ... и 2. КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ.
        Возвращает только наименование (без дозировки, формы и т.п.).
        """
        try:
            pattern = re.compile(
                r"1\.\s*НАИМЕНОВАНИЕ\s+ЛЕКАРСТВЕННОГО\s+ПРЕПАРАТА\s*(.*?)\s*2\.\s*КАЧЕСТВЕННЫЙ\s+И\s+КОЛИЧЕСТВЕННЫЙ\s+СОСТАВ",
                re.IGNORECASE | re.DOTALL,
            )
            m = pattern.search(text)
            if not m:
                return ""

            block = (m.group(1) or "").strip()
            first_line = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")

            if not first_line:
                return ""

            return self._extract_brand_from_line(first_line)

        except Exception as e:
            logger.warning(f"[Ошибка при извлечении названия препарата из ОХЛП]: {e}")
            return ""

    
    def _normalize_bullets_keep(self, text: str, style: str = "dot") -> str:
        """
        Сохраняем маркеры списков в тексте, но приводим к одному виду.
        Схлопывает к одному символу любой набор из: •, дефисы ( -, –, — ),
        а также приватные PUA-глифы (там сидит '').
        """
        # Нормализуем и единообразно заменим NBSP
        text = unicodedata.normalize("NFKC", text).replace("\u00A0", " ")

        # Выбор целевого маркера
        bullet = "•" if style == "dot" else "–"

        # 1) Главная замена: в НАЧАЛЕ КАЖДОЙ СТРОКИ
        # Схлопываем любой «пучок» (PUA, буллеты, дефисы) + пробелы → один маркер + пробел.
        BULLET_CLUSTER = (
            r"[\uF000-\uF8FF"              # приватные (PUA), в т.ч. ''
            r"\u2022\u2023\u25E6\u2219"    # •, ‣, ◦, ∙
            r"\u00B7\u25CF\u25AA\u25A0"    # ·, ●, ▪, ■
            r"\u2043"                      # ⁃ (hyphen bullet)
            r"\-\u2010\u2011\u2012\u2013\u2014]"  # -, ‐, -, ‒, –, —
        )
        # Если после «пучка» идут пробелы и дальше буква/цифра — считаем это пунктом списка
        pattern = rf"(?m)^[ \t]*({BULLET_CLUSTER}(?:[ \t]*{BULLET_CLUSTER})*)[ \t]+(?=[0-9A-Za-zА-Яа-я])"
        text = re.sub(pattern, bullet + " ", text)

        # 2) Добивка частных кейсов после предыдущих правок:
        #   • —/PUA/дефисы всё ещё остались после первого маркера → убираем их
        text = re.sub(r"(?m)^" + re.escape(bullet) + r"[ \t]+(?:[\uF000-\uF8FF\-–—])+[ \t]+", bullet + " ", text)
        #   • двойной буллет
        text = re.sub(r"(?m)^" + re.escape(bullet) + r"[ \t]+" + re.escape(bullet) + r"[ \t]+", bullet + " ", text)

        return text
        
    def _restore_trademarks(self, text: str) -> str:
        """
        Восстанавливает знаки товарных марок, если конвертер превратил их в '0'
        или похожие кружки. Правила не трогают дроби/градусы.
        """
        # 1) Явные текстовые формы → символы
        text = re.sub(r"\(\s*[Rr]\s*\)", "®", text)
        text = re.sub(r"\(\s*T[Mm]\s*\)", "™", text)

        # 2) Одиночные маркеры после слова-бренда → ®
        #    - перед символом: слово, начинающееся с буквы (лат/кирилл), длиной ≥2
        #    - символ: 0 или варианты кружков/градусов
        #    - после символа НЕ должно идти число (в т.ч. через . или ,), и НЕ должно быть C/С (Цельсий)
        trademark_like = r"(?:0|°|º|˚|○)"
        pattern = re.compile(
            rf"(\b[A-Za-zА-Яа-яЁё][\w\-]{{1,}})\s*{trademark_like}\b(?!\s*\d|\s*[.,]\s*\d| *[CcСс])"
        )
        text = re.sub(pattern, r"\1 ®", text)

        return text
    
    def _clean_common(self, text: str) -> str:
        text = html.unescape(text)
        text = self._restore_trademarks(text)

        # 1) Сначала нормализуем маркеры и оставляем их в тексте
        text = self._normalize_bullets_keep(text, style="dot")  # или "dash"
        
        # остальной пайплайн можно оставить:
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"(?mi)^[Pp]age\s*\d+\s*$|^\d+\s*$", "", text)
        text = re.sub(r"(\w)\s*[-–—]\s*(\w)", r"\1-\2", text)  # не трогает наши буллеты
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

        raw_text = unicodedata.normalize("NFKC", raw_text).replace("\u00A0", " ")
        raw_text = self._restore_trademarks(raw_text)
        self._raw_text = raw_text
        logger.info(f"[Исходный текст]:\n{raw_text[:100]}…")

        # 2) определяем тип документа
        # применяем жёсткие эвристики: имя файла и первая строка
        if self.override_type:
            self.doc_type = self.override_type
        elif self.auto_detect_type:
            try:
                from difflib import SequenceMatcher
                # имя файла без пути и расширения
                file_name_lower = Path(self.file_path).stem.lower()
                # если в имени содержится ohlp/охлп → считаем OHLP
                if ('ohlp' in file_name_lower) or ('охлп' in file_name_lower):
                    self.doc_type = 'ohlp'
                else:
                    # первая непустая строка текста
                    first_line = next((ln.strip() for ln in raw_text.splitlines() if ln.strip()), '').lower()
                    target = 'общая характеристика лекарственного препарата'
                    if first_line:
                        ratio = SequenceMatcher(None, first_line, target).ratio()
                        # если похожа на заголовок ОХЛП → OHLP
                        if ratio >= 0.8:
                            self.doc_type = 'ohlp'
                        # явный листок-вкладыш
                        elif first_line.startswith('листок-вкладыш') and ('информация для пациента' in first_line):
                            self.doc_type = 'leaflet'
                        else:
                            self.doc_type = self.detect_document_type(raw_text)
                    else:
                        self.doc_type = self.detect_document_type(raw_text)
            except Exception:
                # fallback на старый детектор
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

        self.text = cleaned
        return self.text
    
    def _normalize_drug_name(self, text: str) -> str:
        """
        Удаляет лишние переносы строк, дефисы в начале строки, спецсимволы и пробелы внутри названия препарата
        """
        text = self._restore_trademarks(text)            # подстраховка
        text = re.sub(r"\n{2,}", "\n", text)             # двойные переводы строк → один
        text = re.sub(r"\s*\n\s*", " ", text)            # убираем переносы внутри названия
        text = re.sub(r"^-+", "", text.strip())          # дефисы в начале строки
        text = re.sub(r"[#–—•▪]", "", text)              # убираем спецсимволы
        text = re.sub(r"\s*-\s*", "-", text)             # пробелы вокруг дефисов
        text = re.sub(r"[ \t]+", " ", text)              # множественные пробелы
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
