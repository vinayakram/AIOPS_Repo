from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, ForeignKey, JSON
)
from server.database.engine import Base


class SystemMetric(Base):
    """Point-in-time snapshot of host system metrics, collected every ~10 seconds."""
    __tablename__ = "system_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)

    # CPU
    cpu_percent = Column(Float, nullable=True)           # overall %
    cpu_per_core_json = Column(Text, nullable=True)      # JSON list of per-core %
    cpu_freq_mhz = Column(Float, nullable=True)          # current frequency

    # Memory
    mem_total_mb = Column(Float, nullable=True)
    mem_used_mb = Column(Float, nullable=True)
    mem_available_mb = Column(Float, nullable=True)
    mem_percent = Column(Float, nullable=True)
    swap_used_mb = Column(Float, nullable=True)
    swap_percent = Column(Float, nullable=True)

    # Disk I/O (delta since last sample, bytes/sec)
    disk_read_bytes_sec = Column(Float, nullable=True)
    disk_write_bytes_sec = Column(Float, nullable=True)
    disk_read_iops = Column(Float, nullable=True)
    disk_write_iops = Column(Float, nullable=True)

    # Network I/O (delta since last sample, bytes/sec)
    net_bytes_sent_sec = Column(Float, nullable=True)
    net_bytes_recv_sec = Column(Float, nullable=True)
    net_packets_sent_sec = Column(Float, nullable=True)
    net_packets_recv_sec = Column(Float, nullable=True)
    net_active_connections = Column(Integer, nullable=True)

    # Process info
    process_count = Column(Integer, nullable=True)


class IssueAnalysis(Base):
    """LLM-generated root-cause analysis for a detected issue."""
    __tablename__ = "issue_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False, unique=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    model_used = Column(String, nullable=True)
    status = Column(String, default="pending")    # pending | done | failed

    # Structured LLM output
    likely_cause = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    remediation_type = Column(String, nullable=True)
    handoff_plan = Column(Text, nullable=True)
    full_summary = Column(Text, nullable=True)    # raw LLM text

    # Bilingual display fields. Legacy columns above remain populated so older
    # readers continue to work; UI/API reads should prefer these language fields.
    likely_cause_en = Column(Text, nullable=True)
    likely_cause_ja = Column(Text, nullable=True)
    evidence_en = Column(Text, nullable=True)
    evidence_ja = Column(Text, nullable=True)
    recommended_action_en = Column(Text, nullable=True)
    recommended_action_ja = Column(Text, nullable=True)
    full_summary_en = Column(Text, nullable=True)
    full_summary_ja = Column(Text, nullable=True)
    language_status = Column(String, nullable=True)  # pending | ready | partial | failed

    # Snapshot of context used (JSON)
    context_snapshot_json = Column(Text, nullable=True)

    # Full response from external RCA microservice (JSON string)
    rca_json = Column(Text, nullable=True)


class Trace(Base):
    __tablename__ = "traces"

    id = Column(String, primary_key=True)          # trace_id from SDK
    app_name = Column(String, nullable=False)
    run_id = Column(String, nullable=True)          # LangGraph run_id
    session_id = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    status = Column(String, default="ok")           # ok | error
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    total_duration_ms = Column(Float, nullable=True)
    input_preview = Column(Text, nullable=True)
    output_preview = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)     # JSON string


class Span(Base):
    __tablename__ = "spans"

    id = Column(String, primary_key=True)
    trace_id = Column(String, ForeignKey("traces.id"), nullable=False)
    parent_span_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    span_type = Column(String, default="chain")     # chain | llm | tool | retriever
    status = Column(String, default="ok")           # ok | error
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=True)
    input_preview = Column(Text, nullable=True)
    output_preview = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    model_name = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_name = Column(String, nullable=False)
    issue_type = Column(String, nullable=False)     # high_latency | error_spike | repeated_error | custom
    severity = Column(String, nullable=False)       # low | medium | high | critical
    status = Column(String, default="OPEN")         # OPEN | ACKNOWLEDGED | ESCALATED | RESOLVED
    fingerprint = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    title_en = Column(String, nullable=True)
    title_ja = Column(String, nullable=True)
    description_en = Column(Text, nullable=True)
    description_ja = Column(Text, nullable=True)
    span_name = Column(String, nullable=True)
    trace_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    escalation_count = Column(Integer, default=0)
    metadata_json = Column(Text, nullable=True)
    rule_id = Column(String, nullable=True)        # NFR rule ID e.g. NFR-8a
    # Recurrence tracking
    base_fingerprint = Column(String, nullable=True, index=True)  # stable fp across recurrences
    previous_issue_id = Column(Integer, nullable=True)             # id of prior resolved issue
    recurrence_count = Column(Integer, default=0)                  # 0 = first occurrence


