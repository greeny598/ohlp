
import re
from typing import Dict, List, Tuple, Optional

__all__ = ["split_ohlp_sections"]

HEADING_RE = re.compile(
    r"(?m)^\s*(?:[•\-\*]+)?\s*(?P<num>\d+(?:\.\d+)*)\s*[\.)]\s*(?P<title>[^\n\r]+?)\s*$"
)

WS_RE = re.compile(r"[\t\u00A0\u2007\u202F]+")
MULTINL_RE = re.compile(r"\n{3,}")


def _normalize_text(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = WS_RE.sub(" ", s)
    s = MULTINL_RE.sub("\n\n", s)
    return s


def _normalize_key(num: str, title: str) -> str:
    t = (title or "").strip().rstrip(" .\t")
    return f"{num} {t}"


def _collect_candidates(text: str) -> List[Tuple[int, int, str, str]]:
    out: List[Tuple[int, int, str, str]] = []
    for m in HEADING_RE.finditer(text):
        num = m.group("num")
        title = m.group("title")
        start, end = m.span()
        out.append((start, end, num, title))
    out.sort(key=lambda x: x[0])
    return out


def split_ohlp_sections(text: str, sections: Optional[List[str]] = None) -> Dict[str, str]:
    """
    Return a dict mapping section headings *as they appear in the document*
    to their bodies. Only headings that actually exist in the text are keys.

    Args:
        text: raw extracted document text
        sections: optional list of reference section titles; used only as hints
                  by the caller, never injected into the output.
    """
    raw = _normalize_text(text)
    cands = _collect_candidates(raw)
    result: Dict[str, str] = {}
    for idx, (start, end, num, title) in enumerate(cands):
        next_start = cands[idx + 1][0] if idx + 1 < len(cands) else len(raw)
        body = raw[end:next_start].strip("\n ")
        key = _normalize_key(num, title)
        result[key] = body
    return result
