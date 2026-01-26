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
# Извлечение markdown (для просмотра) и строк (для разметки)
# =========================
def extract_markdown_and_lines(file) -> Tuple[str, List[str]]:
    """
    Используем ваш DocumentLoader.
    В вашем проекте loader._raw_text обычно содержит исходный markdown (docling export_to_markdown),
    а loader.load() возвращает очищенный текст.
    """
    loader = DocumentLoader(file.name)
    cleaned_text = loader.load() or ""
    markdown_text = getattr(loader, "_raw_text", "") or cleaned_text
    lines = [ln.strip() for ln in cleaned_text.splitlines() if ln.strip()]
    return markdown_text, lines


# =========================
# Авто-границы (подсказка): HEADING_RE + мягкое правило "4.7 Заголовок"
# =========================
_RELAXED_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>\S.*)$")


def _is_probably_heading(num: str, title: str) -> bool:
    # Подпункты 4.7 / 6.2.3 почти наверняка заголовки
    if "." in num:
        return True
    # Верхний уровень без точек — только если похоже на капс и не коротко
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
# ЖЁСТКАЯ нарезка по границам (ручные границы = приоритет/команда)
# =========================
def split_by_line_boundaries(lines: List[str], boundaries: List[int]) -> List[Dict[str, Any]]:
    """
    Возвращает список секций:
      {
        "header_index": int,
        "Заголовок": str,
        "Текст": str
      }
    """
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
        return pd.DataFrame({"Заголовок": [], "Превью": []})
    rows = []
    for s in sections:
        rows.append(
            {
                "Заголовок": s.get("Заголовок", ""),
                "Превью": make_preview(s.get("Текст", ""), int(preview_n)),
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
    """
    Левый список строк для разметки. В Gradio 5.x HTML в Dataframe часто экранируется,
    поэтому используем markdown + эмодзи-метки, сохраняя эргономику.
    """
    if not lines:
        return pd.DataFrame({"№": [], "Текст": []})

    mset = set(coerce_int_list(manual))
    pset = set(coerce_int_list(predicted))

    start = max(0, int(window_start or 0))
    if start >= len(lines):
        start = 0
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
    """
    Преобразует секции в blocks dict и порядок sections_order.
    Ключ = Заголовок (как есть). Если есть дубликаты заголовков — добавляем суффикс.
    """
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
# Публичная функция: встраиваемый редактор разбиения
# ============================================================
def build_section_editor(
    title: str = "Разбиение на разделы",
    show_file_input: bool = True,
) -> Dict[str, Any]:
    """
    Создаёт UI-редактор разбиения, который можно встроить в main.py.
    Возвращает dict с компонентами и state, чтобы main.py мог подключить дальнейший pipeline.

    Ключевые state для интеграции:
      - approved_state: bool
      - final_blocks_state: dict[str, str]
      - final_sections_order_state: list[str]
      - final_boundaries_state: list[int]
      - lines_state/predicted_state/manual_state/sections_state — для отладки/переиспользования
    """
    with gr.Group() as root:
        gr.Markdown(f"### {title}")
        gr.Markdown("🟢 ручные границы · 🟧 авто-границы")

        # --------- State (внутренние + интеграционные) ----------
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
        if show_file_input:
            file_input = gr.File(label="Загрузить документ (PDF/DOCX)", file_count="single")
        else:
            file_input = None

        with gr.Row():
            preview_lines = gr.Slider(
                minimum=1, maximum=30, step=1, value=6,
                label="Превью справа: первые N строк"
            )
            split_btn = gr.Button("Пересобрать разбиение", variant="secondary")
            approve_btn = gr.Button("Подтвердить разбиение", variant="primary")

        # --------- Layout ----------
        with gr.Row():
            with gr.Column(scale=1):
                with gr.Tabs():
                    with gr.Tab("Просмотр"):
                        doc_view = gr.Markdown(value="")
                    with gr.Tab("Разметка"):
                        lines_table = gr.Dataframe(
                            headers=["№", "Текст"],
                            datatype=["number", "markdown"],
                            interactive=False,
                            wrap=True,
                            max_height=620,
                        )

            with gr.Column(scale=1):
                sections_table = gr.Dataframe(
                    headers=["Заголовок", "Превью"],
                    datatype=["markdown", "markdown"],
                    interactive=False,
                    wrap=True,
                    max_height=680,
                )

        # =========================
        # Handlers
        # =========================
        def _reset_approval():
            return False, {}, [], [], ""

        def on_file_upload(file, preview_n):
            if file is None:
                return (
                    "", [], [], [], [], 0,
                    "", None, None,
                    *_reset_approval()
                )

            md, lines = extract_markdown_and_lines(file)
            predicted = predict_boundaries_from_lines(lines)
            manual: List[int] = []

            # первичная разбивка по авто
            sections = split_by_line_boundaries(lines, predicted)
            df_sections = sections_to_preview_df(sections, int(preview_n))

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
            window_start = int(window_start or 0)

            if not hasattr(evt, "index") or evt.index is None:
                df = build_lines_view_df(lines, manual, predicted, window_start)
                return manual, predicted, df, *_reset_approval()

            row_in_view = evt.index[0] if isinstance(evt.index, (tuple, list)) else int(evt.index)
            orig_row = window_start + int(row_in_view)

            if orig_row < 0 or orig_row >= len(lines):
                df = build_lines_view_df(lines, manual, predicted, window_start)
                return manual, predicted, df, *_reset_approval()

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

        def on_split(manual, predicted, lines, preview_n):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            combined = sorted(set(manual) | set(predicted))
            sections = split_by_line_boundaries(lines, combined)
            df_sections = sections_to_preview_df(sections, int(preview_n))

            return df_sections, sections, predicted, *_reset_approval()

        def on_preview_change(sections, preview_n):
            sections = sections or []
            return sections_to_preview_df(sections, int(preview_n))

        def on_section_select(sections, manual, predicted, lines, evt: gr.SelectData):
            sections = sections or []
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            if not hasattr(evt, "index") or evt.index is None or not sections:
                return gr.update(), 0

            row = evt.index[0] if isinstance(evt.index, (tuple, list)) else int(evt.index)
            if row < 0 or row >= len(sections):
                return gr.update(), 0

            header_index = int(sections[row].get("header_index", 0))
            window_start = max(0, header_index - WINDOW_RADIUS)

            df_left = build_lines_view_df(lines, manual, predicted, window_start)
            return df_left, window_start

        def on_approve(manual, predicted, lines):
            manual = coerce_int_list(manual)
            predicted = coerce_int_list(predicted)
            lines = lines or []

            combined = sorted(set(manual) | set(predicted))
            sections = split_by_line_boundaries(lines, combined)

            blocks, order = sections_to_blocks(sections)

            approved = True
            msg = "✅ Разбиение подтверждено. Дальнейшая обработка должна использовать только эти блоки."
            return approved, blocks, order, combined, msg

        # =========================
        # Wiring
        # =========================
        if file_input is not None:
            file_input.upload(
                on_file_upload,
                inputs=[file_input, preview_lines],
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
            inputs=[manual_state, predicted_state, lines_state, preview_lines],
            outputs=[
                sections_table, sections_state, predicted_state,
                approved_state, final_blocks_state, final_sections_order_state, final_boundaries_state, status
            ],
        )

        preview_lines.change(
            on_preview_change,
            inputs=[sections_state, preview_lines],
            outputs=[sections_table],
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
            "preview_lines": preview_lines,
            "split_btn": split_btn,
            "approve_btn": approve_btn,
            "file_input": file_input,
        },
    }


# ============================================================
# Демо-режим (если запускать отдельно)
# ============================================================
def build_demo() -> gr.Blocks:
    with gr.Blocks() as demo:
        editor = build_section_editor(show_file_input=True)
        # editor["root"] уже вставлен в блоки через gr.Group контекст
        # поэтому ничего дополнительно не нужно
        _ = editor
    return demo


if __name__ == "__main__":
    app = build_demo()
    app.launch()
