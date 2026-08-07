"""Fixtures partagees entre modules de test.

`fake_ollama_server` simule un backend Ollama reel via un vrai serveur HTTP
local (thread daemon), pour les tests qui doivent exercer le transport HTTP
d'`OllamaLLM` sans dependre d'une installation Ollama ni du reseau externe.
Partagee entre `test_integration_llm_provider.py` et `test_composition.py`.
"""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class FakeOllamaHandler(BaseHTTPRequestHandler):
    # Reponse fixee par le test avant l'appel HTTP (voir chaque test).
    response_body: bytes = b"{}"

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def fake_ollama_server() -> Iterator[HTTPServer]:
    server = HTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
