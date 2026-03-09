"""
language.py — detect whether a document is French or English.
Uses langdetect (fully offline). Only two outputs: 'fr' or 'en'.
"""
from langdetect import detect, DetectorFactory

# Make detection deterministic
DetectorFactory.seed = 42


def detect_language(text: str) -> str:
    """Returns 'fr' or 'en'. Defaults to 'fr' on failure (house language)."""
    try:
        lang = detect(text[:2000])  # first 2000 chars are sufficient
        return "fr" if lang.startswith("fr") else "en"
    except Exception:
        return "fr"
