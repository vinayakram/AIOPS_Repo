"""
Unit tests for server/database/models.py

Verifies schema creation, constraints, and ORM behaviour.
Uses in-memory SQLite only — never touches aiops.db.

Coverage target: 100% on models.py (see CLAUDE.md §4d)
"""
import pytest
from datetime import datetime
from sqlalchemy.exc import IntegrityError

from server.database.models import (
    Trace,
    Span,
    Issue,
    EscalationRule,
    TraceLog,
    EscalationLog,
)


class TestTraceModel:
    def test_create_minimal_trace(self, db_session):
        trace = Trace(id="t1", app_name="my-agent")
        db_session.add(trace)
        db_session.commit()
        found = db_session.query(Trace).filter_by(id="t1").first()
        assert found is not None
        assert found.app_name == "my-agent"

    def test_default_status_is_ok(self, db_session):
        trace = Trace(id="t2", app_name="my-agent")
        db_session.add(trace)
        db_session.commit()
        found = db_session.query(Trace).filter_by(id="t2").first()
        assert found.status == "ok"

    def test_started_at_auto_set(self, db_session):
        trace = Trace(id="t3", app_name="my-agent")
        db_session.add(trace)
        db_session.commit()
        found = db_session.query(Trace).filter_by(id="t3").first()
        assert isinstance(found.started_at, datetime)

    def test_duplicate_id_raises(self, db_session):
        db_session.add(Trace(id="dup", app_name="a"))
        db_session.commit()
        db_session.add(Trace(id="dup", app_name="b"))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestSpanModel:
    def test_create_span_with_trace(self, db_session):
        db_session.add(Trace(id="t1", app_name="app"))
        db_session.commit()
        span = Span(id="s1", trace_id="t1", name="llm_call")
        db_session.add(span)
        db_session.commit()
        found = db_session.query(Span).filter_by(id="s1").first()
        assert found.trace_id == "t1"
        assert found.name == "llm_call"

    def test_default_span_type_is_chain(self, db_session):
        db_session.add(Trace(id="t2", app_name="app"))
        db_session.commit()
        db_session.add(Span(id="s2", trace_id="t2", name="pipeline"))
        db_session.commit()
        found = db_session.query(Span).filter_by(id="s2").first()
        assert found.span_type == "chain"

    def test_default_status_is_ok(self, db_session):
        db_session.add(Trace(id="t3", app_name="app"))
        db_session.commit()
        db_session.add(Span(id="s3", trace_id="t3", name="tool_call"))
        db_session.commit()
        found = db_session.query(Span).filter_by(id="s3").first()
        assert found.status == "ok"


class TestIssueModel:
    def test_create_issue(self, db_session):
        issue = Issue(
            app_name="my-agent",
            issue_type="high_latency",
            severity="medium",
            fingerprint="fp-001",
            title="High latency detected",
        )
        db_session.add(issue)
        db_session.commit()
        found = db_session.query(Issue).filter_by(fingerprint="fp-001").first()
        assert found is not None
        assert found.status == "OPEN"  # default

    def test_fingerprint_unique_constraint(self, db_session):
        db_session.add(Issue(
            app_name="app", issue_type="t", severity="low",
            fingerprint="same-fp", title="A",
        ))
        db_session.commit()
        db_session.add(Issue(
            app_name="app", issue_type="t", severity="low",
            fingerprint="same-fp", title="B",
        ))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_escalation_count_defaults_to_zero(self, db_session):
        issue = Issue(
            app_name="app", issue_type="t", severity="low",
            fingerprint="fp-002", title="Test",
        )
        db_session.add(issue)
        db_session.commit()
        found = db_session.query(Issue).filter_by(fingerprint="fp-002").first()
        assert found.escalation_count == 0


class TestEscalationRuleModel:
    def test_create_rule(self, db_session):
        rule = EscalationRule(
            name="Alert high severity",
            condition_type="severity_gte",
            condition_value=2.0,
            action_type="log",
        )
        db_session.add(rule)
        db_session.commit()
        found = db_session.query(EscalationRule).filter_by(name="Alert high severity").first()
        assert found is not None
        assert found.enabled is True  # default

    def test_rule_applies_to_all_apps_when_app_name_is_null(self, db_session):
        rule = EscalationRule(
            name="Global rule",
            condition_type="severity_gte",
            condition_value=1.0,
            action_type="webhook",
        )
        db_session.add(rule)
        db_session.commit()
        found = db_session.query(EscalationRule).filter_by(name="Global rule").first()
        assert found.app_name is None


class TestTraceLogModel:
    def test_create_trace_log(self, db_session):
        db_session.add(Trace(id="t1", app_name="app"))
        db_session.commit()
        log = TraceLog(trace_id="t1", level="INFO", message="started")
        db_session.add(log)
        db_session.commit()
        found = db_session.query(TraceLog).filter_by(trace_id="t1").first()
        assert found.message == "started"
        assert found.level == "INFO"


class TestEscalationLogModel:
    def test_create_escalation_log(self, db_session):
        esc_log = EscalationLog(action_type="webhook", status="fired")
        db_session.add(esc_log)
        db_session.commit()
        found = db_session.query(EscalationLog).first()
        assert found.status == "fired"
