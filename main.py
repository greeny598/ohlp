# main.py
import glob
import os
import argparse
import logging
import asyncio

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.DEBUG,  # Или DEBUG для более подробного логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log", encoding='utf-8')  # Вывод в файл
    ]
)

import gradio as gr

# Импорт новой функции генерации отчета
# Предполагается, что в utils/__init__.py есть: from .report_generator import generate_report
# или можно импортировать напрямую:
#from utils.report_generator import generate_report
from utils.report_generator import generate_report_async

logger = logging.getLogger(__name__)

# --- Настройка аргументов командной строки ---
parser = argparse.ArgumentParser(
    description="Генерация отчета с возможностью выбора провайдера API и параметров сервера Gradio."
)
parser.add_argument(
    "--provider", "-p", default="yandex",
    help="Имя провайдера для SectionChecker (по умолчанию 'yandex')"
)
parser.add_argument(
    "--host", "-H", default="0.0.0.0",
    help="Адрес сервера Gradio"
)
parser.add_argument(
    "--port", "-P", type=int, default=7860,
    help="Порт сервера Gradio"
)
parser.add_argument(
    "--share", "-s", action="store_true",
    help="Включить публичный шаринг Gradio"
)
parser.add_argument(
    "--n_users", "-n", type=int, default=50,
    help="Максимальное количество одновременных сессий пользователей"
)

args = parser.parse_args()

# --- Константы по умолчанию ---
TEMPLATE_DIR = os.getenv("TEMPLATE_DIR", "templates")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "results")
REPORT_PREFIX = os.getenv("REPORT_PREFIX", "report")
PROVIDER = args.provider

# --- Получение списка шаблонов ---
def get_available_templates():
    """Получает список доступных шаблонов из папки TEMPLATE_DIR."""
    try:
        templates = [os.path.basename(p) for p in glob.glob(os.path.join(TEMPLATE_DIR, "*.docx"))]
        logger.debug(f"Найденные шаблоны: {templates}")
        return templates
    except Exception as e:
        logger.error(f"Ошибка при поиске шаблонов в '{TEMPLATE_DIR}': {e}")
        return []

templates = get_available_templates()

# --- Настройка Gradio интерфейса ---
custom_css = """
.compact-file {
  display: inline-block !important;
  width: 30% !important;
  margin-right: 1% !important;
  vertical-align: top;
}
.compact-file .file-upload {
  min-height: 0.25rem !important;
  height: 0.5rem !important;
  padding: 0.25rem !important;
}
.compact-file .file-upload .file-input {
  height: 0.5rem !important;
  line-height: 0.5rem !important;
}
.compact-file .file-upload .file-input button {
  height: 0.5rem !important;
  line-height: 0.5rem !important;
}
"""

with gr.Blocks(css=custom_css) as iface:
    gr.Markdown("## 📄 Сравнение инструкций и формирование отчета")
    with gr.Row():
        test_input = gr.File(
            label="📥 Проверяемая инструкция",
            file_types=[".pdf", ".PDF", ".docx", ".DOCX"],
            type="filepath",
            elem_classes="compact-file"
        )
        ref_input = gr.File(
            label="📘 Эталонная инструкция",
            file_types=[".pdf", ".docx"],
            type="filepath",
            elem_classes="compact-file"
        )
        rec_input = gr.File(
            label="📑 Рекомендации по заполнению",
            file_types=[".pdf", ".PDF", ".docx", ".DOCX"],
            type="filepath",
            elem_classes="compact-file"
        )

    tmpl_input = gr.Dropdown(
        label="📑 Шаблон отчёта",
        choices=templates,
        value=templates[0] if templates else None,
        interactive=True
    )

    output_file = gr.File(label="📤 DOCX-отчет")

    # --- Асинхронный обработчик кнопки ---
    async def on_compare(t, r, c, tmpl):
        return await generate_report_async(
            t, r, c, tmpl,
            template_dir=TEMPLATE_DIR,
            output_dir=OUTPUT_DIR,
            provider=PROVIDER,
            prefix=REPORT_PREFIX
        )

    compare_btn = gr.Button("🔍 Сравнить и сформировать отчет")
    compare_btn.click(
        fn=on_compare,
        inputs=[test_input, ref_input, rec_input, tmpl_input],
        outputs=output_file,
        show_progress=True
    )

    # Разрешаем нескольким обработчикам выполняться параллельно и ограничиваем длину очереди
    iface.queue(default_concurrency_limit=args.n_users, max_size=10)

# --- Точка входа ---
if __name__ == "__main__":
    logger.info("Запуск Gradio-интерфейса...")
    logger.info(f"Используемый провайдер LLM: {PROVIDER}")
    logger.info(f"Папка с шаблонами: {TEMPLATE_DIR}")
    logger.info(f"Папка для отчетов: {OUTPUT_DIR}")
    logger.info(f"Префикс отчетов: {REPORT_PREFIX}")
    logger.info(f"Доступные шаблоны: {templates}")

    try:
        iface.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share
        )
        logger.info("Gradio-интерфейс запущен.")
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения (Ctrl+C), остановка сервера.")
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске Gradio-интерфейса: {e}", exc_info=True)
        raise