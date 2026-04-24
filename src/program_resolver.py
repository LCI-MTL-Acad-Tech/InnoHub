"""
program_resolver.py — resolve free-text program names to canonical codes.

IMPORTANT: only codes are ever stored. Labels are used for matching only.

Canonical codes (programs.csv):
  IT DEC:   420.BP  420.BR  420.BX  420.B0 (stream unknown)
  IT AEC:   LEA.3Q  LEA.99  LEA.DQ
  Design:   570.E0  NTA.21  571.A0  571.C0
  Gestion:  410.B0  LCA.71  410.D0  LCA.70  410.G0  LCA.5G
  Pending:  570.??  (interior design DEC vs AEC unclear)
            ???     (completely unresolved)
"""
import re
from rapidfuzz import fuzz

# ── Code aliases (old/alternate formats → canonical) ──────────────────────────
_CODE_ALIASES: dict[str, str] = {
    "420.bp":  "420.BP",
    "420.br":  "420.BR",
    "420.bx":  "420.BX",
    "420.b0":  "420.B0",
    "570.e0":  "570.E0",
    "571.a0":  "571.A0",
    "571.c0":  "571.C0",
    "lea.3q":  "LEA.3Q",
    "lea.99":  "LEA.99",
    "lea.dq":  "LEA.DQ",
    "nta.21":  "NTA.21",
    "410.g0":  "410.G0",
    "lca.5g":  "LCA.5G",
    "410.b0":  "410.B0",
    "lca.71":  "LCA.71",
    "410.d0":  "410.D0",
    "lca.70":  "LCA.70",
    "nwy.1x":  "NWY.1X",
}

# ── Signal lists ──────────────────────────────────────────────────────────────
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
_LEA_PROG_SIGNALS = [
    "programmeur-analyste", "programmeur analyste",
    "programmer analyst", "programmer-analyst",
    "lea.3q", "lea 3q",
    "information technology programmer",
    "it analyst", "analyste en technologie",
]
_LEA_NET_SIGNALS = [
    "lea.99", "lea 99",
    "network management aec", "gestion de réseaux aec", "networking aec",
]
_AI_SIGNALS = [
    "artificial intelligence", "machine learning",
    "intelligence artificielle", "apprentissage automatique",
    "ai and machine", "aec in artificial", "intelligence and machine", "lea.dq",
]
_INTERIOR_SIGNALS = [
    "design d'intérieur", "design intérieur", "interior design", "design interieur",
]
_FASHION_SIGNALS = [
    "mode", "fashion", "commercialisation", "design de la mode",
]
_LOGISTICS_SIGNALS = [
    "logistique du transport", "transportation logistics",
    "logistique de transport", "logistics du transport",
    "410.g0", "lca.5g",
]
_ACCOUNTING_SIGNALS = [
    "comptabilité", "comptabilite", "accounting", "410.b0", "lca.71",
    "cpa", "tenue de livres", "bookkeeping",
]
_COMMERCE_SIGNALS = [
    "gestion de commerces", "gestion de commerce", "commerce management",
    "410.d0", "lca.70", "gestion commerciale",
    "business management", "gestion des affaires", "business admin",
    "administration des affaires",
]
_CREATIVE_MGMT_SIGNALS = [
    "industries créatives", "industries creatives", "creative industries",
    "creative industry", "gestion des industries créatives",
    "gestion des industries creatives", "profile in creative industries",
    "profil gestion des industries", "410.x0",
]
_SOCIAL_MEDIA_SIGNALS = [
    "réseaux sociaux", "reseaux sociaux", "social media", "médias sociaux",
    "stratégie numérique", "strategie numerique", "xxx.yy",
]

_IT_DEC_CODES   = {"420.BP", "420.BR", "420.BX", "420.B0"}
_IT_AEC_CODES   = {"LEA.3Q", "LEA.99", "LEA.DQ"}
_INTERIOR_CODES = {"570.E0", "NTA.21"}
_FASHION_CODES  = {"571.A0", "571.C0"}
_LOGISTICS_CODES   = {"410.G0", "LCA.5G"}
_ACCOUNTING_CODES  = {"410.B0", "LCA.71"}
_COMMERCE_CODES    = {"410.D0", "LCA.70"}
_DEC_AEC_PAIRS = {
    "logistics":   (_LOGISTICS_CODES,  "410.G0", "LCA.5G"),
    "accounting":  (_ACCOUNTING_CODES, "410.B0", "LCA.71"),
    "commerce":    (_COMMERCE_CODES,   "410.D0", "LCA.70"),
    "interior":    (_INTERIOR_CODES,   "570.E0", "NTA.21"),
}

_INTERIOR_PENDING = "570.??"


