import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

DATABASE_URL = getattr(settings, "DATABASE_URL", None)
if not DATABASE_URL:
    db_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "aura_store.db")
    DATABASE_URL = f"sqlite:///{db_file.replace(chr(92), '/')}"

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@contextmanager
def get_db_session():
    """Context manager cung cấp SQLAlchemy Session có quản lý transaction tự động (ACID)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """Dependency cho FastAPI endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
