import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# DB_PATH lets production point at a mounted volume (e.g. /data/places.db on Fly);
# defaults to a file in the working directory for local dev.
DB_PATH = os.getenv("DB_PATH", "./places.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)

# WAL mode allows concurrent readers + one writer, preventing lock contention
with engine.connect() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    conn.exec_driver_sql("PRAGMA busy_timeout=30000")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
