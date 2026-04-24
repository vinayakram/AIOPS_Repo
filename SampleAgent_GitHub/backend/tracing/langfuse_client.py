"""
Langfuse tracing wrapper for the SampleAgent pipeline.

Each call to /api/query creates one Langfuse trace with child spans for:
  pubmed_fetch → embedding → pagerank → faiss_retrieval → openai_generation

Compatible with Langfuse SDK v4+.
All timings are also stored locally in SQLite (trace_logs table) so the
in-app dashboard works even when Langfuse is not configured.
"""

import time
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timezone

try:
    from langfuse import Langfuse
    try:
        from langfuse.types import TraceContext as LFTraceContext
        LANGFUSE_V4 = True
    except ImportError:
        LANGFUSE_V4 = False
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    LANGFUSE_V4 = False

from ..config import settings


# ── helpers ──────────────────────────────────────────────────────────────────

def _ms_to_dt(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ── step timer ────────────────────────────────────────────────────────────────

class StepTimer:
    """Tracks start/end time and metadata for a single pipeline step."""

    def __init__(self, name: str):
        self.name = name
        self.start_ms: float = time.time() * 1000
        self.end_ms: Optional[float] = None
        self.input: dict = {}
        self.output: dict = {}

    def end(self, output: dict = None):
        self.end_ms = time.time() * 1000
        self.output = output or {}

    @property
    def duration_ms(self) -> float:
        end = self.end_ms or (time.time() * 1000)
        return end - self.start_ms

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 1),
            "input": self.input,
            "output": self.output,
        }


# ── trace context ─────────────────────────────────────────────────────────────

class TraceContext:
    """Holds all timing/metadata for one pipeline execution."""

    def __init__(
        self,
        trace_id: str,
        query: str,
        user_id: str = None,
        lf_client=None,
    ):
        self.trace_id = trace_id
        self.query = query
        self.user_id = user_id
        self.start_ms = time.time() * 1000
        self.steps: Dict[str, StepTimer] = {}
        # Langfuse v4 objects
        self._lf_client = lf_client            # Langfuse instance
        self._lf_root_obs = None               # root observation (span)
        self._lf_root_cm = None                # root context manager (keeps root "current")
        self._lf_span_cms: Dict[str, Any] = {} # name → context manager for each step

    # ── step management ──────────────────────────────────────────────────────

    def start_step(self, name: str, input_data: dict = None) -> StepTimer:
        timer = StepTimer(name)
        timer.input = input_data or {}
        self.steps[name] = timer

        if self._lf_client and LANGFUSE_V4 and self._lf_root_cm is not None:
            try:
                # Use start_as_current_observation so child spans nest correctly
                # inside the root span via thread-local context stack.
                # Each step cm.__enter__ pushes, cm.__exit__ pops at end_step.
                cm = self._lf_client.start_as_current_observation(
                    name=name,
                    as_type="span",
                    input=input_data or {},
                )
                cm.__enter__()
                self._lf_span_cms[name] = cm
            except Exception as e:
                print(f"[Langfuse] start_step({name}) error: {e}")

        return timer

    def end_step(self, name: str, output: dict = None):
        if name in self.steps:
            self.steps[name].end(output)

        if name in self._lf_span_cms:
            try:
                cm = self._lf_span_cms.pop(name)
                # Update output on the current observation before exiting
                if hasattr(cm, "__self__") or hasattr(cm, "_observation"):
                    obs = getattr(cm, "_observation", None) or getattr(cm, "__self__", None)
                    if obs and hasattr(obs, "update"):
                        obs.update(output=output or {})
                cm.__exit__(None, None, None)
            except Exception as e:
                print(f"[Langfuse] end_step({name}) error: {e}")

    # ── convenience ──────────────────────────────────────────────────────────

    @property
    def total_duration_ms(self) -> float:
        return (time.time() * 1000) - self.start_ms

    def steps_summary(self) -> list:
        return [s.to_dict() for s in self.steps.values()]


# ── tracer singleton ─────────────────────────────────────────────────────────

