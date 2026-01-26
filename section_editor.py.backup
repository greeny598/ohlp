import re
from typing import List, Dict, Any

import gradio as gr
import pandas as pd

from utils.document_loader import DocumentLoader
from ohlp_parser import HEADING_RE


# ============================================================
# Извлечение строк
# ============================================================

def extract_lines(file) -> List[str]:
    loader = DocumentLoader(file.name)
    text = loader.load()
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


# ============================================================
# Авто-границы (подсказка): HEADING_RE + мягкое правило
# ============================================================

_RELAXED_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>\S.*)$")


def _is_probably_heading(num: str, title: str) -> bool:
    if "." in num:
        return True
    t = (title or "").strip()
    return len(t) >= 5 and (t.upper() == t)


def predict_boundaries_from_lines(lines: List[str]) -> List[int]:
    out: List[int] = []
    for i, line in enumerate(lines):
        if not line:
            continue
        if HEADING_RE.match(line):
            out.append(i)
            continue
        m = _RELAXED_RE.match(line)
        if m and _is_probably_heading(m.group("num"), m.group("title")):
            out.append(i)
    return sorted(set(out))


# ============================================================
# Утилиты state
# ============================================================

def coerce_int_list(seq) -> List[int]:
    if not seq:
        return []
    out: List[int] = []
    for x in seq:
        try:
            out.append(int(x))
        except Exception:
            continue
    return out


# ============================================================
# Жёсткая разбивка по индексам строк
# ============================================================

def split_by_line_boundaries(lines: List[str], boundaries: List[int]) -> List[Dict[str, Any]]:
    if not lines:
        return []

    b = sorted(set(coerce_int_list(boundaries)))
    if not b:
        b = [0]
    if b[0] != 0:
        b = [0] + b

    sections: List[Dict[str, Any]] = []
    for k, start in enumerate(b):
        end = b[k + 1] if k + 1 < len(b) else len(lines)
        header = lines[start].strip()
        body = "\n".join(lines[start + 1:end]).strip()
        sections.append({"Заголовок": header, "Текст": body})
    return sections


def make_preview(text: str, n_lines: int) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    head = parts[: int(n_lines)]
    preview = "\n".join(head).strip()
    if len(parts) > int(n_lines):
        preview += "\n…"
    return preview


def sections_to_preview_df(sections: List[Dict[str, Any]], preview_n: int) -> pd.DataFrame:
    if not sections:
        return pd.DataFrame({"Заголовок": [], "Содержание": []})
    rows = []
    for s in sections:
        rows.append(
            {
                "Заголовок": s.get("Заголовок", ""),
                "Содержание": make_preview(s.get("Текст", ""), preview_n),
            }
        )
    return pd.DataFrame(rows)


# ============================================================
# Левая таблица: подсветка строк
# ============================================================

def build_lines_df(lines: List[str], manual: List[int], predicted: List[int]) -> pd.DataFrame:
    mset = set(coerce_int_list(manual))
    pset = set(coerce_int_list(predicted))

    rows = []
    for i, line in enumerate(lines):
        bg = ""
        if i in mset:
            bg = "background-color:#d4f7d4;"   # зелёный
        elif i in pset:
            bg = "background-color:#ffe0b2;"   # оранжевый

        rows.append(
            {
                "№": i,
                "Текст": f"<div style='{bg} padding:4px; white-space:pre-wrap;'>{line}</div>",
            }
        )
    return pd.DataFrame(rows)


# ============================================================
# UI
# ============================================================

