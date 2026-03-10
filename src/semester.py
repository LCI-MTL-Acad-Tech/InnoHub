"""
semester.py — parse, normalise, sort and display academic semesters.

Canonical term names and synonyms are loaded from config.toml at runtime,
making this module institution-agnostic.

Storage format in JSON/CSV: "<Term> <YYYY>"  e.g. "Fall 2024", "Winter 2025"

Accepted freeform input (resolved via config synonyms):
    "2024 Fall"  "fall of 24"  "Fall 24"  "F24"  "Autumn 2024"
    "A2024"      "24A"         "Winter 25" "W25"  "2025-Winter"
    "verano 24"  "otoño 2024"  "été 2025"  "S24"
    "2025-W"     "2024-F"      "printemps 2025"

Year: 4-digit (2024) or 2-digit (24 → 2024).

Academic year grouping:
    Determined by academic_year_start in config.
    AY starting Fall 2024 → label "AY2024-2025"
    If academic_year_start is the first term in terms[], calendar year
    and academic year are equivalent — no separate grouping needed.
"""
import re
from typing import NamedTuple


# ── Config loading ────────────────────────────────────────────────────────────

_config_cache: dict | None = None

def _load_config() -> dict:
    """Load and cache the [semesters] section from config.toml. Safe before setup."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        import tomllib
        with open("config.toml", "rb") as f:
            cfg = tomllib.load(f)
        _config_cache = cfg.get("semesters", {})
        return _config_cache
    except FileNotFoundError:
        return {}  # not cached — will retry on next call


def _terms() -> list[str]:
    """Canonical term names in calendar order, e.g. ['Winter', 'Summer', 'Fall']."""
    return _load_config().get("terms", ["Winter", "Summer", "Fall"])


def _synonyms() -> dict[str, set[str]]:
    """
    Return {canonical_term: {synonym_lowercase, ...}} from config.
    Falls back to sensible defaults if config has no synonyms section.
    """
    raw = _load_config().get("synonyms", {})
    if raw:
        return {term: {s.lower() for s in syns} for term, syns in raw.items()}
    # Hardcoded fallback — matches the defaults in config.template.toml
    return {
        "Winter": {
            "winter", "spring", "hiver", "printemps", "invierno", "primavera",
            "h", "w", "p", "i",
        },
        "Summer": {
            "summer", "été", "ete", "verano", "s", "e", "v",
        },
        "Fall": {
            "fall", "autumn", "automne", "otoño", "otono",
            "a", "f", "o",
        },
    }


def _ay_start() -> str:
    """Canonical name of the term that starts the academic year."""
    return _load_config().get("academic_year_start", _terms()[-1])


# ── Semester NamedTuple ───────────────────────────────────────────────────────

class Semester(NamedTuple):
    term: str   # canonical name from config, e.g. "Fall"
    year: int

    def __str__(self) -> str:
        return f"{self.term} {self.year}"

    def to_storage(self) -> str:
        """Canonical string written to JSON and CSV: 'Fall 2024'."""
        return f"{self.term} {self.year}"

    def to_short(self) -> str:
        """
        Short display form using first letter of term + year: 'F2024', 'W2025'.
        Falls back to full term if first letter is ambiguous.
        """
        terms = _terms()
        initials = [t[0].upper() for t in terms]
        letter   = self.term[0].upper()
        # Use first letter only if it's unique across all terms
        if initials.count(letter) == 1:
            return f"{letter}{self.year}"
        # Otherwise abbreviate to 3 chars
        return f"{self.term[:3]}{self.year}"

    def calendar_year(self) -> int:
        return self.year

    def academic_year(self) -> tuple[int, int]:
        """
        Return (start_year, end_year) of the academic year this semester belongs to.
        If academic_year_start is the first term, start==end (same as calendar year).
        """
        terms   = _terms()
        ay_term = _ay_start()

        ay_idx   = terms.index(ay_term) if ay_term in terms else 0
        this_idx = terms.index(self.term) if self.term in terms else 0

        if ay_idx == 0:
            # Academic year = calendar year, no boundary crossing
            return (self.year, self.year)

        if this_idx >= ay_idx:
            # This semester is in the first part of the AY
            return (self.year, self.year + 1)
        else:
            # This semester is in the second part (wraps from previous AY start)
            return (self.year - 1, self.year)

    def academic_year_label(self) -> str:
        """e.g. 'AY2024-2025' or 'AY2024' when calendar == academic year."""
        start, end = self.academic_year()
        if start == end:
            return f"AY{start}"
        return f"AY{start}-{end}"

    def sort_key_calendar(self) -> tuple[int, int]:
        """Sort key for calendar year ordering: (year, term_index)."""
        terms = _terms()
        idx   = terms.index(self.term) if self.term in terms else 0
        return (self.year, idx)

    def sort_key_academic(self) -> tuple[int, int, int]:
        """Sort key for academic year ordering: (ay_start, ay_term_idx, term_idx)."""
        terms    = _terms()
        ay_start = self.academic_year()[0]
        ay_term  = _ay_start()
        ay_idx   = terms.index(ay_term) if ay_term in terms else 0
        this_idx = terms.index(self.term) if self.term in terms else 0
        # Re-index so academic year start = 0
        reindexed = (this_idx - ay_idx) % len(terms)
        return (ay_start, reindexed)


# ── Term resolution ───────────────────────────────────────────────────────────

def _resolve_term(raw: str) -> str | None:
    """Return canonical term name, or None if unrecognised."""
    key   = raw.strip().lower()
    syns  = _synonyms()
    terms = _terms()
    for term in terms:
        candidates = syns.get(term, {term.lower()})
        if key in candidates or key == term.lower():
            return term
    return None


def _resolve_year(raw: str) -> int | None:
    """Accept 4-digit or 2-digit year strings."""
    try:
        y = int(raw.strip())
        if 0 <= y <= 99:
            y += 2000
        if 1990 <= y <= 2099:
            return y
    except ValueError:
        pass
    return None


# ── Parser ────────────────────────────────────────────────────────────────────

def parse(raw: str) -> "Semester | None":
    """
    Try to extract a (term, year) from a freeform string.
    Returns a Semester or None if parsing fails.
    """
    if not raw or not raw.strip():
        return None

    s = raw.strip()

    # Compact: F24, Fall2024, 24F, 2024Fall
    m = re.fullmatch(r"([A-Za-zÀ-ÿ]+)(\d{2,4})", s)
    if m:
        term = _resolve_term(m.group(1))
        year = _resolve_year(m.group(2))
        if term and year:
            return Semester(term, year)

    m = re.fullmatch(r"(\d{2,4})([A-Za-zÀ-ÿ]+)", s)
    if m:
        year = _resolve_year(m.group(1))
        term = _resolve_term(m.group(2))
        if term and year:
            return Semester(term, year)

    # Dash-separated: 2025-Fall, 2024-F
    m = re.fullmatch(r"(\d{4})-([A-Za-zÀ-ÿ]+)", s)
    if m:
        year = _resolve_year(m.group(1))
        term = _resolve_term(m.group(2))
        if term and year:
            return Semester(term, year)

    # Freeform: strip noise words then scan tokens
    cleaned = re.sub(r"\b(of|the|du|de|del|la|le|l')\b", " ", s, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\sÀ-ÿ]", " ", cleaned)
    tokens  = cleaned.split()

    term = year = None
    for token in tokens:
        if term is None:
            t = _resolve_term(token)
            if t:
                term = t
                continue
        if year is None:
            y = _resolve_year(token)
            if y:
                year = y
                continue

    if term and year:
        return Semester(term, year)

    return None


# ── Grouping helpers ──────────────────────────────────────────────────────────

def group_by_calendar(semesters: list["Semester"]) -> dict[int, list["Semester"]]:
    """Return {calendar_year: [Semester, ...]} sorted within each year."""
    groups: dict[int, list[Semester]] = {}
    for s in semesters:
        groups.setdefault(s.calendar_year(), []).append(s)
    for year in groups:
        groups[year].sort(key=lambda s: s.sort_key_calendar())
    return dict(sorted(groups.items()))


def group_by_academic(semesters: list["Semester"]) -> dict[str, list["Semester"]]:
    """
    Return {ay_label: [Semester, ...]} sorted within each AY.
    Falls back to calendar grouping if academic_year_start is the first term.
    """
    terms   = _terms()
    ay_term = _ay_start()

    # Detect equivalence — silent fallback
    if not terms or ay_term == terms[0]:
        cal = group_by_calendar(semesters)
        return {f"AY{year}": sems for year, sems in cal.items()}

    groups: dict[str, list[Semester]] = {}
    for s in semesters:
        label = s.academic_year_label()
        groups.setdefault(label, []).append(s)
    for label in groups:
        groups[label].sort(key=lambda s: s.sort_key_academic())
    return dict(sorted(groups.items()))


# ── Interactive prompt ────────────────────────────────────────────────────────

def prompt(args=None, label: str = "Semester") -> "Semester":
    """Loop until a valid semester is entered. Tries args.semester first."""
    raw = getattr(args, "semester", None) if args else None
    if raw:
        sem = parse(raw)
        if sem:
            return sem
        print(f"  Could not parse '{raw}' — please enter manually.")

    terms    = _terms()
    examples = "  /  ".join(f"{t[:1]}2025" for t in terms)

    while True:
        raw = input(f"  {label} (e.g. {examples}  or  Fall 2025): ").strip()
        if not raw:
            continue
        sem = parse(raw)
        if sem:
            return sem
        print(
            f"  Could not understand '{raw}'.\n"
            f"  Known terms: {', '.join(terms)}\n"
            f"  Examples: Fall 2024  /  F24  /  2024-Fall  /  otoño 2024"
        )


def from_storage(s: str) -> "Semester | None":
    """Parse a storage string back to a Semester. Returns None on failure."""
    return parse(s)
