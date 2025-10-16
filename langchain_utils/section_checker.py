import logging
from typing import Dict, Any, Literal, List, Tuple, Optional
import json
import re
from difflib import SequenceMatcher

from pydantic import BaseModel, validator, Extra, ValidationError
from langchain.prompts import PromptTemplate
from langchain_core.runnables import RunnableSequence
from langchain_core.utils.json import parse_json_markdown
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama, YandexGPT

from config_reader import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Recommendation(BaseModel):
    compliance: Literal['complied', 'partial', 'not_complied']
    comments: str

    class Config:
        extra = Extra.forbid  # запрещаем лишние поля

    @validator('comments', pre=True)
    def ensure_comments_str(cls, v: Any) -> str:
        # если LLM прислала словарь — сериализуем его в JSON, иначе приводим к строке
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return str(v)


class SectionChecker:
    def __init__(self, api_provider: str = 'yandex'):
        self.yandexgpt_key = config.yandexgpt_KEY.get_secret_value()
        self.yandex_cloud_folder_id = config.yandex_cloud_folder_id.get_secret_value()
        # self.deepseek_key = config.deepseek_KEY.get_secret_value()
        # self.openai_key = config.openai_KEY.get_secret_value()
        self.llm = self._set_api_provider(api_provider)
        self.prompt_diffs = self._create_diffs_prompt_template()
        self.chain_diffs = RunnableSequence(self.prompt_diffs | self.llm)
        self.prompt_recs = self._create_recs_prompt_template()
        self.chain_recs = RunnableSequence(self.prompt_recs | self.llm)

    # --------------- Новая/улучшенная утилитарная часть для структурного сопоставления ---------------

    # снимаем "10.", "4.1.", "9 -", "7) " и т.п.
    _num_prefix = re.compile(r'^\s*\d+(?:\.\d+)?\s*[.)-–—]*\s*')
    _ws_multi = re.compile(r'\s+')
    _edge_punct = re.compile(r'^[\s<>\-\–—:;,\.\)\(]+|[\s<>\-\–—:;,\.\)\(]+$')

    _optional_markers = re.compile(
        r'\((?:заполняется\s+при\s+необходимости|если\s+применимо|при\s+необходимости)\)',
        flags=re.IGNORECASE
    )

    @classmethod
    def normalize_title(cls, title: str) -> str:
        """
        Нормализуем заголовок для сравнения:
        - снимаем числовой префикс (но сам текст заголовка не подменяем)
        - схлопываем пробелы
        - убираем крайние угловые скобки/пунктуацию, если они "обрамляют" заголовок
        Важно: регистр не меняем, содержимое скобок сохраняем — это позволяет ловить мелкие расхождения
        вроде «НОМЕР (НОМЕРА) ...» vs «НОМЕР ...».
        """
        if title is None:
            return ''
        t = title.strip()
        t = cls._num_prefix.sub('', t)
        t = cls._edge_punct.sub('', t)
        t = cls._ws_multi.sub(' ', t)
        return t.strip()

    @staticmethod
    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _best_match(self, actual: str, sections_norm: List[str]) -> Tuple[int, float]:
        """Находит лучший эталон по похожести для actual среди нормализованных sections_norm."""
        best_i, best_s = -1, 0.0
        for i, s in enumerate(sections_norm):
            sim = self.similarity(actual, s)
            if sim > best_s:
                best_s, best_i = sim, i
        return best_i, best_s

    def compare_structure(
        self,
        sections: List[str],
        test_blocks: Dict[str, str],
        sim_minor_diff: float = 0.995,  # почти идентично, иначе — замечание о расхождении формулировки
        sim_any_match: float = 0.80     # ниже — считаем, что соответствия НЕТ (лишняя секция)
    ) -> List[Dict[str, Any]]:
        """
        Сравнивает структуру: эталонные заголовки (sections) vs. заголовки документа (ключи test_blocks).
        ВНИМАНИЕ: НИКОГДА не подменяет заголовки из документа — только формирует рекомендации.

        Возвращает список рекомендаций (каждая — dict как у Recommendation + поле "section" для адресации).
        """
        recommendations: List[Dict[str, Any]] = []

        # Нормализованные списки для сравнения
        actual_titles = list(test_blocks.keys())
        actual_norm = [self.normalize_title(x) for x in actual_titles]

        sections = sections or []
        sections_norm = [self.normalize_title(x) for x in sections]

        # Куда сопоставился каждый actual и каждый expected
        matched_expected_idx: Dict[int, int] = {}  # actual_idx -> expected_idx
        used_expected: set = set()

        # 1) проходим по фактическим заголовкам и ищем ближайший эталон
        for i, a_norm in enumerate(actual_norm):
            exp_i, sim = self._best_match(a_norm, sections_norm) if sections_norm else (-1, 0.0)

            if exp_i == -1 or sim < sim_any_match:
                # Лишняя секция — не нашли ничего достаточно похожего
                recommendations.append({
                    "section": actual_titles[i],
                    "compliance": "not_complied",
                    "comments": (
                        "В документе обнаружен раздел, отсутствующий в рекомендуемом перечне: "
                        f"«{actual_titles[i]}». Проверьте корректность включения данного раздела."
                    )
                })
            else:
                # Есть разумное совпадение с эталоном
                matched_expected_idx[i] = exp_i
                used_expected.add(exp_i)

                # Отмечаем даже минимальные отличия формулировки
                exp_title = sections[exp_i]
                act_title = actual_titles[i]
                if a_norm.lower() != sections_norm[exp_i].lower() or sim < sim_minor_diff:
                    comments = (
                        "Название раздела в документе отличается от рекомендуемого.\n"
                        f"— Рекомендуемый заголовок: «{exp_title}»\n"
                        f"— Фактический заголовок: «{act_title}»\n"
                        "Рекомендация: привести формулировку в соответствие с эталоном "
                        "(если это не обусловлено утверждённой формой документа)."
                    )
                    recommendations.append({
                        "section": act_title,
                        "compliance": "partial",
                        "comments": comments
                    })

        # 2) теперь отметим отсутствующие в документе разделы из эталона
        for exp_idx, exp_title in enumerate(sections):
            if exp_idx not in used_expected:
                # Не найдено соответствующего фактического заголовка
                is_optional = bool(self._optional_markers.search(exp_title))
                note = " Раздел помечен как необязательный (заполняется при необходимости/если применимо)." if is_optional else ""
                recommendations.append({
                    "section": exp_title,
                    "compliance": "not_complied",
                    "comments": f"В документе отсутствует рекомендуемый раздел: «{exp_title}».{note}"
                })

        return recommendations

    # ---------------------------------- LLM часть (без изменений) ----------------------------------

    def _set_api_provider(self, api_provider: str) -> ChatOpenAI:
        if api_provider == 'yandex':
            return YandexGPT(
                api_key=self.yandexgpt_key,
                folder_id=self.yandex_cloud_folder_id,
                modelUri=f"gpt://{self.yandex_cloud_folder_id}/yandexgpt/latest",
                temperature=0.1,
                maxTokens=2000
            )
        elif api_provider == 'deepseek':
            return ChatOpenAI(
                api_key=self.deepseek_key,
                model='deepseek-chat',
                base_url='https://api.deepseek.com',
                temperature=0.1,
                timeout=60.0
            )
        elif api_provider == 'ollama':
            return Ollama(
                model="gemma3:12b",
                temperature=0.1
            )
        else:
            return ChatOpenAI(
                api_key=self.openai_key,
                model='gpt-4o-mini',
                temperature=0.2,
                timeout=60.0
            )

    def _create_diffs_prompt_template(self) -> PromptTemplate:
        template = """
            Вы — эксперт по регистрации лекарственных средств.
            Перед вами два текста инструкций:
            - Эталонная инструкция: {expected_text}
            - Проверяемая инструкция: {actual_text}

            Ваша задача — выявить и перечислить только фактические
            изменения между ними. Выведите результат строго в следующем JSON-формате:

            [
              {{
                "section": "Название секции",
                "old": "Фрагмент из эталонного текста",
                "actual": "Фрагмент из проверяемого текста",
                "difference": "В чем конкретно разница"
              }}
            ]

            Никаких пояснений, комментариев, вступлений — только JSON.
        """
        return PromptTemplate(
            input_variables=["expected_text", "actual_text"],
            template=template
        )

    def _create_recs_prompt_template(self) -> PromptTemplate:
        template = """
            Вы — эксперт по регистрации лекарственных средств.  
            Вам даны два текста:  
            - Рекомендации по заполнению раздела инструкции: {recommendations_text}  
            - Текст проверяемого раздела инструкции: {actual_text}  
            Ваша задача — очень строго проверить соответствие инструкции рекомендациям и выдать результат в следующем формате:
               1. Определите степень соответствия:
                  - "complied" - полное соответствие
                  - "partial" - частичное соответствие (могут быть некоторые отклонения от рекомендаций)
                  - "not_complied" - полное несоответствие, отсутствие раздела в инструкции или раздел не заполнен (есть тольно заголовок раздела)
               2. Подготовьте комментарий:
                  - Для "complied": краткое подтверждение соответствия
                  - Для "partial" или "not_complied": конкретное описание недостающего/некорректного элемента c четким и 
                  подробным изложением отличий от рекомендаций
               
               3. Обязательные требования:
                   - Работайте как самый придирчивый эксперт. Старайтесь найти даже мелкие несоответствия.
                   - Будьте предельно конкретны, не используйте общих фраз, например "Указаны важные меры предосторожности при применении препарата,
                   однако не все аспекты безопасности подробно раскрыты" (отсутствует конкретика)
                   - Отмечайте явные пропуски обязательной информации.
                   - При обнаружении несоответствия, цитируйте пункт инструкции.
                   - Не пропускайте даже мелких отклонений от рекомендаций. Например: 
                   "Не указаны стандартные фразы о назначении риски ( «Линия разлома не предназначена для разделения таблетки»)"
                   - Сохраняйте нейтральный профессиональный тон комментариев
               
               4. Результат предоставьте в формате JSON с полями:
                   - "compliance" - степень соответствия ("complied"/"partial"/"not_complied")
                   - "comments" - пояснительный комментарий (тип данных - только строка!)

            Выведите результат строго в формате JSON-массива, без дополнительных пояснений или текста вне структуры JSON.
        """
        return PromptTemplate(
            input_variables=["recommendations_text", "actual_text"],
            template=template
        )

    def check_diffs(self, expected_text: str, actual_text: str) -> Any:
        logger.info("Отправляем запрос на детектирование изменений...")
        try:
            response = self.chain_diffs.invoke({
                "expected_text": expected_text,
                "actual_text": actual_text
            })
            return getattr(response, 'content', response)
        except Exception as e:
            logger.error(f"Ошибка при обработке diffs: {e}")
            return None

    def check_recommends(self,
                         recommendations_text: str,
                         actual_text: str) -> Dict[str, Any]:
        """Возвращает единый объект с полями compliance и comments для раздела."""
        try:
            response = self.chain_recs.invoke({
                "recommendations_text": recommendations_text,
                "actual_text": actual_text
            })
            raw = getattr(response, 'content', response)

            # Приведение к списку словарей
            parsed = []
            if isinstance(raw, str):
                try:
                    tmp = parse_json_markdown(raw)
                except Exception as err:
                    logger.error(f"Парсинг JSON провален: {err}")
                    tmp = []
                if isinstance(tmp, dict):
                    parsed = [tmp]
                elif isinstance(tmp, list):
                    parsed = tmp
            elif isinstance(raw, list):
                parsed = raw

            # Валидация и автокорректировка
            recommendations: Any = None
            for idx, rec in enumerate(parsed):
                if not isinstance(rec, dict):
                    logger.warning(f"Элемент #{idx} не dict, пропускаем: {rec!r}")
                    continue
                try:
                    rec_obj = Recommendation.parse_obj(rec)
                    recommendations = rec_obj.dict()
                    break  # берем первый валидный
                except ValidationError as ve:
                    logger.warning(f"Validation failed #{idx}: {ve}")
                    # попытка починки comments
                    rec_fixed = rec.copy()
                    rec_fixed['comments'] = json.dumps(rec_fixed.get('comments', ''), ensure_ascii=False)
                    try:
                        rec_obj = Recommendation.parse_obj(rec_fixed)
                        recommendations = rec_obj.dict()
                        break
                    except ValidationError:
                        logger.error(f"Не удалось починить элемент #{idx}.")

            if recommendations:
                return recommendations
            # если ни один элемент не прошёл — возвращаем дефолт
            logger.error(f"Нет валидных рекомендаций для раздела")
            return {'compliance': 'not_complied', 'comments': ''}

        except Exception as e:
            logger.error(f"Ошибка при обработке recommends: {e}")
            return {'compliance': 'not_complied', 'comments': ''}

    async def check_recommends_async(self,
                                     recommendations_text: str,
                                     actual_text: str) -> Dict[str, Any]:
        """
        Асинхронная версия проверки соответствия раздела рекомендациям.
        Возвращает объект с полями: {"compliance": "...", "comments": "..."}.
        """
        try:
            # Асинхронный вызов цепочки LangChain
            response = await self.chain_recs.ainvoke({
                "recommendations_text": recommendations_text,
                "actual_text": actual_text
            })
            raw = getattr(response, 'content', response)

            # Приведение к списку словарей (полная совместимость с sync-версией)
            parsed = []
            if isinstance(raw, str):
                try:
                    tmp = parse_json_markdown(raw)
                except Exception as err:
                    logger.error(f"Парсинг JSON провален: {err}")
                    tmp = []
                if isinstance(tmp, dict):
                    parsed = [tmp]
                elif isinstance(tmp, list):
                    parsed = tmp
            elif isinstance(raw, list):
                parsed = raw

            # Валидация и автокорректировка (как в sync-версии)
            recommendations: Any = None
            for idx, rec in enumerate(parsed):
                if not isinstance(rec, dict):
                    logger.warning(f"Элемент #{idx} не dict, пропускаем: {rec!r}")
                    continue
                try:
                    rec_obj = Recommendation.parse_obj(rec)
                    recommendations = rec_obj.dict()
                    break  # берём первый валидный
                except ValidationError as ve:
                    logger.warning(f"Validation failed #{idx}: {ve}")
                    # попытка починки comments
                    rec_fixed = rec.copy()
                    rec_fixed['comments'] = json.dumps(
                        rec_fixed.get('comments', ''),
                        ensure_ascii=False
                    )
                    try:
                        rec_obj = Recommendation.parse_obj(rec_fixed)
                        recommendations = rec_obj.dict()
                        break
                    except ValidationError:
                        logger.error(f"Не удалось починить элемент #{idx}.")

            if recommendations:
                return recommendations

            # если ни один элемент не прошёл — возвращаем дефолт
            logger.error("Нет валидных рекомендаций для раздела")
            return {'compliance': 'not_complied', 'comments': ''}

        except Exception as e:
            logger.error(f"Ошибка при обработке recommends (async): {e}")
            return {'compliance': 'not_complied', 'comments': ''}
