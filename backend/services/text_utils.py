import re

try:
    from langdetect import detect as _langdetect
    from langdetect import DetectorFactory as _DetectorFactory
    _DetectorFactory.seed = 0
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False

# ISO 639-1 codes for languages that use the Latin script
_LATIN_LANGS = frozenset({
    "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "fi",
    "fr", "gl", "hr", "hu", "id", "it", "lt", "lv", "ms", "nl",
    "no", "pl", "pt", "ro", "sk", "sl", "sq", "sv", "tl", "tr", "vi",
})


# Type/locality words dropped from the *end* of a name so that
# "Korean BBQ restaurant" → "korean bbq" and "Insadong neighborhood" → "insadong".
# Only stripped as trailing tokens — leading type words ("Cafe Bora") are part of the name.
_STOP_SUFFIXES = frozenset({
    "restaurant", "cafe", "café",
    "neighborhood", "neighbourhood", "area", "district",
})

_PAREN_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_name(name: str | None) -> str:
    """Normalize a place name for cheap equality/prefix matching.

    Lowercases, drops parentheticals and punctuation, and strips trailing
    type/locality stop-suffixes. Non-Latin scripts (e.g. Hangul) are preserved.
    """
    if not name:
        return ""
    s = name.lower()
    s = _PAREN_RE.sub(" ", s)     # drop "(...)" segments (romanizations, area tags)
    s = _PUNCT_RE.sub(" ", s)     # punctuation → space
    tokens = s.split()
    while tokens and tokens[-1] in _STOP_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _transcript_matches_caption(transcript: str, caption: str) -> bool:
    """Return False if the transcript is clearly from a different video than the caption.

    Catches the case where Instagram CDN content-addressed storage returns the same
    video file URL for many different posts — the transcriber then returns a cached
    transcript that belongs to a completely different video.
    """
    if not transcript or not caption:
        return True
    # Primary: langdetect language comparison
    if _LANGDETECT_AVAILABLE and len(transcript) >= 20 and len(caption) >= 20:
        try:
            t_lang = _langdetect(transcript)
            c_lang = _langdetect(caption)
            if t_lang in _LATIN_LANGS and c_lang not in _LATIN_LANGS:
                return False
        except Exception:
            pass
    # Fallback: non-ASCII heuristic
    non_ascii = sum(1 for c in caption if ord(c) > 127)
    if non_ascii / max(len(caption), 1) > 0.3:
        ascii_in_transcript = sum(1 for c in transcript if ord(c) < 128)
        if ascii_in_transcript / max(len(transcript), 1) > 0.95:
            return False
    return True
