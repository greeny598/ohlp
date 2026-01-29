import os
import glob
import argparse
import logging
from typing import Dict, Tuple, List

import gradio as gr

from utils.report_generator import generate_report_async
from section_editor import (
    build_section_editor,
    extract_markdown_and_lines,
    predict_boundaries_from_lines,
    split_by_line_boundaries,
    build_lines_view_df,
    sections_to_preview_df,
)

# ============================================================
# Логирование
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("app.log", encoding="utf-8")]
)
logger = logging.getLogger(__name__)

# ============================================================
# CLI
# ============================================================
parser = argparse.ArgumentParser()
parser.add_argument("--provider", "-p", default="yandex")
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--port", type=int, default=7860)
parser.add_argument("--share", action="store_true")
args = parser.parse_args()

PROVIDER = args.provider

# ============================================================
# Пути и шаблоны
# ============================================================
TEMPLATE_DIR = os.getenv("TEMPLATE_DIR", "templates")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "results")
REPORT_PREFIX = os.getenv("REPORT_PREFIX", "report")


def get_templates():
    return [
        os.path.basename(p)
        for p in glob.glob(os.path.join(TEMPLATE_DIR, "*.docx"))
    ]


templates = get_templates()

# ============================================================
# Кэш извлечения (TEST / REF)
# ============================================================
_extract_cache: Dict[Tuple[str, float], Tuple[str, List[str]]] = {}


def cached_extract(path: str) -> Tuple[str, List[str]]:
    mtime = os.path.getmtime(path)
    key = (path, mtime)

    if key in _extract_cache:
        return _extract_cache[key]

    class _F:
        def __init__(self, name: str):
            self.name = name

    md, lines = extract_markdown_and_lines(_F(path))
    _extract_cache[key] = (md, lines)
    return md, lines


def _reset_editor_approval_payload():
    return False, {}, [], [], ""


def _badges(a_test: bool, a_ref: bool) -> str:
    t = "✅" if a_test else "⏳"
    r = "✅" if a_ref else "⏳"
    return f"**TEST: {t}** · **REF: {r}**"


