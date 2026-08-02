"""Tests for the mechanical name splitter (backfill_english_names.split_inline_english).

Phase 2 is an LLM call and isn't exercised here; phase 1 is pure string logic and is
the part that decides whether a name gets rewritten without human review.
"""
import pytest

from backfill_english_names import has_cjk, split_inline_english


@pytest.mark.parametrize("name,expected", [
    # English in a parenthetical after the Korean
    ("풍물시장 (Pungmul Market)", ("Pungmul Market", "풍물시장")),
    ("한남동한방통닭 (Hannamdong Hanbang Tongdak)", ("Hannamdong Hanbang Tongdak", "한남동한방통닭")),
    ("BBQ연구소 (Barbecue Lab)", ("Barbecue Lab", "BBQ연구소")),
    # Korean in the parenthetical, English outside
    ("Kodachaya (코다차야)", ("Kodachaya", "코다차야")),
    ("Oyster Grill Restaurant (토끼로 245-95)", ("Oyster Grill Restaurant", "토끼로 245-95")),
    # Appended without parentheses, either order
    ("신사형통정형외과 Sinsa Hyeong Tong Clinic", ("Sinsa Hyeong Tong Clinic", "신사형통정형외과")),
    ("윤슬 YOONSEUL", ("YOONSEUL", "윤슬")),
    # Han characters count as non-Latin too
    ("釀酒的尹酒母 (Brewmaster Yun)", ("Brewmaster Yun", "釀酒的尹酒母")),
])
def test_splittable_names(name, expected):
    assert split_inline_english(name) == expected


@pytest.mark.parametrize("name", [
    "한이식당",                  # pure Korean — phase 2's job, not phase 1's
    "먹고 또 먹고",              # pure Korean, multiple tokens
    "4번 할매집",                # digits are not an English name
    "Gyeongbokgung Palace",     # already English
    "",
])
def test_unsplittable_names(name):
    assert split_inline_english(name) is None


def test_latin_embedded_in_a_korean_token_is_not_split():
    """"블랙BBQ" is one Korean word that happens to contain Latin letters — the Latin run
    is part of the name, not a translation of it, so splitting would truncate it."""
    assert split_inline_english("제주옥탑 블랙BBQ") is None


def test_interleaved_scripts_are_not_split():
    """Splitting an interleaved name would reorder its words, so it is left alone."""
    assert split_inline_english("서울 Grand 호텔") is None


def test_short_latin_fragment_is_not_an_english_name():
    """A 1-2 letter Latin run is an initial or a unit, not a name worth promoting."""
    assert split_inline_english("김밥 A") is None


def test_has_cjk():
    assert has_cjk("한이식당") is True
    assert has_cjk("釀酒") is True
    assert has_cjk("Seoul Forest") is False
    assert has_cjk(None) is False


@pytest.mark.parametrize("name", [
    "솥뚜껑 BBQ",      # "BBQ" is a category word welded onto the name, not a translation
    "명동 Cafe",
])
def test_short_appended_word_is_not_promoted(name):
    """Without parentheses there is no signal that the Latin run translates the Korean,
    so a short one is treated as part of the name — promoting it would drop the rest."""
    assert split_inline_english(name) is None


def test_long_appended_word_is_still_promoted():
    """A full romanization appended without parentheses is still a real name."""
    assert split_inline_english("윤슬 YOONSEUL") == ("YOONSEUL", "윤슬")


def test_short_word_in_parentheses_is_promoted():
    """Parentheses are an explicit 'this is the other name' marker, so the length
    guard does not apply inside them."""
    assert split_inline_english("연구소 (Lab)") == ("Lab", "연구소")


def test_parenthetical_annotating_part_of_the_name_is_left_alone():
    """"구오 (Guo) Saengjeonpo Mandu" glosses only the first word. Promoting "Guo" would
    throw away "Saengjeonpo Mandu", so this is deferred to the LLM phase."""
    assert split_inline_english("구오 (Guo) Saengjeonpo Mandu") is None


def test_appended_length_gate_counts_letters_only():
    """Punctuation and digits must not pad a category word past the minimum."""
    assert split_inline_english("솥뚜껑 k-bbq") is None


# ── Validation of model-returned names (_is_clean_english) ────────────────────

from backfill_english_names import _is_clean_english  # noqa: E402


@pytest.mark.parametrize("name", [
    "Hani Sikdang",
    "Café Bora",          # accented Latin is normal in an English name
    "Le Chocolat Maxime Frédéric",
    "Black BBQ",
])
def test_clean_english_names_are_accepted(name):
    assert _is_clean_english(name) is True


@pytest.mark.parametrize("name,reason", [
    ("Kodachaя", "Cyrillic ya masquerading as Latin 'ya'"),
    ("Οptima Wellness", "Greek capital omicron for 'O'"),
    ("한이식당", "still Korean"),
    ("", "empty"),
    ("   ", "whitespace only"),
    (None, "null from the model"),
])
def test_unusable_names_are_rejected(name, reason):
    assert _is_clean_english(name) is False, reason
