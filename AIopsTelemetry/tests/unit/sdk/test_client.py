"""
Unit tests for aiops_sdk/client.py

TDD template — each test block is a stub showing WHAT to test.
Fill in assertions as you build the implementation.

Coverage target: 100% (see CLAUDE.md §4a)
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from aiops_sdk.client import AIopsClient, TraceBuffer
from aiops_sdk.config import AIopsConfig


# ── TraceBuffer ───────────────────────────────────────────────────────────────

class TestTraceBuffer:
    def test_initial_status_is_ok(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        assert buf.status == "ok"

    def test_input_preview_truncated_to_500_chars(self):
        long_input = "x" * 1000
        buf = TraceBuffer(trace_id="t1", app_name="test-app", input_preview=long_input)
        assert len(buf.input_preview) == 500

    def test_finish_sets_ended_at(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        assert buf.ended_at is None
        buf.finish()
        assert buf.ended_at is not None

    def test_finish_sets_status(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        buf.finish(status="error")
        assert buf.status == "error"

    def test_total_duration_ms_none_before_finish(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        assert buf.total_duration_ms() is None

    def test_total_duration_ms_positive_after_finish(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        buf.finish()
        assert buf.total_duration_ms() >= 0

    def test_to_payload_contains_required_keys(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        buf.finish()
        payload = buf.to_payload()
        for key in ("id", "app_name", "status", "started_at", "spans", "logs"):
            assert key in payload

    def test_add_span_appended_to_spans(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        span = MagicMock()
        buf.add_span(span)
        assert span in buf.spans

    def test_log_appended(self):
        buf = TraceBuffer(trace_id="t1", app_name="test-app")
        buf.log("INFO", "hello")
        assert len(buf.logs) == 1
        assert buf.logs[0]["message"] == "hello"
        assert buf.logs[0]["level"] == "INFO"


# ── AIopsClient singleton ─────────────────────────────────────────────────────

class TestAIopsClientConfigure:
    def setup_method(self):
        # Reset singleton between tests
        AIopsClient._instance = None

    def test_configure_returns_instance(self):
        client = AIopsClient.configure(server_url="http://localhost:7000", app_name="test")
        assert isinstance(client, AIopsClient)

    def test_get_returns_same_instance_after_configure(self):
        client = AIopsClient.configure(server_url="http://localhost:7000", app_name="test")
        assert AIopsClient.get() is client

    def test_get_creates_default_instance_when_not_configured(self):
        instance = AIopsClient.get()
        assert isinstance(instance, AIopsClient)


# ── Trace lifecycle ───────────────────────────────────────────────────────────

class TestAIopsClientTraceLifecycle:
    def setup_method(self):
        AIopsClient._instance = None
        self.client = AIopsClient.configure(server_url="http://localhost:7000", app_name="test")

    def test_start_trace_returns_string_id(self):
        tid = self.client.start_trace()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_start_trace_uses_provided_trace_id(self):
        tid = self.client.start_trace(trace_id="custom-id")
        assert tid == "custom-id"

    def test_start_trace_creates_buffer(self):
        tid = self.client.start_trace()
        assert tid in self.client._buffers

    def test_finish_trace_removes_buffer(self):
        tid = self.client.start_trace()
        with patch.object(self.client, "_flush"):
            self.client.finish_trace(tid)
        assert tid not in self.client._buffers

    def test_finish_trace_calls_flush(self):
        tid = self.client.start_trace()
        with patch.object(self.client, "_flush") as mock_flush:
            self.client.finish_trace(tid)
        mock_flush.assert_called_once()

    def test_finish_trace_unknown_id_does_not_raise(self):
        self.client.finish_trace("nonexistent-id")  # should not raise

    def test_add_span_to_active_trace(self):
        tid = self.client.start_trace()
        span = MagicMock()
        span.to_dict.return_value = {}
        self.client.add_span(tid, span)
        assert span in self.client._buffers[tid].spans

    def test_log_appended_to_active_trace(self):
        tid = self.client.start_trace()
        self.client.log(tid, "WARNING", "something happened")
        assert len(self.client._buffers[tid].logs) == 1


# ── Thread safety ─────────────────────────────────────────────────────────────

class TestAIopsClientThreadSafety:
    def setup_method(self):
        AIopsClient._instance = None
        self.client = AIopsClient.configure(server_url="http://localhost:7000", app_name="test")

    def test_concurrent_start_trace_no_collision(self):
        trace_ids = []
        errors = []

        def _start():
            try:
                tid = self.client.start_trace()
                trace_ids.append(tid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_start) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(trace_ids) == 50
        assert len(set(trace_ids)) == 50  # all unique


# ── Flush (HTTP) ──────────────────────────────────────────────────────────────

class TestAIopsClientFlush:
    def setup_method(self):
        AIopsClient._instance = None
        self.client = AIopsClient.configure(server_url="http://localhost:7000", app_name="test")

    @patch("aiops_sdk.client.requests.post")
    def test_flush_posts_to_ingest_url(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201)
        buf = TraceBuffer("t1", "test-app")
        buf.finish()
        self.client._flush(buf)
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "ingest" in call_url

    @patch("aiops_sdk.client.requests.post")
    def test_flush_does_not_raise_on_server_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="Internal Server Error")
        buf = TraceBuffer("t1", "test-app")
        buf.finish()
        self.client._flush(buf)  # must not raise

    @patch("aiops_sdk.client.requests.post", side_effect=ConnectionError("refused"))
    def test_flush_does_not_raise_on_network_error(self, _mock_post):
        buf = TraceBuffer("t1", "test-app")
        buf.finish()
        self.client._flush(buf)  # must not raise


# ── Disabled client ───────────────────────────────────────────────────────────

class TestAIopsClientDisabled:
    def setup_method(self):
        AIopsClient._instance = None
        self.client = AIopsClient(AIopsConfig(enabled=False))

    def test_start_trace_returns_id_when_disabled(self):
        tid = self.client.start_trace()
        assert isinstance(tid, str)

    def test_finish_trace_does_not_flush_when_disabled(self):
        with patch.object(self.client, "_flush") as mock_flush:
            tid = self.client.start_trace()
            self.client.finish_trace(tid)
        mock_flush.assert_not_called()
