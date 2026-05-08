"""
Runtime state flags for the MedicalAgent backend that can be toggled via the admin API
without restarting the server. Tracks LLM access enablement and records disabled-LLM query
attempts within a rolling 10-minute window to trigger error escalation in AIops demos.

サーバを再起動せずに管理者APIで切り替え可能なMedicalAgentバックエンドのランタイム状態フラグ。
LLMアクセス有効状態を管理し、AIopsデモにおけるエラーエスカレーション発火のため、10分間の
ローリングウィンドウ内のLLM無効化クエリ試行回数を記録する。
"""

import time


llm_enabled: bool = True

LLM_DISABLED_WINDOW_SECONDS = 10 * 60
LLM_DISABLED_ERROR_THRESHOLD = 3

_llm_disabled_attempts: list[float] = []


def record_llm_disabled_attempt() -> int:
    """Track disabled-LLM query attempts in a rolling 10-minute window."""
    now = time.time()
    cutoff = now - LLM_DISABLED_WINDOW_SECONDS
    _llm_disabled_attempts[:] = [ts for ts in _llm_disabled_attempts if ts >= cutoff]
    _llm_disabled_attempts.append(now)
    return len(_llm_disabled_attempts)


def reset_llm_disabled_attempts() -> None:
    _llm_disabled_attempts.clear()
