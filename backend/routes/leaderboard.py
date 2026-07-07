from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from database import get_db
from models import Place, Vote
from schemas import LeaderboardEntry

router = APIRouter()


@router.get("/api/leaderboard", response_model=list[LeaderboardEntry])
def get_leaderboard(category: str | None = None, db: Session = Depends(get_db)):
    # SQL: aggregate vote scores per primary_author
    q = (
        db.query(
            Place.primary_author,
            func.max(Place.primary_author_profile_url).label("profile_url"),
            func.coalesce(func.sum(Vote.value), 0).label("total_score"),
            func.count(distinct(Place.id)).label("attributed_count"),
        )
        .outerjoin(Vote, Vote.place_id == Place.id)
        .filter(Place.is_context.isnot(True))  # exclude ambient home-base / media
        .group_by(Place.primary_author)
    )
    if category:
        q = q.filter(Place.category == category)
    rows = q.order_by(
        func.coalesce(func.sum(Vote.value), 0).desc(),
        func.count(distinct(Place.id)).desc(),
    ).all()

    # Python-side: count mentions across all_authors JSON arrays
    places_q = db.query(Place.all_authors).filter(Place.is_context.isnot(True))
    if category:
        places_q = places_q.filter(Place.category == category)
    all_places = places_q.all()
    mention_counts: dict[str, int] = {}
    for (authors,) in all_places:
        for author in (authors or []):
            uname = author.get("username", "")
            if uname:
                mention_counts[uname] = mention_counts.get(uname, 0) + 1

    return [
        LeaderboardEntry(
            username=row.primary_author or "",
            platform_id=None,
            profile_url=row.profile_url,
            total_score=row.total_score,
            attributed_count=row.attributed_count,
            mentioned_count=mention_counts.get(row.primary_author or "", 0),
        )
        for row in rows
        if row.primary_author
    ]
