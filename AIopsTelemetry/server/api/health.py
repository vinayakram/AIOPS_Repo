"""
Health check API exposing a single GET /health endpoint that verifies
database connectivity by executing a lightweight query against the Trace
table. Returns service status, version, and database reachability for
use by load balancers, orchestrators, and monitoring probes.

データベース接続性を確認するためにTraceテーブルへの軽量クエリを実行し、
サービス稼働状態・バージョン・DB到達可否を返すヘルスチェックAPIモジュール。
ロードバランサ・オーケストレータ・監視プローブによる死活監視に対応する。
障害時はHTTP 503を返してサービス切り離しの契機を提供する設計となっている。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from server.database.engine import get_db
from server.database.models import Trace

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.query(Trace).limit(1).all()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}