# ============================================================
# UI
# ============================================================
with gr.Blocks() as app:
    gr.Markdown("## 📄 Сравнение инструкций — ручной контроль разбиения")

    # --------------------------------------------------------
    # 1. Загрузка документов
    # --------------------------------------------------------
    with gr.Row():
        test_input = gr.File(
            label="📥 Проверяемая инструкция (TEST)",
            file_types=[".pdf", ".docx"],
            type="filepath",
        )
        ref_input = gr.File(
            label="📘 Эталонная инструкция (REF)",
            file_types=[".pdf", ".docx"],
            type="filepath",
        )
        rec_input = gr.File(
            label="📑 Рекомендации",
            file_types=[".pdf", ".docx"],
            type="filepath",
        )

    tmpl_input = gr.Dropdown(
        label="📑 Шаблон отчёта",
        choices=templates,
        value=templates[0] if templates else None,
    )

    gr.Markdown("---")

    # --------------------------------------------------------
    # 2. Ручная разбивка
    # --------------------------------------------------------
    gr.Markdown("### Шаг 1. Ручной контроль разбиения")
    start_split_btn = gr.Button(
        "▶ Запустить начальную разбивку на блоки",
        variant="secondary",
    )

    with gr.Tabs():
        with gr.Tab("Проверяемая инструкция (TEST)"):
            editor_test = build_section_editor(
                title="",
                show_file_input=False,
            )
        with gr.Tab("Эталонная инструкция (REF)"):
            editor_ref = build_section_editor(
                title="",
                show_file_input=False,
            )

    t_states = editor_test["states"]
    t_comps = editor_test["components"]

    r_states = editor_ref["states"]
    r_comps = editor_ref["components"]

    # --------------------------------------------------------
    # 3. Кнопка отчёта + индикаторы
    # --------------------------------------------------------
    gr.Markdown("---")
    generate_btn = gr.Button(
        "🚀 Сформировать отчёт",
        variant="primary",
        interactive=False,
    )
    badges_line = gr.Markdown(_badges(False, False))
    output_file = gr.File(label="📤 Итоговый DOCX")

    # ========================================================
    # Начальная авто-разбивка
    # ========================================================
    def on_start_initial_split(test_path, ref_path):
        if not test_path:
            raise gr.Error("Выберите файл TEST.")
        if not ref_path:
            raise gr.Error("Выберите файл REF.")

        # --- TEST ---
        md_t, lines_t = cached_extract(test_path)
        pred_t = predict_boundaries_from_lines(lines_t)
        man_t: List[int] = []
        sections_t = split_by_line_boundaries(lines_t, pred_t)
        df_sec_t = sections_to_preview_df(sections_t)
        df_lines_t = build_lines_view_df(lines_t, man_t, pred_t, 0)

        # --- REF ---
        md_r, lines_r = cached_extract(ref_path)
        pred_r = predict_boundaries_from_lines(lines_r)
        man_r: List[int] = []
        sections_r = split_by_line_boundaries(lines_r, pred_r)
        df_sec_r = sections_to_preview_df(sections_r)
        df_lines_r = build_lines_view_df(lines_r, man_r, pred_r, 0)

        return (
            # TEST
            md_t, lines_t, pred_t, man_t, sections_t, 0,
            md_t, df_lines_t, df_sec_t,
            *_reset_editor_approval_payload(),

            # REF
            md_r, lines_r, pred_r, man_r, sections_r, 0,
            md_r, df_lines_r, df_sec_r,
            *_reset_editor_approval_payload(),
        )

    start_split_btn.click(
        on_start_initial_split,
        inputs=[test_input, ref_input],
        outputs=[
            # TEST
            t_states["markdown_state"],
            t_states["lines_state"],
            t_states["predicted_state"],
            t_states["manual_state"],
            t_states["sections_state"],
            t_states["window_start_state"],
            t_comps["doc_view"],
            t_comps["lines_table"],
            t_comps["sections_table"],
            t_states["approved_state"],
            t_states["final_blocks_state"],
            t_states["final_sections_order_state"],
            t_states["final_boundaries_state"],
            t_comps["status"],

            # REF
            r_states["markdown_state"],
            r_states["lines_state"],
            r_states["predicted_state"],
            r_states["manual_state"],
            r_states["sections_state"],
            r_states["window_start_state"],
            r_comps["doc_view"],
            r_comps["lines_table"],
            r_comps["sections_table"],
            r_states["approved_state"],
            r_states["final_blocks_state"],
            r_states["final_sections_order_state"],
            r_states["final_boundaries_state"],
            r_comps["status"],
        ],
    )

    # --------------------------------------------------------
    # Индикаторы + активация кнопки
    # --------------------------------------------------------
    def on_approval_change(a_test: bool, a_ref: bool):
        return (
            gr.update(interactive=a_test and a_ref),
            _badges(a_test, a_ref),
        )

    t_states["approved_state"].change(
        on_approval_change,
        inputs=[t_states["approved_state"], r_states["approved_state"]],
        outputs=[generate_btn, badges_line],
    )
    r_states["approved_state"].change(
        on_approval_change,
        inputs=[t_states["approved_state"], r_states["approved_state"]],
        outputs=[generate_btn, badges_line],
    )

    # --------------------------------------------------------
    # Генерация отчёта
    # --------------------------------------------------------
    async def on_generate(
        test_path,
        ref_path,
        rec_path,
        template_name,
        a_test,
        a_ref,
        test_blocks,
        ref_blocks,
        sections_order,
    ):
        if not (a_test and a_ref):
            raise gr.Error("Подтвердите разбиение TEST и REF.")
        if not test_blocks or not ref_blocks:
            raise gr.Error("Нет утверждённых блоков.")

        return await generate_report_async(
            test_path=test_path,
            ref_path=ref_path,
            rec_path=rec_path,
            template_name=template_name,
            template_dir=TEMPLATE_DIR,
            output_dir=OUTPUT_DIR,
            provider=PROVIDER,
            prefix=REPORT_PREFIX,
            test_blocks=test_blocks,
            ref_blocks=ref_blocks,
            sections_order=sections_order,
        )

    generate_btn.click(
        on_generate,
        inputs=[
            test_input,
            ref_input,
            rec_input,
            tmpl_input,
            t_states["approved_state"],
            r_states["approved_state"],
            t_states["final_blocks_state"],
            r_states["final_blocks_state"],
            t_states["final_sections_order_state"],
        ],
        outputs=output_file,
    )

# ============================================================
# Launch
# ============================================================
if __name__ == "__main__":
    logger.info("Запуск приложения")
    app.queue(max_size=10).launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
    )
