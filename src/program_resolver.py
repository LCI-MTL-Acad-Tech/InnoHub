"""
program_resolver.py — resolve free-text program names to canonical codes.

Canonical codes in programs.csv:
  420.BP   IT – Programmation (DEC)
  420.BR   IT – Réseaux et sécurité (DEC)
  420.BX   IT – Jeux vidéo (DEC)
  420.B0   IT – profil inconnu (stream unclear at ingest time — valid stored code)
  570.E0   Design d'intérieur (DEC)
  NTA.21   Design d'intérieur (AEC)
  570.??   Design d'intérieur – DEC vs AEC unclear (pending, not stored long-term)
  571.A0   Design de la mode
  571.C0   Commercialisation de la mode
  LEA.3Q   Programmeur-analyste – Programmation (AEC)
  LEA.99   Programmeur-analyste – Réseaux et sécurité (AEC)
  LEA.DQ   Intelligence artificielle (AEC)
  410.G0   Techniques de la logistique du transport (profiles TBD)

Resolution order:
  1. Exact code match
  2. Code embedded in text (420.BP, 420.B0, LEA.3Q, etc.)
  3. Fuzzy match against label_fr / label_en (score ≥ 80)
     — blocked for LEA.3Q when IT-generic signals are present (avoids
       false match on "technologies de l'information" substring)
  4. Heuristic signals
  5. Unknown / interactive fallback
"""
import re
from rapidfuzz import fuzz

# ── Code aliases (old formats → canonical) ────────────────────────────────────
# Note: 420.B0 is now a real stored code, not an alias
_CODE_ALIASES: dict[str, str] = {
    "420.bp": "420.BP",
    "420.br": "420.BR",
    "420.bx": "420.BX",
    "420.b0": "420.B0",   # valid stored code — stream unknown
    "570.e0": "570.E0",
    "571.a0": "571.A0",
    "571.c0": "571.C0",
    "lea.3q": "LEA.3Q",
    "lea.99": "LEA.99",
    "lea.dq": "LEA.DQ",
    "nta.21": "NTA.21",
    "410.g0": "410.G0",
}

# ── IT DEC signals ────────────────────────────────────────────────────────────
_IT_GENERIC = [
    "informatique", "computer science technology",
    "technologies de l'information", "technologies de linformation",
    "dec-technique", "technique de l'informatique",
    "techniques de l'informatique",
    "dec en technologies de l'information",
    "dec en technologies de linformation",
]
_IT_PROG_SIGNALS = [
    "profil programmation", "profile in programming", "profil programmateur",
    "– programmation", "- programmation", "– programming", "- programming",
]
_IT_NET_SIGNALS = [
    "réseau", "reseau", "network", "security", "sécurité", "securite",
    "gestion de réseaux", "profile 420.br",
]
_IT_GAME_SIGNALS = [
    "jeux vidéo", "jeux video", "video game", "game programming", "jeu video",
]

# ── AEC IT signals ────────────────────────────────────────────────────────────
# LEA codes are AEC programs — distinct from the DEC 420.x family
_LEA_PROG_SIGNALS = [
    "programmeur-analyste", "programmeur analyste",
    "programmer analyst", "programmer-analyst",
    "lea.3q", "lea 3q",
    "information technology programmer",
    "it analyst", "analyste en technologie",
]
_LEA_NET_SIGNALS = [
    "lea.99", "lea 99",
    "network management aec", "gestion de réseaux aec",
    "networking aec",
]

# ── AI signals → LEA.DQ ───────────────────────────────────────────────────────
_AI_SIGNALS = [
    "artificial intelligence", "machine learning",
    "intelligence artificielle", "apprentissage automatique",
    "ai and machine", "aec in artificial",
    "intelligence and machine", "lea.dq",
]

# ── Other signals ─────────────────────────────────────────────────────────────
_FASHION_SIGNALS = [
    "mode", "fashion", "commercialisation", "design de la mode",
]
_INTERIOR_SIGNALS = [
    "design d'intérieur", "design intérieur", "interior design",
    "design interieur",
]
_LOGISTICS_SIGNALS = [
    "logistique", "logistics", "transport logistique",
    "transportation logistics", "410.g0",
]

