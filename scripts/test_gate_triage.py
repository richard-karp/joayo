"""Fixtures for gate_triage's classifier. Every case is a real row from a gate run.

⛔ The suite exists because the classifier's errors are ASYMMETRIC: a generous misclassification
moves a row out of REVIEW and improves the headline. Three versions of this file each shipped a
different generous bug — an end-anchored branch regex that missed four chains, an EXTENT check
ordered ahead of the name test so a wrong mountain was excused, and a NAME_SPLIT trigger that
compared romanized text to Hangul with difflib (a comparison that returns ~0 for correct and
wrong pairs alike). Each looked like precision.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from gate_triage import classify, romanize, names_match, transliterates


def c(name, native, sub, google):
    return classify({"location_name": name, "native_name": native,
                     "subcategory": sub, "g_name": google})


def test_romanization_is_close_enough_to_compare():
    assert romanize("계족산") == "gyejoksan"
    assert romanize("용마산") == "yongmasan"
    assert romanize("아차산") == "achasan"
    assert romanize("바위파스타바") == "bawipaseutaba"


def test_extent_may_not_absorb_a_different_feature():
    # 용마산 vs 아차산 — adjacent ridge, different mountain. v2 filed this EXTENT and folded it in.
    assert c("Yongmasan", "용마산", "outdoor", "아차산") == "REVIEW"


def test_extent_excuses_distance_when_the_name_agrees():
    assert c("Gyejoksan", "계족산", "nature", "계족산") == "EXTENT"
    assert c("Bijarim Forest", "비자림", "nature", "비자림") == "EXTENT"
    assert c("Myeongnyang Strait", "명량해협", "nature", "명량해협 울돌목") == "EXTENT"
    assert c("Seongsu", "성수", "neighborhood", "성수동2가") == "EXTENT"


def test_chain_markers_are_found_mid_string():
    # v1 anchored to end-of-string and missed all four of these.
    assert c("Hwadeok Gogitgan", None, "korean_bbq",
             "⭐️ 화덕고깃간 방이점 | Hwadeok Gogitgan") == "CHAIN"
    assert c("Sihyunhada Photo Studio", "시현하다", "experience", "시현하다 홍대 스페이스") == "CHAIN"
    assert c("Matina Lounge", "마티나 라운지", "landmark",
             "마티나 라운지 인천공항1터미널서편") == "CHAIN"
    assert c("CU", None, "street_food_stall", "CU 중구명동점") == "CHAIN"


def test_centre_is_not_a_branch_marker():
    # 센터 is excluded on purpose: it would sweep up a corporate campus that may genuinely be
    # elsewhere. The row stays unexplained rather than being excused.
    assert c("SM Entertainment", None, "landmark", "SM엔터테인먼트 스튜디오센터") == "REVIEW"


def test_name_check_catches_a_real_mismatch_and_an_innocent_loanword():
    assert c("Tachibana", "바위파스타바", "restaurant", "바위파스타바") == "NAME_CHECK"
    assert c("Color of You", "컬러 오브 유", "beauty_clinic", "컬러오브유") == "NAME_CHECK"


def test_a_correct_romanized_pair_is_not_a_name_check():
    # The v3 bug: every row whose native name matched the provider was flagged, because
    # romanized-vs-Hangul edit distance is ~0 for correct pairs too.
    assert c("Gyejoksan", "계족산", "nature", "계족산") != "NAME_CHECK"
    assert c("Kaisendong Unido", "카이센동우니도", "restaurant", "카이센동 우니도") == "REVIEW"


def test_no_anchor_when_identity_is_unprovable():
    assert c("Minish Dental Clinic", None, "dental", "미니쉬치과병원") == "NO_ANCHOR"


def test_names_match_uses_prefix_not_substring():
    assert names_match("성수", "성수동2가")
    assert names_match("마티나 라운지", "마티나 라운지 인천공항서편")
    assert not names_match("용마산", "아차산")


def test_real_disagreements_survive_every_excuse():
    assert c("Dotori", "도토리", "restaurant", "도토리오븐") == "REVIEW"
