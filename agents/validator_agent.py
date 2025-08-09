from typing import Dict, List
import logging
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

class ValidatorAgent:
    """Agent responsible for validating the results of a segmentation pass.
    It examines the extracted section texts to detect missing data, empty
    sections and duplicated content across different sections. The validator
    returns a report indicating the status of each section.
    """
    def __init__(self, expected_sections: List[str]) -> None:
        self.expected_sections = expected_sections

    def _is_empty(self, text: str) -> bool:
        """Determine whether a section's text should be considered empty.
        Blank strings or those containing only whitespace are treated as empty."""
        return not text or not text.strip()

    def _find_duplicates(self, sections: Dict[str, str]) -> Dict[str, List[str]]:
        """Detect duplicate or near‑duplicate section contents.
        Returns a mapping from a canonical section to a list of keys that share
        the same (or very similar) content."""
        duplicates: Dict[str, List[str]] = {}
        texts_seen: Dict[str, str] = {}
        for key, text in sections.items():
            if self._is_empty(text):
                continue
            # Use a simple canonical representation: collapse whitespace
            canonical = " ".join(text.split()).lower()
            found = False
            for canon, orig_key in texts_seen.items():
                # approximate matching using rapidfuzz ratio
                if fuzz.ratio(canonical, canon) > 95:
                    # treat as duplicate
                    duplicates.setdefault(orig_key, []).append(key)
                    found = True
                    break
            if not found:
                texts_seen[canonical] = key
        return duplicates

    def validate(self, segmented: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """Validate each extracted section, checking for missing or empty
        content and reporting duplicates. Each section result contains a
        status and an optional comment explaining the issue.

        Parameters
        ----------
        segmented : Dict[str, str]
            Mapping from section names to extracted content.

        Returns
        -------
        Dict[str, Dict[str, str]]
            Mapping from section name to a dictionary with keys:
                - "status": "ok", "empty", or "duplicate"
                - "comment": explanation if status is not "ok""" 
        result: Dict[str, Dict[str, str]] = {}
        # identify duplicates among non-empty sections
        duplicates = self._find_duplicates(segmented)

        for key in self.expected_sections:
            text = segmented.get(key, "")
            if self._is_empty(text):
                result[key] = {
                    "status": "empty",
                    "comment": "Раздел пуст или отсутствует"
                }
                continue
            # check if this section duplicates another one
            dup_sources = [source for source, dups in duplicates.items() if key in dups]
            if dup_sources:
                orig = dup_sources[0]
                result[key] = {
                    "status": "duplicate",
                    "comment": f"Содержимое дублирует раздел '{orig}'"
                }
                continue
            # otherwise mark as ok
            result[key] = {"status": "ok", "comment": ""}
        return result
