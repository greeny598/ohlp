"""
Gradio demo for previewing section splits in an OHLP document.

This script allows you to load a reference and a test document, split
them into sections using the existing `split_ohlp_sections` function,
and display the detected sections side-by-side.  Each row of the
resulting tables contains the full text of the section body rather
than just a preview.  This makes it easy to inspect the results of
automatic parsing and see where the split logic may have gone wrong.

The left table corresponds to the **test** document (проверяемый),
while the right table corresponds to the **reference** document
(эталонный).  Each table has two columns: the original heading and
the full body text.  The number of lines is not limited; the entire
section body is shown.

Usage::

    poetry run python section_editor_demo.py

Dependencies:

    gradio
    pandas
    utils.document_loader (from your project)
    ohlp_parser (from your project)

Note: This code assumes you have installed the required dependencies
and that your Python environment can import `ohlp_parser` and
`utils.document_loader` from the current project.  It is provided as
an illustrative starting point and is not integrated into the
existing ``main.py`` user interface.
"""

from __future__ import annotations

from typing import Dict, Tuple

import gradio as gr
import pandas as pd

from utils.document_loader import DocumentLoader
from ohlp_parser import split_ohlp_sections


def sections_to_df(sections: Dict[str, str]) -> pd.DataFrame:
    """Convert a mapping of section headings to bodies into a DataFrame.

    Parameters
    ----------
    sections: dict
        A mapping from original heading strings to the full section body.

    Returns
    -------
    pandas.DataFrame
        A two-column DataFrame with columns ``Заголовок раздела`` and
        ``Текст раздела``.  Each row corresponds to one section.  The
        full body is included in the second column without truncation.
    """
    rows = []
    for heading, body in sections.items():
        rows.append({
            "Заголовок раздела": heading.strip(),
            "Текст раздела": body.strip(),
        })
    return pd.DataFrame(rows, columns=["Заголовок раздела", "Текст раздела"])


def load_and_split(file_obj) -> Tuple[Dict[str, str], str]:
    """Load the document via DocumentLoader and split into sections.

    Parameters
    ----------
    file_obj: gradio.File data
        The uploaded file object returned by Gradio.  It will have a
        `.name` attribute representing the path to the uploaded file on
        the server.

    Returns
    -------
    tuple of (sections dict, meta string)
        The first element is a mapping from section headings to bodies.
        The second element is a status message describing the result.
    """
    if file_obj is None:
        return {}, "Файл не выбран"

    path = getattr(file_obj, "name", None) or str(file_obj)
    try:
        loader = DocumentLoader(file_path=path, auto_detect_type=True)
        text = loader.load()
        sections = split_ohlp_sections(text, sections=None)
        meta = f"Файл: {path}\nНайдено разделов: {len(sections)}"
        return sections, meta
    except Exception as e:
        return {}, f"Ошибка: {e}"


def build_previews(test_file, ref_file):
    """Build DataFrames and meta messages for test and reference files.

    The order of arguments corresponds to the test (проверяемый) and
    reference (эталонный) documents.  The DataFrames are built using
    ``sections_to_df`` and include the full section body in each row.

    Returns
    -------
    tuple
        (test_df, ref_df, test_meta, ref_meta)
    """
    test_sections, test_meta = load_and_split(test_file)
    ref_sections, ref_meta = load_and_split(ref_file)
    test_df = sections_to_df(test_sections)
    ref_df = sections_to_df(ref_sections)
    return test_df, ref_df, test_meta, ref_meta


with gr.Blocks(
    title="Предпросмотр разбиения ОХЛП на разделы",
    # Custom CSS to vertically align DataFrame cells to the top.  We define a
    # class ``top-align`` and apply it to our DataFrame components below.
    css="""
    .top-align td {
        vertical-align: top;
    }
    """,
) as demo:
    gr.Markdown(
        "### Предпросмотр разбиения на разделы\n"
        "Слева — **проверяемый документ**, справа — **эталонный**.\n\n"
        "В каждой таблице показан заголовок раздела и **полный текст**\n"
        "раздела, без ограничений по числу строк.  Это позволяет быстро\n"
        "увидеть, где автоматика ошиблась (например, пропал раздел 4\n"
        "или поехали номера) и проверить содержимое каждого раздела."
    )

    with gr.Row():
        test_file = gr.File(
            label="Проверяемый документ (PDF / DOCX)",
            file_types=[".pdf", ".docx", ".txt"],
        )
        ref_file = gr.File(
            label="Эталонный документ (PDF / DOCX)",
            file_types=[".pdf", ".docx", ".txt"],
        )

    btn = gr.Button("Показать разбиение")

    with gr.Row():
        test_meta = gr.Textbox(label="Статус проверяемого", interactive=False)
        ref_meta = gr.Textbox(label="Статус эталонного", interactive=False)

    with gr.Row():
        test_df = gr.DataFrame(
            value=pd.DataFrame(columns=["Заголовок раздела", "Текст раздела"]),
            interactive=False,
            wrap=True,
            label="Проверяемый документ — разделы",
            elem_classes="top-align",
        )
        ref_df = gr.DataFrame(
            value=pd.DataFrame(columns=["Заголовок раздела", "Текст раздела"]),
            interactive=False,
            wrap=True,
            label="Эталонный документ — разделы",
            elem_classes="top-align",
        )

    # When the button is clicked, generate the tables and update meta information.
    btn.click(
        fn=build_previews,
        inputs=[test_file, ref_file],
        outputs=[test_df, ref_df, test_meta, ref_meta],
    )

if __name__ == "__main__":
    # Use server_name="0.0.0.0" for broader accessibility; set share=False for local.
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)