"""Faux llama-server pour les tests : mêmes options et mêmes routes HTTP que le vrai.

    python fake_llama_server.py -m modele.gguf --host 127.0.0.1 --port 8080 -c 8192 -ngl 999

GET /health : 503 pendant le « chargement » (FAKE_LOAD_SECONDS, 0,3 s par défaut), puis 200.
POST /v1/chat/completions : répond en citant le fichier du modèle et le dernier message.
Si le fichier du modèle contient « oom », s'arrête au chargement avec « out of memory ».
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("-m", required=True)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("-c", type=int, default=4096)
parser.add_argument("-ngl", type=int, default=0)
args, extra = parser.parse_known_args()

model = Path(args.m)
if b"oom" in model.read_bytes()[:100]:
    print("ggml_cuda: out of memory", file=sys.stderr, flush=True)
    sys.exit(1)
ready_at = time.monotonic() + float(os.environ.get("FAKE_LOAD_SECONDS", "0.3"))
print(f"faux llama-server : {model.name} sur le port {args.port}, options {extra}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            ok = time.monotonic() >= ready_at
            self._json(200 if ok else 503, {"status": "ok" if ok else "loading model"})
        else:
            self._json(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = body["messages"][-1]["content"]
        delay = float(os.environ.get("FAKE_REPLY_SECONDS", "0"))
        if delay:
            time.sleep(delay)
        content = f"[{model.stem}] Réponse à : {last}"
        self._json(200, {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(last.split()) + 10, "completion_tokens": len(content.split())},
        })


ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
