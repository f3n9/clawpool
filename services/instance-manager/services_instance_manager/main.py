#!/usr/bin/env python3
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer


def resolve_container_name(employee_id, user_sub, mapping):
    identity = employee_id or user_sub
    if not identity:
        raise ValueError("missing identity")
    if identity in mapping:
        return mapping[identity]
    return f"openclaw-{identity}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        employee_id = self.headers.get("X-Employee-Id")
        user_sub = self.headers.get("X-User-Sub")
        try:
            container = resolve_container_name(employee_id, user_sub, {})
        except ValueError:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.end_headers()
            self.wfile.write(b"missing identity")
            return

        self.send_response(HTTPStatus.OK)
        self.end_headers()
        self.wfile.write(container.encode("utf-8"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
