from typing import List, Dict, Tuple
import logging

from agents.segmenter_agent import SegmenterAgent
from agents.validator_agent import ValidatorAgent

logger = logging.getLogger(__name__)

class SegmentationManager:
    """
    Coordinates the segmentation and validation process. It will
    iteratively attempt to segment the document until the validator
    reports that all sections are non-empty and unique, or until a
    maximum number of attempts is reached. The threshold for fuzzy
    matching can be adjusted between iterations to broaden or narrow
    the search.
    """
    def __init__(
        self,
        max_iterations: int = 3,
        initial_threshold: int = 70,
        threshold_step: int = -5,
        mode: str = "ohlp",
        use_llm_fallback: bool = False,
        llm_provider: str = "yandex",
        llm_first: bool = False,
    ) -> None:
        """
        Parameters
        ----------
        max_iterations : int
            Maximum number of segmentation attempts before giving up.
        initial_threshold : int
            Starting value for fuzzy matching threshold.
        threshold_step : int
            Adjustment applied to the threshold after each unsuccessful
            iteration. A negative step widens matching.
        mode : str
            Strategy used by the SegmenterAgent ("ohlp" or "leaflet").
        """
        self.max_iterations = max_iterations
        self.initial_threshold = initial_threshold
        self.threshold_step = threshold_step
        self.mode = mode
        self.use_llm_fallback = use_llm_fallback
        self.llm_provider = llm_provider
        self.llm_first = llm_first

    def segment_and_validate(
        self, text: str, sections: List[str]
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        """
        Perform segmentation and validation in a loop. Returns the last
        segmentation and validation results, even if they contain errors.

        Parameters
        ----------
        text : str
            The full document text.
        sections : List[str]
            Ordered list of expected section headings.

        Returns
        -------
        Tuple[Dict[str, str], Dict[str, Dict[str, str]]]
            A tuple containing the final segmented dictionary and the
            validation report.
        """
        threshold = self.initial_threshold
        last_segmentation: Dict[str, str] = {sec: "" for sec in sections}
        last_validation: Dict[str, Dict[str, str]] = {
            sec: {"status": "empty", "comment": "Не обрабатывалось"}
            for sec in sections
        }

        # If we should use LLM first, attempt segmentation by headings before any heuristic attempts.
        # This allows the model to determine section boundaries when heuristic parsing tends to overfill
        # or incorrectly assign text to sections. If the LLM returns valid segmentation (all
        # sections marked 'ok' by the validator), the function returns early. Otherwise,
        # the heuristic parser will be used in the standard iterative manner below.
        if self.use_llm_fallback and self.llm_first:
            try:
                # Attempt relative import from langchain_utils package first
                from langchain_utils.llm_segmenter_agent import LLMSegmenterAgent  # type: ignore
            except ImportError:
                try:
                    from llm_segmenter_agent import LLMSegmenterAgent  # type: ignore
                except ImportError:
                    LLMSegmenterAgent = None  # type: ignore
            if LLMSegmenterAgent:
                llm_agent = LLMSegmenterAgent(api_provider=self.llm_provider)
                try:
                    segmentation_candidate = llm_agent.segment_by_headings(
                        text,
                        sections,
                        threshold=self.initial_threshold,
                        mode=self.mode,
                    )
                except Exception as llm_exc:
                    logger.error(f"LLM first-pass segmentation failed: {llm_exc}")
                    segmentation_candidate = {sec: "" for sec in sections}
                val_agent_first = ValidatorAgent(expected_sections=sections)
                validation_candidate = val_agent_first.validate(segmentation_candidate)
                # If all sections are 'ok', return immediately
                if all(item["status"] == "ok" for item in validation_candidate.values()):
                    return segmentation_candidate, validation_candidate
                # Otherwise, store and proceed to heuristic attempts
                last_segmentation = segmentation_candidate
                last_validation = validation_candidate

        for attempt in range(self.max_iterations):
            logger.info(
                f"Segmentation attempt {attempt + 1} with threshold {threshold}"
            )
            seg_agent = SegmenterAgent(threshold=threshold, mode=self.mode)
            segmentation = seg_agent.segment(text, sections)

            val_agent = ValidatorAgent(expected_sections=sections)
            validation = val_agent.validate(segmentation)

            # Determine if segmentation is satisfactory:
            # all sections must be "ok"
            if all(item["status"] == "ok" for item in validation.values()):
                return segmentation, validation

            # Save current results and adjust threshold for next iteration
            last_segmentation = segmentation
            last_validation = validation
            threshold += self.threshold_step

        # If heuristic segmentation failed on all attempts and LLM fallback is enabled and was not used first, try LLM
        if self.use_llm_fallback and not self.llm_first:
            logger.warning(
                "Maximum segmentation attempts reached without full success; switching to LLM fallback"
            )
            try:
                # Attempt relative import from langchain_utils package first
                from langchain_utils.llm_segmenter_agent import LLMSegmenterAgent  # type: ignore
            except ImportError:
                try:
                    from llm_segmenter_agent import LLMSegmenterAgent  # type: ignore
                except ImportError:
                    LLMSegmenterAgent = None  # type: ignore
            if LLMSegmenterAgent:
                llm_agent = LLMSegmenterAgent(api_provider=self.llm_provider)
                try:
                    # Use LLM to identify headings and then apply heuristic segmentation.
                    segmentation = llm_agent.segment_by_headings(
                        text,
                        sections,
                        threshold=self.initial_threshold,
                        mode=self.mode,
                    )
                except Exception as llm_exc:
                    logger.error(f"LLM fallback segmentation failed: {llm_exc}")
                    segmentation = {sec: "" for sec in sections}
                # validate the LLM segmentation using a new ValidatorAgent
                val_agent = ValidatorAgent(expected_sections=sections)
                validation = val_agent.validate(segmentation)
                return segmentation, validation
            logger.error(
                "LLM fallback not available; returning last heuristic results"
            )
            return last_segmentation, last_validation

        # Return the last attempt if none were fully successful and LLM fallback is disabled
        logger.warning(
            "Maximum segmentation attempts reached without full success"
        )
        return last_segmentation, last_validation
