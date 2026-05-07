from __future__ import annotations

from typing import Any

from server.engine.llm_translator import translate_to_japanese

LANG_JA = "ja"
LANG_EN = "en"

# Service display names — these are proper nouns, not translations.
# The Japanese names are official product names used consistently across
# the dashboard, not LLM-generated text.
_SERVICE_NAMES: dict[str, str] = {
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
    "pod_resource_guard": "Podリソースガード",
}


def normalize_lang(lang: str | None) -> str:
    return LANG_EN if (lang or "").lower().startswith("en") else LANG_JA


def app_display_name_ja(app_name: str | None) -> str:
    key = (app_name or "").lower()
    return _SERVICE_NAMES.get(key, app_name or "対象サービス")


def issue_title_ja(title: str | None, *, app_name: str | None = None, rule_id: str | None = None) -> str:
    """Return a native Japanese title via LLM translation.

    Falls back to the English title if the LLM is unavailable.
    """
    text = title or ""
    if not text:
        return text

    # Replace known service names in the English title before translating
    # so the LLM preserves the official Japanese product name.
    enriched = text
    if app_name:
        enriched = enriched.replace(app_name, app_display_name_ja(app_name))

    translated = translate_to_japanese(enriched)
    return translated or text


def issue_description_ja(
    description: str | None,
    *,
    app_name: str | None = None,
    rule_id: str | None = None,
) -> str | None:
    """Translate an issue description to native Japanese via LLM."""
    if not description:
        return description

    enriched = description
    if app_name:
        enriched = enriched.replace(app_name, app_display_name_ja(app_name))

    translated = translate_to_japanese(enriched)
    return translated or description


def bilingual_analysis_fields(
    *,
    likely_cause: str | None,
    evidence: str | None,
    recommended_action: str | None,
    full_summary: str | None,
    rca_data: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Build bilingual RCA fields.

    Prefers native `_ja` fields already produced by the LLM agent.
    Falls back to LLM translation of the English text.
    """
    rca_data = rca_data or {}
    extracted = _extract_language_blocks(rca_data)

    en = {
        "likely_cause": extracted.get("likely_cause_en") or likely_cause,
        "evidence": extracted.get("evidence_en") or evidence,
        "recommended_action": extracted.get("recommended_action_en") or recommended_action,
        "full_summary": extracted.get("full_summary_en") or full_summary,
    }

    ja: dict[str, str | None] = {}
    for field in ("likely_cause", "evidence", "recommended_action", "full_summary"):
        ja[field] = (
            extracted.get(f"{field}_ja")
            or translate_to_japanese(en[field])
        )

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


def localize_observability_text(
    text: str | None,
    lang: str | None,
    *,
    app_name: str | None = None,
    dependency: str | None = None,
) -> str | None:
    """Translate observability text to Japanese using LLM when lang=ja."""
    if normalize_lang(lang) != LANG_JA:
        return text
    if not text:
        return text

    # Replace service names with their Japanese display names before translating.
    enriched = text
    for en_name, ja_name in _SERVICE_NAMES.items():
        if en_name in enriched:
            enriched = enriched.replace(en_name, ja_name)

    translated = translate_to_japanese(enriched)
    return translated or text


# ── Internal helpers ────────────────────────────────────────────────────

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
