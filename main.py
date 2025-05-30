import os
from datetime import datetime
import gradio as gr
from utils.document_loader import extract_text_from_document
from langchain_utils.section_checker import SectionChecker
from utils.docx_writer import fill_template

# Заглушка для рекомендаций (пока не используется в отчете)
def stub_recommendations(rec_fill_file):
    return rec_fill_file.name if rec_fill_file else None

# Основная функция генерации отчета с Gradio-интерфейсом
def generate_report(test_file, ref_file, rec_fill_file, progress=gr.Progress()):
    # Инициализация
    progress(0.05, desc="Инициализация...")

    # Шаг 1: извлечение текста из PDF
    progress(0.2, desc="Извлечение текста из PDF...")
    test_text = extract_text_from_document(test_file.name)
    ref_text = extract_text_from_document(ref_file.name)
    # rec_stub = stub_recommendations(rec_fill_file)

    # Шаг 2: сравнение инструкций через LLM
    progress(0.5, desc="Сравнение инструкций через LLM...")
    checker = SectionChecker(api_provider='deepseek')
    diffs = checker.check_sections(ref_text, test_text)

    # Шаг 3: очистка JSON и подготовка списка различий
    progress(0.7, desc="Очистка и форматирование данных...")
    clean = checker.clean_json_from_md(diffs)

    # Шаг 4: формирование и сохранение DOCX-отчета
    progress(0.85, desc="Формирование отчета DOCX...")
    base_name = os.path.splitext(os.path.basename(test_file.name))[0]
    info = {
        'DRUG_NAME': base_name,
        'DIFFERENCES': clean,
        'DATE': datetime.now().strftime('%d.%m.%Y г.')
    }

    out_dir = 'results'
    os.makedirs(out_dir, exist_ok=True)
    timestamp = int(datetime.now().timestamp())
    output_path = os.path.join(out_dir, f'report_{base_name}_{timestamp}.docx')

    # Используем шаблон для различий
    fill_template(
        template_path='templates/differences_template.docx',
        output_path=output_path,
        info=info
    )

    progress(1.0, desc="Отчет готов!")
    return output_path

# CSS стили для интерфейса
custom_css = """
label {font-size: 12px !important;}
.container {max-width: 600px !important;}
.gradio-container {padding: 10px !important;}
input, button {margin: 5px 0 !important;}
"""

with gr.Blocks(css=custom_css) as iface:
    gr.Markdown("## Сравнение инструкций")
    gr.Markdown("Загрузите PDF-файлы:")

    with gr.Row():
        with gr.Column():
            test_input = gr.UploadButton("Загрузка проверяемой инструкции", file_types=[".pdf"])
            test_label = gr.Text(label="Загруженный документ:", value="", interactive=False)
        with gr.Column():
            ref_input = gr.UploadButton("Загрузка эталона", file_types=[".pdf"])
            ref_label = gr.Text(label="Загруженный документ:", value="", interactive=False)
        with gr.Column():
            rec_input = gr.UploadButton("Загрузка рекомендаций по заполнению", file_types=[".pdf"])
            rec_label = gr.Text(label="Загруженный документ:", value="", interactive=False)

    # Обработчики загрузки
    test_input.upload(lambda f: f.name or "", inputs=[test_input], outputs=[test_label])
    ref_input.upload(lambda f: f.name or "", inputs=[ref_input], outputs=[ref_label])
    rec_input.upload(lambda f: f.name or "", inputs=[rec_input], outputs=[rec_label])

    # Кнопка запуска сравнения
    compare_btn = gr.Button("Сравнить", size="sm")
    output_file = gr.File(label="Отчет (DOCX)", file_types=[".docx"])

    compare_btn.click(
        fn=generate_report,
        inputs=[test_input, ref_input, rec_input],
        outputs=output_file
    )

if __name__ == '__main__':
    iface.launch()
