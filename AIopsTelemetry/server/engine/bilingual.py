from __future__ import annotations

import re
from typing import Any


LANG_JA = "ja"
LANG_EN = "en"


def normalize_lang(lang: str | None) -> str:
    return LANG_EN if (lang or "").lower().startswith("en") else LANG_JA


def app_display_name_ja(app_name: str | None) -> str:
    names = {
        "sample-agent": "サンプル医療検索エージェント",
        "sample_agent": "サンプル医療検索エージェント",
        "sampleagent": "サンプル医療検索エージェント",
        "medical-agent": "医療検索エージェント",
        "medical-search-api": "医療検索API",
        "triage-agent": "トリアージエージェント",
        "observability-gateway": "監視ゲートウェイ",
        "prometheus-bridge": "Prometheusブリッジ",
        "joshu-chat": "Joshuチャット",
        "rca-worker": "RCAワーカー",
        "trace-store": "トレースストア",
        "gateway-api": "ゲートウェイAPI",
        "rca-assistant": "RCAアシスタント",
        "web-search-agent": "Web検索エージェント",
    }
    key = (app_name or "").lower()
    return names.get(key, app_name or "対象サービス")


def issue_title_ja(title: str | None, *, app_name: str | None = None, rule_id: str | None = None) -> str:
    """Return a concise Japanese title for known detector titles.

    This is intentionally deterministic so issue-list rendering never waits on
    an LLM. Unknown titles are kept as-is.
    """
    text = title or ""
    app = app_name or _extract_app(text)
    app_ja = app_display_name_ja(app)
    industry_titles = [
        (r"oom|out of memory|memory pressure|container killed", f"{app_ja}がメモリ不足で停止しました"),
        (r"disk|volume|space|inode", f"{app_ja}のストレージ容量が不足しています"),
        (r"connection pool|database.*pool", f"{app_ja}のDB接続プールが枯渇しています"),
        (r"dns|resolution", f"{app_ja}でDNS解決に失敗しています"),
        (r"tls|certificate|secret", f"{app_ja}の証明書またはシークレットに問題があります"),
        (r"deployment|latest deployment|regression", f"{app_ja}でデプロイ後の不具合が発生しています"),
        (r"queue|backlog|worker queue", f"{app_ja}のキュー滞留が増えています"),
        (r"cache|stampede|cache miss", f"{app_ja}でキャッシュ起因の遅延が発生しています"),
        (r"autoscal|did not scale|scale during cpu", f"{app_ja}の自動スケールが作動していません"),
        (r"storage.*latency|i/o|iops|write latency", f"{app_ja}のストレージI/Oが飽和しています"),
        (r"worker pool|thread pool|workers are busy", f"{app_ja}のワーカープールが枯渇しています"),
    ]
    for pattern, translated in industry_titles:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return translated
    if rule_id == "NFR-33" or re.search(r"application not reachable", text, flags=re.IGNORECASE):
        return f"{app_ja}が利用できません"
    if rule_id == "NFR-30" or re.search(r"preprocess|query validation|special character", text, flags=re.IGNORECASE):
        return f"{app_ja}の入力確認でエラーが発生しました"
    if rule_id in {"NFR-31", "NFR-32"} or re.search(r"llm|rate limit|openai", text, flags=re.IGNORECASE):
        return f"{app_ja}のAI応答でエラーが発生しました"
    replacements = [
        (r"^3 consecutive trace failures in (.+)$", r"\1 で3回連続のトレース失敗"),
        (r"^HTTP error rate >=?5% in (.+)$", r"\1 でHTTPエラー率が5%以上"),
        (r"^HTTP error rate ≥5% in (.+)$", r"\1 でHTTPエラー率が5%以上"),
        (r"^HTTP error rate >=?1% in (.+)$", r"\1 でHTTPエラー率が1%以上"),
        (r"^HTTP error rate ≥1% in (.+)$", r"\1 でHTTPエラー率が1%以上"),
        (r"^Exception count doubled in (.+)$", r"\1 で例外件数が倍増"),
        (r"^Response time 2x target in (.+)$", r"\1 で応答時間が目標の2倍を超過"),
        (r"^Response time exceeds target in (.+)$", r"\1 で応答時間が目標を超過"),
        (r"^p95 response time 2x target in (.+)$", r"\1 でp95応答時間が目標の2倍を超過"),
        (r"^p95 response time exceeds target in (.+)$", r"\1 でp95応答時間が目標を超過"),
        (r"^Execution time drift in (.+)$", r"\1 で実行時間が悪化"),
        (r"^Consecutive LLM failures in (.+)$", r"\1 でLLM呼び出しが連続失敗"),
        (r"^LLM failure rate .* in (.+)$", r"\1 でLLM失敗率が上昇"),
        (r"^Timeout rate .* in (.+)$", r"\1 でタイムアウト率が上昇"),
        (r"^Token usage spike in (.+)$", r"\1 でトークン使用量が急増"),
        (r"^Output error detected in (.+)$", r"\1 の出力にエラー兆候を検出"),
    ]
    for pattern, repl in replacements:
        if re.search(pattern, text, flags=re.IGNORECASE):
            translated = re.sub(pattern, repl, text, flags=re.IGNORECASE)
            return translated.replace(app or "", app_ja)

    if rule_id:
        return f"{app_ja}で対応が必要な問題を検出しました"
    return text