class EscalationRule(Base):
    __tablename__ = "escalation_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_name = Column(String, nullable=True)        # None = applies to all apps
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    condition_type = Column(String, nullable=False) # duration_ms_gt | error_rate_gt | repeated_error_count_gte | open_issue_age_gt | severity_gte
    condition_value = Column(Float, nullable=False)
    condition_span_name = Column(String, nullable=True)
    action_type = Column(String, nullable=False)    # webhook | log | escalate_issue
    action_config = Column(Text, nullable=True)     # JSON: {url, method, headers, body_template}
    created_at = Column(DateTime, default=datetime.utcnow)
    nfr_id = Column(String, nullable=True, index=True)   # e.g. "NFR-8a" — links to issue_detector
    description = Column(Text, nullable=True)            # human-readable summary of the rule


class TraceLog(Base):
    __tablename__ = "trace_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, ForeignKey("traces.id"), nullable=False)
    level = Column(String, nullable=False)      # DEBUG | INFO | WARNING | ERROR
    logger = Column(String, nullable=True)      # agent | tool | llm | system
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(Text, nullable=True)


class EscalationLog(Base):
    __tablename__ = "escalation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True)
    rule_id = Column(Integer, ForeignKey("escalation_rules.id"), nullable=True)
    action_type = Column(String, nullable=False)
    status = Column(String, nullable=False)         # fired | failed | skipped
    detail = Column(Text, nullable=True)
    fired_at = Column(DateTime, default=datetime.utcnow)


class RCAIncidentPattern(Base):
    __tablename__ = "rca_incident_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=False)
    signal_type = Column(String, nullable=True)
    affected_layer = Column(String, nullable=True)       # app | infra | llm | network | dependency | data
    industry_category = Column(String, nullable=True)    # capacity | timeout | saturation | regression | ...
    default_remediation_type = Column(String, nullable=True)
    severity_hint = Column(String, nullable=True)
    keywords_json = Column(Text, nullable=True)
    embedding_json = Column(Text, nullable=True)         # SQLite fallback; Postgres may also have vector column
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RCAResolutionPlaybook(Base):
    __tablename__ = "rca_resolution_playbooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern_id = Column(Integer, ForeignKey("rca_incident_patterns.id"), nullable=False)
    title = Column(String, nullable=False)
    recommended_action = Column(Text, nullable=False)
    remediation_type = Column(String, nullable=False)
    validation_steps_json = Column(Text, nullable=True)
    rollback_steps_json = Column(Text, nullable=True)
    risk_notes = Column(Text, nullable=True)
    source = Column(String, default="industry")          # industry | organization | vendor | manual
    priority = Column(Integer, default=50)
    embedding_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RCAIncidentMemory(Base):
    __tablename__ = "rca_incident_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True, index=True)
    app_name = Column(String, nullable=True, index=True)
    rule_id = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    root_cause = Column(Text, nullable=True)
    remediation_type = Column(String, nullable=True)
    action_taken = Column(Text, nullable=True)
    resolution_status = Column(String, default="unknown")  # succeeded | failed | partial | unknown
    recurrence_after_fix = Column(Boolean, default=False)
    validation_result = Column(Text, nullable=True)
    pr_url = Column(String, nullable=True)
    embedding_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class RCAPatternMatch(Base):
    __tablename__ = "rca_pattern_matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False, index=True)
    pattern_id = Column(Integer, ForeignKey("rca_incident_patterns.id"), nullable=False)
    similarity_score = Column(Float, nullable=True)
    match_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RCAKnowledgeFeedback(Base):
    __tablename__ = "rca_knowledge_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True, index=True)
    was_helpful = Column(Boolean, nullable=True)
    was_correct = Column(Boolean, nullable=True)
    actual_root_cause = Column(Text, nullable=True)
    actual_fix = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
