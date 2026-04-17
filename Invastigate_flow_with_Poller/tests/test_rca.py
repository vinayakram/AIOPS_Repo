"""
Tests for the RCA Agent.

Run with: pytest tests/test_rca.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.normalization import Entities, ErrorType, NormalizedIncident
from app.models.correlation import AnalysisDomain
from app.models.error_analysis import (
    ErrorAnalysisResult,
    ErrorCategory,
    ErrorDetail,
    ErrorImpact,
    ErrorPattern,
    ErrorSeverity,
)
from app.models.rca import (
    CausalLink,
    CausalLinkType,
    ContributingFactor,
    FailureTimeline,
    FiveWhyAnalysis,
    RCARequest,
    RCAResponse,
    RCAResult,
    RootCause,
    RootCauseCategory,
    WhyStep,
)


client = TestClient(app)


# ── Helpers: build sample data ────────────────────────────────────────


def _make_incident(**overrides) -> NormalizedIncident:
    defaults = dict(
        error_type=ErrorType.AI_AGENT,
        error_summary="LLM access disabled in agent summarizer-v2",
        timestamp="2026-04-10T04:19:55.204Z",
        confidence=0.9,
        entities=Entities(
            agent_id="summarizer-v2",
            service="openai_generation",
            trace_id="trace-abc-123",
        ),
        signals=["llm_error", "disabled"],
    )
    defaults.update(overrides)
    return NormalizedIncident(**defaults)


def _make_error_analysis(**overrides) -> ErrorAnalysisResult:
    defaults = dict(
        analysis_summary="LLM access disabled causing agent pipeline failure",
        analysis_target=AnalysisDomain.AGENT,
        errors=[
            ErrorDetail(
                error_id="ERR-001",
                category=ErrorCategory.LLM_FAILURE,
                severity=ErrorSeverity.CRITICAL,
                component="openai_generation",
                error_message="LLM access is disabled for this account",
                timestamp="2026-04-10T04:19:57.963Z",
                evidence="GENERATION 'openai_generation' | status: ERROR",
                source="langfuse",
            ),
            ErrorDetail(
                error_id="ERR-002",
                category=ErrorCategory.TOOL_CALL_FAILURE,
                severity=ErrorSeverity.HIGH,
                component="summarizer-v2",
                error_message="Agent returned error output",
                timestamp="2026-04-10T04:19:57.964Z",
                evidence="Trace output contains error payload",
                source="langfuse",
            ),
        ],
        error_patterns=[
            ErrorPattern(
                pattern_name="Cascading LLM Failure",
                description="LLM disabled error propagated downstream",
                occurrence_count=2,
                affected_components=["openai_generation", "summarizer-v2"],
                error_ids=["ERR-001", "ERR-002"],
            ),
        ],
        error_impacts=[
            ErrorImpact(
                affected_service="summarizer-v2",
                impact_description="Complete agent failure",
                severity=ErrorSeverity.CRITICAL,
            ),
        ],
        error_propagation_path=[
            "LLM access disabled → openai_generation ERROR → summarizer-v2 error output"
        ],
        confidence=0.92,
    )
    defaults.update(overrides)
    return ErrorAnalysisResult(**defaults)


# ── Unit: Request Model ────────────────────────────────────────────────


class TestRCARequestModel:
    def test_valid_agent_target_with_trace_id(self):
        req = RCARequest(
            error_analysis=_make_error_analysis(),
            rca_target=AnalysisDomain.AGENT,
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.rca_target == AnalysisDomain.AGENT
        assert req.trace_id == "trace-abc-123"
        assert len(req.error_analysis.errors) == 2

    def test_valid_infra_target_without_trace_id(self):
        req = RCARequest(
            error_analysis=_make_error_analysis(
                analysis_target=AnalysisDomain.INFRA_LOGS,
            ),
            rca_target=AnalysisDomain.INFRA_LOGS,
            incident=_make_incident(error_type=ErrorType.INFRA),
            agent_name="retrieval-agent",
        )
        assert req.rca_target == AnalysisDomain.INFRA_LOGS
        assert req.trace_id is None

    def test_valid_unknown_target(self):
        req = RCARequest(
            error_analysis=_make_error_analysis(
                analysis_target=AnalysisDomain.UNKNOWN,
            ),
            rca_target=AnalysisDomain.UNKNOWN,
            incident=_make_incident(error_type=ErrorType.UNKNOWN),
            trace_id="trace-xyz-789",
            agent_name="planner-v1",
        )
        assert req.rca_target == AnalysisDomain.UNKNOWN

    def test_missing_error_analysis_raises(self):
        with pytest.raises(Exception):
            RCARequest(
                rca_target=AnalysisDomain.AGENT,
                incident=_make_incident(),
                agent_name="test",
            )

    def test_missing_rca_target_raises(self):
        with pytest.raises(Exception):
            RCARequest(
                error_analysis=_make_error_analysis(),
                incident=_make_incident(),
                agent_name="test",
            )

    def test_missing_incident_raises(self):
        with pytest.raises(Exception):
            RCARequest(
                error_analysis=_make_error_analysis(),
                rca_target=AnalysisDomain.AGENT,
                agent_name="test",
            )

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            RCARequest(
                error_analysis=_make_error_analysis(),
                rca_target=AnalysisDomain.AGENT,
                incident=_make_incident(),
            )


# ── Unit: Response Models ──────────────────────────────────────────────


class TestRCAResponseModels:
    def test_causal_link(self):
        cl = CausalLink(
            source_event="LLM access disabled at provider level",
            target_event="openai_generation span returned ERROR",
            link_type=CausalLinkType.DIRECT_CAUSE,
            evidence="Langfuse span status=ERROR with message: LLM access disabled",
        )
        assert cl.link_type == CausalLinkType.DIRECT_CAUSE

    def test_all_causal_link_types(self):
        for lt in CausalLinkType:
            cl = CausalLink(
                source_event="A",
                target_event="B",
                link_type=lt,
                evidence="test",
            )
            assert cl.link_type == lt

    def test_contributing_factor(self):
        cf = ContributingFactor(
            factor="No retry logic configured for LLM calls",
            component="summarizer-v2",
            evidence="Agent config shows max_retries=0",
            severity="secondary",
        )
        assert cf.component == "summarizer-v2"

    def test_root_cause(self):
        rc = RootCause(
            category=RootCauseCategory.LLM_PROVIDER,
            component="openai_generation",
            description="LLM provider disabled access for this account",
            evidence=["GENERATION span status=ERROR: LLM access is disabled"],
            error_ids=["ERR-001"],
            confidence=0.95,
        )
        assert rc.category == RootCauseCategory.LLM_PROVIDER
        assert rc.confidence == 0.95

    def test_root_cause_requires_evidence(self):
        with pytest.raises(Exception):
            RootCause(
                category=RootCauseCategory.UNKNOWN,
                component="test",
                description="test",
                evidence=[],
                confidence=0.5,
            )

    def test_root_cause_confidence_out_of_range(self):
        with pytest.raises(Exception):
            RootCause(
                category=RootCauseCategory.UNKNOWN,
                component="test",
                description="test",
                evidence=["test"],
                confidence=1.5,
            )

    def test_all_root_cause_categories(self):
        for cat in RootCauseCategory:
            rc = RootCause(
                category=cat,
                component="test",
                description="test",
                evidence=["test evidence"],
                confidence=0.5,
            )
            assert rc.category == cat

    def test_failure_timeline(self):
        ft = FailureTimeline(
            timestamp="2026-04-10T04:19:57.962Z",
            component="openai_generation",
            event="GENERATION span failed with LLM access disabled",
            is_root_cause=True,
        )
        assert ft.is_root_cause is True

    def test_failure_timeline_defaults(self):
        ft = FailureTimeline(
            timestamp="2026-04-10T04:19:57.964Z",
            component="summarizer-v2",
            event="Agent returned error output",
        )
        assert ft.is_root_cause is False

    def test_full_rca_result(self):
        result = RCAResult(
            rca_summary="The root cause is LLM provider access being disabled for the account used by openai_generation. This caused the generation span to fail immediately with zero tokens produced, which propagated downstream to the summarizer-v2 agent.",
            root_cause=RootCause(
                category=RootCauseCategory.LLM_PROVIDER,
                component="openai_generation",
                description="LLM provider disabled API access for this account, causing all generation requests to fail immediately",
                evidence=[
                    "GENERATION 'openai_generation' | status: ERROR | Error: LLM access is disabled",
                    "Tokens: in=0 out=0 — confirms no processing occurred",
                ],
                error_ids=["ERR-001"],
                confidence=0.95,
            ),
            causal_chain=[
                CausalLink(
                    source_event="LLM provider disabled account access",
                    target_event="openai_generation span returned ERROR status",
                    link_type=CausalLinkType.DIRECT_CAUSE,
                    evidence="GENERATION span status=ERROR, error_message=LLM access disabled",
                ),
                CausalLink(
                    source_event="openai_generation span ERROR",
                    target_event="summarizer-v2 trace returned error output",
                    link_type=CausalLinkType.DIRECT_CAUSE,
                    evidence="Trace output contains error payload from upstream generation failure",
                ),
            ],
            contributing_factors=[
                ContributingFactor(
                    factor="No fallback LLM provider configured",
                    component="summarizer-v2",
                    evidence="Agent config shows single LLM provider with no fallback",
                    severity="secondary",
                ),
            ],
            failure_timeline=[
                FailureTimeline(
                    timestamp="2026-04-10T04:19:57.962Z",
                    component="openai_generation",
                    event="GENERATION span started — immediately rejected by provider",
                    is_root_cause=True,
                ),
                FailureTimeline(
                    timestamp="2026-04-10T04:19:57.963Z",
                    component="openai_generation",
                    event="GENERATION span completed with ERROR status",
                    is_root_cause=False,
                ),
                FailureTimeline(
                    timestamp="2026-04-10T04:19:57.964Z",
                    component="summarizer-v2",
                    event="Agent trace completed with error in output",
                    is_root_cause=False,
                ),
            ],
            blast_radius=["openai_generation", "summarizer-v2"],
            five_why_analysis=FiveWhyAnalysis(
                problem_statement="summarizer-v2 failed on all traces — LLM access is disabled",
                whys=[
                    WhyStep(step=1, question="Why did the agent fail?",
                            answer="openai_generation returned ERROR on every call",
                            evidence="GENERATION span status=ERROR",
                            component="openai_generation"),
                    WhyStep(step=2, question="Why did openai_generation return ERROR?",
                            answer="LLM access is disabled for this account",
                            evidence="Error: LLM access is disabled (demo error mode)",
                            component="openai_generation"),
                    WhyStep(step=3, question="Why is LLM access disabled?",
                            answer="Service is running in demo error mode",
                            evidence="Error message references 'demo error mode'",
                            component="summarizer-v2"),
                    WhyStep(step=4, question="Why is demo error mode active?",
                            answer="Config flag toggled without re-enabling LLM access",
                            evidence="Error instructs 'Enable LLM Access' via UI",
                            component="summarizer-v2"),
                    WhyStep(step=5, question="Why was no pre-flight check in place?",
                            answer="No readiness gate validates LLM access before accepting requests",
                            evidence="No early-rejection log found — service accepted requests and failed at LLM call",
                            component="summarizer-v2"),
                ],
                fundamental_root_cause="Demo error mode was deployed without a readiness gate enforcing LLM access state",
            ),
            confidence=0.95,
        )
        assert len(result.causal_chain) == 2
        assert len(result.contributing_factors) == 1
        assert len(result.failure_timeline) == 3
        assert len(result.blast_radius) == 2
        assert result.root_cause.category == RootCauseCategory.LLM_PROVIDER
        assert result.confidence == 0.95

    def test_rca_result_requires_at_least_one_causal_link(self):
        with pytest.raises(Exception):
            RCAResult(
                rca_summary="test",
                root_cause=RootCause(
                    category=RootCauseCategory.UNKNOWN,
                    component="test",
                    description="test",
                    evidence=["test"],
                    confidence=0.5,
                ),
                causal_chain=[],
                confidence=0.5,
            )

    def test_rca_result_confidence_out_of_range(self):
        with pytest.raises(Exception):
            RCAResult(
                rca_summary="test",
                root_cause=RootCause(
                    category=RootCauseCategory.UNKNOWN,
                    component="test",
                    description="test",
                    evidence=["test"],
                    confidence=0.5,
                ),
                causal_chain=[
                    CausalLink(
                        source_event="A",
                        target_event="B",
                        link_type=CausalLinkType.DIRECT_CAUSE,
                        evidence="test",
                    ),
                ],
                confidence=1.5,
            )

    def test_rca_response_wrapper(self):
        result = RCAResult(
            rca_summary="DNS failure was the root cause",
            root_cause=RootCause(
                category=RootCauseCategory.DNS,
                component="coredns",
                description="CoreDNS SERVFAIL caused cascading failures",
                evidence=["dns_failures metric SERVFAIL rate > 0"],
                confidence=0.88,
            ),
            causal_chain=[
                CausalLink(
                    source_event="CoreDNS SERVFAIL",
                    target_event="vector-db unreachable",
                    link_type=CausalLinkType.DIRECT_CAUSE,
                    evidence="DNS failure preceded connection refused by 15s",
                ),
            ],
            blast_radius=["coredns", "vector-db", "retrieval-agent"],
            five_why_analysis=FiveWhyAnalysis(
                problem_statement="retrieval-agent failed — DNS resolution failures observed",
                whys=[
                    WhyStep(step=1, question="Why did retrieval-agent fail?",
                            answer="vector-db was unreachable",
                            evidence="Connection refused to vector-db",
                            component="retrieval-agent"),
                    WhyStep(step=2, question="Why was vector-db unreachable?",
                            answer="DNS resolution for vector-db failed",
                            evidence="CoreDNS SERVFAIL rate > 0",
                            component="coredns"),
                    WhyStep(step=3, question="Why did DNS resolution fail?",
                            answer="CoreDNS was returning SERVFAIL for internal service names",
                            evidence="dns_failures metric SERVFAIL rate > 0",
                            component="coredns"),
                    WhyStep(step=4, question="Why was CoreDNS returning SERVFAIL?",
                            answer="CoreDNS upstream resolver was misconfigured",
                            evidence="CoreDNS logs show upstream timeout",
                            component="coredns"),
                    WhyStep(step=5, question="Why was the upstream resolver misconfigured?",
                            answer="Network policy change blocked CoreDNS upstream egress",
                            evidence="Network policy audit log shows egress rule added before incident",
                            component="network-policy"),
                ],
                fundamental_root_cause="A network policy change blocked CoreDNS upstream egress, causing DNS SERVFAIL for all internal service resolution",
            ),
            confidence=0.88,
        )
        resp = RCAResponse(
            rca=result,
            rca_target=AnalysisDomain.INFRA_LOGS,
            data_sources=["prometheus"],
            total_logs_analyzed=24,
            processing_time_ms=4567.8,
        )
        assert resp.rca_target == AnalysisDomain.INFRA_LOGS
        assert resp.data_sources == ["prometheus"]
        assert resp.total_logs_analyzed == 24

    def test_rca_response_unknown_target(self):
        result = RCAResult(
            rca_summary="Memory pressure caused timeout",
            root_cause=RootCause(
                category=RootCauseCategory.MEMORY,
                component="planner-v1-pod",
                description="Memory exceeded 90% causing latency spike",
                evidence=["container_memory_usage_bytes exceeded limit"],
                confidence=0.72,
            ),
            causal_chain=[
                CausalLink(
                    source_event="Memory pressure",
                    target_event="P99 latency spike",
                    link_type=CausalLinkType.DIRECT_CAUSE,
                    evidence="Memory spike preceded latency spike by 30s",
                ),
            ],
            five_why_analysis=FiveWhyAnalysis(
                problem_statement="planner-v1 experiencing high P99 latency and timeouts",
                whys=[
                    WhyStep(step=1, question="Why is P99 latency spiking?",
                            answer="Container memory usage exceeded 90% threshold",
                            evidence="container_memory_usage_bytes exceeded limit",
                            component="planner-v1-pod"),
                    WhyStep(step=2, question="Why did memory exceed 90%?",
                            answer="Memory usage grew steadily without being reclaimed",
                            evidence="memory_usage metric shows monotonic increase over 10 min",
                            component="planner-v1-pod"),
                    WhyStep(step=3, question="Why was memory not reclaimed?",
                            answer="No memory limit or eviction policy was configured for the pod",
                            evidence="Pod spec shows no resource limits set",
                            component="planner-v1-pod"),
                    WhyStep(step=4, question="Why were no resource limits set?",
                            answer="Deployment manifest was created without resource constraints",
                            evidence="kubectl describe pod shows no limits field",
                            component="planner-v1-pod"),
                    WhyStep(step=5, question="Why was the manifest missing resource constraints?",
                            answer="No resource policy enforcement gate exists in the CI/CD pipeline",
                            evidence="No admission webhook or OPA policy found that enforces limits",
                            component="ci-cd-pipeline"),
                ],
                fundamental_root_cause="No resource limit enforcement in the CI/CD pipeline allowed the pod to be deployed without memory constraints",
            ),
            confidence=0.72,
        )
        resp = RCAResponse(
            rca=result,
            rca_target=AnalysisDomain.UNKNOWN,
            data_sources=["langfuse", "prometheus"],
            total_logs_analyzed=35,
            processing_time_ms=5123.4,
        )
        assert resp.rca_target == AnalysisDomain.UNKNOWN
        assert resp.data_sources == ["langfuse", "prometheus"]

    def test_json_schema_generation(self):
        schema = RCAResult.model_json_schema()
        assert "rca_summary" in schema["properties"]
        assert "root_cause" in schema["properties"]
        assert "causal_chain" in schema["properties"]
        assert "contributing_factors" in schema["properties"]
        assert "failure_timeline" in schema["properties"]
        assert "blast_radius" in schema["properties"]
        assert "five_why_analysis" in schema["properties"]
        assert "confidence" in schema["properties"]


# ── Integration: API Endpoint ──────────────────────────────────────────


class TestRCAEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/rca", json={})
        assert resp.status_code == 422

    def test_rejects_missing_error_analysis(self):
        resp = client.post(
            "/api/v1/rca",
            json={
                "rca_target": "Agent",
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_rca_target(self):
        resp = client.post(
            "/api/v1/rca",
            json={
                "error_analysis": {
                    "analysis_summary": "test",
                    "analysis_target": "Agent",
                    "errors": [{
                        "error_id": "ERR-001",
                        "category": "llm_failure",
                        "severity": "critical",
                        "component": "test",
                        "error_message": "test",
                        "timestamp": "2026-04-10T04:19:57.962Z",
                        "evidence": "test",
                        "source": "langfuse",
                    }],
                    "confidence": 0.9,
                },
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_incident(self):
        resp = client.post(
            "/api/v1/rca",
            json={
                "error_analysis": {
                    "analysis_summary": "test",
                    "analysis_target": "Agent",
                    "errors": [{
                        "error_id": "ERR-001",
                        "category": "llm_failure",
                        "severity": "critical",
                        "component": "test",
                        "error_message": "test",
                        "timestamp": "2026-04-10T04:19:57.962Z",
                        "evidence": "test",
                        "source": "langfuse",
                    }],
                    "confidence": 0.9,
                },
                "rca_target": "Agent",
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/rca",
            json={
                "error_analysis": {
                    "analysis_summary": "test",
                    "analysis_target": "Agent",
                    "errors": [{
                        "error_id": "ERR-001",
                        "category": "llm_failure",
                        "severity": "critical",
                        "component": "test",
                        "error_message": "test",
                        "timestamp": "2026-04-10T04:19:57.962Z",
                        "evidence": "test",
                        "source": "langfuse",
                    }],
                    "confidence": 0.9,
                },
                "rca_target": "Agent",
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
            },
        )
        assert resp.status_code == 422

    def test_accepts_valid_payload_agent_target(self):
        """Shape validation only — LLM call requires API key."""
        req = RCARequest(
            error_analysis=_make_error_analysis(),
            rca_target=AnalysisDomain.AGENT,
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.rca_target == AnalysisDomain.AGENT

    def test_accepts_valid_payload_infra_target(self):
        req = RCARequest(
            error_analysis=_make_error_analysis(
                analysis_target=AnalysisDomain.INFRA_LOGS,
            ),
            rca_target=AnalysisDomain.INFRA_LOGS,
            incident=_make_incident(error_type=ErrorType.INFRA),
            agent_name="retrieval-agent",
        )
        assert req.rca_target == AnalysisDomain.INFRA_LOGS
        assert req.trace_id is None

    def test_accepts_valid_payload_unknown_target(self):
        req = RCARequest(
            error_analysis=_make_error_analysis(
                analysis_target=AnalysisDomain.UNKNOWN,
            ),
            rca_target=AnalysisDomain.UNKNOWN,
            incident=_make_incident(error_type=ErrorType.UNKNOWN),
            trace_id="trace-pln-789",
            agent_name="planner-v1",
        )
        assert req.rca_target == AnalysisDomain.UNKNOWN
