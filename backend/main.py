from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(raise_error_if_not_found=True))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models import Base
from routes import extract, jobs, export, votes, leaderboard, places

Base.metadata.create_all(bind=engine)

# Additive migrations for columns added after initial schema creation
with engine.connect() as _conn:
    for _sql in [
        "ALTER TABLE jobs ADD COLUMN warnings TEXT DEFAULT '[]'",
        "ALTER TABLE jobs ADD COLUMN paused_reason TEXT",
        "ALTER TABLE jobs ADD COLUMN remaining_posts TEXT DEFAULT '[]'",
    ]:
        try:
            _conn.execute(__import__("sqlalchemy").text(_sql))
            _conn.commit()
        except Exception:
            pass  # column already exists

app = FastAPI(title="Social Data Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract.router)
app.include_router(jobs.router)
app.include_router(export.router)
app.include_router(votes.router)
app.include_router(leaderboard.router)
app.include_router(places.router)
