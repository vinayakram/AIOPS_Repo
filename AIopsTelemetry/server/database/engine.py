from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from server.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from server.database import models  # noqa: F401 — registers all models
    Base.metadata.create_all(bind=engine)
    # SQLite does not add columns to existing tables via create_all.
    # Run lightweight migrations for new columns here.
    with engine.connect() as conn:
        _add_column_if_missing(conn, "issues", "rule_id", "VARCHAR")
        _add_column_if_missing(conn, "issues", "base_fingerprint", "VARCHAR")
        _add_column_if_missing(conn, "issues", "previous_issue_id", "INTEGER")
        _add_column_if_missing(conn, "issues", "recurrence_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "issues", "title_en", "VARCHAR")
        _add_column_if_missing(conn, "issues", "title_ja", "VARCHAR")
        _add_column_if_missing(conn, "issues", "description_en", "TEXT")
        _add_column_if_missing(conn, "issues", "description_ja", "TEXT")
        _add_column_if_missing(conn, "escalation_rules", "nfr_id", "VARCHAR")
        _add_column_if_missing(conn, "escalation_rules", "description", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "rca_json", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "remediation_type", "VARCHAR")
        _add_column_if_missing(conn, "issue_analyses", "handoff_plan", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "likely_cause_en", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "likely_cause_ja", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "evidence_en", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "evidence_ja", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "recommended_action_en", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "recommended_action_ja", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "full_summary_en", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "full_summary_ja", "TEXT")
        _add_column_if_missing(conn, "issue_analyses", "language_status", "VARCHAR")
        # Backfill base_fingerprint for rows created before recurrence tracking
        from sqlalchemy import text
        conn.execute(text(
            "UPDATE issues SET base_fingerprint = fingerprint WHERE base_fingerprint IS NULL"
        ))
        conn.execute(text(
            "UPDATE issues SET title_en = title WHERE title_en IS NULL"
        ))
        conn.execute(text(
            "UPDATE issues SET description_en = description WHERE description_en IS NULL"
        ))
        conn.execute(text(
            "UPDATE issue_analyses SET likely_cause_en = likely_cause WHERE likely_cause_en IS NULL"
        ))
        conn.execute(text(
            "UPDATE issue_analyses SET evidence_en = evidence WHERE evidence_en IS NULL"
        ))
        conn.execute(text(
            "UPDATE issue_analyses SET recommended_action_en = recommended_action WHERE recommended_action_en IS NULL"
        ))
        conn.execute(text(
            "UPDATE issue_analyses SET full_summary_en = full_summary WHERE full_summary_en IS NULL"
        ))
        conn.commit()
    _seed_nfr_escalation_rules()
    _backfill_bilingual_display_fields()
    if settings.RCA_KB_ENABLED:
        try:
            from server.engine.knowledge_base import init_knowledge_base
            init_knowledge_base()
        except Exception as e:
            import logging
            logging.getLogger("aiops.db").warning("RCA knowledge base init failed: %s", e)


# ── NFR rule seed data ────────────────────────────────────────────────────────
# (nfr_id, name, description, condition_type, condition_value, span_name)
_NFR_SEED_RULES = [
    ("NFR-2",   "Consecutive Trace Failures",        "3 consecutive trace failures → SEV1 critical",                   "repeated_error_count_gte", 3,     None),
    ("NFR-7",   "Response Time Target",              "Avg response time ≥ target ms → SEV2 high",                      "duration_ms_gt",           5000,  None),
    ("NFR-7a",  "Response Time 2× Target",           "Avg response time ≥ 2× target ms → SEV1 critical",               "duration_ms_gt",           10000, None),
    ("NFR-7p95", "p95 Response Time Target",         "p95 response time ≥ target ms under concurrent load → SEV2 high", "duration_ms_gt",           5000,  None),
    ("NFR-7p95a","p95 Response Time 2× Target",      "p95 response time ≥ 2× target ms under concurrent load → SEV1 critical", "duration_ms_gt",     10000, None),
    ("NFR-8",   "Error Rate ≥ 1%",                   "HTTP 5xx error rate ≥ 1% over check window → SEV2 high",         "error_rate_gt",            1.0,   None),
    ("NFR-8a",  "Error Rate ≥ 5%",                   "HTTP 5xx error rate ≥ 5% over check window → SEV1 critical",     "error_rate_gt",            5.0,   None),
    ("NFR-9",   "Exception Count Spike",             "Recent-window errors ≥ 2× previous window (min 5), excluding pod-threshold demo traces → SEV3 medium", "repeated_error_count_gte", 5, None),
    ("NFR-11",  "CPU Utilisation High",              "CPU utilisation ≥ 80% → SEV2 high",                              "error_rate_gt",            80.0,  None),
    ("NFR-11a", "CPU Utilisation Critical",          "CPU utilisation ≥ 95% → SEV1 critical",                          "error_rate_gt",            95.0,  None),
    ("NFR-12",  "Memory Utilisation High",           "Memory utilisation ≥ 80% → SEV2 high",                           "error_rate_gt",            80.0,  None),
    ("NFR-13",  "Memory Utilisation Critical",       "Memory utilisation ≥ 90% → SEV1 critical",                       "error_rate_gt",            90.0,  None),
    ("NFR-14",  "Disk Utilisation High",             "Disk utilisation ≥ 80% → SEV3 medium",                           "error_rate_gt",            80.0,  None),
    ("NFR-14a", "Disk Utilisation Critical",         "Disk utilisation ≥ 90% → SEV2 high",                             "error_rate_gt",            90.0,  None),
    ("NFR-19",  "Execution Time Drift",              "Recent avg execution time ≥ 120% of baseline → SEV2 high",       "duration_ms_gt",           120,   None),
    ("NFR-22",  "Consecutive LLM Failures",          "5 consecutive LLM span failures → SEV2 high",                    "repeated_error_count_gte", 5,     "llm"),
    ("NFR-22a", "Consecutive LLM Failures Critical", "10 consecutive LLM span failures → SEV1 critical",               "repeated_error_count_gte", 10,    "llm"),
    ("NFR-24",  "GenAI Failure Rate ≥ 3%",           "LLM call failure rate ≥ 3% in window → SEV2 high",               "error_rate_gt",            3.0,   "llm"),
    ("NFR-24a", "GenAI Failure Rate ≥ 10%",          "LLM call failure rate ≥ 10% in window → SEV1 critical",          "error_rate_gt",            10.0,  "llm"),
    ("NFR-25",  "Timeout Rate ≥ 3%",                 "Spans with timeout errors ≥ 3% in window → SEV2 high",           "error_rate_gt",            3.0,   None),
    ("NFR-25a", "Timeout Rate ≥ 10%",                "Spans with timeout errors ≥ 10% in window → SEV1 critical",      "error_rate_gt",            10.0,  None),
    ("NFR-26",  "Token Spike",                       "Average token count ≥ 50% above baseline → SEV3 medium",         "error_rate_gt",            50.0,  "llm"),
    ("NFR-29",  "Output Error Detection",            "Trace output contains ⚠️ / error patterns → SEV2/3",             "error_rate_gt",            0.0,   None),
    ("NFR-30",  "Query Preprocessing Error",         "sample-agent query preprocessing failure → SEV2 high",           "repeated_error_count_gte", 1,     "query_validation"),
    ("NFR-31",  "LLM Disabled Query Burst",          "3 sample-agent queries with LLM disabled within 10 min → SEV1 critical", "repeated_error_count_gte", 3, "openai_generation"),
    ("NFR-32",  "LLM Rate Limit Exceeded",           "sample-agent LLM deployment rate limit exceeded → SEV2 high",    "repeated_error_count_gte", 1,     "openai_generation"),
    ("NFR-33",  "Application Reachability Failure",  "3 sample-agent availability guard failures within 5 min → SEV1 critical", "repeated_error_count_gte", 3, "pod_resource_guard"),
]


def _seed_nfr_escalation_rules():
    """Insert NFR escalation rules that don't yet exist (idempotent)."""
    from server.database.models import EscalationRule
    db = SessionLocal()
    try:
        for (nfr_id, name, description, cond_type, cond_val, span_name) in _NFR_SEED_RULES:
            existing = (
                db.query(EscalationRule)
                .filter(EscalationRule.nfr_id == nfr_id)
                .first()
            )
            if existing:
                existing.name = name
                existing.description = description
                existing.condition_type = cond_type
                existing.condition_value = cond_val
                existing.condition_span_name = span_name
                continue
            db.add(EscalationRule(
                nfr_id=nfr_id,
                name=name,
                description=description,
                enabled=True,
                condition_type=cond_type,
                condition_value=cond_val,
                condition_span_name=span_name,
                action_type="escalate_issue",
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("aiops.db").warning("NFR rule seed failed: %s", e)
    finally:
        db.close()


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(conn)
    existing = {c["name"] for c in insp.get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()


def _backfill_bilingual_display_fields():
    """Populate Japanese display fields for rows that predate bilingual storage."""
    from server.database.models import Issue, IssueAnalysis
    from server.engine.bilingual import (
        bilingual_analysis_fields,
        issue_description_ja,
        issue_title_ja,
    )

    db = SessionLocal()
    try:
        changed = False
        issues = db.query(Issue).filter(
            (Issue.title_en.is_(None)) |
            (Issue.title_ja.is_(None)) |
            (Issue.description_en.is_(None)) |
            (Issue.description_ja.is_(None))
        ).limit(1000).all()
        for issue in issues:
            if not issue.title_en:
                issue.title_en = issue.title
                changed = True
            if not issue.title_ja:
                issue.title_ja = issue_title_ja(
                    issue.title, app_name=issue.app_name, rule_id=issue.rule_id
                )
                changed = True
            if issue.description and not issue.description_en:
                issue.description_en = issue.description
                changed = True
            if issue.description and not issue.description_ja:
                issue.description_ja = issue_description_ja(
                    issue.description,
                    app_name=issue.app_name,
                    rule_id=issue.rule_id,
                )
                changed = True

        analyses = db.query(IssueAnalysis).filter(
            IssueAnalysis.status == "done",
            (
                (IssueAnalysis.language_status.is_(None)) |
                (IssueAnalysis.language_status != "ready") |
                (IssueAnalysis.likely_cause_ja.is_(None)) |
                (IssueAnalysis.recommended_action_ja.is_(None))
            ),
        ).limit(1000).all()
        for analysis in analyses:
            fields = bilingual_analysis_fields(
                likely_cause=analysis.likely_cause,
                evidence=analysis.evidence,
                recommended_action=analysis.recommended_action,
                full_summary=analysis.full_summary,
            )
            for key, value in fields.items():
                if not getattr(analysis, key, None):
                    setattr(analysis, key, value)
                    changed = True
            if analysis.language_status != "ready":
                analysis.language_status = "ready"
                changed = True

        if changed:
            db.commit()
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("aiops.db").warning("Bilingual backfill failed: %s", e)
    finally:
        db.close()