def build_demo() -> gr.Blocks:
    with gr.Blocks() as demo:
        # Минимальная легенда
        gr.Markdown("🟢 ручная разбивка · 🟧 авто-разбивка")

        # State
        lines_state = gr.State([])
        predicted_state = gr.State([])
        manual_state = gr.State([])
        sections_state = gr.State([])

        file_input = gr.File(label="Загрузить документ (PDF/DOCX)", file_count="single")

        with gr.Row():
            preview_lines = gr.Slider(
                minimum=1,
                maximum=30,
                step=1,
                value=6,
                label="Превью: первые N строк",
                scale=1,
            )

        with gr.Row():
            with gr.Column(scale=1):
                lines_table = gr.Dataframe(
                    headers=["№", "Текст"],
                    datatype=["number", "html"],
                    interactive=False,
                    wrap=True,
                    max_height=560,
                )
            with gr.Column(scale=1):
                sections_table = gr.Dataframe(
                    headers=["Заголовок", "Превью"],
                    datatype=["markdown", "markdown"],
                    interactive=False,
                    wrap=True,
                    max_height=560,
                )

        split_btn = gr.Button("Разбить на разделы", variant="primary")

        selected_header = gr.Markdown("")
        selected_full_text = gr.Textbox(label="Полный текст", value="", lines=18)

        # -----------------------------
        # Upload
        # -----------------------------
        def on_file_upload(file, preview_n):
            if file is None:
                return [], [], [], [], None, None, "", ""

            lines = extract_lines(file)
            predicted = predict_boundaries_from_lines(lines)
            manual: List[int] = []

            df_lines = build_lines_df(lines, manual, predicted)

            combined = sorted(set(predicted))
            sections = split_by_line_boundaries(lines, combined)
            df_sections = sections_to_preview_df(sections, int(preview_n))

            return (
                lines,
                predicted,
                manual,
                sections,
                df_lines,
                df_sections,
                "",
                "",
            )

        file_input.upload(
            on_file_upload,
            inputs=[file_input, preview_lines],
            outputs=[
                lines_state,
                predicted_state,
                manual_state,
                sections_state,
                lines_table,
                sections_table,
                selected_header,
                selected_full_text,
            ],
        )

        # -----------------------------
        # Click on left table
        # -----------------------------
        def on_line_select(manual, predicted, lines, evt: gr.SelectData):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            if not hasattr(evt, "index") or evt.index is None:
                df = build_lines_df(lines, manual, predicted)
                return manual, predicted, df

            row = evt.index[0] if isinstance(evt.index, (tuple, list)) else int(evt.index)

            mset = set(manual)
            pset = set(predicted)

            if row in mset:
                mset.remove(row)
            elif row in pset:
                pset.remove(row)
            else:
                mset.add(row)

            manual_new = sorted(mset)
            predicted_new = sorted(pset)

            df = build_lines_df(lines, manual_new, predicted_new)
            return manual_new, predicted_new, df

        lines_table.select(
            on_line_select,
            inputs=[manual_state, predicted_state, lines_state],
            outputs=[manual_state, predicted_state, lines_table],
        )

        # -----------------------------
        # Split by boundaries
        # -----------------------------
        def on_split(manual, predicted, lines, preview_n):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            combined = sorted(set(manual) | set(predicted))
            sections = split_by_line_boundaries(lines, combined)
            df_sections = sections_to_preview_df(sections, int(preview_n))

            return df_sections, predicted, sections, "", ""

        split_btn.click(
            on_split,
            inputs=[manual_state, predicted_state, lines_state, preview_lines],
            outputs=[sections_table, predicted_state, sections_state, selected_header, selected_full_text],
        )

        # -----------------------------
        # Click on right table -> expand
        # -----------------------------
        def on_section_select(sections, evt: gr.SelectData):
            sections = sections or []
            if not hasattr(evt, "index") or evt.index is None:
                return "", ""
            row = evt.index[0] if isinstance(evt.index, (tuple, list)) else int(evt.index)
            if row < 0 or row >= len(sections):
                return "", ""
            sec = sections[row]
            return f"**{sec.get('Заголовок', '')}**", sec.get("Текст", "")

        sections_table.select(
            on_section_select,
            inputs=[sections_state],
            outputs=[selected_header, selected_full_text],
        )

        # -----------------------------
        # Preview slider: live update
        # -----------------------------
        def on_preview_change(sections, preview_n):
            sections = sections or []
            return sections_to_preview_df(sections, int(preview_n))

        preview_lines.change(
            on_preview_change,
            inputs=[sections_state, preview_lines],
            outputs=[sections_table],
        )

    return demo


if __name__ == "__main__":
    app = build_demo()
    app.launch()