def _n(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[''`]", "'", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _contains(text: str, signals: list[str]) -> bool:
    return any(sig in text for sig in signals)


def _extract_embedded_code(text: str) -> str | None:
    """Find a code-like pattern in the text, return canonical alias or raw."""
    m = re.search(
        r"\b(\d{3}\.[A-Za-z0-9]{2,3}|[A-Z]{2,3}\.\d{1,2}[A-Za-z]?)\b",
        text
    )
    if m:
        raw = m.group(1).lower()
        return _CODE_ALIASES.get(raw, m.group(1).upper())
    return None


def _is_aec(text: str) -> bool:
    return ("aec" in text or "lca" in text or "acs" in text
            or "attestation" in text)


def _is_dec(text: str) -> bool:
    return ("dec" in text or "dcs" in text or "diploma" in text
            or "diplôme" in text or "diplome" in text)


def _disambiguate(codes: set[str], dec_code: str, aec_code: str,
                  programs: list[dict], t: str,
                  interactive: bool,
                  raw: str = "") -> tuple[str, str]:
    """Resolve DEC vs AEC for a paired program using context or prompt."""
    if _is_aec(t):
        return aec_code, "fuzzy"
    if _is_dec(t):
        return dec_code, "fuzzy"
    if not interactive:
        return dec_code, "fuzzy"  # default to DEC when unclear
    # Show the student's original input so the coordinator can judge
    if raw.strip():
        import pydoc
        pydoc.pager(f"Student wrote:\n\n{raw.strip()}")
    opts = [p for p in programs if p["code"] in codes]
    print(f"\n  DEC or AEC?")
    for i, p in enumerate(opts, 1):
        print(f"    {i}  {p['code']}  —  {p.get('label_fr', '')}")
    val = input("  Enter number (or blank for DEC): ").strip()
    try:
        return opts[int(val) - 1]["code"], "manual"
    except (ValueError, IndexError):
        return dec_code, "fuzzy"


def resolve(
    raw: str,
    programs: list[dict],
    interactive: bool = True,
) -> tuple[str, str]:
    """
    Resolve a free-text program name to a canonical code.
    ALWAYS returns a code, never a label string.

    Returns (code, confidence):
      "exact"    — matched a known code directly
      "embedded" — code found embedded in the text
      "fuzzy"    — matched via label similarity or signal
      "pending"  — DEC vs AEC unclear for interior design (570.??)
      "manual"   — user typed a code at the interactive prompt
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
        if embedded == "420.B0":
            if _contains(t, _IT_GAME_SIGNALS): return "420.BX", "embedded"
            if _contains(t, _IT_NET_SIGNALS):  return "420.BR", "embedded"
            if _contains(t, _IT_PROG_SIGNALS) or "programming" in t or "programmation" in t:
                return "420.BP", "embedded"
        # Some form responses embed a generic commerce code (410.D0) but the
        # surrounding text identifies the creative industries profile (410.X0)
        if embedded == "410.D0" and _contains(t, _CREATIVE_MGMT_SIGNALS):
            return "410.X0", "embedded"
        return embedded, "embedded"

    # ── 3. Fuzzy match against labels ────────────────────────────────────────
    scored = []
    for p in active:
        fr = fuzz.token_set_ratio(t, _n(p.get("label_fr", "")))
        en = fuzz.token_set_ratio(t, _n(p.get("label_en", "")))
        scored.append((p["code"], max(fr, en)))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_code, top_score = scored[0] if scored else ("", 0)

    _dec_signals = _IT_GENERIC + ["computer science", "informatique", "dec ", "dcs "]
    if top_score >= 80:
        # Block IT AEC fuzzy match when DEC signals present
        if top_code in _IT_AEC_CODES and _contains(t, _dec_signals):
            pass
        elif top_code not in _IT_DEC_CODES and top_code not in _INTERIOR_CODES:
            return top_code, "fuzzy"

    # ── 4. Heuristic signals ──────────────────────────────────────────────────

    # AI → LEA.DQ
    if _contains(t, _AI_SIGNALS):
        return "LEA.DQ", "fuzzy"

    # AEC networking → LEA.99 (only with explicit AEC context)
    if _contains(t, _LEA_NET_SIGNALS) and _contains(t, ["aec", "lea"]):
        return "LEA.99", "fuzzy"

    # AEC programming → LEA.3Q
    if _contains(t, _LEA_PROG_SIGNALS):
        return "LEA.3Q", "fuzzy"

    # IT DEC
    is_it = (
        _contains(t, _IT_GENERIC)
        or _contains(t, _IT_PROG_SIGNALS + _IT_NET_SIGNALS + _IT_GAME_SIGNALS)
        or "computer science" in t
        or "informatique" in t
    )
    if is_it:
        if _contains(t, _IT_GAME_SIGNALS): return "420.BX", "fuzzy"
        if _contains(t, _IT_NET_SIGNALS):  return "420.BR", "fuzzy"
        has_prog = _contains(t, _IT_PROG_SIGNALS) or (
            ("programmation" in t or "programming" in t)
            and _contains(t, [
                "computer science technology", "informatique",
                "technique de l'informatique", "techniques de",
                "dec ", "dcs ", "technique ",
            ])
        )
        if has_prog: return "420.BP", "fuzzy"
        return "420.B0", "fuzzy"

    # Interior design (DEC/AEC pair)
    if _contains(t, _INTERIOR_SIGNALS):
        return _disambiguate(_INTERIOR_CODES, "570.E0", "NTA.21",
                             programs, t, interactive, raw=raw)

    # Fashion
    if _contains(t, _FASHION_SIGNALS):
        if _contains(t, ["commercialisation", "merchandising"]): return "571.C0", "fuzzy"
        if _contains(t, ["design de la mode", "fashion design"]): return "571.A0", "fuzzy"
        if interactive:
            if raw.strip():
                import pydoc
                pydoc.pager(f"Student wrote:\n\n{raw.strip()}")
            opts = [p for p in programs if p["code"] in _FASHION_CODES]
            print("\n  Fashion — which program?")
            for i, p in enumerate(opts, 1):
                print(f"    {i}  {p['code']}  —  {p.get('label_fr', '')}")
            raw2 = input("  Enter number (or blank for 571.A0): ").strip()
            try:
                return opts[int(raw2) - 1]["code"], "manual"
            except (ValueError, IndexError):
                pass
        return "571.A0", "fuzzy"

    # Social media strategy
    if _contains(t, _SOCIAL_MEDIA_SIGNALS):
        return "NWY.1X", "fuzzy"

    # Accounting (DEC/AEC pair)
    if _contains(t, _ACCOUNTING_SIGNALS):
        return _disambiguate(_ACCOUNTING_CODES, "410.B0", "LCA.71",
                             programs, t, interactive, raw=raw)

    # Creative industries management (DEC only — 410.X0, no AEC pair)
    if _contains(t, _CREATIVE_MGMT_SIGNALS):
        return "410.X0", "fuzzy"

    # Commerce management (DEC/AEC pair)
    if _contains(t, _COMMERCE_SIGNALS):
        return _disambiguate(_COMMERCE_CODES, "410.D0", "LCA.70",
                             programs, t, interactive, raw=raw)

    # Logistics (DEC/AEC pair)
    if _contains(t, _LOGISTICS_SIGNALS):
        return _disambiguate(_LOGISTICS_CODES, "410.G0", "LCA.5G",
                             programs, t, interactive, raw=raw)

    # ── 5. Low-confidence fuzzy ───────────────────────────────────────────────
    if top_score >= 60:
        return top_code, "fuzzy"

    # ── 6. Fallback ───────────────────────────────────────────────────────────
    if not interactive:
        return "???", "unknown"   # never store a label as code

    print(f"\n  Could not resolve program: '{raw}'")
    print(f"  Known codes: {', '.join(codes)}")
    typed = input("  Enter code (or blank to skip): ").strip().upper()
    if not typed or typed not in codes:
        if typed:
            # Not a known code — store it but warn
            print(f"  Warning: '{typed}' is not a known code — stored as-is.")
            return typed, "manual"
        return "???", "unknown"
    return typed, "manual"


def refine_it_stream(cv_text: str) -> str | None:
    """
    Given the raw text of a student CV, try to infer which IT DEC stream
    they are in when their stored program code is 420.B0 (stream unknown).

    Returns the refined code ("420.BP", "420.BR", or "420.BX") or None if
    the text does not contain a clear enough signal — in which case the
    caller should keep 420.B0.

    The heuristic mirrors program_resolver.py's IT stream detection but is
    applied to free-form CV text rather than a form field, so it must be
    a little more conservative to avoid false positives.
    """
    t = _n(cv_text)

    # Game programming — strongest signal (very domain-specific vocabulary)
    if _contains(t, _IT_GAME_SIGNALS) or _contains(t, [
        "game developer", "game engine", "unity", "unreal", "godot",
        "jeux vidéo", "programmation de jeux", "game programming",
    ]):
        return "420.BX"

    # Network / security — fairly specific vocabulary
    if _contains(t, _IT_NET_SIGNALS) or _contains(t, [
        "cybersecurity", "cybersécurité", "firewall", "cisco", "ccna",
        "vpn", "wireshark", "sécurité réseau", "network security",
        "gestion de réseaux", "administration réseau",
    ]):
        return "420.BR"

    # Programming — only count strong markers, not just the word "programming"
    # since every IT student has that word somewhere
    if _contains(t, [
        "profil programmation", "profile in programming",
        "développement web", "web development",
        "développement d'applications", "application development",
        "développeur", "developer", "software engineer",
        "python", "javascript", "typescript", "react", "angular", "vue",
        "java ", "c#", "php", "laravel", "django", "spring",
        ".net", "node.js", "mobile development", "développement mobile",
    ]):
        return "420.BP"

    return None  # no clear signal — keep 420.B0
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
