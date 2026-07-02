"""Live extraction-quality eval.

Runs the real extractor against a set of fixture posts and checks that the
expected places are recovered (recall) and that forbidden junk/incidental names
are not emitted. This is the regression net for prompt (#14) and two-tier model
(#18) changes, which have no natural unit coverage.

It hits the Anthropic API, so it is skipped unless RUN_EVAL=1 and a key is set:

    RUN_EVAL=1 ./venv/bin/python -m pytest tests/eval -q -s

Tune the recall bar with EVAL_MIN_RECALL (default 0.8).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.extractor import extract
from services.raw_post import RawPost
from services.text_utils import normalize_name

_FIXTURES = Path(__file__).parent / "fixtures.json"
_MIN_RECALL = float(os.getenv("EVAL_MIN_RECALL", "0.8"))

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.getenv("RUN_EVAL") or not os.getenv("ANTHROPIC_API_KEY"),
        reason="set RUN_EVAL=1 and ANTHROPIC_API_KEY to run the live extraction eval",
    ),
]


def _load_cases():
    data = json.loads(_FIXTURES.read_text())
    return [(c["name"], c) for c in data["cases"]]


def _raw_post(post: dict) -> RawPost:
    return RawPost(
        platform=post.get("platform", "instagram"),
        url=post["url"],
        author=post.get("author", "eval_user"),
        author_platform_id=post.get("author_platform_id", "eval-1"),
        caption=post.get("caption", ""),
        hashtags=post.get("hashtags", []),
        tagged_accounts=post.get("tagged_accounts", []),
        video_cdn_url=post.get("video_cdn_url"),
        location_string=post.get("location_string"),
        top_comments=post.get("top_comments", []),
        date_posted=datetime(2024, 1, 1, tzinfo=timezone.utc),
        raw_json={},
    )


@pytest.mark.parametrize("case_name,case", _load_cases(), ids=lambda v: v if isinstance(v, str) else "")
def test_extraction_recall(case_name, case):
    raw = _raw_post(case["post"])
    places = extract(raw, case.get("transcript"))
    actual = {normalize_name(p.location_name) for p in places}

    expected = [normalize_name(n) for n in case.get("expected_places", [])]
    forbidden = [normalize_name(n) for n in case.get("forbidden_places", [])]

    matched = [e for e in expected if e in actual]
    recall = len(matched) / len(expected) if expected else 1.0
    hit_forbidden = [f for f in forbidden if f in actual]

    print(f"\n[{case_name}] recall={recall:.2f} "
          f"expected={expected} actual={sorted(actual)} forbidden_hit={hit_forbidden}")

    assert not hit_forbidden, f"emitted forbidden names: {hit_forbidden}"
    assert recall >= _MIN_RECALL, f"recall {recall:.2f} < {_MIN_RECALL}; missing={[e for e in expected if e not in actual]}"
