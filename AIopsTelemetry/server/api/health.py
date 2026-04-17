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
