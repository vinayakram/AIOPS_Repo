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
