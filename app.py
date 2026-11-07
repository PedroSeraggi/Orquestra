"""Liga a Orquestra e abre a tela no navegador. Uso: python app.py
(no Windows, `pythonw app.py` roda sem janela). Se ela já estiver ligada, só abre a tela."""
import socket
import time
import webbrowser

import orquestra

URL = f"http://localhost:{orquestra.PORTA}"


def no_ar() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", orquestra.PORTA), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    if not no_ar():
        orquestra.servidor()
        print(f"Orquestra ligada em {URL} (Ctrl+C para desligar)")
    webbrowser.open(URL)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
