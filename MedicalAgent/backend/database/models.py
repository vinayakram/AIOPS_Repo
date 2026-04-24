from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./sample_agent.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Integer, default=1)


class TraceLog(Base):
    """Stores one row per /api/query call for the in-app dashboard."""
    __tablename__ = "trace_logs"

    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String, unique=True, nullable=False)
    user_id = Column(String, nullable=True)
    query = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    total_duration_ms = Column(Float, default=0.0)
    articles_fetched = Column(Integer, default=0)
    pagerank_method = Column(String, default="n/a")
    top_k = Column(Integer, default=5)
    # JSON array of {name, duration_ms, input, output} dicts
    steps_json = Column(Text, default="[]")
    answer_preview = Column(Text, default="")
    langfuse_url = Column(String, nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)


def seed_default_user() -> None:
    """Create the default admin user if it does not already exist."""
    from ..auth.password_handler import hash_password

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        if not user:
            db.add(User(
                username="admin",
                email="admin@localhost",
                hashed_password=hash_password("admin"),
                full_name="Administrator",
                is_active=1,
            ))
            db.commit()
        elif not user.hashed_password or user.hashed_password == "admin":
            # Fix plain-text or missing password from a previous bad seed
            user.hashed_password = hash_password("admin")
            db.commit()
    finally:
        db.close()