_IT_DEC_CODES   = {"420.BP", "420.BR", "420.BX", "420.B0"}
_IT_AEC_CODES   = {"LEA.3Q", "LEA.99", "LEA.DQ"}
_INTERIOR_CODES = {"570.E0", "NTA.21"}
_FASHION_CODES  = {"571.A0", "571.C0"}

# Pending placeholder — DEC vs AEC unclear for interior design
_INTERIOR_PENDING = "570.??"


def _n(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[''`]", "'", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _contains(text: str, signals: list[str]) -> bool:
    return any(sig in text for sig in signals)


def _extract_embedded_code(text: str) -> str | None:
    """
    Find a code-like pattern in the text.
    Returns the canonical alias if found, else None.
    """
    m = re.search(
        r"\b(\d{3}\.[A-Za-z0-9]{2,3}|[A-Z]{2,3}\.\d{1,2}[A-Za-z]?)\b",
        text
    )
    if m:
        raw = m.group(1).lower()
        return _CODE_ALIASES.get(raw, m.group(1).upper())
    return None


def resolve(
    raw: str,
    programs: list[dict],
    interactive: bool = True,
) -> tuple[str, str]:
    """
    Resolve a free-text program name to a canonical code.

    Returns (code, confidence):
      "exact"    — matched a known code directly
      "embedded" — code found embedded in the text
      "fuzzy"    — matched via label similarity
      "pending"  — DEC vs AEC unclear for interior design (570.??)
      "manual"   — user typed a code at the interactive prompt
      "external" — recognised category not yet in our list (stored as-is)
      "unknown"  — could not resolve
    """
    if not raw or not raw.strip():
        return "???", "unknown"

    active = [p for p in programs if p.get("active", "true") == "true"]
    codes  = [p["code"] for p in active]
    t      = _n(raw)

    # ── 1. Exact code match ───────────────────────────────────────────────────
    raw_up = raw.strip().upper()
    if raw_up in codes:
        return raw_up, "exact"

    # ── 2. Embedded code ──────────────────────────────────────────────────────
    embedded = _extract_embedded_code(raw)
    if embedded and embedded in codes:
        # Embedded code is unambiguous — but check for stream refinement
        if embedded == "420.B0":
            # 420.B0 is valid to store, but see if stream signals clarify it
            if _contains(t, _IT_GAME_SIGNALS):
                return "420.BX", "embedded"
            if _contains(t, _IT_NET_SIGNALS):
                return "420.BR", "embedded"
            if _contains(t, _IT_PROG_SIGNALS) or "programming" in t or "programmation" in t:
                return "420.BP", "embedded"
        return embedded, "embedded"

    # ── 3. Fuzzy match against labels ────────────────────────────────────────
    scored = []
    for p in active:
        fr = fuzz.token_set_ratio(t, _n(p.get("label_fr", "")))
        en = fuzz.token_set_ratio(t, _n(p.get("label_en", "")))
        scored.append((p["code"], max(fr, en)))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_code, top_score = scored[0] if scored else ("", 0)

    if top_score >= 80:
        # Block any LEA/AEC fuzzy match when the text clearly describes a DEC
        # IT program — shared substrings like "technologies de l'information"
        # and "network and security management" appear in both families
        _dec_signals = _IT_GENERIC + ["computer science", "informatique", "dec ", "dcs "]
        if top_code in _IT_AEC_CODES and _contains(t, _dec_signals):
            pass  # fall through to heuristics
        elif top_code not in _IT_DEC_CODES and top_code not in _INTERIOR_CODES:
            return top_code, "fuzzy"

    # ── 4. Heuristic signals ──────────────────────────────────────────────────

    # AI → LEA.DQ (check before other IT signals)
    if _contains(t, _AI_SIGNALS):
        return "LEA.DQ", "fuzzy"

    # AEC networking → LEA.99 (only when explicitly AEC context)
    if _contains(t, _LEA_NET_SIGNALS) and _contains(t, ["aec", "lea"]):
        return "LEA.99", "fuzzy"

    # AEC programming → LEA.3Q (check before generic IT)
    if _contains(t, _LEA_PROG_SIGNALS):
        return "LEA.3Q", "fuzzy"

    # IT DEC — determine stream
    is_it = (
        _contains(t, _IT_GENERIC)
        or _contains(t, _IT_PROG_SIGNALS + _IT_NET_SIGNALS + _IT_GAME_SIGNALS)
        or "computer science" in t
        or "informatique" in t
    )
    if is_it:
        if _contains(t, _IT_GAME_SIGNALS):
            return "420.BX", "fuzzy"
        if _contains(t, _IT_NET_SIGNALS):
            return "420.BR", "fuzzy"
        # "programming"/"programmation" only resolves to 420.BP when paired
        # with a strong IT-DEC marker (not just bare "computer science")
        has_prog = _contains(t, _IT_PROG_SIGNALS) or (
            ("programmation" in t or "programming" in t)
            and _contains(t, [
                "computer science technology", "informatique",
                "technique de l'informatique", "techniques de",
                "dec ", "dcs ", "technique ",
            ])
        )
        if has_prog:
            return "420.BP", "fuzzy"
        return "420.B0", "fuzzy"  # IT confirmed, stream unknown

    # Interior design
    if _contains(t, _INTERIOR_SIGNALS):
        if "nta" in t or ("aec" in t and "interior" in t):
            return "NTA.21", "fuzzy"
        if "570" in t or "dec" in t:
            return "570.E0", "fuzzy"
        if interactive:
            return _disambiguate_interior(programs)
        return _INTERIOR_PENDING, "pending"

    # Fashion
    if _contains(t, _FASHION_SIGNALS):
        if _contains(t, ["commercialisation", "merchandising"]):
            return "571.C0", "fuzzy"
        if _contains(t, ["design de la mode", "fashion design"]):
            return "571.A0", "fuzzy"
        if interactive:
            return _disambiguate_fashion(programs)
        return "571.??", "pending"

    # Logistics → 410.G0
    if _contains(t, _LOGISTICS_SIGNALS):
        return "410.G0", "fuzzy"

    # ── 5. Low-confidence fuzzy ───────────────────────────────────────────────
    if top_score >= 60:
        return top_code, "fuzzy"

    # ── 6. Interactive fallback ───────────────────────────────────────────────
    if not interactive:
        return raw.strip() or "???", "external"

    print(f"\n  Could not resolve program: '{raw}'")
    print(f"  Known codes: {', '.join(codes)}")
    typed = input("  Enter code (or blank to skip): ").strip()
    if not typed:
        return "???", "unknown"
    return typed.upper(), "manual"


def _disambiguate_interior(programs: list[dict]) -> tuple[str, str]:
    opts = [p for p in programs if p["code"] in _INTERIOR_CODES]
    print("\n  Interior design — DEC or AEC?")
    for i, p in enumerate(opts, 1):
        print(f"    {i}  {p['code']}  —  {p.get('label_fr', '')}")
    raw = input("  Enter number (or blank to keep as pending): ").strip()
    try:
        return opts[int(raw) - 1]["code"], "manual"
    except (ValueError, IndexError):
        return _INTERIOR_PENDING, "pending"


def _disambiguate_fashion(programs: list[dict]) -> tuple[str, str]:
    opts = [p for p in programs if p["code"] in _FASHION_CODES]
    print("\n  Fashion — Design de la mode or Commercialisation?")
    for i, p in enumerate(opts, 1):
        print(f"    {i}  {p['code']}  —  {p.get('label_fr', '')}")
    raw = input("  Enter number (or blank for pending): ").strip()
    try:
        return opts[int(raw) - 1]["code"], "manual"
    except (ValueError, IndexError):
        return "571.??", "pending"


def resolve_pending_interior(programs: list[dict], interactive: bool = True) -> str | None:
    opts = [p for p in programs if p["code"] in _INTERIOR_CODES
            and p.get("active", "true") == "true"]
    if not interactive or not opts:
        return None
    print("\n  Interior design — DEC (570.E0) or AEC (NTA.21)?")
    for i, p in enumerate(opts, 1):
        print(f"    {i}  {p['code']}  —  {p.get('label_fr', '')}")
    raw = input("  Enter number (or blank to keep as pending): ").strip()
    try:
        return opts[int(raw) - 1]["code"]
    except (ValueError, IndexError):
        return None
