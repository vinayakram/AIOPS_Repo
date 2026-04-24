#!/usr/bin/env python3
"""
Guided end-to-end demo runner for:

  SampleAgent -> AIopsTelemetry -> Invastigate RCA -> Remediation

This script is demo-first rather than validator-first:
- narrates the architecture in English or Japanese
- keeps the existing operational checks
- showcases trace ingestion, issue detection, RCA, and remediation
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# ── Terminal colours ──────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"

DEMO_SPEEDS: dict[str, float] = {
    "fast": 0.35,
    "normal": 1.0,
    "slow": 1.8,
}


TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "AIops Guided Demo",
        "subtitle": "SampleAgent -> AIopsTelemetry -> Invastigate RCA -> Remediation",
        "intro": (
            "This walkthrough shows how a real Sample Agent request becomes an operational "
            "incident, how the RCA pipeline reasons over Langfuse and Prometheus evidence, "
            "and how the platform can continue into remediation."
        ),
        "services": "Services",
        "flow": "Story Flow",
        "flow_body": (
            "1. Ask a real sample question.\n"
            "2. Capture the trace and sync it into AIopsTelemetry.\n"
            "3. Raise a latency issue using synthetic pressure plus the real trace.\n"
            "4. Run the external multi-agent RCA pipeline.\n"
            "5. Trigger remediation and watch it complete."
        ),
        "why": "Why this is interesting",
        "why_body": (
            "The demo is intentionally simple for the audience, but the system underneath "
            "is not: application traces come from Langfuse, infrastructure evidence can come "
            "from Prometheus, AIopsTelemetry performs detection and lifecycle management, and "
            "Invastigate provides staged RCA across multiple agents."
        ),
        "step_health": "Health-check all services",
        "step_auth": "Authenticate with SampleAgent",
        "step_cleanup": "Resolve stale open latency issues",
        "step_seed": "Inject synthetic slow traces",
        "step_query": "Send real sample query",
        "step_register": "Register the real trace in AIopsTelemetry",
        "step_issue": "Wait for AIopsTelemetry to raise an issue",
        "step_trace": "Verify trace correlation",
        "step_rca_start": "Trigger external RCA pipeline",
        "step_rca_wait": "Wait for RCA completion",
        "step_validate": "Validate pipeline output",
        "step_rem_start": "Trigger remediation",
        "step_rem_wait": "Wait for remediation completion",
        "step_summary": "Executive Summary",
        "narr_health": "We first verify the three systems are reachable so the demo story is continuous.",
        "narr_auth": "We authenticate as a real user because the request should look like a normal product flow, not a mocked backend-only test.",
        "narr_cleanup": "We clear old open latency issues so this run creates a fresh, easy-to-explain incident.",
        "narr_seed": "We add synthetic slow traces to build enough pressure for the latency rule to fire, while keeping the final representative trace real.",
        "narr_query": "Now we send a real sample question through the RAG app and capture the trace_id that ties the whole demo together.",
        "narr_register": "SampleAgent forwards traces asynchronously, so we explicitly register the real trace in AIopsTelemetry to make the correlation deterministic for the demo.",
        "narr_issue": "AIopsTelemetry now acts as the operational brain: it sees the latency pattern and opens an issue.",
        "narr_trace": "This is the key handoff: the incident should point back to the trace that came from the actual user request.",
        "narr_rca_start": "The RCA service is multi-stage: normalization, correlation, root-cause analysis, and recommendations.",
        "narr_rca_wait": "While waiting, Invastigate can use Langfuse trace evidence and Prometheus metrics depending on what is available.",
        "narr_validate": "We verify that the external pipeline returned useful structure, not just a single text blob.",
        "narr_rem_start": "The last stage is remediation: AIopsTelemetry can hand the issue to an automated fix pipeline.",
        "narr_rem_wait": "A successful remediation run means the platform moved beyond detection and diagnosis into action.",
        "health_abort": "One or more services are unreachable. Start them before running the demo.",
        "auth_abort": "SampleAgent is not reachable for login.",
        "query_timeout": "SampleAgent query timed out after 120 seconds.",
        "no_issue_abort": "No latency issue appeared within the timeout window.",
        "rca_timeout": "RCA did not complete within the timeout window.",
        "rem_timeout": "Remediation did not complete within the timeout window.",
        "langfuse_missing": "No Langfuse URL in the response. Langfuse may not be configured.",
        "rem_skip": "Remediation step skipped by flag.",
        "rem_done": "Remediation completed.",
        "rem_failed": "Remediation failed.",
        "rem_unavailable": "Remediation endpoint unavailable or could not be started.",
        "journey": "Journey",
        "journey_body": "User query -> Langfuse trace -> AIopsTelemetry issue -> RCA -> remediation",
        "issue": "Issue",
        "root_cause": "Root Cause Analysis",
        "recommendations": "Recommendations",
        "pipeline": "Pipeline Steps",
        "validation": "Validation Checks",
        "remediation": "Remediation",
        "source_usage": "Evidence Sources",
        "final_pass": "The full demo flow completed successfully.",
        "final_warn": "The demo flow completed with partial success. Review the failed checks above.",
        "language": "Language",
        "demo_speed": "Demo Speed",
        "mode": "Mode",
        "presenter_hint": "Presenter mode: use 'slow' for narration, 'normal' for live walkthroughs, and 'fast' for rehearsal.",
        "mode_hint": "Modes: 'full' runs everything, 'rca-only' stops after diagnosis, 'remediation-only' emphasizes the fix pipeline.",
        "trace_history_hint": "You can cross-check this trace in SampleAgent dashboard or Langfuse while the script runs.",
    },
    "ja": {
        "title": "AIops ガイド付きデモ",
        "subtitle": "SampleAgent -> AIopsTelemetry -> Invastigate RCA -> Remediation",
        "intro": (
            "このデモは、実際の Sample Agent リクエストがどのように運用インシデントになり、"
            "Langfuse と Prometheus の証拠を使って RCA が実行され、最後に remediation まで進むかを示します。"
        ),
        "services": "サービス",
        "flow": "デモの流れ",
        "flow_body": (
            "1. 実際の医療質問を送信します。\n"
            "2. trace を取得して AIopsTelemetry に連携します。\n"
            "3. synthetic な負荷と実 trace を使って latency issue を発生させます。\n"
            "4. 外部のマルチエージェント RCA パイプラインを実行します。\n"
            "5. remediation を起動し、完了まで確認します。"
        ),
        "why": "このデモの見どころ",
        "why_body": (
            "観客にはシンプルに見せつつ、裏側では複数レイヤーが連携しています。"
            "アプリケーショントレースは Langfuse、インフラ証拠は Prometheus、"
            "AIopsTelemetry が検知とライフサイクル管理を行い、Invastigate が多段 RCA を実行します。"
        ),
        "step_health": "各サービスのヘルスチェック",
        "step_auth": "SampleAgent に認証",
        "step_cleanup": "古い latency issue を解決済みにする",
        "step_seed": "synthetic slow trace を注入",
        "step_query": "実際の医療質問を送信",
        "step_register": "実 trace を AIopsTelemetry に登録",
        "step_issue": "AIopsTelemetry が issue を起票するのを待つ",
        "step_trace": "trace の相関を確認",
        "step_rca_start": "外部 RCA パイプラインを起動",
        "step_rca_wait": "RCA 完了待ち",
        "step_validate": "パイプライン出力を検証",
        "step_rem_start": "remediation を起動",
        "step_rem_wait": "remediation 完了待ち",
        "step_summary": "エグゼクティブサマリー",
        "narr_health": "まず 3 つのサービスが到達可能かを確認し、デモ全体が止まらない状態にします。",
        "narr_auth": "バックエンドだけの擬似実行ではなく、実ユーザーの利用フローとして見せるため認証します。",
        "narr_cleanup": "過去の open issue があると説明しづらくなるので、このデモ用に新しい issue を作りやすくします。",
        "narr_seed": "latency ルールが確実に発火するよう synthetic trace を入れつつ、代表 trace は本物のユーザー要求にします。",
        "narr_query": "ここで実際の医療質問を投げ、その trace_id をデモ全体の共通キーとして使います。",
        "narr_register": "SampleAgent からの転送は非同期なので、デモでは相関を安定させるため実 trace を明示的に再登録します。",
        "narr_issue": "ここから AIopsTelemetry が運用レイヤーとして動き、latency パターンを見て issue を作成します。",
        "narr_trace": "重要な受け渡しポイントです。incident が実際のユーザーリクエスト由来の trace を指していることを確認します。",
        "narr_rca_start": "RCA は単発の要約ではなく、normalization、correlation、root cause、recommendation の多段処理です。",
        "narr_rca_wait": "待機中に Invastigate は、利用可能な証拠に応じて Langfuse trace や Prometheus metrics を使用します。",
        "narr_validate": "単なるテキスト 1 本ではなく、構造化された結果が返っていることを確認します。",
        "narr_rem_start": "最後は remediation です。AIopsTelemetry から自動修正パイプラインへ接続します。",
        "narr_rem_wait": "remediation が成功すれば、検知と診断だけでなく実際のアクションまで進めたことになります。",
        "health_abort": "1 つ以上のサービスに接続できません。起動してから再実行してください。",
        "auth_abort": "SampleAgent にログインできません。",
        "query_timeout": "SampleAgent のクエリが 120 秒でタイムアウトしました。",
        "no_issue_abort": "タイムアウト内に latency issue が発生しませんでした。",
        "rca_timeout": "タイムアウト内に RCA が完了しませんでした。",
        "rem_timeout": "タイムアウト内に remediation が完了しませんでした。",
        "langfuse_missing": "レスポンスに Langfuse URL がありません。Langfuse が未設定の可能性があります。",
        "rem_skip": "フラグにより remediation ステップをスキップしました。",
        "rem_done": "remediation が完了しました。",
        "rem_failed": "remediation に失敗しました。",
        "rem_unavailable": "remediation API を開始できないか、利用できませんでした。",
        "journey": "全体の流れ",
        "journey_body": "ユーザー質問 -> Langfuse trace -> AIopsTelemetry issue -> RCA -> remediation",
        "issue": "Issue",
        "root_cause": "根本原因分析",
        "recommendations": "推奨対応",
        "pipeline": "パイプラインステップ",
        "validation": "検証結果",
        "remediation": "Remediation",
        "source_usage": "使用した証拠ソース",
        "final_pass": "デモ全体が正常に完了しました。",
        "final_warn": "デモは部分成功です。失敗した項目を確認してください。",
        "language": "言語",
        "demo_speed": "デモ速度",
        "mode": "モード",
        "presenter_hint": "Presenter mode: narration 重視は 'slow'、通常デモは 'normal'、リハーサルは 'fast' を使ってください。",
        "mode_hint": "Modes: 'full' は全体実行、'rca-only' は診断まで、'remediation-only' は修正パイプラインを強調します。",
        "trace_history_hint": "実行中に SampleAgent ダッシュボードや Langfuse でこの trace を確認できます。",
    },
}


def tr(lang: str, key: str, **kwargs: Any) -> str:
    text = TEXT[lang][key]
    return text.format(**kwargs)


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {CYAN}→{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def _dim(msg: str) -> None:
    print(f"  {DIM}{msg}{RESET}")


def _step(n: int | str, title: str, narrative: str) -> None:
    bar = "─" * 64
    print(f"\n{BOLD}{BLUE}┌{bar}┐{RESET}")
    print(f"{BOLD}{BLUE}│  Step {str(n):<3}{title:<54}│{RESET}")
    print(f"{BOLD}{BLUE}└{bar}┘{RESET}")
    print(f"  {DIM}{narrative}{RESET}")


def _section(title: str) -> None:
    eq = "═" * 70
    print(f"\n{BOLD}{MAGENTA}{eq}{RESET}")
    print(f"{BOLD}{MAGENTA}  {title}{RESET}")
    print(f"{BOLD}{MAGENTA}{eq}{RESET}")


def _abort(msg: str) -> None:
    print(f"\n{RED}{BOLD}ABORT:{RESET} {RED}{msg}{RESET}")
    sys.exit(1)


def _pause(args: argparse.Namespace, seconds: float) -> None:
    if seconds <= 0:
        return
    time.sleep(seconds * DEMO_SPEEDS[args.demo_speed])


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Guided end-to-end demo: SampleAgent -> AIopsTelemetry -> Invastigate RCA -> Remediation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--sample-url", default="http://localhost:8002")
    p.add_argument("--aiops-url", default="http://localhost:7000")
    p.add_argument("--rca-url", default="http://localhost:8000")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin")
    p.add_argument(
        "--query",
        default="What are the latest treatment options for Type 2 Diabetes with cardiovascular complications?",
    )
    p.add_argument("--issue-poll-timeout", type=int, default=90)
    p.add_argument("--rca-poll-timeout", type=int, default=300)
    p.add_argument("--remediation-poll-timeout", type=int, default=300)
    p.add_argument("--lang", choices=("en", "ja"), default="en")
    p.add_argument("--demo-speed", choices=("fast", "normal", "slow"), default="normal")
    p.add_argument("--mode", choices=("full", "rca-only", "remediation-only"), default="full")
    p.add_argument("--skip-remediation", action="store_true")
    return p.parse_args()


def _get(url: str, **kwargs: Any) -> requests.Response:
    return requests.get(url, timeout=15, **kwargs)


def _post(url: str, timeout: int = 30, **kwargs: Any) -> requests.Response:
    return requests.post(url, timeout=timeout, **kwargs)


def print_intro(args: argparse.Namespace) -> None:
    lang = args.lang
    print(f"\n{BOLD}{WHITE}{'═'*70}{RESET}")
    print(f"{BOLD}{WHITE}  {tr(lang, 'title')}{RESET}")
    print(f"{BOLD}{WHITE}  {tr(lang, 'subtitle')}{RESET}")
    print(f"{BOLD}{WHITE}{'═'*70}{RESET}")
    print(f"\n  {DIM}{tr(lang, 'intro')}{RESET}")

    _section(tr(lang, "services"))
    print(f"  {BOLD}SampleAgent   {RESET}: {args.sample_url}")
    print(f"  {BOLD}AIopsTelemetry {RESET}: {args.aiops_url}")
    print(f"  {BOLD}Invastigate RCA{RESET}: {args.rca_url}")
    print(f"  {BOLD}{tr(lang, 'language')}{RESET}: {args.lang}")
    print(f"  {BOLD}{tr(lang, 'demo_speed')}{RESET}: {args.demo_speed}")
    print(f"  {BOLD}{tr(lang, 'mode')}{RESET}: {args.mode}")
    _dim(tr(lang, "presenter_hint"))
    _dim(tr(lang, "mode_hint"))

    _section(tr(lang, "flow"))
    for line in tr(lang, "flow_body").splitlines():
        print(f"  {line}")

    _section(tr(lang, "why"))
    print(f"  {tr(lang, 'why_body')}")


def step1_health(args: argparse.Namespace) -> None:
    lang = args.lang
    _step(1, tr(lang, "step_health"), tr(lang, "narr_health"))
    _pause(args, 0.4)
    checks = [
        ("SampleAgent", f"{args.sample_url}/api/health"),
        ("AIopsTelemetry", f"{args.aiops_url}/health"),
        ("Invastigate RCA", f"{args.rca_url}/health"),
    ]
    any_down = False
    for name, url in checks:
        try:
            r = _get(url)
            if r.status_code < 400:
                _ok(f"{name} — HTTP {r.status_code} ({url})")
            else:
                _warn(f"{name} — HTTP {r.status_code} ({url})")
                any_down = True
        except requests.exceptions.ConnectionError:
            _fail(f"{name} — connection refused ({url})")
            any_down = True
        except requests.exceptions.Timeout:
            _warn(f"{name} — timeout ({url})")
            any_down = True

    if any_down:
        _abort(tr(lang, "health_abort"))


def step2_auth(args: argparse.Namespace) -> str:
    lang = args.lang
    _step(2, tr(lang, "step_auth"), tr(lang, "narr_auth"))
    _pause(args, 0.4)
    _info(f"POST {args.sample_url}/auth/login (user={args.username})")
    try:
        r = _post(
            f"{args.sample_url}/auth/login",
            data={"username": args.username, "password": args.password},
        )
    except requests.exceptions.ConnectionError:
        _abort(tr(lang, "auth_abort"))

    if r.status_code == 401:
        _abort(f"Login failed ({r.json().get('detail', '')})")
    if r.status_code != 200:
        _abort(f"Login failed HTTP {r.status_code}: {r.text[:200]}")

    token = r.json().get("access_token")
    if not token:
        _abort("Login response did not include access_token.")

    _ok(f"Authenticated as '{r.json().get('username')}'")
    return token


def step2b_resolve_stale_issues(args: argparse.Namespace) -> None:
    lang = args.lang
    _step("2b", tr(lang, "step_cleanup"), tr(lang, "narr_cleanup"))
    _pause(args, 0.35)
    try:
        r = _get(
            f"{args.aiops_url}/api/issues",
            params={"app_name": "sample-agent", "status": "OPEN", "limit": 50},
        )
        if r.status_code != 200:
            _warn(f"Could not fetch issues (HTTP {r.status_code})")
            return
        issues = r.json().get("issues", [])
        latency_issues = [
            i for i in issues
            if i.get("rule_id") in ("NFR-7", "NFR-7a")
            or i.get("issue_type") == "nfr_response_time"
        ]
        if not latency_issues:
            _ok("No stale latency issues found")
            return
        for issue in latency_issues:
            rr = _post(f"{args.aiops_url}/api/issues/{issue['id']}/resolve")
            if rr.status_code == 200:
                _ok(f"Resolved stale issue #{issue['id']}")
            else:
                _warn(f"Could not resolve issue #{issue['id']} (HTTP {rr.status_code})")
    except requests.exceptions.RequestException as exc:
        _warn(f"Stale-issue cleanup failed: {exc}")


def step3_seed_traces(args: argparse.Namespace) -> list[str]:
    lang = args.lang
    _step(3, tr(lang, "step_seed"), tr(lang, "narr_seed"))
    _pause(args, 0.45)
    ingest_url = f"{args.aiops_url}/api/ingest/trace"
    now = datetime.now(timezone.utc)
    injected: list[str] = []

    for i in range(4):
        trace_id = uuid.uuid4().hex
        started = now - timedelta(seconds=(4 - i) * 15)
        ended = started + timedelta(milliseconds=8500)
        payload = {
            "id": trace_id,
            "app_name": "sample-agent",
            "status": "ok",
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "total_duration_ms": 8500.0,
            "input_preview": f"[guided-demo synthetic trace {i + 1}/4]",
            "output_preview": "Synthetic slow trace for latency threshold demonstration.",
            "spans": [
                {
                    "id": uuid.uuid4().hex,
                    "trace_id": trace_id,
                    "name": "openai_generation",
                    "span_type": "llm",
                    "status": "ok",
                    "started_at": started.isoformat(),
                    "ended_at": ended.isoformat(),
                    "duration_ms": 8500.0,
                }
            ],
        }
        try:
            r = _post(ingest_url, json=payload)
            if r.status_code == 200:
                injected.append(trace_id)
                _ok(f"Synthetic trace {i + 1}/4 ingested ({trace_id[:12]}...)")
            else:
                _warn(f"Synthetic trace {i + 1}/4 returned HTTP {r.status_code}")
        except requests.exceptions.RequestException as exc:
            _warn(f"Synthetic trace {i + 1}/4 failed: {exc}")

    if not injected:
        _abort("No synthetic traces could be injected.")
    _info(f"{len(injected)} synthetic traces injected")
    return injected


def step4_query(args: argparse.Namespace, token: str) -> tuple[str, float, str | None, str]:
    lang = args.lang
    _step(4, tr(lang, "step_query"), tr(lang, "narr_query"))
    _pause(args, 0.5)
    _info(f'Query: "{args.query[:120]}{"..." if len(args.query) > 120 else ""}"')
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    try:
        r = _post(
            f"{args.sample_url}/api/query",
            json={"query": args.query, "max_articles": 10, "top_k": 3},
            headers=headers,
            timeout=120,
        )
    except requests.exceptions.Timeout:
        _abort(tr(lang, "query_timeout"))
    except requests.exceptions.RequestException as exc:
        _abort(f"SampleAgent query failed: {exc}")

    elapsed = time.time() - t0
    if r.status_code != 200:
        _abort(f"Query returned HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    trace_id = data.get("trace_id")
    answer = data.get("answer", "")
    if not trace_id:
        _abort("Query response did not include trace_id.")

    langfuse_url = data.get("langfuse_url")
    _ok(f"Query completed in {elapsed:.1f}s")
    _ok(f"trace_id = {trace_id}")
    _dim(f"Answer preview: {answer[:140]}{'...' if len(answer) > 140 else ''}")
    if langfuse_url:
        _info(f"Langfuse trace: {langfuse_url}")
        _dim(tr(lang, "trace_history_hint"))
    else:
        _warn(tr(lang, "langfuse_missing"))

    return trace_id, elapsed * 1000, langfuse_url, answer


def step4b_ingest_real_trace(args: argparse.Namespace, real_trace_id: str, duration_ms: float) -> None:
    lang = args.lang
    _step("4b", tr(lang, "step_register"), tr(lang, "narr_register"))
    _pause(args, 0.35)
    now = datetime.now(timezone.utc)
    started = now - timedelta(milliseconds=duration_ms)
    payload = {
        "id": real_trace_id,
        "app_name": "sample-agent",
        "status": "ok",
        "started_at": started.isoformat(),
        "ended_at": now.isoformat(),
        "total_duration_ms": duration_ms,
        "input_preview": "[guided-demo real sample query]",
        "output_preview": "Real SampleAgent response registered for deterministic RCA correlation.",
        "spans": [
            {
                "id": uuid.uuid4().hex,
                "trace_id": real_trace_id,
                "name": "rag_pipeline",
                "span_type": "chain",
                "status": "ok",
                "started_at": started.isoformat(),
                "ended_at": now.isoformat(),
                "duration_ms": duration_ms,
            }
        ],
    }
    try:
        r = _post(f"{args.aiops_url}/api/ingest/trace", json=payload)
        if r.status_code == 200:
            _ok(f"Real trace registered ({real_trace_id[:12]}... / {duration_ms / 1000:.1f}s)")
        else:
            _warn(f"Real trace ingest returned HTTP {r.status_code}")
    except requests.exceptions.RequestException as exc:
        _warn(f"Real trace ingest failed: {exc}")


def step5_wait_for_issue(args: argparse.Namespace, real_trace_id: str) -> dict[str, Any]:
    lang = args.lang
    _step(5, tr(lang, "step_issue"), tr(lang, "narr_issue"))
    _pause(args, 0.45)
    _info(f"Polling {args.aiops_url}/api/issues every 5s")
    deadline = time.time() + args.issue_poll_timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"  {DIM}[{attempt:>2}] checking... ({remaining}s remaining){RESET}", end="\r")
        try:
            r = _get(
                f"{args.aiops_url}/api/issues",
                params={"app_name": "sample-agent", "status": "OPEN", "limit": 50},
            )
            if r.status_code == 200:
                issues = r.json().get("issues", [])
                latency_issues = [
                    i for i in issues
                    if i.get("issue_type") == "nfr_response_time"
                    or i.get("rule_id") in ("NFR-7", "NFR-7a")
                ]
                if latency_issues:
                    issue = next(
                        (i for i in latency_issues if i.get("trace_id") == real_trace_id),
                        latency_issues[0],
                    )
                    print()
                    _ok(
                        f"Issue #{issue['id']} found "
                        f"(rule={issue.get('rule_id', '?')} severity={issue.get('severity', '?')})"
                    )
                    _ok(f"title: {issue.get('title', '')}")
                    _ok(f"trace_id: {issue.get('trace_id') or '(none)'}")
                    return issue
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    print()
    _abort(tr(lang, "no_issue_abort"))


def step6_assert_trace_id(args: argparse.Namespace, issue: dict[str, Any], real_trace_id: str) -> None:
    lang = args.lang
    _step(6, tr(lang, "step_trace"), tr(lang, "narr_trace"))
    _pause(args, 0.3)
    issue_trace = issue.get("trace_id")
    if issue_trace == real_trace_id:
        _ok(f"Representative trace matches the SampleAgent request ({real_trace_id})")
    elif issue_trace:
        _warn(f"Issue trace differs from the real query trace ({issue_trace} != {real_trace_id})")
    else:
        _warn("Issue has no trace_id; RCA may fall back to legacy analysis")


def step7_trigger_rca(args: argparse.Namespace, issue_id: int) -> None:
    lang = args.lang
    _step(7, tr(lang, "step_rca_start"), tr(lang, "narr_rca_start"))
    _pause(args, 0.45)
    url = f"{args.aiops_url}/api/analysis/issues/{issue_id}"
    _info(f"POST {url}?force=true")
    try:
        r = _post(f"{url}?force=true")
    except requests.exceptions.RequestException as exc:
        _abort(f"RCA trigger request failed: {exc}")
    if r.status_code not in (200, 202):
        _abort(f"RCA trigger returned HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    _ok(f"RCA accepted (status={data.get('status', '?')} analysis_id={data.get('id', '?')})")


def step8_wait_for_rca(args: argparse.Namespace, issue_id: int) -> dict[str, Any]:
    lang = args.lang
    _step(8, tr(lang, "step_rca_wait"), tr(lang, "narr_rca_wait"))
    _pause(args, 0.45)
    url = f"{args.aiops_url}/api/analysis/issues/{issue_id}"
    deadline = time.time() + args.rca_poll_timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"  {DIM}[{attempt:>2}] polling... ({remaining}s remaining){RESET}", end="\r")
        try:
            r = _get(url)
            if r.status_code == 200:
                data = r.json()
                status = data.get("status")
                if status == "done":
                    print()
                    _ok(
                        f"RCA complete "
                        f"(model={data.get('model_used', '?')} generated_at={data.get('generated_at', '?')})"
                    )
                    return data
                if status == "failed":
                    print()
                    _abort(f"RCA failed: {data.get('full_summary', '(no detail)')[:300]}")
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    print()
    _abort(tr(lang, "rca_timeout"))


def step9_validate(args: argparse.Namespace, analysis: dict[str, Any]) -> dict[str, bool]:
    lang = args.lang
    _step(9, tr(lang, "step_validate"), tr(lang, "narr_validate"))
    _pause(args, 0.35)
    checks: dict[str, bool] = {}

    def chk(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = condition
        suffix = f" — {detail}" if detail else ""
        if condition:
            _ok(f"{name}{suffix}")
        else:
            _fail(f"{name}{suffix}")

    rca_data = analysis.get("rca_data") or analysis.get("data") or {}
    steps = rca_data.get("pipeline_steps") or []
    norm_response = rca_data.get("normalization") or {}
    rca_response = rca_data.get("rca") or {}
    rec_response = rca_data.get("recommendations") or {}
    rca_block = rca_response.get("rca") or rca_response
    rec_block = rec_response.get("recommendations") or rec_response

    chk("status = done", analysis.get("status") == "done")
    chk("model_used present", bool(analysis.get("model_used")))
    chk("rca_data present", bool(rca_data))
    chk("pipeline_steps present", bool(steps), f"{len(steps)} steps")
    chk("normalization present", bool(norm_response))
    chk("rca present", bool(rca_response))
    chk("recommendations present", bool(rec_response))
    chk("root_cause present", bool(rca_block.get("root_cause")))
    chk("rca_summary present", bool(rca_block.get("rca_summary")))
    chk("solutions present", bool(rec_block.get("solutions") or []))

    data_sources = sorted(
        set((rca_block.get("data_sources") or []) + (rec_block.get("data_sources") or []))
    )
    chk("evidence source captured", bool(data_sources), ", ".join(data_sources))
    return checks


def step10_trigger_remediation(args: argparse.Namespace, issue_id: int) -> str | None:
    lang = args.lang
    _step(10, tr(lang, "step_rem_start"), tr(lang, "narr_rem_start"))
    _pause(args, 0.45)
    if args.skip_remediation:
        _warn(tr(lang, "rem_skip"))
        return None
    try:
        r = _post(f"{args.aiops_url}/api/issues/{issue_id}/autofix")
    except requests.exceptions.RequestException as exc:
        _warn(f"{tr(lang, 'rem_unavailable')}: {exc}")
        return None
    if r.status_code not in (200, 202):
        _warn(f"{tr(lang, 'rem_unavailable')}: HTTP {r.status_code} {r.text[:160]}")
        return None
    data = r.json()
    job_id = data.get("job_id")
    if not job_id:
        _warn(tr(lang, "rem_unavailable"))
        return None
    _ok(f"Remediation job started (job_id={job_id})")
    return job_id


def step11_wait_for_remediation(args: argparse.Namespace, issue_id: int, job_id: str | None) -> dict[str, Any] | None:
    lang = args.lang
    _step(11, tr(lang, "step_rem_wait"), tr(lang, "narr_rem_wait"))
    _pause(args, 0.35)
    if not job_id:
        _warn(tr(lang, "rem_skip"))
        return None
    url = f"{args.aiops_url}/api/issues/autofix/{job_id}"
    deadline = time.time() + args.remediation_poll_timeout
    last_output_len = 0
    while time.time() < deadline:
        try:
            r = _get(url)
            if r.status_code == 200:
                job = r.json()
                output = job.get("output") or ""
                if len(output) > last_output_len:
                    new_chunk = output[last_output_len:].strip()
                    if new_chunk:
                        for line in new_chunk.splitlines()[-6:]:
                            _dim(line)
                    last_output_len = len(output)
                status = job.get("status")
                if status == "done":
                    _ok(tr(lang, "rem_done"))
                    rr = _post(f"{args.aiops_url}/api/issues/{issue_id}/resolve")
                    if rr.status_code == 200:
                        _ok(f"Issue #{issue_id} marked RESOLVED")
                    return job
                if status == "failed":
                    _warn(tr(lang, "rem_failed"))
                    return job
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    _warn(tr(lang, "rem_timeout"))
    return None


def step12_summary(
    args: argparse.Namespace,
    trace_id: str,
    langfuse_url: str | None,
    issue: dict[str, Any],
    analysis: dict[str, Any],
    checks: dict[str, bool],
    remediation_job: dict[str, Any] | None,
) -> None:
    lang = args.lang
    _step(12, tr(lang, "step_summary"), tr(lang, "journey_body"))
    _pause(args, 0.5)

    rca_data = analysis.get("rca_data") or analysis.get("data") or {}
    norm_response = rca_data.get("normalization") or {}
    norm_block = norm_response.get("incident") or norm_response
    rca_response = rca_data.get("rca") or {}
    rca_block = rca_response.get("rca") or rca_response
    rec_response = rca_data.get("recommendations") or {}
    rec_block = rec_response.get("recommendations") or rec_response

    _section(tr(lang, "journey"))
    print(f"  {tr(lang, 'journey_body')}")

    _section(tr(lang, "issue"))
    print(f"  {BOLD}Issue ID   {RESET}: {issue.get('id')}")
    print(f"  {BOLD}App        {RESET}: {issue.get('app_name')}")
    print(f"  {BOLD}Rule       {RESET}: {issue.get('rule_id')} — {issue.get('title')}")
    print(f"  {BOLD}Severity   {RESET}: {issue.get('severity')}")
    print(f"  {BOLD}Status     {RESET}: {issue.get('status')}")
    print(f"  {BOLD}Trace ID   {RESET}: {issue.get('trace_id')}")
    print(f"  {BOLD}Real Trace {RESET}: {trace_id}")
    if langfuse_url:
        print(f"  {BOLD}Langfuse   {RESET}: {langfuse_url}")

    _section(tr(lang, "source_usage"))
    sources = []
    for block in (norm_response, rca_block, rec_block):
        sources.extend(block.get("data_sources") or [])
    sources = sorted(set(sources))
    if sources:
        print(f"  {', '.join(sources)}")
    else:
        print("  (not provided in response)")

    _section(tr(lang, "root_cause"))
    root_cause = rca_block.get("root_cause") or {}
    print(f"  {BOLD}Summary    {RESET}: {rca_block.get('rca_summary', '—')}")
    print(f"  {BOLD}Component  {RESET}: {root_cause.get('component', '—')}")
    print(f"  {BOLD}Category   {RESET}: {root_cause.get('category', '—')}")
    print(f"  {BOLD}Confidence {RESET}: {root_cause.get('confidence', '—')}")
    print(f"  {BOLD}Description{RESET}: {root_cause.get('description', '—')}")

    evidence = root_cause.get("evidence") or []
    if evidence:
        print(f"\n  {BOLD}Evidence{RESET}:")
        for item in evidence[:4]:
            print(f"    • {item}")

    if norm_block:
        print(f"\n  {BOLD}Normalization{RESET}:")
        print(f"    error_type={norm_block.get('error_type', '—')}")
        print(f"    confidence={norm_block.get('confidence', '—')}")

    _section(tr(lang, "recommendations"))
    summary = rec_block.get("recommendation_summary")
    if summary:
        print(f"  {summary}\n")
    for sol in (rec_block.get("solutions") or [])[:3]:
        root_flag = " [root-cause]" if sol.get("addresses_root_cause") else ""
        print(f"  #{sol.get('rank', '?')} {sol.get('title', '')}{root_flag}")
        print(f"     {sol.get('description', '')}")
        print(f"     effort={sol.get('effort', '')} category={sol.get('category', '')}")

    _section(tr(lang, "pipeline"))
    for step in (rca_data.get("pipeline_steps") or []):
        name = step.get("agent") or step.get("name") or "?"
        status = (step.get("status") or "?").upper()
        ms = step.get("processing_time_ms") or 0
        print(f"  {status:<12} {name:<20} {ms/1000:.1f}s")
    total = rca_data.get("total_processing_time_ms") or 0
    if total:
        print(f"\n  {BOLD}Total time{RESET}: {total/1000:.1f}s")

    _section(tr(lang, "remediation"))
    if args.skip_remediation:
        print(f"  {tr(lang, 'rem_skip')}")
    elif remediation_job:
        print(f"  {BOLD}Status {RESET}: {remediation_job.get('status')}")
        print(f"  {BOLD}Job ID {RESET}: {remediation_job.get('job_id')}")
        print(f"  {BOLD}App    {RESET}: {remediation_job.get('app_name')}")
        finished = remediation_job.get("finished_at") or "—"
        print(f"  {BOLD}Ended  {RESET}: {finished}")
    else:
        print(f"  {tr(lang, 'rem_unavailable')}")

    _section(tr(lang, "validation"))
    passed = 0
    for name, ok_val in checks.items():
        mark = f"{GREEN}PASS{RESET}" if ok_val else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")
        if ok_val:
            passed += 1

    if remediation_job and remediation_job.get("status") == "done":
        passed += 1
        print(f"  [{GREEN}PASS{RESET}] remediation job completed")
    elif not args.skip_remediation:
        print(f"  [{YELLOW}WARN{RESET}] remediation not completed")

    print()
    if passed >= len(checks):
        print(f"{BOLD}{GREEN}  ✓ {tr(lang, 'final_pass')}{RESET}\n")
    else:
        print(f"{BOLD}{YELLOW}  ⚠ {tr(lang, 'final_warn')}{RESET}\n")


def main() -> None:
    args = _parse()
    if args.mode == "rca-only":
        args.skip_remediation = True
    print_intro(args)

    step1_health(args)
    token = step2_auth(args)
    step2b_resolve_stale_issues(args)
    step3_seed_traces(args)
    trace_id, duration_ms, langfuse_url, _answer = step4_query(args, token)
    step4b_ingest_real_trace(args, trace_id, duration_ms)
    issue = step5_wait_for_issue(args, trace_id)
    step6_assert_trace_id(args, issue, trace_id)
    step7_trigger_rca(args, issue["id"])
    analysis = step8_wait_for_rca(args, issue["id"])
    checks = step9_validate(args, analysis) if args.mode != "remediation-only" else {}
    remediation_job_id = None
    remediation_job = None
    if args.mode in ("full", "remediation-only"):
        remediation_job_id = step10_trigger_remediation(args, issue["id"])
        remediation_job = step11_wait_for_remediation(args, issue["id"], remediation_job_id)

    try:
        latest_issue = _get(f"{args.aiops_url}/api/issues/{issue['id']}").json()
    except requests.exceptions.RequestException:
        latest_issue = issue

    step12_summary(
        args,
        trace_id,
        langfuse_url,
        latest_issue,
        analysis,
        checks,
        remediation_job,
    )


if __name__ == "__main__":
    main()
