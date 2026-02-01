import re
from typing import List, Dict, Any, Tuple

import gradio as gr
import pandas as pd

from utils.document_loader import DocumentLoader
from ohlp_parser import HEADING_RE


# =========================
# Параметры UI
# =========================
# Раньше был viewport/window_start. Теперь мы показываем ВЕСЬ документ слева.
# Оставляем константы на будущее (и чтобы не ломать совместимость при откате).
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

    # Если границ вообще нет — fallback: всё как один блок от начала
    if not b:
        b = [0]

    # Раньше: всегда добавляли 0, из-за чего "ОБЩАЯ ХАРАКТЕРИСТИКА ..." становилась заголовком блока.
    # Теперь: добавляем 0 ТОЛЬКО если первая строка выглядит как нумерованный заголовок.
    if b[0] != 0:
        first_line = (lines[0] or "").lstrip()
        starts_with_digit = bool(first_line) and first_line[0].isdigit()
        if starts_with_digit:
            b = [0] + b
        # иначе — оставляем преамбулу вне блоков и начинаем с первой найденной границы (обычно "1. ...")

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


# ============================================================
# ЛЕВАЯ ТАБЛИЦА: ВСЕ СТРОКИ ДОКУМЕНТА (НЕЗАВИСИМО ОТ ПРАВОЙ)
# ============================================================
def build_lines_view_df(
    lines: List[str],
    manual: List[int],
    predicted: List[int],
) -> pd.DataFrame:
    if not lines:
        return pd.DataFrame({"№": [], "Текст": []})

    mset = set(coerce_int_list(manual))
    pset = set(coerce_int_list(predicted))

    rows = []
    for i, line in enumerate(lines):
        prefix = ""
        if i in mset:
            prefix = "🟢 "
        elif i in pset:
            prefix = "🟧 "
        rows.append({"№": i, "Текст": f"{prefix}{line}"})

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
            #btn-split button,
            #btn-split {
                border: 1px solid #000 !important;
                border-radius: 6px !important;
            }

            #btn-approve button,
            #btn-approve {
                border: 1px solid #000 !important;
                border-radius: 6px !important;
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

        # Оставляем для совместимости с main.py, но НЕ используем.
        window_start_state = gr.State(0)

        approved_state = gr.State(False)
        final_blocks_state = gr.State({})
        final_sections_order_state = gr.State([])
        final_boundaries_state = gr.State([])

        status = gr.Markdown("")

        # --------- Controls ----------
        with gr.Row(elem_classes=["section-editor-controls"]):
            # preview_lines = gr.Slider(...)   # 🔕 временно отключено (закомментировано, не удалено)

            split_btn = gr.Button(
                "Пересобрать",
                variant="secondary",
                elem_id="btn-split",
            )

            approve_btn = gr.Button(
                "Подтвердить",
                variant="primary",
                elem_id="btn-approve",
            )


        # Совместимость с main.py (он может писать сюда md), но вкладку "просмотр" мы не показываем
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

            # ЛЕВАЯ ТАБЛИЦА: ВСЕ СТРОКИ
            df_lines = build_lines_view_df(lines, manual, predicted)

            return (
                md, lines, predicted, manual, sections, 0,
                md, df_lines, df_sections,
                *_reset_approval()
            )

        def on_line_select(manual, predicted, lines, evt: gr.SelectData):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            # Теперь индекс строки = индекс в документе (без window_start)
            orig_row = int(evt.index[0])

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

            df = build_lines_view_df(lines, manual_new, predicted_new)
            return manual_new, predicted_new, df, *_reset_approval()

        def on_split(manual, predicted, lines):
            combined = sorted(set(coerce_int_list(manual)) | set(coerce_int_list(predicted)))
            sections = split_by_line_boundaries(lines or [], combined)
            return sections_to_preview_df(sections), sections, predicted, *_reset_approval()

        # ВАЖНО: мы специально НЕ делаем sections_table.select(...) чтобы правая не влияла на левую

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
            inputs=[manual_state, predicted_state, lines_state],
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
            "window_start_state": window_start_state,  # оставлено для совместимости
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
