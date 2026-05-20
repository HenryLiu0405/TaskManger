"""Minimal Unitree simulator backend and HTTP server wrapper.

Provides an in-process backend `UnitreeSimBackend` that wraps the existing
`FakeRobotService` and implements the same `execute`/`snapshot` interface.

Also supplies a small HTTP server for running the simulator as a separate
process (endpoints: POST /api/command, GET /api/snapshot).
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

from phi_robot.fake_robot_service import FakeRobotService, DEFAULT_FAKE_SPEC


logger = logging.getLogger("phi_robot.unitree_sim")


class UnitreeSimBackend:
    """In-process backend suitable as a `RobotBackend` implementation.

    Methods mirror `FakeRobotService` but provide a small compatibility layer.
    """

    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        spec = dict(spec or DEFAULT_FAKE_SPEC)
        self._svc = FakeRobotService.from_spec(spec)

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> "UnitreeSimBackend":
        return cls(spec=spec)

    def execute(self, tool: str, args: dict[str, Any], *, request_id: str, goal_id: str, step_id: str) -> dict[str, Any]:
        return self._svc.execute(tool, args, request_id=request_id, goal_id=goal_id, step_id=step_id)

    def reset(self) -> None:
        self._svc.reset()

    def snapshot(self) -> dict[str, Any]:
        return self._svc.snapshot()


class _Handler(BaseHTTPRequestHandler):
    server_version = "phi_robot-unitree-sim/0.1"

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _print_req(self, label: str, tool: str = "", step_id: str = "") -> None:
        """Print structured request info to stdout (terminal-visible)."""
        if tool:
            print(f"  [sim] <<< {label} tool={tool}  step_id={step_id}", flush=True)
        else:
            print(f"  [sim] <<< {label}", flush=True)

    def _print_resp(self, result: dict) -> None:
        status = result.get("status", "?")
        symbol = "+" if status == "ok" else "!"
        err = result.get("error_code", "")
        msg = result.get("message", "")
        if err:
            print(f"  [sim] >>> {symbol} status={status}  error={err}  {msg}", flush=True)
        else:
            print(f"  [sim] >>> {symbol} status={status}  {msg}", flush=True)

    def do_POST(self):
        if self.path == "/api/reset":
            return self.do_POST_reset()

        if self.path != "/api/command":
            return self._send_json({"error": "not found"}, status=404)

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            return self._send_json({"error": "invalid json", "detail": str(exc)}, status=400)

        tool = payload.get("tool")
        request_id = payload.get("request_id", "req-local")
        goal_id = payload.get("goal_id", "goal-local")
        step_id = payload.get("step_id", "step-local")
        args = payload.get("args", {})

        self._print_req("POST /api/command", tool=tool, step_id=step_id)

        try:
            resp = self.server.backend.execute(tool, args, request_id=request_id, goal_id=goal_id, step_id=step_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("backend execute failed")
            return self._send_json({"request_id": request_id, "status": "error", "error_code": "INTERNAL_ERROR", "message": str(exc)}, status=500)

        self._print_resp(resp)
        return self._send_json(resp)

    def do_GET(self):
        if self.path == "/api/snapshot":
            try:
                resp = self.server.backend.snapshot()
            except Exception as exc:
                logger.exception("snapshot failed")
                return self._send_json({"error": str(exc)}, status=500)
            return self._send_json(resp)
        return self._send_json({"error": "not found"}, status=404)

    def do_POST_reset(self):
        self._print_req("POST /api/reset")
        try:
            self.server.backend.reset()
            result = {"status": "ok", "message": "reset complete"}
            self._print_resp(result)
            return self._send_json(result)
        except Exception as exc:
            logger.exception("reset failed")
            return self._send_json({"error": str(exc)}, status=500)

    def log_message(self, format: str, *args) -> None:
        # Suppress default HTTP request logging (our _print_req/_print_resp replaces it)
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server(host: str = "0.0.0.0", port: int = 8080, spec: dict[str, Any] | None = None) -> None:
    backend = UnitreeSimBackend(spec=spec)
    server = ThreadedHTTPServer((host, port), _Handler)
    server.backend = backend
    logger.info("starting UnitreeSim HTTP server on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down UnitreeSim server")
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run UnitreeSim HTTP server (phi_robot)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_server(host=args.host, port=args.port)
