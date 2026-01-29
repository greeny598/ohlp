import re
from typing import List, Dict, Any, Tuple, Optional

import gradio as gr
import pandas as pd

from utils.document_loader import DocumentLoader
from ohlp_parser import HEADING_RE


# =========================
# Параметры UI
# =========================
WINDOW_RADIUS = 90
WINDOW_SIZE = WINDOW_RADIUS * 2 + 1


# =========================
# Извлечение markdown и строк
# =========================
def extract_markdown_and_lines(file) -> Tuple[str, List[str]]:
    loader = DocumentLoader(file.name)
    cleaned_text = loader.load() or ""
    markdown_text = getattr(loader, "_raw_text", "") or cleaned_text
    lines = [ln.strip() for ln in cleaned_text.splitlines() if ln.strip()]
    return markdown_text, lines


# =========================
# Авто-границы
# =========================
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


# =========================
# Утилиты
# =========================
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


# =========================
# Жёсткая нарезка
# =========================
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
        sections.append({"header_index": start, "Заголовок": header, "Текст": body})
    return sections


def make_preview(text: str, n_lines: int = 6) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    head = parts[:n_lines]
    preview = "\n".join(head).strip()
    if len(parts) > n_lines:
        preview += "\n…"
    return preview


def sections_to_preview_df(sections: List[Dict[str, Any]]) -> pd.DataFrame:
    if not sections:
        return pd.DataFrame({"Заголовок": [], "Превью": []})
    rows = []
    for s in sections:
        rows.append(
            {
                "Заголовок": s.get("Заголовок", ""),
                "Превью": make_preview(s.get("Текст", "")),
            }
        )
    return pd.DataFrame(rows)


def build_lines_view_df(
    lines: List[str],
    manual: List[int],
    predicted: List[int],
    window_start: int,
    window_size: int = WINDOW_SIZE,
) -> pd.DataFrame:
    if not lines:
        return pd.DataFrame({"№": [], "Текст": []})

    mset = set(coerce_int_list(manual))
    pset = set(coerce_int_list(predicted))

    start = max(0, int(window_start or 0))
    end = min(len(lines), start + int(window_size))

    rows = []
    for i in range(start, end):
        prefix = ""
        if i in mset:
            prefix = "🟢 "
        elif i in pset:
            prefix = "🟧 "
        rows.append({"№": i, "Текст": f"{prefix}{lines[i]}"})

    return pd.DataFrame(rows)


