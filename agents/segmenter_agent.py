import re
from typing import List, Dict, Tuple, Optional
from rapidfuzz import process, fuzz

class SegmenterAgent:
    """
    Agent responsible for determining section boundaries within a large text
    document. It wraps the existing procedural functions into a simple class
    interface and preserves compatibility with the original API. The agent can
    perform segmentation for different document types, such as OHLP
    instructions and leaflets.

    Parameters
    ----------
    threshold : int, optional
        Similarity threshold for fuzzy matching when attempting to align
        section headings. Defaults to 70.
    mode : str, optional
        Determines which segmentation strategy to use. Supported values are
        "ohlp" for standard OHLP instructions and "leaflet" for leaflet
        instructions. Defaults to "ohlp".
    """
    def __init__(self, threshold: int = 70, mode: str = "ohlp") -> None:
        self.threshold = threshold
        self.mode = mode

    @staticmethod
    def _normalize_heading(text: str) -> str:
        """
        Lower-cases the heading, strips punctuation and extra whitespace.
        """
        cleaned = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", cleaned, flags=re.UNICODE).strip().lower()

    def _compute_section_positions(
        self, text: str, sections_raw: List[str], threshold: Optional[int] = None
    ) -> Tuple[List[int], List[int]]:
        """
        Compute the byte offsets for the start and end of each section within
        the provided text. This method mirrors the original
        `compute_section_positions` function but is encapsulated within the
        class. The optional `threshold` overrides the instance-wide setting.
        """
        thr = threshold if threshold is not None else self.threshold
        lines = text.splitlines(keepends=True)
        # plain lines without trailing newline for matching
        plain = [ln.rstrip("\n") for ln in lines]
        offsets, cur = [], 0
        for ln in lines:
            offsets.append(cur)
            cur += len(ln)

        # find candidate header lines that look like numbered headings
        header_idxs = [
            i for i, ln in enumerate(plain)
            if re.match(r"^\s*\d+(?:\.\d+)*[.)]\s+", ln)
        ]

        plain_norm = [self._normalize_heading(ln) for ln in plain]
        sections_norm = [self._normalize_heading(sec) for sec in sections_raw]

        starts: List[int] = []
        ends: List[int] = []
        for sec_norm in sections_norm:
            idx: Optional[int] = None

            # Strategy 1: substring search – collect *all* candidates and pick the second if possible
            candidates = [i for i in header_idxs if sec_norm in plain_norm[i]]
            if len(candidates) >= 2:
                idx = candidates[1]
            elif len(candidates) == 1:
                idx = candidates[0]

            # Strategy 2: startswith search – similar logic
            if idx is None:
                candidates = [i for i in header_idxs if plain_norm[i].startswith(sec_norm)]
                if len(candidates) >= 2:
                    idx = candidates[1]
                elif len(candidates) == 1:
                    idx = candidates[0]

            # Strategy 3: fuzzy match when needed
            if idx is None and header_idxs:
                choices = [plain_norm[i] for i in header_idxs]
                best = process.extractOne(sec_norm, choices, scorer=fuzz.partial_ratio)
                if best and best[1] >= thr:
                    rel = best[2]
                    idx = header_idxs[rel]

            if idx is not None:
                starts.append(offsets[idx])
                ends.append(offsets[idx] + len(lines[idx]))
            else:
                # mark missing section with -1
                starts.append(-1)
                ends.append(-1)

        return starts, ends

    def _split_ohlp_sections(
        self, text: str, sections: List[str], threshold: Optional[int] = None
    ) -> Dict[str, str]:
        """
        Split a large instruction text into sections based on a list of
        headings. This replicates the behaviour of the original
        `split_ohlp_sections` function.

        Returns a dictionary mapping each section name to its corresponding text
        (with duplicates removed and heading lines stripped).
        """
        thr = threshold if threshold is not None else self.threshold
        starts, ends = self._compute_section_positions(text, sections, thr)
        result: Dict[str, str] = {}

        for i, sec in enumerate(sections):
            start = ends[i] if ends[i] >= 0 else 0
            next_start = (
                starts[i + 1]
                if i + 1 < len(sections) and starts[i + 1] >= 0
                else len(text)
            )

            raw = text[start:next_start].strip()
            # split lines and remove empty ones
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

            def normalize_cmp(s: str) -> str:
                # only letters and digits, lower-cased
                return re.sub(r"\W+", "", s.lower())

            sec_key = normalize_cmp(sec)

            # remove header repeats at the beginning
            while lines and normalize_cmp(lines[0]) == sec_key:
                lines.pop(0)

            # remove consecutive duplicates
            deduped: List[str] = []
            prev: Optional[str] = None
            for ln in lines:
                if ln != prev:
                    deduped.append(ln)
                prev = ln

            result[sec] = "\n".join(deduped)

        return result

    def _split_leaflet_sections(
        self, text: str, sections: List[str], threshold: Optional[int] = None
    ) -> Dict[str, str]:
        """
        Split a leaflet text into sections by mapping numeric prefixes to the
        expected section headings. This method mirrors the original
        `split_leaflet_sections` function and supports fuzzy matching on the
        section titles.
        """
        thr = threshold if threshold is not None else self.threshold
        # Replace non-breaking spaces with regular spaces, then split into lines
        lines = text.replace("\xa0", " ").splitlines()

        # Find the start of the Table of Contents (Содержание)
        toc_start = next((i for i, ln in enumerate(lines) if "Содержание" in ln), None)
        if toc_start is None:
            raise ValueError('Не найден блок "Содержание"')

        # Find the first numbered line after the TOC, marking the start of content
        toc_end = next(
            (j for j in range(toc_start + 1, len(lines))
             if re.match(r"^\s*\d+\.\s*", lines[j])),
            None
        )
        if toc_end is None:
            raise ValueError('Не удалось определить начало содержательной части')

        main_lines = lines[toc_end:]

        # Build mappings from numbers to section names and normalized headings
        num_to_section: Dict[str, str] = {}
        placeholder_norms: Dict[str, str] = {}
        for sec in sections:
            m = re.match(r"^\s*(\d+)\.\s*(.*)$", sec)
            if m:
                num, title = m.groups()
                num_to_section[num] = sec
                placeholder_norms[num] = self._normalize_heading(title)

        heading_re = re.compile(r"^(?P<num>\d+)\.\s*(?P<title>.+?)(?=[\-–—]|$)")

        headings: List[Tuple[int, str]] = []
        for idx, ln in enumerate(main_lines):
            stripped = ln.strip()
            m = heading_re.match(stripped)
            if not m:
                continue
            num = m.group("num")
            section = num_to_section.get(num)
            if section is None:
                # Fallback: fuzzy match the title to the placeholder norms
                raw_title = m.group("title").strip()
                norm_raw = self._normalize_heading(raw_title)
                best = process.extractOne(
                    norm_raw,
                    list(placeholder_norms.values()),
                    scorer=fuzz.partial_ratio
                )
                if best and best[1] >= thr:
                    # find corresponding section by inverted lookup
                    for key, norm in placeholder_norms.items():
                        if norm == best[0]:
                            section = num_to_section[key]
                            break
            if section:
                headings.append((idx, section))

        if not headings:
            raise ValueError("Не найдены заголовки в содержательной части")

        # Add sentinel at the end of the document
        indices = [idx for idx, _ in headings] + [len(main_lines)]

        result: Dict[str, str] = {}
        for i, (idx, section) in enumerate(headings):
            start = indices[i] + 1
            end = indices[i + 1]
            block = "\n".join(main_lines[start:end]).strip()
            result[section] = block

        return result

    def segment(
        self, text: str, sections: List[str],
        threshold: Optional[int] = None
    ) -> Dict[str, str]:
        """
        Public method to perform segmentation on the provided text using
        either the OHLP or leaflet strategy based on the agent's mode.

        Parameters
        ----------
        text : str
            The entire instruction or leaflet text.
        sections : List[str]
            Ordered list of expected section headings.
        threshold : int, optional
            Overrides the agent's default threshold for fuzzy matching.

        Returns
        -------
        Dict[str, str]
            Mapping of each expected section heading to the extracted text for
            that section. If a section cannot be located, its value will be an
            empty string.
        """
        thr = threshold if threshold is not None else self.threshold
        try:
            if self.mode == "leaflet":
                return self._split_leaflet_sections(text, sections, thr)
            # default to OHLP mode
            return self._split_ohlp_sections(text, sections, thr)
        except Exception:
            # On error, return empty sections to allow the validator to mark failures
            return {sec: "" for sec in sections}