class LangfuseTracer:
    def __init__(self):
        self._client = None
        self.enabled = False
        self._init()

    def _init(self):
        if not LANGFUSE_AVAILABLE:
            print("[Langfuse] SDK not installed — pip install langfuse")
            return
        if not LANGFUSE_V4:
            print("[Langfuse] langfuse.types.TraceContext not found — SDK v4+ required")
            return
        sk = settings.LANGFUSE_SECRET_KEY
        pk = settings.LANGFUSE_PUBLIC_KEY
        if not (sk and pk):
            print("[Langfuse] Keys not set — tracing to Langfuse disabled (local traces still saved)")
            return
        try:
            self._client = Langfuse(
                secret_key=sk,
                public_key=pk,
                host=settings.LANGFUSE_HOST,
            )
            self.enabled = True
            print(f"[Langfuse] Connected to {settings.LANGFUSE_HOST}")
        except Exception as e:
            print(f"[Langfuse] Connection failed: {e}")

    # ── public API ────────────────────────────────────────────────────────────

    def new_trace(self, query: str, user_id: str = None) -> TraceContext:
        # v4 requires 32 lowercase hex chars, no dashes
        trace_id = uuid.uuid4().hex
        ctx = TraceContext(
            trace_id=trace_id,
            query=query,
            user_id=user_id,
            lf_client=self._client if self.enabled else None,
        )
        if self.enabled and self._client:
            try:
                root_cm = self._client.start_as_current_observation(
                    trace_context=LFTraceContext(trace_id=trace_id),
                    name="sample-agent",
                    as_type="span",
                    input={"query": query},
                    metadata={"user_id": user_id, "tags": ["sample-agent", "pubmed", "pagerank"]},
                )
                root_obs = root_cm.__enter__()
                ctx._lf_root_obs = root_obs
                ctx._lf_root_cm = root_cm
            except Exception as e:
                print(f"[Langfuse] new_trace error: {e}")
        return ctx

    def finish_trace(self, ctx: TraceContext, result: dict, error: str = None):
        """Call after the pipeline completes (or fails) to close the Langfuse trace.

        Pass error=<message> when the pipeline raised an exception so that
        Langfuse records the trace with level ERROR and the sync correctly
        derives status='error' for the local AIops copy.
        """
        if not (ctx._lf_root_cm and self._client):
            return
        try:
            # Close any child spans that were left open by an exception.
            # When there is an error, mark the unclosed span with ERROR level
            # so the Langfuse sync can detect it via the observations array.
            for name, cm in list(ctx._lf_span_cms.items()):
                try:
                    if error:
                        obs = getattr(cm, "_observation", None) or getattr(cm, "__self__", None)
                        if obs and hasattr(obs, "update"):
                            obs.update(level="ERROR", status_message=error)
                    cm.__exit__(None, None, None)
                except Exception:
                    pass
            ctx._lf_span_cms.clear()

            if ctx._lf_root_obs and hasattr(ctx._lf_root_obs, "update"):
                update_kwargs: dict = {
                    "metadata": {"total_duration_ms": round(ctx.total_duration_ms, 1)},
                }
                if error:
                    update_kwargs["level"] = "ERROR"
                    update_kwargs["status_message"] = error
                    update_kwargs["output"] = {"error": error}
                else:
                    update_kwargs["output"] = {
                        "answer_preview": result.get("answer", "")[:300],
                        "total_fetched": result.get("total_fetched", 0),
                        "pagerank_method": result.get("pagerank_method", "n/a"),
                        "sources_count": len(result.get("sources", [])),
                    }
                ctx._lf_root_obs.update(**update_kwargs)

            ctx._lf_root_cm.__exit__(None, None, None)
            self._client.flush()
        except Exception as e:
            print(f"[Langfuse] finish_trace error: {e}")

    def add_generation(
        self,
        ctx: TraceContext,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        answer: str,
    ):
        """Record the OpenAI generation as a Langfuse Generation object."""
        if not (self.enabled and self._client and LANGFUSE_V4):
            return
        try:
            # add_generation is called while root span is still "current" on the
            # thread-local stack, so start_as_current_observation auto-parents it
            gen_cm = self._client.start_as_current_observation(
                name="openai-answer-generation",
                as_type="generation",
                input={"model": model},
                model=model,
            )
            gen_obs = gen_cm.__enter__()
            gen_obs.update(
                output=answer[:500],
                usage_details={"input": prompt_tokens, "output": completion_tokens},
            )
            gen_cm.__exit__(None, None, None)
        except Exception as e:
            print(f"[Langfuse] add_generation error: {e}")


# Module-level singleton — import this everywhere
tracer = LangfuseTracer()
