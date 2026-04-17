import asyncio
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from server.config import settings
from server.database.engine import SessionLocal
from server.database.models import EscalationRule, EscalationLog, Issue
from server.engine.issue_detector import detect_issues
from server.engine.webhook_dispatcher import fire_webhook
from server.engine.langfuse_reporter import reporter as lf_reporter
from server.engine.langfuse_sync import sync_langfuse
import server.engine.reason_analyzer as reason_analyzer
from server.engine import rca_client

logger = logging.getLogger("aiops.escalation")

_running = False


async def start():
    global _running
    _running = True
    logger.info("Escalation engine started (interval=%ds)", settings.ESCALATION_INTERVAL_SECONDS)
    while _running:
        try:
            await _tick()
        except Exception as e:
            logger.exception("Escalation engine tick error: %s", e)
        await asyncio.sleep(settings.ESCALATION_INTERVAL_SECONDS)


def stop():
    global _running
    _running = False
    logger.info("Escalation engine stopping")


async def _tick():
    db: Session = SessionLocal()
    try:
        # 1. Sync Langfuse traces into local DB before detection runs
        await sync_langfuse()

        # 2. Auto-detect issues
        new_issues = detect_issues(db)
        if new_issues:
            db.commit()
            logger.info("Detected %d new issue(s)", len(new_issues))
            for issue in new_issues:
                lf_reporter.report_issue(issue)
                # Kick off external 5-agent RCA pipeline for each new issue.
                # Falls back to legacy reason_analyzer automatically when the
                # issue has no trace_id (see rca_client._run_rca).
                asyncio.create_task(rca_client.request_rca(issue.id))

        # 3. Evaluate escalation rules against open issues
        rules = db.query(EscalationRule).filter(EscalationRule.enabled == True).all()
        open_issues = db.query(Issue).filter(Issue.status.in_(["OPEN", "ACKNOWLEDGED"])).all()

        for issue in open_issues:
            for rule in rules:
                if rule.app_name and rule.app_name != issue.app_name:
                    continue
                if _rule_matches(rule, issue):
                    await _fire_action(db, rule, issue)

        db.commit()
    finally:
        db.close()


def _rule_matches(rule: EscalationRule, issue: Issue) -> bool:
    ct = rule.condition_type
    cv = rule.condition_value

    if ct == "open_issue_age_gt":
        # cv is minutes
        age_minutes = (datetime.utcnow() - issue.created_at).total_seconds() / 60
        return age_minutes > cv

    if ct == "severity_gte":
        severity_map = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return severity_map.get(issue.severity, 0) >= int(cv)

    if ct == "repeated_error_count_gte":
        return issue.escalation_count >= int(cv)

    # duration_ms_gt and error_rate_gt are handled by issue_detector, not here
    return False


async def _fire_action(db: Session, rule: EscalationRule, issue: Issue):
    action = rule.action_type

    # Avoid re-firing the same rule on the same issue too quickly (1 hr cooldown)
    recent_log = (
        db.query(EscalationLog)
        .filter(
            EscalationLog.issue_id == issue.id,
            EscalationLog.rule_id == rule.id,
            EscalationLog.status == "fired",
            EscalationLog.fired_at >= datetime.utcnow() - timedelta(hours=1),
        )
        .first()
    )
    if recent_log:
        return

    if action == "log":
        logger.warning(
            "ESCALATION [%s] issue=%d app=%s title=%s severity=%s",
            rule.name, issue.id, issue.app_name, issue.title, issue.severity,
        )
        _write_log(db, issue.id, rule.id, "log", "fired", "Logged escalation")

    elif action == "escalate_issue":
        issue.status = "ESCALATED"
        issue.escalation_count += 1
        issue.updated_at = datetime.utcnow()
        _write_log(db, issue.id, rule.id, "escalate_issue", "fired", f"Auto-escalated by rule '{rule.name}'")
        logger.info("Issue %d escalated by rule '%s'", issue.id, rule.name)

    elif action == "webhook":
        cfg = json.loads(rule.action_config) if rule.action_config else {}
        url = cfg.get("url", "")
        if not url:
            _write_log(db, issue.id, rule.id, "webhook", "failed", "No URL in action_config")
            return
        payload = {
            "rule": rule.name,
            "issue_id": issue.id,
            "app_name": issue.app_name,
            "title": issue.title,
            "severity": issue.severity,
            "status": issue.status,
            "created_at": issue.created_at.isoformat(),
        }
        success, detail = await fire_webhook(
            url=url,
            payload=payload,
            method=cfg.get("method", "POST"),
            headers=cfg.get("headers"),
        )
        status = "fired" if success else "failed"
        _write_log(db, issue.id, rule.id, "webhook", status, detail)
        logger.info("Webhook %s for issue %d: %s", status, issue.id, detail)


def _write_log(db, issue_id, rule_id, action_type, status, detail):
    db.add(EscalationLog(
        issue_id=issue_id,
        rule_id=rule_id,
        action_type=action_type,
        status=status,
        detail=detail,
    ))
