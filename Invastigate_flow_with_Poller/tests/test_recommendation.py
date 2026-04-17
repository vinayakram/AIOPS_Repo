"""
Tests for the Recommendation Agent.

Run with: pytest tests/test_recommendation.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.normalization import ErrorType
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
    RCAResult,
    RootCause,
    RootCauseCategory,
    WhyStep,
)
from app.models.recommendation import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendationResult,
    Solution,
    SolutionCategory,
    SolutionEffort,
)


client = TestClient(app)


# ── Helpers: build sample data ────────────────────────────────────────


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
            "LLM access disabled → openai_generation ERROR → summarizer-v2 error"
        ],
        confidence=0.95,
    )
    defaults.update(overrides)
    return ErrorAnalysisResult(**defaults)


def _make_rca(**overrides) -> RCAResult:
    defaults = dict(
        rca_summary="LLM provider disabled API access for the account used by openai_generation, causing all generation requests to fail and propagate to the summarizer-v2 agent.",
        root_cause=RootCause(
            category=RootCauseCategory.LLM_PROVIDER,
            component="openai_generation",
            description="LLM provider disabled API access for this account",
            evidence=["GENERATION span status=ERROR: LLM access is disabled"],
            error_ids=["ERR-001"],
            confidence=0.95,
        ),
        causal_chain=[
            CausalLink(
                source_event="LLM provider disabled account access",
                target_event="openai_generation span ERROR",
                link_type=CausalLinkType.DIRECT_CAUSE,
                evidence="Span status=ERROR, message=LLM access disabled",
            ),
            CausalLink(
                source_event="openai_generation span ERROR",
                target_event="summarizer-v2 error output",
                link_type=CausalLinkType.DIRECT_CAUSE,
                evidence="Trace output contains error payload",
            ),
        ],
        contributing_factors=[
            ContributingFactor(
                factor="No fallback LLM provider configured",
                component="summarizer-v2",
                evidence="Agent config shows single provider",
                severity="secondary",
            ),
        ],
        failure_timeline=[
            FailureTimeline(
                timestamp="2026-04-10T04:19:57.962Z",
                component="openai_generation",
                event="GENERATION span rejected by provider",
                is_root_cause=True,
            ),
        ],
        blast_radius=["openai_generation", "summarizer-v2"],
        five_why_analysis=FiveWhyAnalysis(
            problem_statement="summarizer-v2 agent failed on all 3 consecutive traces — LLM access is disabled",
            whys=[
                WhyStep(step=1, question="Why did the agent fail on every request?",
                        answer="The openai_generation span returned ERROR status on every call",
                        evidence="GENERATION span status=ERROR, message=LLM access is disabled",
                        component="openai_generation"),
                WhyStep(step=2, question="Why did openai_generation return ERROR?",
                        answer="LLM access is disabled for the account used by this service",
                        evidence="Error: LLM access is disabled (demo error mode)",
                        component="openai_generation"),
                WhyStep(step=3, question="Why is LLM access disabled?",
                        answer="The service is configured to run in demo error mode",
                        evidence="Error message explicitly references 'demo error mode'",
                        component="summarizer-v2"),
                WhyStep(step=4, question="Why is demo error mode active?",
                        answer="A configuration flag was toggled (via UI or deployment) without re-enabling LLM access",
                        evidence="Error instructs user to click 'Enable LLM Access' in chat UI",
                        component="summarizer-v2"),
                WhyStep(step=5, question="Why was no pre-flight check in place?",
                        answer="No startup or readiness gate validates LLM access state before accepting requests",
                        evidence="Service accepted requests and only failed at the LLM call stage — no early-rejection log",
                        component="summarizer-v2"),
            ],
            fundamental_root_cause="Demo error mode was deployed without a corresponding readiness gate — the configuration toggle was never paired with an enforcement mechanism that rejects requests when LLM access is disabled",
        ),
        confidence=0.95,
    )
    defaults.update(overrides)
    return RCAResult(**defaults)


# ── Unit: Request Model ────────────────────────────────────────────────


class TestRecommendationRequestModel:
    def test_valid_request(self):
        req = RecommendationRequest(
            error_analysis=_make_error_analysis(),
            rca=_make_rca(),
            agent_name="summarizer-v2",
        )
        assert req.agent_name == "summarizer-v2"
        assert len(req.error_analysis.errors) == 2
        assert req.rca.root_cause.category == RootCauseCategory.LLM_PROVIDER

    def test_missing_error_analysis_raises(self):
        with pytest.raises(Exception):
            RecommendationRequest(
                rca=_make_rca(),
                agent_name="test",
            )

    def test_missing_rca_raises(self):
        with pytest.raises(Exception):
            RecommendationRequest(
                error_analysis=_make_error_analysis(),
                agent_name="test",
            )

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            RecommendationRequest(
                error_analysis=_make_error_analysis(),
                rca=_make_rca(),
            )


# ── Unit: Response Models ──────────────────────────────────────────────


class TestRecommendationResponseModels:
    def test_solution(self):
        s = Solution(
            rank=1,
            title="Re-enable LLM API access",
            description="Contact OpenAI to re-enable API access for the account",
            category=SolutionCategory.ACCESS_MANAGEMENT,
            effort=SolutionEffort.QUICK_FIX,
            addresses_root_cause=True,
            affected_components=["openai_generation"],
            expected_outcome="LLM generation resumes, agent pipeline functional",
            error_ids=["ERR-001"],
        )
        assert s.rank == 1
        assert s.addresses_root_cause is True

    def test_solution_rank_out_of_range(self):
        with pytest.raises(Exception):
            Solution(
                rank=5,
                title="test",
                description="test",
                category=SolutionCategory.CONFIG_CHANGE,
                effort=SolutionEffort.LOW,
                addresses_root_cause=False,
                expected_outcome="test",
            )

    def test_solution_rank_zero_invalid(self):
        with pytest.raises(Exception):
            Solution(
                rank=0,
                title="test",
                description="test",
                category=SolutionCategory.CONFIG_CHANGE,
                effort=SolutionEffort.LOW,
                addresses_root_cause=False,
                expected_outcome="test",
            )

    def test_all_solution_categories(self):
        for cat in SolutionCategory:
            s = Solution(
                rank=1,
                title="test",
                description="test",
                category=cat,
                effort=SolutionEffort.MEDIUM,
                addresses_root_cause=False,
                expected_outcome="test",
            )
            assert s.category == cat

    def test_all_solution_efforts(self):
        for eff in SolutionEffort:
            s = Solution(
                rank=1,
                title="test",
                description="test",
                category=SolutionCategory.CONFIG_CHANGE,
                effort=eff,
                addresses_root_cause=False,
                expected_outcome="test",
            )
            assert s.effort == eff

    def test_full_recommendation_result_4_solutions(self):
        result = RecommendationResult(
            recommendation_summary="Re-enable LLM access and add resilience",
            solutions=[
                Solution(
                    rank=1,
                    title="Re-enable LLM API access",
                    description="Contact provider to re-enable",
                    category=SolutionCategory.ACCESS_MANAGEMENT,
                    effort=SolutionEffort.QUICK_FIX,
                    addresses_root_cause=True,
                    affected_components=["openai_generation"],
                    expected_outcome="LLM generation resumes",
                    error_ids=["ERR-001"],
                ),
                Solution(
                    rank=2,
                    title="Add fallback LLM provider",
                    description="Configure a secondary LLM provider",
                    category=SolutionCategory.FALLBACK,
                    effort=SolutionEffort.MEDIUM,
                    addresses_root_cause=False,
                    affected_components=["summarizer-v2"],
                    expected_outcome="Agent survives single-provider outages",
                    error_ids=["ERR-001", "ERR-002"],
                ),
                Solution(
                    rank=3,
                    title="Add retry with exponential backoff",
                    description="Implement retry logic for transient LLM errors",
                    category=SolutionCategory.RETRY_LOGIC,
                    effort=SolutionEffort.LOW,
                    addresses_root_cause=False,
                    affected_components=["openai_generation"],
                    expected_outcome="Transient failures auto-recover",
                    error_ids=["ERR-001"],
                ),
                Solution(
                    rank=4,
                    title="Add LLM health check alerting",
                    description="Set up alerts for LLM provider access failures",
                    category=SolutionCategory.MONITORING,
                    effort=SolutionEffort.LOW,
                    addresses_root_cause=False,
                    affected_components=["openai_generation", "summarizer-v2"],
                    expected_outcome="Early detection of LLM access issues",
                    error_ids=["ERR-001"],
                ),
            ],
            root_cause_addressed="LLM provider disabled API access for openai_generation account",
            confidence=0.93,
        )
        assert len(result.solutions) == 4
        assert result.solutions[0].rank == 1
        assert result.solutions[3].rank == 4
        assert result.confidence == 0.93

    def test_recommendation_result_2_solutions(self):
        """Fewer than 4 solutions when only 2 are genuinely applicable."""
        result = RecommendationResult(
            recommendation_summary="Fix DNS and add resilience",
            solutions=[
                Solution(
                    rank=1,
                    title="Fix CoreDNS configuration",
                    description="Correct the SERVFAIL-causing misconfiguration",
                    category=SolutionCategory.CONFIG_CHANGE,
                    effort=SolutionEffort.QUICK_FIX,
                    addresses_root_cause=True,
                    expected_outcome="DNS resolution restored",
                ),
                Solution(
                    rank=2,
                    title="Add DNS caching layer",
                    description="Deploy a local DNS cache to survive upstream failures",
                    category=SolutionCategory.INFRASTRUCTURE,
                    effort=SolutionEffort.MEDIUM,
                    addresses_root_cause=False,
                    expected_outcome="Services survive brief DNS outages",
                ),
            ],
            root_cause_addressed="CoreDNS SERVFAIL due to misconfiguration",
            confidence=0.88,
        )
        assert len(result.solutions) == 2

    def test_recommendation_result_1_solution(self):
        result = RecommendationResult(
            recommendation_summary="Single solution",
            solutions=[
                Solution(
                    rank=1,
                    title="Re-enable access",
                    description="Re-enable the disabled account",
                    category=SolutionCategory.ACCESS_MANAGEMENT,
                    effort=SolutionEffort.QUICK_FIX,
                    addresses_root_cause=True,
                    expected_outcome="Service restored",
                ),
            ],
            root_cause_addressed="Account disabled",
            confidence=0.95,
        )
        assert len(result.solutions) == 1

    def test_rejects_empty_solutions(self):
        with pytest.raises(Exception):
            RecommendationResult(
                recommendation_summary="test",
                solutions=[],
                root_cause_addressed="test",
                confidence=0.5,
            )

    def test_rejects_more_than_4_solutions(self):
        with pytest.raises(Exception):
            RecommendationResult(
                recommendation_summary="test",
                solutions=[
                    Solution(rank=i, title=f"s{i}", description="d", category=SolutionCategory.CONFIG_CHANGE, effort=SolutionEffort.LOW, addresses_root_cause=False, expected_outcome="o")
                    for i in range(1, 6)
                ],
                root_cause_addressed="test",
                confidence=0.5,
            )

    def test_rejects_duplicate_ranks(self):
        with pytest.raises(Exception):
            RecommendationResult(
                recommendation_summary="test",
                solutions=[
                    Solution(rank=1, title="s1", description="d", category=SolutionCategory.CONFIG_CHANGE, effort=SolutionEffort.LOW, addresses_root_cause=True, expected_outcome="o"),
                    Solution(rank=1, title="s2", description="d", category=SolutionCategory.CODE_FIX, effort=SolutionEffort.LOW, addresses_root_cause=False, expected_outcome="o"),
                ],
                root_cause_addressed="test",
                confidence=0.5,
            )

    def test_rejects_non_sequential_ranks(self):
        with pytest.raises(Exception):
            RecommendationResult(
                recommendation_summary="test",
                solutions=[
                    Solution(rank=1, title="s1", description="d", category=SolutionCategory.CONFIG_CHANGE, effort=SolutionEffort.LOW, addresses_root_cause=True, expected_outcome="o"),
                    Solution(rank=3, title="s2", description="d", category=SolutionCategory.CODE_FIX, effort=SolutionEffort.LOW, addresses_root_cause=False, expected_outcome="o"),
                ],
                root_cause_addressed="test",
                confidence=0.5,
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(Exception):
            RecommendationResult(
                recommendation_summary="test",
                solutions=[
                    Solution(rank=1, title="s1", description="d", category=SolutionCategory.CONFIG_CHANGE, effort=SolutionEffort.LOW, addresses_root_cause=True, expected_outcome="o"),
                ],
                root_cause_addressed="test",
                confidence=1.5,
            )

    def test_response_wrapper(self):
        result = RecommendationResult(
            recommendation_summary="Fix and prevent",
            solutions=[
                Solution(rank=1, title="Fix it", description="Do the fix", category=SolutionCategory.CONFIG_CHANGE, effort=SolutionEffort.QUICK_FIX, addresses_root_cause=True, expected_outcome="Fixed"),
                Solution(rank=2, title="Prevent it", description="Add guard", category=SolutionCategory.MONITORING, effort=SolutionEffort.LOW, addresses_root_cause=False, expected_outcome="Prevented"),
            ],
            root_cause_addressed="The root cause",
            confidence=0.90,
        )
        resp = RecommendationResponse(
            recommendations=result,
            processing_time_ms=2345.6,
        )
        assert len(resp.recommendations.solutions) == 2
        assert resp.processing_time_ms == 2345.6

    def test_json_schema_generation(self):
        schema = RecommendationResult.model_json_schema()
        assert "recommendation_summary" in schema["properties"]
        assert "solutions" in schema["properties"]
        assert "root_cause_addressed" in schema["properties"]
        assert "confidence" in schema["properties"]


# ── Integration: API Endpoint ──────────────────────────────────────────


class TestRecommendationEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/recommend", json={})
        assert resp.status_code == 422

    def test_rejects_missing_error_analysis(self):
        resp = client.post(
            "/api/v1/recommend",
            json={
                "rca": {
                    "rca_summary": "test",
                    "root_cause": {
                        "category": "llm_provider",
                        "component": "test",
                        "description": "test",
                        "evidence": ["test"],
                        "confidence": 0.5,
                    },
                    "causal_chain": [{
                        "source_event": "A",
                        "target_event": "B",
                        "link_type": "direct_cause",
                        "evidence": "test",
                    }],
                    "confidence": 0.5,
                },
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_rca(self):
        resp = client.post(
            "/api/v1/recommend",
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
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/recommend",
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
                "rca": {
                    "rca_summary": "test",
                    "root_cause": {
                        "category": "llm_provider",
                        "component": "test",
                        "description": "test",
                        "evidence": ["test"],
                        "confidence": 0.5,
                    },
                    "causal_chain": [{
                        "source_event": "A",
                        "target_event": "B",
                        "link_type": "direct_cause",
                        "evidence": "test",
                    }],
                    "confidence": 0.5,
                },
            },
        )
        assert resp.status_code == 422

    def test_accepts_valid_payload(self):
        """Shape validation only — LLM call requires API key."""
        req = RecommendationRequest(
            error_analysis=_make_error_analysis(),
            rca=_make_rca(),
            agent_name="summarizer-v2",
        )
        assert req.agent_name == "summarizer-v2"
        assert req.rca.root_cause.category == RootCauseCategory.LLM_PROVIDER
