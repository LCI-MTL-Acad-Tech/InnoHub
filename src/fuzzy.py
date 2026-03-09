"""
fuzzy.py — typo detection and company name resolution.
"""
from rapidfuzz import process, fuzz


def best_match(query: str, candidates: list[str], threshold: float = 85.0):
    """
    Return (best_match, score) if above threshold, else (None, score).
    Uses token_sort_ratio to handle word order differences.
    """
    if not candidates:
        return None, 0.0
    result = process.extractOne(query, candidates, scorer=fuzz.token_sort_ratio)
    if result is None:
        return None, 0.0
    match, score, _ = result
    return (match, score) if score >= threshold else (None, score)


def ranked_matches(query: str, candidates: list[str], limit: int = 5):
    """Return list of (candidate, score) sorted by score descending."""
    results = process.extract(
        query, candidates, scorer=fuzz.token_sort_ratio, limit=limit
    )
    return [(m, s) for m, s, _ in results]


def detect_program_typo(code: str, known_codes: list[str], threshold: float = 70.0):
    """
    Returns the closest known program code if the input looks like a typo,
    or None if the code is genuinely new.
    """
    match, score = best_match(code.upper(), [c.upper() for c in known_codes], threshold)
    if match:
        # Return in original casing
        idx = [c.upper() for c in known_codes].index(match)
        return known_codes[idx], score
    return None, score