def issue_description_ja(
    description: str | None,
    *,
    app_name: str | None = None,
    rule_id: str | None = None,
) -> str | None:
    if not description:
        return description
    app_ja = app_display_name_ja(app_name)
    lowered = description.lower()
    if rule_id == "NFR-33" or "application is not reachable" in lowered:
        pressure = []
        if "cpu" in lowered:
            pressure.append("CPU")
        if "memory" in lowered:
            pressure.append("メモリ")
        pressure_text = "と".join(pressure) if pressure else "実行環境のリソース"
        return (
            f"{app_ja}が一時的に利用できない状態です。"
            f"{pressure_text}の使用状況が安全基準を超えたため、"
            "システムが保護のためにアクセスを止めた可能性があります。"
            "RCAで実際の負荷としきい値設定を確認してください。"
        )
    if rule_id == "NFR-30" or "query" in lowered and "preprocess" in lowered:
        return (
            f"{app_ja}が利用者の入力を処理する前の確認で失敗しました。"
            "入力文字の扱いまたはバリデーション設定を確認してください。"
        )
    industry_descriptions = [
        (("memory", "limit"), f"{app_ja}のメモリ使用量が割り当て上限に近づき、コンテナ再起動や依存サービスの失敗につながっています。まずメモリ容量、レプリカ数、負荷状況を確認してください。"),
        (("disk",), f"{app_ja}でディスク容量またはinodeが不足し、書き込み処理が不安定になっています。不要ファイル、ログローテーション、ボリューム拡張を確認してください。"),
        (("space", "write"), f"{app_ja}でディスク容量またはinodeが不足し、書き込み処理が不安定になっています。不要ファイル、ログローテーション、ボリューム拡張を確認してください。"),
        (("connection", "database"), f"{app_ja}でデータベース接続待ちが発生しています。接続プール設定、最大接続数、長時間保持されている接続を確認してください。"),
        (("dns",), f"{app_ja}から依存先ホスト名を解決できない状態です。DNSリゾルバ、サービスディスカバリ、ネットワーク設定を確認してください。"),
        (("cannot resolve",), f"{app_ja}から依存先ホスト名を解決できない状態です。DNSリゾルバ、サービスディスカバリ、ネットワーク設定を確認してください。"),
        (("tls",), f"{app_ja}でTLSハンドシェイクに失敗しています。証明書の期限、シークレット更新、サービスの再読み込み状態を確認してください。"),
        (("certificate",), f"{app_ja}で証明書の期限または更新に問題があります。証明書をローテーションし、アプリが新しいシークレットを読み込んでいるか確認してください。"),
        (("deployment",), f"{app_ja}で直近のデプロイ後に不具合が発生しています。影響が継続している場合はロールバックを優先し、差分とトレースを確認してください。"),
        (("queue",), f"{app_ja}で処理待ちキューが増加しています。ワーカー数、処理時間、下流依存の遅延を確認してください。"),
        (("worker throughput",), f"{app_ja}で処理待ちキューが増加しています。ワーカー数、処理時間、下流依存の遅延を確認してください。"),
        (("cache",), f"{app_ja}でキャッシュミスが増え、検索や依存先の負荷が上がっています。TTL、ウォームアップ、リクエスト集約を確認してください。"),
        (("cpu", "scale"), f"{app_ja}でCPU負荷が続いていますが、自動スケールが期待通りに動いていません。スケーリング指標、上限、クールダウン設定を確認してください。"),
        (("cpu", "capacity"), f"{app_ja}でCPU負荷が続いていますが、自動スケールが期待通りに動いていません。スケーリング指標、上限、クールダウン設定を確認してください。"),
        (("storage", "latency"), f"{app_ja}でストレージ書き込み遅延が増加しています。IOPS、スループット、書き込み量の急増を確認してください。"),
        (("workers", "busy"), f"{app_ja}でワーカープールが埋まり、リクエスト待ちが発生しています。ワーカー数とブロッキング処理を確認してください。"),
        (("429",), f"{app_ja}でLLMプロバイダーのレート制限が発生しています。リクエスト量、トークン量、バックオフ設定を確認してください。"),
    ]
    for needles, translated in industry_descriptions:
        if all(needle in lowered for needle in needles):
            return translated
    text = description
    replacements = {
        "Last 3 traces all ended with status=error": "直近3件のトレースがすべて error 状態で終了しました。",
        "traces failed": "件のトレースが失敗",
        "in last": "直近",
        "minutes": "分",
        "Avg": "平均",
        "exceeds": "が超過",
        "target": "目標",
        "over last": "直近",
        "Recent window": "現在の時間枠",
        "previous window": "前の時間枠",
        "errors": "エラー",
        "increase": "増加",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\bmin\b", "分", text)
    return text


def bilingual_analysis_fields(
    *,
    likely_cause: str | None,
    evidence: str | None,
    recommended_action: str | None,
    full_summary: str | None,
    rca_data: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Build bilingual RCA fields from native data when present, with fallback.

    The external RCA service may later return explicit *_ja fields; this helper
    already prefers them. Until then, it creates stable Japanese operational text
    from the structured English RCA so reads stay instant and offline-safe.
    """
    rca_data = rca_data or {}
    extracted = _extract_language_blocks(rca_data)
    en = {
        "likely_cause": extracted.get("likely_cause_en") or likely_cause,
        "evidence": extracted.get("evidence_en") or evidence,
        "recommended_action": extracted.get("recommended_action_en") or recommended_action,
        "full_summary": extracted.get("full_summary_en") or full_summary,
    }
    ja = {
        "likely_cause": extracted.get("likely_cause_ja") or _rca_text_ja(en["likely_cause"], "cause"),
        "evidence": extracted.get("evidence_ja") or _evidence_ja(en["evidence"]),
        "recommended_action": extracted.get("recommended_action_ja") or _rca_text_ja(en["recommended_action"], "action"),
        "full_summary": extracted.get("full_summary_ja") or _summary_ja(en["full_summary"], en),
    }
    return {
        "likely_cause_en": en["likely_cause"],
        "evidence_en": en["evidence"],
        "recommended_action_en": en["recommended_action"],
        "full_summary_en": en["full_summary"],
        "likely_cause_ja": ja["likely_cause"],
        "evidence_ja": ja["evidence"],
        "recommended_action_ja": ja["recommended_action"],
        "full_summary_ja": ja["full_summary"],
        "language_status": "ready",
    }


def select_text(row: Any, base: str, lang: str | None) -> str | None:
    lang = normalize_lang(lang)
    preferred = getattr(row, f"{base}_{lang}", None)
    fallback_lang = LANG_EN if lang == LANG_JA else LANG_JA
    fallback = getattr(row, f"{base}_{fallback_lang}", None)
    legacy = getattr(row, base, None)
    return preferred or fallback or legacy


def _extract_app(text: str) -> str | None:
    match = re.search(r"\bin\s+([A-Za-z0-9_.-]+)$", text or "")
    return match.group(1) if match else None


def _extract_language_blocks(data: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key in (
        "likely_cause_en", "likely_cause_ja", "evidence_en", "evidence_ja",
        "recommended_action_en", "recommended_action_ja",
        "full_summary_en", "full_summary_ja",
    ):
        val = _deep_find(data, key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def _deep_find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for val in obj.values():
            found = _deep_find(val, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find(item, key)
            if found is not None:
                return found
    return None


def _rca_text_ja(text: str | None, kind: str) -> str | None:
    if not text:
        return text
    lowered = text.lower()
    if "no error detected" in lowered or "no action required" in lowered:
        return "現在の分析対象では異常は検出されませんでした。" if kind == "cause" else "追加対応は不要です。監視を継続してください。"
    if "pod" in lowered or "threshold" in lowered or "guardrail" in lowered:
        return (
            "sample-agent の実行時リソースしきい値が現在の負荷特性に合っておらず、"
            "可用性ガードが想定より早く作動している可能性があります。"
            if kind == "cause"
            else "sample-agent の実行時しきい値設定を見直し、再起動後に同じガードレールシナリオで検証してください。"
        )
    if "special character" in lowered or "preprocess" in lowered or "query_validation" in lowered:
        return (
            "問い合わせ前処理で特殊文字や未対応文字の正規化が不足し、検索処理に進む前に失敗している可能性があります。"
            if kind == "cause"
            else "入力正規化とバリデーションを追加し、問題の文字列を含む問い合わせで再テストしてください。"
        )
    if "timeout" in lowered:
        return (
            "下流処理または外部APIの応答遅延により、タイムアウトが発生している可能性があります。"
            if kind == "cause"
            else "タイムアウト値、リトライ条件、下流サービスの状態を確認し、同じトレース条件で再検証してください。"
        )
    if "latency" in lowered or "response time" in lowered or "p95" in lowered:
        return (
            "対象サービスの応答時間が通常より悪化しており、処理待ちまたは下流依存の遅延が根本要因の候補です。"
            if kind == "cause"
            else "遅延しているスパンと下流依存を確認し、必要に応じて設定、容量、タイムアウトを調整してください。"
        )
    if "llm" in lowered or "openai" in lowered or "rate limit" in lowered:
        return (
            "LLM呼び出しで失敗またはレート制限が発生し、エージェント処理が正常に完了していない可能性があります。"
            if kind == "cause"
            else "LLMプロバイダーの状態、レート制限、リトライ設定を確認し、失敗した入力で再実行してください。"
        )
    if "memory" in lowered or "cpu" in lowered or "disk" in lowered:
        return (
            "実行環境のリソース使用率が高く、アプリケーション処理に影響している可能性があります。"
            if kind == "cause"
            else "CPU、メモリ、ディスク使用率を確認し、必要に応じてリソース設定または負荷条件を調整してください。"
        )
    return f"RCA結果: {text}" if kind == "cause" else f"推奨対応: {text}"


def _evidence_ja(text: str | None) -> str | None:
    if not text:
        return text
    lines = [line.strip().lstrip("-*• ").strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    return "\n".join(f"・{_rca_text_ja(line, 'cause') or line}" for line in lines[:8])


def _summary_ja(summary: str | None, en: dict[str, str | None]) -> str | None:
    cause = _rca_text_ja(en.get("likely_cause"), "cause")
    action = _rca_text_ja(en.get("recommended_action"), "action")
    if cause or action:
        parts = []
        if cause:
            parts.append(f"原因候補: {cause}")
        if action:
            parts.append(f"推奨対応: {action}")
        return "\n".join(parts)
    return summary
