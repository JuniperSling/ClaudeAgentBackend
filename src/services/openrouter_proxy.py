"""Local reverse proxy that bridges Claude Agent SDK to OpenRouter's Messages API.

The SDK sends requests with `x-api-key` header to an Anthropic-compatible endpoint.
OpenRouter requires `Authorization: Bearer` header instead. This proxy translates
the auth header and forwards through the xray HTTPS proxy for connectivity.

Runs as a lightweight HTTP server inside the Docker container.
"""

import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import httpx

logger = logging.getLogger(__name__)

LISTEN_PORT = 9198
OPENROUTER_BASE = "https://openrouter.ai/api"

_server: HTTPServer | None = None


def start_openrouter_proxy():
    global _server
    _server = HTTPServer(("127.0.0.1", LISTEN_PORT), _ProxyHandler)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()
    logger.info("OpenRouter proxy started on port %d", LISTEN_PORT)


def stop_openrouter_proxy():
    global _server
    if _server:
        _server.shutdown()
        _server = None


class _ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        api_key = self.headers.get("x-api-key", "")
        upstream_url = OPENROUTER_BASE + self.path

        proxy_url = os.environ.get("PROXY_URL", "")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        anthro_ver = self.headers.get("anthropic-version")
        if anthro_ver:
            headers["anthropic-version"] = anthro_ver

        try:
            with httpx.Client(proxy=proxy_url or None, timeout=120) as client:
                resp = client.post(upstream_url, content=body, headers=headers)

            self.send_response(resp.status_code)
            for key in ("content-type", "x-request-id"):
                val = resp.headers.get(key)
                if val:
                    self.send_header(key, val)
            self.send_header("Content-Length", str(len(resp.content)))
            self.end_headers()
            self.wfile.write(resp.content)
        except Exception as e:
            logger.error("OpenRouter proxy error: %s", e)
            err = f'{{"type":"error","error":{{"type":"proxy_error","message":"{e}"}}}}'
            err_bytes = err.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_bytes)))
            self.end_headers()
            self.wfile.write(err_bytes)
