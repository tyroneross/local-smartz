from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from localsmartz.benchmarking import RunMetrics, measure_research_request, summarize_runs


class _BenchmarkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path != "/api/research":
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        events = [
            {"type": "status", "stage": "loading_model", "model": "qwen3:8b-q4_K_M"},
            {"type": "status", "stage": "ready", "model": "qwen3:8b-q4_K_M", "warmup_ms": 7},
            {"type": "text", "content": "hello "},
            {"type": "text", "content": "world"},
            {"type": "done", "duration_ms": 12},
        ]
        for event in events:
            payload = f"data: {json.dumps(event)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()
            time.sleep(0.01)

    def log_message(self, *_args, **_kwargs):  # noqa: D401
        return


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BenchmarkHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread


def test_measure_research_request_collects_sse_milestones():
    srv, _thread = _server()
    base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        result = measure_research_request(base_url, prompt="hello", run_index=1, timeout_s=5)
    finally:
        srv.shutdown()

    assert result.run_index == 1
    assert result.first_byte_ms is not None
    assert result.first_event_ms is not None
    assert result.first_stage_ms is None
    assert result.first_text_ms is not None
    assert result.wall_duration_ms is not None
    assert result.server_duration_ms == 12
    assert result.warmup_ms == 7
    assert result.text_chars == len("hello world")
    assert result.event_counts["status"] == 2
    assert result.event_counts["text"] == 2
    assert result.event_counts["done"] == 1
    assert result.error is None


def test_measure_research_request_can_stop_after_first_stage():
    class _StageHandler(_BenchmarkHandler):
        def do_POST(self):  # noqa: N802
            if self.path != "/api/research":
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            events = [
                {"type": "status", "stage": "loading_model", "model": "qwen3:8b-q4_K_M"},
                {"type": "stage", "stage": "researcher"},
                {"type": "text", "content": "later"},
                {"type": "done", "duration_ms": 99},
            ]
            for event in events:
                payload = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()
                time.sleep(0.01)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StageHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        result = measure_research_request(
            base_url,
            prompt="hello",
            run_index=1,
            timeout_s=5,
            stop_after="first_stage",
        )
    finally:
        srv.shutdown()

    assert result.first_stage_ms is not None
    assert result.wall_duration_ms == result.first_stage_ms
    assert result.server_duration_ms is None


def test_summarize_runs_reports_followup_delta():
    summary = summarize_runs(
        [
            RunMetrics(
                run_index=1,
                wall_duration_ms=1200,
                first_stage_ms=500,
                first_text_ms=800,
                text_chars=100,
            ),
            RunMetrics(
                run_index=2,
                wall_duration_ms=700,
                first_stage_ms=320,
                first_text_ms=450,
                text_chars=100,
            ),
            RunMetrics(
                run_index=3,
                wall_duration_ms=650,
                first_stage_ms=300,
                first_text_ms=430,
                text_chars=100,
            ),
        ],
        startup_ms=2500,
    )

    assert summary["startup_ms"] == 2500
    assert summary["run_count"] == 3
    assert summary["median_wall_ms"] == 700.0
    assert summary["median_first_stage_ms"] == 320.0
    assert summary["followup_median_wall_ms"] == 675.0
    assert summary["first_run_minus_followup_ms"] == 525.0
