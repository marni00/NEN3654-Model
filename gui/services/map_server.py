from __future__ import annotations

import hashlib
import secrets
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LocalMapServer:
    """Serve the map shell and GeoJSON over an isolated loopback HTTP origin.

    MapLibre GL JS v6 uses an ES-module web worker for GeoJSON/vector processing.
    Loading the shell from ``file://`` gives Chromium an opaque origin, which can
    prevent that worker from starting without surfacing a normal map error.
    """

    _STATIC_FILES = {
        "map.html": "text/html; charset=utf-8",
        "map.js": "text/javascript; charset=utf-8",
        "map.css": "text/css; charset=utf-8",
    }

    def __init__(self, web_root: Path) -> None:
        self._web_root = web_root.resolve(strict=True)
        self._token = secrets.token_urlsafe(24)
        self._payload_dir = tempfile.TemporaryDirectory(
            prefix="nen3654_map_"
        )
        self._payload_root = Path(self._payload_dir.name)
        self._payloads: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="nen3654-map-http",
            daemon=True,
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/{self._token}"

    @property
    def map_url(self) -> str:
        return f"{self.base_url}/map.html"

    def publish_geojson(self, dataset: str, layer: str, payload: str) -> str:
        key = hashlib.sha256(
            f"{dataset}\0{layer}".encode("utf-8")
        ).hexdigest()
        encoded = payload.encode("utf-8")
        version = hashlib.sha256(encoded).hexdigest()[:16]
        target = self._payload_root / f"{key}.geojson"
        temporary = self._payload_root / f"{key}.{os.getpid()}.tmp"
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
        with self._lock:
            self._payloads[key] = target
        return f"{self.base_url}/data/{key}.geojson?v={version}"

    def remove_geojson(self, dataset: str, layer: str) -> None:
        key = hashlib.sha256(
            f"{dataset}\0{layer}".encode("utf-8")
        ).hexdigest()
        with self._lock:
            path = self._payloads.pop(key, None)
        if path is not None:
            path.unlink(missing_ok=True)

    def clear_geojson(self) -> None:
        with self._lock:
            paths = tuple(self._payloads.values())
            self._payloads.clear()
        for path in paths:
            path.unlink(missing_ok=True)

    def close(self) -> None:
        server = getattr(self, "_server", None)
        if server is None:
            return
        self._server = None
        server.shutdown()
        server.server_close()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self.clear_geojson()
        self._payload_dir.cleanup()

    def _handler_type(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                owner._handle_get(self)

            def do_HEAD(self) -> None:
                owner._handle_get(self, include_body=False)

            def log_message(self, format: str, *args) -> None:
                return

        return Handler

    def _handle_get(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        include_body: bool = True,
    ) -> None:
        request_path = unquote(urlsplit(handler.path).path)
        prefix = f"/{self._token}/"
        if not request_path.startswith(prefix):
            self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)
            return

        relative = request_path[len(prefix):]
        if relative in self._STATIC_FILES:
            file_path = (self._web_root / relative).resolve()
            if file_path.parent != self._web_root or not file_path.is_file():
                self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)
                return
            self._send(
                handler,
                200,
                file_path.read_bytes(),
                self._STATIC_FILES[relative],
                include_body,
                cache_control="no-cache",
            )
            return

        if relative.startswith("data/") and relative.endswith(".geojson"):
            key = relative[5:-8]
            if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)
                return
            with self._lock:
                payload_path = self._payloads.get(key)
            if payload_path is None:
                self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)
                return
            try:
                payload_size = payload_path.stat().st_size
            except OSError:
                self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)
                return
            self._send_file(
                handler,
                payload_path,
                payload_size,
                "application/geo+json; charset=utf-8",
                include_body,
                cache_control="no-store",
            )
            return

        self._send(handler, 404, b"Not found", "text/plain; charset=utf-8", include_body)

    @staticmethod
    def _send_file(
        handler: BaseHTTPRequestHandler,
        path: Path,
        size: int,
        content_type: str,
        include_body: bool,
        *,
        cache_control: str,
    ) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(size))
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
        handler.end_headers()
        if include_body:
            try:
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        handler.wfile.write(chunk)
            except (OSError, BrokenPipeError, ConnectionError):
                return

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: bytes,
        content_type: str,
        include_body: bool,
        *,
        cache_control: str = "no-store",
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
        handler.end_headers()
        if include_body:
            handler.wfile.write(payload)
