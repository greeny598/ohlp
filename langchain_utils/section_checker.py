import json
import logging
import re
from typing import List, Dict

from langchain.prompts import PromptTemplate
from langchain_core.runnables import RunnableSequence
from langchain_core.utils import json
from langchain_openai import ChatOpenAI
from config_reader import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SectionChecker:
    def __init__(self, api_provider: str = 'openai'):
        self.api_key = config.LLM_KEY.get_secret_value()
        self.llm = self._set_api_provider(api_provider)
        self.prompt = self._create_prompt_template()
        self.chain = RunnableSequence(self.prompt | self.llm)

    def _set_api_provider(self, api_provider: str) -> ChatOpenAI:
        if api_provider == 'deepseek':
            return ChatOpenAI(
                api_key=self.api_key,
                model='deepseek-chat',
                base_url="https://api.deepseek.com",
                temperature=0.2,
                timeout=60.0
            )
        return ChatOpenAI(
            api_key=self.api_key,
            model='gpt-4o-mini',
            temperature=0.2,
            timeout=60.0
        )

    def _create_prompt_template(self) -> PromptTemplate:
        template = """
Вы — эксперт по регистрации лекарственных средств.
Перед вами два текста инструкций:
- Эталонная инструкция: {expected_text}
- Проверяемая инструкция: {actual_text}

Ваша задача — выявить и перечислить только фактические изменения между ними. Выведите результат строго в следующем JSON-формате:

[
  {{
    "section": "Название секции",
    "old": "Фрагмент из эталонного текста",
    "actual": "Фрагмент из проверяемого текста",
    "difference": "В чём конкретно разница"
  }}
]

Никаких пояснений, комментариев, вступлений — только JSON.
        """
        return PromptTemplate(
            input_variables=["expected_text", "actual_text"],
            template=template
        )

    def check_sections(self, expected_text: str, actual_text: str) -> List[Dict]:
        logger.info("Отправляем запрос на детектирование изменений...")
        try:
            response = self.chain.invoke({
                "expected_text": expected_text,
                "actual_text": actual_text
            })
            raw = getattr(response, 'content', response)
            return raw
        except Exception as e:
            logger.error(f"Ошибка при обработке результата LLM: {e}")
            return []

    def clean_json_from_md(self, json_with_markdown):
        clean = json.parse_json_markdown(json_with_markdown)
        return clean
