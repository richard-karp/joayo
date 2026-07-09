import os

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(raise_error_if_not_found=False))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models import Base
from routes import extract, jobs, export, votes, leaderboard, places, admin

Base.metadata.create_all(bind=engine)

# Additive migrations for columns added after initial schema creation
with engine.connect() as _conn:
    for _sql in [
        "ALTER TABLE jobs ADD COLUMN warnings TEXT DEFAULT '[]'",
        "ALTER TABLE jobs ADD COLUMN paused_reason TEXT",
        "ALTER TABLE jobs ADD COLUMN remaining_posts TEXT DEFAULT '[]'",
        "ALTER TABLE places ADD COLUMN venue_place_id TEXT REFERENCES places(id)",
        "ALTER TABLE places ADD COLUMN geocoder TEXT",
        "ALTER TABLE places ADD COLUMN geocoder_place_id TEXT",
        "ALTER TABLE places ADD COLUMN normalized_name TEXT",
        "ALTER TABLE places ADD COLUMN neighborhood TEXT",
        "ALTER TABLE places ADD COLUMN is_context BOOLEAN DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS ix_places_geocoder_place_id ON places(geocoder_place_id)",
        "CREATE INDEX IF NOT EXISTS ix_places_normalized_name ON places(normalized_name)",
    ]:
        try:
            _conn.execute(__import__("sqlalchemy").text(_sql))
            _conn.commit()
        except Exception:
            pass  # column already exists

app = FastAPI(title="joayo")

# Comma-separated allowed origins; defaults to local dev. In production set
# CORS_ORIGINS to the deployed frontend URL(s), e.g. "https://joayo.vercel.app".
_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # No cookies/credentials are used (the extract gate is a custom header), and
    # credentials + a wildcard origin is an invalid/footgun combo — keep it off.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract.router)
app.include_router(jobs.router)
app.include_router(export.router)
app.include_router(votes.router)
app.include_router(leaderboard.router)
app.include_router(places.router)
app.include_router(admin.router)
