import socket
import argparse
from main import iface

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

def is_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Запуск Gradio-интерфейса.")
    parser.add_argument("--port", type=int, default=7860, help="Порт для запуска (по умолчанию 7860)")
    args = parser.parse_args()

    if not is_port_free(args.port):
        print(f"❌ Порт {args.port} уже занят. Попробуйте остановить другой сервис или выбрать другой порт через --port.")
        exit(1)

    ip = get_local_ip()
    print("=" * 60)
    print(f"✅ Gradio-приложение запускается по адресу:")
    print(f"➡ http://{ip}:{args.port}")
    print("=" * 60)

    iface.launch(server_name="0.0.0.0", server_port=args.port, share=False)
