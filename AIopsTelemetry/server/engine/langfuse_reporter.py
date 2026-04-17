"""
Posts newly raised issues to Langfuse as scored events so they appear
in the same Langfuse project as the application traces.
"""
import uuid
import logging

logger = logging.getLogger("aiops.langfuse_reporter")

try:
    from langfuse import Langfuse
    from langfuse.types import TraceContext as LFTraceContext
    _LF_AVAILABLE = True
except ImportError:
    _LF_AVAILABLE = False

from server.config import settings

# SEV display label → numeric score (higher = worse)
_SEV_SCORE = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_SEV_LABEL = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}


class _LFReporter:
    def __init__(self):
        self._client = None
        if not _LF_AVAILABLE:
            return
        sk = settings.LANGFUSE_SECRET_KEY
        pk = settings.LANGFUSE_PUBLIC_KEY
        if not (sk and pk):
            return
        try:
            self._client = Langfuse(
                secret_key=sk,
                public_key=pk,
                host=settings.LANGFUSE_HOST,
            )
            logger.info("Langfuse reporter connected to %s", settings.LANGFUSE_HOST)
        except Exception as e:
            logger.warning("Langfuse reporter init failed: %s", e)

    def report_issue(self, issue) -> None:
        """Send issue as a Langfuse trace+score event."""
        if not self._client:
            return
        try:
            sev_label = _SEV_LABEL.get(issue.severity, issue.severity.upper())
            score_value = _SEV_SCORE.get(issue.severity, 1)
            rule_id = issue.rule_id or issue.issue_type
            trace_id = uuid.uuid4().hex

            # Create a monitoring trace for this issue
            with self._client.start_as_current_observation(
                trace_context=LFTraceContext(trace_id=trace_id),
                name=f"[{sev_label}] {rule_id}",
                as_type="span",
                input={
                    "rule_id": rule_id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "app_name": issue.app_name,
                },
                metadata={
                    "title": issue.title,
                    "description": issue.description,
                    "status": issue.status,
                    "tags": ["aiops-issue", sev_label, issue.app_name],
                },
            ) as obs:
                obs.update(output={"title": issue.title, "description": issue.description})

            # Post a score so it shows in Langfuse's score timeline
            self._client.score(
                trace_id=trace_id,
                name=f"nfr-issue-{sev_label.lower()}",
                value=score_value,
                comment=f"[{sev_label}] {rule_id}: {issue.title}",
                data_type="NUMERIC",
            )
            self._client.flush()
        except Exception as e:
            logger.debug("Langfuse report_issue failed (non-fatal): %s", e)


# module-level singleton
reporter = _LFReporter()
