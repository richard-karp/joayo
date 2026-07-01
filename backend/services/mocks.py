from datetime import datetime, timezone
from services.raw_post import RawPost


def mock_instagram_post(url: str) -> RawPost:
    return RawPost(
        platform="instagram",
        url=url,
        author="korea_travel_mock",
        author_platform_id="123456789",
        caption=(
            "Visited Gyeongbokgung Palace today! 🏯 "
            "The morning light was incredible. Grabbed some street food at Gwangjang Market "
            "after — the bindaetteok was unreal. #seoul #korea #travel #gyeongbokgung"
        ),
        hashtags=["seoul", "korea", "travel", "gyeongbokgung"],
        tagged_accounts=["visitkorea", "seoulofficial"],
        video_cdn_url=None,
        location_string="Seoul, South Korea",
        top_comments=[
            {"text": "I love Gyeongbokgung!", "ownerUsername": "traveler_jane", "timestamp": "2024-01-15"},
        ],
        date_posted=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        raw_json={"mock": True},
    )


def mock_youtube_post(url: str) -> RawPost:
    return RawPost(
        platform="youtube",
        url=url,
        author="korea_travel_yt",
        author_platform_id="UCmock123456",
        caption=(
            "Seoul Travel Guide 2024 — Best places to eat, see, and explore in Seoul. "
            "We visit Gyeongbokgung Palace, Bukchon Hanok Village, and the incredible "
            "street food at Gwangjang Market. #seoul #korea #travel"
        ),
        hashtags=["seoul", "korea", "travel"],
        tagged_accounts=[],
        video_cdn_url=None,
        location_string="Seoul, South Korea",
        top_comments=[],
        date_posted=datetime(2024, 3, 10, 12, 0, 0, tzinfo=timezone.utc),
        raw_json={
            "id": "mock_yt_id",
            "transcript": (
                "Welcome to Seoul! Today we're exploring the best spots in the city. "
                "First stop is Gyeongbokgung Palace, the stunning Joseon Dynasty palace. "
                "Then we head to Bukchon Hanok Village for traditional Korean architecture. "
                "For lunch we grab bindaetteok at Gwangjang Market — the city's oldest market."
            ),
        },
    )


MOCK_TRANSCRIPT = (
    "Today I'm at Gyeongbokgung Palace in Seoul. This is one of the most iconic landmarks in Korea. "
    "Built in 1395, it served as the main royal palace of the Joseon Dynasty. "
    "After visiting the palace, we headed to Gwangjang Market nearby for some incredible street food. "
    "The bindaetteok, a Korean savory pancake, is a must-try here. "
    "We also stopped by Bukchon Hanok Village, a traditional Korean village with beautiful hanok architecture. "
    "For dinner, we went to a famous Korean BBQ restaurant in Insadong neighborhood."
)
