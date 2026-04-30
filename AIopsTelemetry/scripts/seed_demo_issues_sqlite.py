from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from seed_demo_issues import CASES, ROOT
from server.engine.bilingual import issue_description_ja, issue_title_ja


DB_PATH = ROOT / "aiops.db"


def main() -> None:
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        refreshed = 0
        created = 0
        ids: list[int] = []
        for idx, case in enumerate(CASES):
            key, app, issue_type, rule, severity, title, desc, cause, evidence, action = case
            base_fp = hashlib.sha256(f"demo-open-issue:{key}".encode()).hexdigest()[:16]
            status = "OPEN" if idx % 4 else "ESCALATED"
            title_ja = issue_title_ja(title, app_name=app, rule_id=rule)
            desc_ja = issue_description_ja(desc, app_name=app, rule_id=rule)
            metadata = json.dumps({"seed": "demo-open-issues", "seed_key": key})
            cur.execute("select id from issues where base_fingerprint=?", (base_fp,))
            row = cur.fetchone()
            if row:
                issue_id = int(row[0])
                refreshed += 1
                cur.execute(
                    """
                    update issues
                       set app_name=?, issue_type=?, severity=?, status=?, title=?, description=?,
                           title_en=?, title_ja=?, description_en=?, description_ja=?,
                           span_name=?, trace_id=?, rule_id=?, metadata_json=?,
                           updated_at=?, resolved_at=null
                     where id=?
                    """,
                    (
                        app, issue_type, severity, status, title, desc,
                        title, title_ja, desc, desc_ja,
                        f"demo.{issue_type}", f"demo-{key}", rule, metadata,
                        now, issue_id,
                    ),
                )
            else:
                created += 1
                cur.execute(
                    """
                    insert into issues (
                        app_name, issue_type, severity, status, fingerprint, title, description,
                        title_en, title_ja, description_en, description_ja, span_name, trace_id,
                        created_at, updated_at, escalation_count, metadata_json, rule_id,
                        base_fingerprint, recurrence_count
                    ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        app, issue_type, severity, status, base_fp, title, desc,
                        title, title_ja, desc, desc_ja, f"demo.{issue_type}", f"demo-{key}",
                        now, now, 0, metadata, rule, base_fp, 0,
                    ),
                )
                issue_id = int(cur.lastrowid)
            cur.execute(
                """
                insert into issue_analyses (
                    issue_id, generated_at, model_used, status, likely_cause, evidence,
                    recommended_action, remediation_type, likely_cause_en, evidence_en,
                    recommended_action_en, likely_cause_ja, evidence_ja, recommended_action_ja,
                    language_status
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(issue_id) do update set
                    generated_at=excluded.generated_at,
                    model_used=excluded.model_used,
                    status=excluded.status,
                    likely_cause=excluded.likely_cause,
                    evidence=excluded.evidence,
                    recommended_action=excluded.recommended_action,
                    remediation_type=excluded.remediation_type,
                    likely_cause_en=excluded.likely_cause_en,
                    evidence_en=excluded.evidence_en,
                    recommended_action_en=excluded.recommended_action_en,
                    likely_cause_ja=excluded.likely_cause_ja,
                    evidence_ja=excluded.evidence_ja,
                    recommended_action_ja=excluded.recommended_action_ja,
                    language_status=excluded.language_status
                """,
                (
                    issue_id, now, "seeded-rca-knowledge", "done", cause, evidence,
                    action, "infra_change", cause, evidence, action,
                    issue_description_ja(cause, app_name=app, rule_id=rule),
                    issue_description_ja(evidence, app_name=app, rule_id=rule),
                    issue_description_ja(action, app_name=app, rule_id=rule),
                    "ready",
                ),
            )
            ids.append(issue_id)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"created": created, "refreshed": refreshed, "total": len(CASES), "issue_ids": ids}))


if __name__ == "__main__":
    main()
