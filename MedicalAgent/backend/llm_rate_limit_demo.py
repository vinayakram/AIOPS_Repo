from __future__ import annotations

import threading
import time
from collections import deque

from openai import APIStatusError, AzureOpenAI, RateLimitError

from .config import settings
from .monitoring import observe_llm_request, set_llm_rate_limit_state


SCENARIO = "llm_rate_limit"


class LLMRateLimitExceeded(RuntimeError):
    pass


class LowRateLimitAzureClient:
    def __init__(self) -> None:
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()
        self._client: AzureOpenAI | None = None

    @property
    def deployment(self) -> str:
        return settings.LOW_RATE_LIMIT_OPENAI_DEPLOYMENT

    @property
    def model(self) -> str:
        return settings.LOW_RATE_LIMIT_OPENAI_MODEL

    @property
    def limit(self) -> int:
        return max(1, int(settings.LOW_RATE_LIMIT_REQUESTS_PER_MINUTE))

    def _get_client(self) -> AzureOpenAI:
        if not settings.LOW_RATE_LIMIT_OPENAI_API_KEY:
            raise RuntimeError("LOW_RATE_LIMIT_OPENAI_API_KEY is required for the LLM rate-limit scenario.")
        if not settings.LOW_RATE_LIMIT_OPENAI_ENDPOINT:
            raise RuntimeError("LOW_RATE_LIMIT_OPENAI_ENDPOINT is required for the LLM rate-limit scenario.")

        if self._client is None:
            self._client = AzureOpenAI(
                api_key=settings.LOW_RATE_LIMIT_OPENAI_API_KEY,
                azure_endpoint=settings.LOW_RATE_LIMIT_OPENAI_ENDPOINT,
                api_version=settings.LOW_RATE_LIMIT_OPENAI_API_VERSION,
                max_retries=0,
                timeout=20,
            )
        return self._client

    def call(self, prompt: str) -> dict:
        return self._call_azure(prompt)

    def _record_window_hit(self) -> tuple[int, int]:
        now = time.time()
        with self._lock:
            while self._hits and now - self._hits[0] >= 60:
                self._hits.popleft()

            self._hits.append(now)
            current_hits = len(self._hits)
            remaining = max(0, self.limit - current_hits)
            set_llm_rate_limit_state(
                scenario=SCENARIO,
                deployment=self.deployment,
                model=self.model,
                current_window_hits=current_hits,
                limit_per_minute=self.limit,
                remaining=remaining,
            )
            return current_hits, remaining

    def _call_azure(self, prompt: str) -> dict:
        current_hits, remaining = self._record_window_hit()
        try:
            response = self._get_client().chat.completions.create(
                model=self.deployment,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a concise medical research assistant. "
                            "This call is part of an AIOps rate-limit validation scenario."
                        ),
                    },
                    {"role": "user", "content": prompt[:1000]},
                ],
                temperature=0.1,
                max_tokens=120,
            )
            observe_llm_request(SCENARIO, self.deployment, self.model, "success")
            answer = (response.choices[0].message.content or "").strip()
            return {
                "answer": answer or "Azure OpenAI call completed successfully.",
                "current_window_hits": current_hits,
                "limit_per_minute": self.limit,
                "remaining": remaining,
                "provider_status": "success",
            }
        except RateLimitError as exc:
            observe_llm_request(SCENARIO, self.deployment, self.model, "rate_limited")
            raise LLMRateLimitExceeded(self._format_provider_error(exc, current_hits)) from exc
        except APIStatusError as exc:
            status_code = getattr(exc, "status_code", None)
            status = "rate_limited" if status_code == 429 else "error"
            observe_llm_request(SCENARIO, self.deployment, self.model, status)
            if status_code == 429:
                raise LLMRateLimitExceeded(self._format_provider_error(exc, current_hits)) from exc
            raise RuntimeError(self._format_provider_error(exc, current_hits)) from exc
        except Exception as exc:
            observe_llm_request(SCENARIO, self.deployment, self.model, "error")
            raise RuntimeError(self._format_provider_error(exc, current_hits)) from exc

    def _format_provider_error(self, exc: Exception, current_hits: int) -> str:
        status_code = getattr(exc, "status_code", None)
        retry_after = None
        response = getattr(exc, "response", None)
        if response is not None:
            retry_after = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset-requests")

        details = [
            f"Azure OpenAI call failed for deployment {self.deployment}",
            f"observed {current_hits} requests in the MedicalAgent 60s window",
            f"configured scenario limit is {self.limit} requests/minute",
        ]
        if status_code:
            details.append(f"provider HTTP status={status_code}")
        if retry_after:
            details.append(f"provider retry/reset hint={retry_after}")
        details.append(f"provider error={str(exc)[:500]}")
        return "; ".join(details)


low_rate_limit_client = LowRateLimitAzureClient()
