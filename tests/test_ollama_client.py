"""Tests the real OllamaProvider code against a fake local Ollama server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import jobs, llm, services
from tests.helpers import make_campaign


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        req = json.loads(self.rfile.read(n))
        assert req["stream"] is False and req["format"] == "json"
        user = req["messages"][1]["content"]
        meta = {"topic": "Fake topic"}
        if "PLATFORM: x" in user:
            out = llm.MockProvider().generate("draft", "", "", {**meta, "platform": "x"})
        elif "PLATFORM:" in user:
            out = llm.MockProvider().generate("draft", "", "", {**meta, "platform": "linkedin"})
        elif "banner" in user:
            out = llm.MockProvider().generate("visual", "", "", meta)
        else:
            out = llm.MockProvider().generate("brief", "", "", meta)
        body = json.dumps({"message": {"role": "assistant", "content": out}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


def test_full_pipeline_through_ollama_client(monkeypatch):
    srv = _serve(FakeOllama)
    monkeypatch.setenv("SGA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SGA_OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("SGA_OLLAMA_MODEL", "llama3.2")
    assert llm.get_provider().health()["ok"]
    cid, jid = make_campaign()
    assert jobs.get_job(jid)["status"] == "DONE", jobs.get_job(jid)
    assert len(services.get_campaign(cid)["posts"]) == 2
    srv.shutdown()


def test_health_reports_missing_model_and_server_down(monkeypatch):
    srv = _serve(FakeOllama)
    monkeypatch.setenv("SGA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SGA_OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("SGA_OLLAMA_MODEL", "othermodel")
    h = llm.get_provider().health()
    assert not h["ok"] and "ollama pull othermodel" in h["detail"]
    srv.shutdown()
    monkeypatch.setenv("SGA_OLLAMA_URL", "http://127.0.0.1:1")
    assert not llm.get_provider().health()["ok"]
    with pytest.raises(llm.LLMUnavailable):
        llm.get_provider().generate("brief", "s", "p")


def test_http_404_means_model_missing(monkeypatch):
    class NotFound(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"model not found"}')

    srv = _serve(NotFound)
    p = llm.OllamaProvider(f"http://127.0.0.1:{srv.server_port}", "nomodel", 5)
    with pytest.raises(llm.LLMUnavailable, match="ollama pull nomodel"):
        p.generate("brief", "s", "p")
    srv.shutdown()
