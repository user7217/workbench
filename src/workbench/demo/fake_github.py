"""Fixture-backed fake GitHub REST server for discovery in demos."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class FakeGitHubServer:
    """Tiny local fixture-backed HTTP server for GitHub REST discovery in demos."""

    def __init__(self, fixtures_path: Path) -> None:
        self.fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        fixtures = self.fixtures

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                owner = fixtures.get("owner", "demouser")
                repos = fixtures.get("repos", [])
                if parsed.path == f"/users/{owner}/repos":
                    body = json.dumps(repos).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                for r in repos:
                    if parsed.path == f"/repos/{r['full_name']}/commits":
                        body = json.dumps([{"sha": r["root_commit"]}]).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(body)
                        return

                self.send_response(404)
                self.end_headers()

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
