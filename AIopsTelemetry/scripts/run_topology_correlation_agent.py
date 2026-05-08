"""
CLI script that runs the topology correlation agent against recent issues in the database.
Accepts a configurable time window and minimum confidence score threshold, then invokes
correlate_recent_topology_issues to identify root-cause and downstream symptom relationships
across topology issues and prints the structured correlation results as JSON to stdout.

データベース内の最近の課題に対してトポロジー相関エージェントを実行するCLIスクリプト。
設定可能な時間ウィンドウと最小信頼スコアしきい値を受け取り、
correlate_recent_topology_issuesを呼び出してトポロジー課題間の根本原因と
下流症状の関係を特定し、構造化された相関結果をJSON形式で標準出力に出力する。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from server.database.engine import SessionLocal, init_db
from server.engine.topology_correlation_agent import correlate_recent_topology_issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Correlate topology root issues with downstream application symptoms.")
    parser.add_argument("--window-minutes", type=int, default=30)
    parser.add_argument("--min-score", type=int, default=55)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        results = correlate_recent_topology_issues(db, window_minutes=args.window_minutes, min_score=args.min_score)
        print(json.dumps({"correlations": results}, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
