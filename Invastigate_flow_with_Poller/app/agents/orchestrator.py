from __future__ import annotations

import time

from app.core.logging import logger
from app.agents.normalization_agent import NormalizationAgent
from app.agents.correlation_agent import CorrelationAgent
from app.agents.error_analysis_agent import ErrorAnalysisAgent
from app.agents.rca_agent import RCAAgent
from app.agents.recommendation_agent import RecommendationAgent
from app.models.normalization import NormalizationRequest, NormalizationResponse, ErrorType
from app.models.correlation import CorrelationRequest, CorrelationResponse
from app.models.error_analysis import ErrorAnalysisRequest, ErrorAnalysisResponse
from app.models.rca import RCARequest, RCAResponse
from app.models.recommendation import RecommendationRequest, RecommendationResponse
from app.models.orchestrator import (
    InvestigationRequest,
    InvestigationResponse,
    PipelineStep,
)
from app.services.trace_store import TraceStore
from app.services.event_bus import get_event_bus


class Orchestrator:
    """
    Pipeline orchestrator — chains all 5 agents and stores every
    agent's input/output in SQLite keyed by trace_id.
    """

    def __init__(self) -> None:
        self._normalization = NormalizationAgent()
        self._correlation = CorrelationAgent()
        self._error_analysis = ErrorAnalysisAgent()
        self._rca = RCAAgent()
        self._recommendation = RecommendationAgent()
        self._store = TraceStore()
        self._bus = get_event_bus()

    async def investigate(self, request: InvestigationRequest) -> InvestigationResponse:
        pipeline_start = time.perf_counter()
        steps: list[PipelineStep] = []
        trace_id = request.trace_id
        bus = self._bus

        norm_resp: NormalizationResponse | None = None
        corr_resp: CorrelationResponse | None = None
        ea_resp: ErrorAnalysisResponse | None = None
        rca_resp: RCAResponse | None = None
        rec_resp: RecommendationResponse | None = None

        # Create trace row
        try:
            await self._store.create_trace(trace_id, request.agent_name, request.timestamp)
        except Exception as exc:
            logger.warning("Failed to create trace row: %s", exc)

        await bus.publish(trace_id, {
            "type": "pipeline_started",
            "trace_id": trace_id,
            "agent_name": request.agent_name,
            "timestamp": request.timestamp,
        })

        # ── Step 1: Normalization ─────────────────────────────────────
        logger.info("Pipeline [%s] | Step 1/5 — Normalization", trace_id)
        step_start = time.perf_counter()
        norm_req = NormalizationRequest(
            timestamp=request.timestamp,
            trace_id=trace_id,
            agent_name=request.agent_name,
        )
        await bus.publish(trace_id, {
            "type": "step_started", "agent": "normalization", "step": 1,
            "input": norm_req.model_dump(mode="json"),
        })
        try:
            norm_resp = await self._normalization.normalize(norm_req)
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="normalization", status="completed", processing_time_ms=round(step_ms, 2)))
            await self._persist(trace_id, "normalization", norm_req, norm_resp)
            await bus.publish(trace_id, {
                "type": "step_completed", "agent": "normalization", "step": 1,
                "status": "completed", "processing_time_ms": round(step_ms, 2),
                "input": norm_req.model_dump(mode="json"),
                "output": norm_resp.model_dump(mode="json"),
                "data_sources": [norm_resp.data_source.value.lower()],
                "logs_count": norm_resp.raw_log_count,
                "confidence": norm_resp.incident.confidence,
            })
        except Exception as exc:
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="normalization", status="failed", processing_time_ms=round(step_ms, 2), error=str(exc)))
            await self._persist(trace_id, "normalization", norm_req)
            await bus.publish(trace_id, {
                "type": "step_failed", "agent": "normalization", "step": 1,
                "error": str(exc), "processing_time_ms": round(step_ms, 2),
            })
            return await self._finalize(trace_id, steps, pipeline_start, False, norm_resp=norm_resp, bus=bus)

        # Short-circuit NO_ERROR
        if norm_resp.incident.error_type == ErrorType.NO_ERROR:
            logger.info("Pipeline [%s] | NO_ERROR — stopping early", trace_id)
            return await self._finalize(trace_id, steps, pipeline_start, True, norm_resp=norm_resp, bus=bus)

        # ── Step 2: Correlation ───────────────────────────────────────
        logger.info("Pipeline [%s] | Step 2/5 — Correlation", trace_id)
        step_start = time.perf_counter()
        corr_req = CorrelationRequest(
            incident=norm_resp.incident, trace_id=trace_id, agent_name=request.agent_name,
        )
        await bus.publish(trace_id, {
            "type": "step_started", "agent": "correlation", "step": 2,
            "input": corr_req.model_dump(mode="json"),
        })
        try:
            corr_resp = await self._correlation.correlate(corr_req)
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="correlation", status="completed", processing_time_ms=round(step_ms, 2)))
            await self._persist(trace_id, "correlation", corr_req, corr_resp)
            await bus.publish(trace_id, {
                "type": "step_completed", "agent": "correlation", "step": 2,
                "status": "completed", "processing_time_ms": round(step_ms, 2),
                "input": corr_req.model_dump(mode="json"),
                "output": corr_resp.model_dump(mode="json"),
                "data_sources": corr_resp.data_sources,
                "logs_count": corr_resp.total_logs_analyzed,
                "confidence": corr_resp.correlation.root_cause_candidate.confidence,
            })
        except Exception as exc:
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="correlation", status="failed", processing_time_ms=round(step_ms, 2), error=str(exc)))
            await self._persist(trace_id, "correlation", corr_req)
            await bus.publish(trace_id, {
                "type": "step_failed", "agent": "correlation", "step": 2,
                "error": str(exc), "processing_time_ms": round(step_ms, 2),
            })
            return await self._finalize(trace_id, steps, pipeline_start, False, norm_resp=norm_resp, bus=bus)

        # ── Step 3: Error Analysis ────────────────────────────────────
        logger.info("Pipeline [%s] | Step 3/5 — Error Analysis", trace_id)
        step_start = time.perf_counter()
        ea_req = ErrorAnalysisRequest(
            correlation=corr_resp.correlation, incident=norm_resp.incident,
            trace_id=trace_id, agent_name=request.agent_name,
        )
        await bus.publish(trace_id, {
            "type": "step_started", "agent": "error_analysis", "step": 3,
            "input": ea_req.model_dump(mode="json"),
        })
        try:
            ea_resp = await self._error_analysis.analyze(ea_req)
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="error_analysis", status="completed", processing_time_ms=round(step_ms, 2)))
            await self._persist(trace_id, "error_analysis", ea_req, ea_resp)
            await bus.publish(trace_id, {
                "type": "step_completed", "agent": "error_analysis", "step": 3,
                "status": "completed", "processing_time_ms": round(step_ms, 2),
                "input": ea_req.model_dump(mode="json"),
                "output": ea_resp.model_dump(mode="json"),
                "data_sources": ea_resp.data_sources,
                "logs_count": ea_resp.total_logs_analyzed,
                "confidence": ea_resp.analysis.confidence,
            })
        except Exception as exc:
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="error_analysis", status="failed", processing_time_ms=round(step_ms, 2), error=str(exc)))
            await self._persist(trace_id, "error_analysis", ea_req)
            await bus.publish(trace_id, {
                "type": "step_failed", "agent": "error_analysis", "step": 3,
                "error": str(exc), "processing_time_ms": round(step_ms, 2),
            })
            return await self._finalize(trace_id, steps, pipeline_start, False, norm_resp=norm_resp, corr_resp=corr_resp, bus=bus)

        # ── Step 4: RCA ───────────────────────────────────────────────
        logger.info("Pipeline [%s] | Step 4/5 — RCA", trace_id)
        step_start = time.perf_counter()
        rca_req = RCARequest(
            error_analysis=ea_resp.analysis, rca_target=ea_resp.rca_target,
            incident=norm_resp.incident, trace_id=trace_id, agent_name=request.agent_name,
        )
        await bus.publish(trace_id, {
            "type": "step_started", "agent": "rca", "step": 4,
            "input": rca_req.model_dump(mode="json"),
        })
        try:
            rca_resp = await self._rca.analyze_root_cause(rca_req)
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="rca", status="completed", processing_time_ms=round(step_ms, 2)))
            await self._persist(trace_id, "rca", rca_req, rca_resp)
            await bus.publish(trace_id, {
                "type": "step_completed", "agent": "rca", "step": 4,
                "status": "completed", "processing_time_ms": round(step_ms, 2),
                "input": rca_req.model_dump(mode="json"),
                "output": rca_resp.model_dump(mode="json"),
                "data_sources": rca_resp.data_sources,
                "logs_count": rca_resp.total_logs_analyzed,
                "confidence": rca_resp.rca.confidence,
            })
        except Exception as exc:
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="rca", status="failed", processing_time_ms=round(step_ms, 2), error=str(exc)))
            await self._persist(trace_id, "rca", rca_req)
            await bus.publish(trace_id, {
                "type": "step_failed", "agent": "rca", "step": 4,
                "error": str(exc), "processing_time_ms": round(step_ms, 2),
            })
            return await self._finalize(trace_id, steps, pipeline_start, False, norm_resp=norm_resp, corr_resp=corr_resp, ea_resp=ea_resp, bus=bus)

        # ── Step 5: Recommendation ────────────────────────────────────
        logger.info("Pipeline [%s] | Step 5/5 — Recommendation", trace_id)
        step_start = time.perf_counter()
        rec_req = RecommendationRequest(
            error_analysis=ea_resp.analysis, rca=rca_resp.rca, agent_name=request.agent_name,
        )
        await bus.publish(trace_id, {
            "type": "step_started", "agent": "recommendation", "step": 5,
            "input": rec_req.model_dump(mode="json"),
        })
        try:
            rec_resp = await self._recommendation.recommend(rec_req)
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="recommendation", status="completed", processing_time_ms=round(step_ms, 2)))
            await self._persist(trace_id, "recommendation", rec_req, rec_resp)
            await bus.publish(trace_id, {
                "type": "step_completed", "agent": "recommendation", "step": 5,
                "status": "completed", "processing_time_ms": round(step_ms, 2),
                "input": rec_req.model_dump(mode="json"),
                "output": rec_resp.model_dump(mode="json"),
                "data_sources": [], "logs_count": 0,
                "confidence": None,
            })
        except Exception as exc:
            step_ms = (time.perf_counter() - step_start) * 1000
            steps.append(PipelineStep(agent="recommendation", status="failed", processing_time_ms=round(step_ms, 2), error=str(exc)))
            await self._persist(trace_id, "recommendation", rec_req)
            await bus.publish(trace_id, {
                "type": "step_failed", "agent": "recommendation", "step": 5,
                "error": str(exc), "processing_time_ms": round(step_ms, 2),
            })
            return await self._finalize(trace_id, steps, pipeline_start, False, norm_resp=norm_resp, corr_resp=corr_resp, ea_resp=ea_resp, rca_resp=rca_resp, bus=bus)

        # ── Done ──────────────────────────────────────────────────────
        logger.info("Pipeline [%s] | All 5 steps completed", trace_id)
        return await self._finalize(
            trace_id, steps, pipeline_start, True,
            norm_resp=norm_resp, corr_resp=corr_resp, ea_resp=ea_resp,
            rca_resp=rca_resp, rec_resp=rec_resp, bus=bus,
        )

    # ── Helpers ───────────────────────────────────────────────────────

    async def _persist(self, trace_id: str, agent: str, input_model=None, output_model=None) -> None:
        """Save agent I/O to SQLite — failures are logged but don't break the pipeline."""
        try:
            input_data = input_model.model_dump(mode="json") if input_model else None
            output_data = output_model.model_dump(mode="json") if output_model else None
            await self._store.save_agent_io(trace_id, agent, input_data, output_data)
        except Exception as exc:
            logger.warning("Failed to persist %s I/O for %s: %s", agent, trace_id, exc)

    async def _finalize(
        self, trace_id: str, steps: list[PipelineStep], pipeline_start: float,
        completed: bool, **agent_responses,
    ) -> InvestigationResponse:
        total_ms = (time.perf_counter() - pipeline_start) * 1000
        try:
            await self._store.complete_trace(trace_id, "completed" if completed else "failed")
        except Exception as exc:
            logger.warning("Failed to finalize trace %s: %s", trace_id, exc)

        response = InvestigationResponse(
            trace_id=trace_id,
            normalization=agent_responses.get("norm_resp"),
            correlation=agent_responses.get("corr_resp"),
            error_analysis=agent_responses.get("ea_resp"),
            rca=agent_responses.get("rca_resp"),
            recommendations=agent_responses.get("rec_resp"),
            pipeline_steps=steps,
            total_processing_time_ms=round(total_ms, 2),
            completed=completed,
        )

        bus = agent_responses.get("bus", self._bus)
        await bus.publish(trace_id, {
            "type": "pipeline_completed",
            "trace_id": trace_id,
            "completed": completed,
            "total_processing_time_ms": round(total_ms, 2),
            "steps": [s.model_dump() for s in steps],
        })

        return response
