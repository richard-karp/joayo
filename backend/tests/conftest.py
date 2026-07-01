from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from services.raw_post import RawPost


def _make_in_memory_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared connection — tables visible across all sessions
    )


@pytest.fixture()
def db_session():
    engine = _make_in_memory_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def make_raw_post(**kwargs) -> RawPost:
    defaults = dict(
        platform="instagram",
        url="https://www.instagram.com/p/test123/",
        author="travel_user",
        author_platform_id="111",
        caption="Visited Gyeongbokgung Palace today! Amazing place.",
        hashtags=["seoul", "korea"],
        tagged_accounts=["visitkorea"],
        video_cdn_url=None,
        location_string="Seoul, South Korea",
        top_comments=[],
        date_posted=datetime(2024, 3, 15, tzinfo=timezone.utc),
        raw_json={},
    )
    defaults.update(kwargs)
    return RawPost(**defaults)
