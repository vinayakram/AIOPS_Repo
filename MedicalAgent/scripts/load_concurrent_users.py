#!/usr/bin/env python3
"""
Concurrent load generator for the SampleAgent /api/query endpoint used in MedicalAgent demos.
Authenticates once then fires configurable numbers of parallel requests using a thread pool,
cycling through a default set of medical prompts. Reports p50/p95/max latency, throughput,
non-2xx response counts, and transport errors on completion.

MedicalAgentデモで使用するSampleAgentの/api/queryエンドポイント向け並行負荷生成ツール。
一度認証を行った後、スレッドプールを使用して設定可能な数の並行リクエストを送信し、
デフォルトの医療プロンプト集をサイクルする。完了時にp50/p95/最大レイテンシ・スループット・
非2xxレスポンス数・転送エラー数を報告する。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass


DEFAULT_PROMPTS = [
    "What are common symptoms of hypertension?",
    "Summarize treatment options for type 2 diabetes.",
    "What are warning signs for sepsis?",
    "Explain asthma rescue inhaler usage.",
    "What lifestyle advice helps high cholesterol?",
]


@dataclass
class Result:
    status: int
    latency_ms: float
    error: str = ""


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile) - 1))
    return ordered[index]


def _login(base_url: str, username: str, password: str, timeout: float) -> str:
    login_url = base_url.rstrip("/") + "/auth/login"
    form = urllib.parse.urlencode({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        login_url,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload["access_token"]


def _post_json(url: str, payload: dict, timeout: float, token: str) -> Result:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AIOPS-DEMO": "load_test",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.getcode()
            return Result(status=status, latency_ms=(time.perf_counter() - start) * 1000)
    except urllib.error.HTTPError as exc:
        return Result(
            status=exc.code,
            latency_ms=(time.perf_counter() - start) * 1000,
            error=str(exc),
        )
    except Exception as exc:
        return Result(
            status=0,
            latency_ms=(time.perf_counter() - start) * 1000,
            error=str(exc),
        )


def _build_payload(prompt: str, index: int, max_articles: int, top_k: int) -> dict:
    return {
        "query": prompt,
        "max_articles": max_articles,
        "top_k": top_k,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent load generator for SampleAgent.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="SampleAgent base URL")
    parser.add_argument("--username", default="admin", help="SampleAgent username")
    parser.add_argument("--password", default="admin", help="SampleAgent password")
    parser.add_argument("--users", type=int, default=75, help="Concurrent users")
    parser.add_argument("--requests", type=int, default=200, help="Total requests")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    parser.add_argument("--max-articles", type=int, default=30, help="Articles requested per query")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K articles returned per query")
    parser.add_argument("--prompt", action="append", help="Prompt to cycle through; can be passed multiple times")
    args = parser.parse_args()

    prompts = args.prompt or DEFAULT_PROMPTS
    base_url = args.base_url.rstrip("/")
    query_url = base_url + "/api/query"
    token = _login(base_url, args.username, args.password, args.timeout)
    started = time.perf_counter()
    results: list[Result] = []

    with ThreadPoolExecutor(max_workers=args.users) as executor:
        futures = []
        for index in range(args.requests):
            prompt = prompts[index % len(prompts)]
            payload = _build_payload(prompt, index, args.max_articles, args.top_k)
            futures.append(executor.submit(_post_json, query_url, payload, args.timeout, token))
        for future in as_completed(futures):
            results.append(future.result())

    elapsed = time.perf_counter() - started
    latencies = [r.latency_ms for r in results]
    failures = [r for r in results if r.status < 200 or r.status >= 300]
    errors = [r for r in results if r.error]

    print("SampleAgent concurrent load summary")
    print(f"Endpoint: {query_url}")
    print(f"Concurrent users: {args.users}")
    print(f"Requests: {len(results)}")
    print(f"Elapsed seconds: {elapsed:.2f}")
    print(f"Throughput req/s: {len(results) / elapsed:.2f}" if elapsed else "Throughput req/s: n/a")
    print(f"p50 latency ms: {statistics.median(latencies):.0f}" if latencies else "p50 latency ms: n/a")
    print(f"p95 latency ms: {_percentile(latencies, 0.95):.0f}")
    print(f"max latency ms: {max(latencies):.0f}" if latencies else "max latency ms: n/a")
    print(f"non-2xx responses: {len(failures)}")
    print(f"transport/errors: {len(errors)}")
    if errors:
        print(f"sample error: {errors[0].error[:200]}")

    return 1 if failures or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
