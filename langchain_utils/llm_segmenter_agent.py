import json
import logging
from typing import List, Dict

from langchain.prompts import PromptTemplate
from langchain_core.runnables import RunnableSequence
from langchain_core.utils.json import parse_json_markdown
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama, YandexGPT

from config_reader import config

logger = logging.getLogger(__name__)


class LLMSegmenterAgent:
    """
    Agent that leverages a language model to split a full instruction text
    into sections based on a list of expected section headings. Unlike the
    heuristic SegmenterAgent, this agent formulates a prompt and relies on
    the model to identify and return the content of each section.
    """

    def __init__(self, api_provider: str = "yandex") -> None:
        self.api_provider = api_provider
        self.llm = self._set_api_provider(api_provider)
        # Prompt for full‑content segmentation (legacy behaviour)
        self.prompt_template = self._create_prompt_template()
        self.chain = RunnableSequence(self.prompt_template | self.llm)
        # Prompt for heading extraction. This template instructs the model to
        # return only the actual heading lines from the document that
        # correspond to each expected section. It avoids including any
        # section contents in the response, which helps prevent
        # duplication when subheadings are present under a parent heading.
        self.headings_prompt_template = self._create_headings_prompt_template()
        self.headings_chain = RunnableSequence(self.headings_prompt_template | self.llm)

    def _set_api_provider(self, api_provider: str):
        """
        Instantiate an LLM from the specified provider. Mirrors the logic used
        in SectionChecker to select between YandexGPT, DeepSeek, Ollama and OpenAI.
        """
        # retrieve secrets from config
        yandex_key = config.yandexgpt_KEY.get_secret_value()
        folder_id = config.yandex_cloud_folder_id.get_secret_value()
        deepseek_key = config.deepseek_KEY.get_secret_value()
        openai_key = config.openai_KEY.get_secret_value()

        if api_provider == "yandex":
            return YandexGPT(
                api_key=yandex_key,
                folder_id=folder_id,
                modelUri=f"gpt://{folder_id}/yandexgpt",
                temperature=0.2,
                maxTokens=2000,
            )
        elif api_provider == "deepseek":
            return ChatOpenAI(
                api_key=deepseek_key,
                model="deepseek-chat",
                base_url="https://api.deepseek.com",
                temperature=0.2,
                timeout=60.0,
            )
        elif api_provider == "ollama":
            return Ollama(model="gemma3:12b", temperature=0.2)
        else:
            # Default to openai GPT model
            return ChatOpenAI(
                api_key=openai_key,
                model="gpt-4o-mini",
                temperature=0.2,
                timeout=60.0,
            )

    def _create_prompt_template(self) -> PromptTemplate:
        """
        Construct a prompt instructing the language model to map each
        expected section to its extracted content. The model is asked to
        produce a JSON object with section names as keys.
        """
        template = """
            Вы — эксперт по регистрации лекарственных средств. Вам дан полный текст инструкции:
            {full_text}
            Также предоставлен упорядоченный список ожидаемых разделов: {sections_list}.
            Для каждого раздела из списка нужно определить его содержимое в инструкции.

            Требования:
            - Сравните список ожидаемых разделов с фактическим текстом инструкции.
            - Верните результат **строго** в виде JSON-словаря, где ключ — точное имя раздела,
              а значение — полный текст соответствующего раздела **без заголовка**.
            - Если раздел полностью отсутствует в инструкции, укажите пустую строку "".
            - Не добавляйте пояснений, комментариев или форматирования Markdown — только JSON.
        """
        return PromptTemplate(
            input_variables=["full_text", "sections_list"],
            template=template,
        )

    def _create_headings_prompt_template(self) -> PromptTemplate:
        """
        Construct a prompt that asks the language model to map each expected
        section to the exact heading line as it appears in the document. The
        model must not include any of the section contents – only the heading
        itself. If a heading is missing, the value should be an empty string.

        The template returns a JSON object where each key is the expected
        section name (from the provided list) and each value is the
        corresponding heading line copied verbatim from the document.
        """
        template = """
            Вы — эксперт по регистрации лекарственных средств. Вам дан полный текст инструкции:
            {full_text}
            Также предоставлен упорядоченный список ожидаемых разделов: {sections_list}.
            Для каждого раздела из списка определите строку заголовка в инструкции.

            Требования:
            - Заголовок должен точно соответствовать строке в исходном тексте (включая номер раздела, точки, тире и другие символы).
            - Не возвращайте текст разделов, только строку заголовка. Если заголовок отсутствует, верните пустую строку "".
            - Итоговый ответ должен быть в виде JSON‑словаря, где ключ — ожидаемое название раздела,
              а значение — фактический заголовок из текста или пустая строка.
            - Никаких пояснений, комментариев или форматирования Markdown — только JSON.
        """
        return PromptTemplate(
            input_variables=["full_text", "sections_list"],
            template=template,
        )

    def segment(self, text: str, sections: List[str]) -> Dict[str, str]:
        """
        Call the language model to perform segmentation. In case of errors or
        invalid output, a fallback dictionary with empty strings is returned.

        Parameters
        ----------
        text : str
            The full instruction text.
        sections : List[str]
            Ordered list of expected section headings.

        Returns
        -------
        Dict[str, str]
            Mapping from section names to extracted content.
        """
        # Serialize the section list to a JSON-formatted string for the prompt
        sections_str = json.dumps(sections, ensure_ascii=False)
        try:
            response = self.chain.invoke({"full_text": text, "sections_list": sections_str})
        except Exception as e:
            logger.error(f"LLM segmentation failed: {e}")
            return {sec: "" for sec in sections}

        raw = getattr(response, "content", response)
        # Attempt to parse the returned JSON using LangChain's helper
        try:
            data = parse_json_markdown(raw)
            if isinstance(data, dict):
                # Ensure that all expected sections are present
                return {sec: data.get(sec, "") for sec in sections}
        except Exception as parse_err:
            logger.error(f"Parsing LLM segmentation response failed: {parse_err}")

        # Fallback: return empty dictionary if parsing failed
        return {sec: "" for sec in sections}

    def find_headings(self, text: str, sections: List[str]) -> Dict[str, str]:
        """
        Use the language model to find the exact heading lines in the document
        corresponding to each expected section. The method returns a mapping
        from the expected section names to the actual headings as they
        appear in the document. If a heading is not found, an empty string
        is returned for that section.

        Parameters
        ----------
        text : str
            The full instruction text.
        sections : List[str]
            Ordered list of expected section headings.

        Returns
        -------
        Dict[str, str]
            Mapping from expected section names to the actual heading lines.
        """
        sections_str = json.dumps(sections, ensure_ascii=False)
        try:
            response = self.headings_chain.invoke({
                "full_text": text,
                "sections_list": sections_str,
            })
        except Exception as e:
            logger.error(f"LLM heading extraction failed: {e}")
            return {sec: "" for sec in sections}
        raw = getattr(response, "content", response)
        try:
            data = parse_json_markdown(raw)
            if isinstance(data, dict):
                # ensure all expected sections have a key
                return {sec: str(data.get(sec, "")) for sec in sections}
        except Exception as parse_err:
            logger.error(f"Parsing LLM headings response failed: {parse_err}")
        return {sec: "" for sec in sections}

    def segment_by_headings(
        self,
        text: str,
        sections: List[str],
        threshold: int = 70,
        mode: str = "ohlp",
    ) -> Dict[str, str]:
        """
        Perform segmentation by first using the language model to locate the
        actual heading lines for the expected sections, then applying the
        heuristic SegmenterAgent on those headings. This approach avoids
        duplicating subheading content under parent sections and prevents
        the LLM from re‑generating the document text.

        Parameters
        ----------
        text : str
            The full instruction text.
        sections : List[str]
            Ordered list of expected section headings.
        threshold : int
            Similarity threshold for fuzzy matching used by the heuristic
            SegmenterAgent.
        mode : str
            Segmentation mode to pass to SegmenterAgent ("ohlp" or "leaflet").

        Returns
        -------
        Dict[str, str]
            Mapping from expected section names to extracted contents.
        """
        # Step 1: use LLM to find actual headings in the document
        heading_map = self.find_headings(text, sections)
        # Build a list of actual headings in the same order as expected
        # If the LLM couldn't find a heading, fall back to the expected name
        actual_headings: List[str] = [heading_map.get(sec, "") or sec for sec in sections]
        try:
            from agents.segmenter_agent import SegmenterAgent  # type: ignore
        except ImportError:
            # If import fails (e.g. package structure), return empty sections
            logger.error("Could not import SegmenterAgent for LLM segmentation by headings")
            return {sec: "" for sec in sections}
        # Use heuristic segmentation on the actual headings
        seg_agent = SegmenterAgent(threshold=threshold, mode=mode)
        segmentation_actual = seg_agent.segment(text, actual_headings)
        # Remap results back to the expected section names
        result: Dict[str, str] = {}
        for idx, sec in enumerate(sections):
            actual = actual_headings[idx]
            result[sec] = segmentation_actual.get(actual, "")
        return result