def sections_to_blocks(sections: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    blocks: Dict[str, str] = {}
    order: List[str] = []
    seen: Dict[str, int] = {}

    for s in sections or []:
        title = (s.get("Заголовок") or "").strip() or "Без названия"
        body = s.get("Текст", "") or ""

        if title in seen:
            seen[title] += 1
            key = f"{title} [{seen[title]}]"
        else:
            seen[title] = 1
            key = title

        blocks[key] = body
        order.append(key)

    return blocks, order


# ============================================================
# Редактор разбиения
# ============================================================
def build_section_editor(
    title: str = "Разбиение на разделы",
    show_file_input: bool = True,
) -> Dict[str, Any]:

    TABLE_HEIGHT = 640

    with gr.Group() as root:
        gr.HTML("""
        <style>

        /* 🟧 Пересобрать — авто */
        .gr-button.orange-btn > button {
            background-color: #ffe0b2 !important;
            border: 1px solid #fb8c00 !important;
            color: #5d4037 !important;
        }
        .gr-button.orange-btn > button:hover {
            background-color: #ffd180 !important;
        }

        /* 🟢 Подтвердить — ручное */
        .gr-button.green-btn > button {
            background-color: #c8e6c9 !important;
            border: 1px solid #43a047 !important;
            color: #1b5e20 !important;
        }
        .gr-button.green-btn > button:hover {
            background-color: #b2dfdb !important;
        }

        </style>
        """)

        if title:
            gr.Markdown(f"### {title}")

        gr.Markdown("🟢 ручные границы · 🟧 авто-границы")

        # --------- State ----------
        markdown_state = gr.State("")
        lines_state = gr.State([])
        predicted_state = gr.State([])
        manual_state = gr.State([])
        sections_state = gr.State([])
        window_start_state = gr.State(0)

        approved_state = gr.State(False)
        final_blocks_state = gr.State({})
        final_sections_order_state = gr.State([])
        final_boundaries_state = gr.State([])

        status = gr.Markdown("")

        # --------- Controls ----------
        with gr.Row():
            # preview_lines = gr.Slider(...)   # 🔕 временно отключено
            split_btn = gr.Button(
                "Пересобрать",
                variant="secondary",
                elem_classes=["orange-btn"]
            )

            approve_btn = gr.Button(
                "Подтвердить",
                variant="secondary",
                elem_classes=["green-btn"]
            )

        doc_view = gr.Markdown(value="", visible=False)

        with gr.Row():
            with gr.Column(scale=1):
                lines_table = gr.Dataframe(
                    headers=["№", "Текст"],
                    datatype=["number", "markdown"],
                    interactive=False,
                    wrap=True,
                    max_height=TABLE_HEIGHT,
                )
            with gr.Column(scale=1):
                sections_table = gr.Dataframe(
                    headers=["Заголовок", "Превью"],
                    datatype=["markdown", "markdown"],
                    interactive=False,
                    wrap=True,
                    max_height=TABLE_HEIGHT,
                )

        if show_file_input:
            file_input = gr.File(label="Загрузить документ (PDF/DOCX)", file_count="single")
        else:
            file_input = None

        # --------- Handlers ----------
        def _reset_approval():
            return False, {}, [], [], ""

        def on_file_upload(file):
            if file is None:
                return (
                    "", [], [], [], [], 0,
                    "", None, None,
                    *_reset_approval()
                )

            md, lines = extract_markdown_and_lines(file)
            predicted = predict_boundaries_from_lines(lines)
            manual: List[int] = []

            sections = split_by_line_boundaries(lines, predicted)
            df_sections = sections_to_preview_df(sections)

            window_start = 0
            df_lines = build_lines_view_df(lines, manual, predicted, window_start)

            return (
                md, lines, predicted, manual, sections, window_start,
                md, df_lines, df_sections,
                *_reset_approval()
            )

        def on_line_select(manual, predicted, lines, window_start, evt: gr.SelectData):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            row = evt.index[0]
            orig_row = window_start + row

            mset = set(manual)
            pset = set(predicted)

            if orig_row in mset:
                mset.remove(orig_row)
            elif orig_row in pset:
                pset.remove(orig_row)
            else:
                mset.add(orig_row)

            manual_new = sorted(mset)
            predicted_new = sorted(pset)

            df = build_lines_view_df(lines, manual_new, predicted_new, window_start)
            return manual_new, predicted_new, df, *_reset_approval()

        def on_split(manual, predicted, lines):
            combined = sorted(set(coerce_int_list(manual)) | set(coerce_int_list(predicted)))
            sections = split_by_line_boundaries(lines or [], combined)
            return sections_to_preview_df(sections), sections, predicted, *_reset_approval()

        def on_section_select(sections, manual, predicted, lines, evt: gr.SelectData):
            row = evt.index[0]
            header_index = int(sections[row].get("header_index", 0))
            window_start = max(0, header_index - WINDOW_RADIUS)
            df = build_lines_view_df(lines or [], manual or [], predicted or [], window_start)
            return df, window_start

        def on_approve(manual, predicted, lines):
            combined = sorted(set(coerce_int_list(manual)) | set(coerce_int_list(predicted)))
            sections = split_by_line_boundaries(lines or [], combined)
            blocks, order = sections_to_blocks(sections)
            return True, blocks, order, combined, "✅ Разбиение подтверждено."

        # --------- Wiring ----------
        if file_input is not None:
            file_input.upload(
                on_file_upload,
                inputs=[file_input],
                outputs=[
                    markdown_state, lines_state, predicted_state, manual_state, sections_state, window_start_state,
                    doc_view, lines_table, sections_table,
                    approved_state, final_blocks_state, final_sections_order_state, final_boundaries_state, status
                ],
            )

        lines_table.select(
            on_line_select,
            inputs=[manual_state, predicted_state, lines_state, window_start_state],
            outputs=[
                manual_state, predicted_state, lines_table,
                approved_state, final_blocks_state, final_sections_order_state, final_boundaries_state, status
            ],
        )

        split_btn.click(
            on_split,
            inputs=[manual_state, predicted_state, lines_state],
            outputs=[
                sections_table, sections_state, predicted_state,
                approved_state, final_blocks_state, final_sections_order_state, final_boundaries_state, status
            ],
        )

        sections_table.select(
            on_section_select,
            inputs=[sections_state, manual_state, predicted_state, lines_state],
            outputs=[lines_table, window_start_state],
        )

        approve_btn.click(
            on_approve,
            inputs=[manual_state, predicted_state, lines_state],
            outputs=[approved_state, final_blocks_state, final_sections_order_state, final_boundaries_state, status],
        )

    return {
        "root": root,
        "states": {
            "markdown_state": markdown_state,
            "lines_state": lines_state,
            "predicted_state": predicted_state,
            "manual_state": manual_state,
            "sections_state": sections_state,
            "window_start_state": window_start_state,
            "approved_state": approved_state,
            "final_blocks_state": final_blocks_state,
            "final_sections_order_state": final_sections_order_state,
            "final_boundaries_state": final_boundaries_state,
        },
        "components": {
            "status": status,
            "doc_view": doc_view,
            "lines_table": lines_table,
            "sections_table": sections_table,
            "split_btn": split_btn,
            "approve_btn": approve_btn,
            "file_input": file_input,
        },
    }
