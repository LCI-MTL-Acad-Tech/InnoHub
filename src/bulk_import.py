"""
bulk_import.py — batch import from a raw/ folder produced by MS Forms exports.

Expected structure:
  raw/
    students.csv   — MS Forms export, semicolon-delimited
    projects.csv   — MS Forms export, semicolon-delimited (optional)
    CV/            — one PDF per student CV
    CL/            — one PDF per student cover letter (optional per student)
    Desc/          — additional project documents (optional, possibly empty)

Run with:
  innovhub import --dir raw/ --semester "Winter 2026"
  innovhub import --dir raw/ --semester "Winter 2026" --dry-run
"""
import csv
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich import box


TODAY = date.today().isoformat()

# MS Forms column names — students
_COL_ID       = "Votre numéro d'étudiant / Your student ID number"
_COL_EMAIL    = "Votre adresse e-mail LCI / Your college email"
_COL_PROGRAM  = "Nom de votre programme d'études / Your study program name "
_COL_CV       = "Votre CV d'une page à jour / Your up-to-date one-page CV"
_COL_CL       = "Lettre de motivation / Cover letter"
_COL_LINKEDIN = "URL de votre profil LinkedIn / Your LinkedIn profile URL"
_COL_PORTFOLIO= "URL(s) de portfolio(s) / Your portfolio URL(s)"

# MS Forms column names — projects
_PCOL_NAME    = "Votre nom / Your name"
_PCOL_EMAIL   = "Adresse e-mail / Contact email"
_PCOL_CLIENT  = "Description du client / Client description"
_PCOL_TITLE   = "Titre du projet / Project title"
_PCOL_DESC    = "Description du projet / Project description"
_PCOL_TASKS   = "Décomposition des tâches / Task breakdown"
_PCOL_CONTACT = "Veuillez décrire votre mode et fréquence de contact préférés pendant le travail sur le projet.\n\nPlease describe your preferred mode and frequency of contact during the project work.\n"
_PCOL_MORE    = "Envoyez-vous plus d'informations ? / Are you sending more information?"


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    console   = Console()
    raw_dir   = Path(args.dir)
    semester  = args.semester
    dry_run   = getattr(args, "dry_run", False)
    default_h = getattr(args, "hours", 135)

    if not raw_dir.exists() or not raw_dir.is_dir():
        console.print(f"  [red]Directory not found: {raw_dir}[/red]")
        sys.exit(1)

    from src.semester import prompt as prompt_sem, parse as parse_sem
    import types
    sem_obj = parse_sem(semester)
    if not sem_obj:
        sem_obj = prompt_sem(types.SimpleNamespace(semester=semester))
    semester_str = sem_obj.to_storage()

    console.print(
        f"\n  [bold]◈ Innovation Hub — Bulk Import[/bold]"
        f"  [dim]semester: {semester_str}[/dim]"
        + ("  [yellow bold][DRY RUN][/yellow bold]" if dry_run else "")
        + "\n"
    )

    # ── Students ──────────────────────────────────────────────────────────────
    students_csv = raw_dir / "students.csv"
    if students_csv.exists():
        _import_students(students_csv, raw_dir, semester_str, default_h,
                         dry_run, console)
    else:
        console.print("  [dim]No students.csv found — skipping students.[/dim]")

    # ── Projects ──────────────────────────────────────────────────────────────
    projects_csv = raw_dir / "projects.csv"
    if projects_csv.exists():
        _import_projects(projects_csv, raw_dir, semester_str, dry_run, console)
    else:
        console.print("  [dim]No projects.csv found — skipping projects.[/dim]")


# ── Student import ────────────────────────────────────────────────────────────

def _import_students(
    csv_path: Path,
    raw_dir: Path,
    semester: str,
    default_hours: int,
    dry_run: bool,
    console: Console,
) -> None:
    from src.store import load_programs, save_json, list_ids, load_json, PATHS
    from src.program_resolver import resolve, resolve_pending_interior
    from src.embed import embed_text, save_embedding
    from src.parse import parse_file
    from src.audit import log as audit_log

    cv_dir  = raw_dir / "CV"
    cl_dir  = raw_dir / "CL"
    doc_dir = Path(PATHS["documents"]) / "students"
    emb_dir = Path(PATHS["embeddings"]) / "students"

    programs = load_programs()
    existing = set(list_ids("students"))

    rows = _read_csv(csv_path)
    if not rows:
        console.print("  [red]students.csv is empty or unreadable.[/red]")
        return

    console.print(f"  [bold]Students[/bold]  ({len(rows)} rows in CSV)\n")

    results = []   # (student_number, name, program, confidence, status, notes)

    for i, row in enumerate(rows, 1):
        sid   = _get(row, _COL_ID)
        email = _get(row, _COL_EMAIL)
        prog_raw = _get(row, _COL_PROGRAM)
        cv_fname  = _get(row, _COL_CV)
        cl_fname  = _get(row, _COL_CL)
        linkedin  = _get(row, _COL_LINKEDIN)
        portfolio_raw = _get(row, _COL_PORTFOLIO)
        portfolio_urls = [u.strip() for u in re.split(r"[,\n]", portfolio_raw) if u.strip()]

        # ── Validate student ID ───────────────────────────────────────────────
        if not sid:
            _row_warn(console, i, "—", "No student ID — skipped")
            results.append(("?", f"row {i}", "?", "?", "skipped", "no student ID"))
            continue

        # ── Extract name from CV filename ─────────────────────────────────────
        name = _name_from_forms_filename(cv_fname) or _name_from_forms_filename(cl_fname) or ""

        # ── Resolve program ───────────────────────────────────────────────────
        code, confidence = resolve(prog_raw, programs, interactive=not dry_run)

        if code == "420.B0":
            # 420.B0 is a valid stored code — IT confirmed, stream unknown
            _row_warn(console, i, sid,
                      f"IT stream unclear for '{prog_raw}' — stored as 420.B0 (confirm later)")
        elif code == "570.??":
            _row_warn(console, i, sid, f"Interior design DEC/AEC unclear for '{prog_raw}' — stored as 570.??")
            if not dry_run:
                resolved = resolve_pending_interior(programs)
                if resolved:
                    code = resolved
                    confidence = "manual"
        elif code == "???":
            _row_warn(console, i, sid, f"Could not resolve program '{prog_raw}'")

        # ── Find document files ───────────────────────────────────────────────
        cv_path = _find_file(cv_dir, cv_fname) if cv_fname else None
        cl_path = _find_file(cl_dir, cl_fname) if cl_fname else None

        if not cv_path:
            _row_warn(console, i, sid, f"CV file not found: '{cv_fname}'")

        # ── Skip if already exists and CV not found ───────────────────────────
        if sid in existing and not cv_path:
            _row_warn(console, i, sid, "Already exists and no new CV — skipped")
            results.append((sid, name, code, confidence, "skipped", "exists, no CV"))
            continue

        # ── Dry run output ────────────────────────────────────────────────────
        if dry_run:
            status = "would ingest" if sid not in existing else "would update"
            _row_ok(console, i, sid, name, code, confidence, cv_path, cl_path, dry_run=True)
            results.append((sid, name, code, confidence, status, ""))
            continue

        # ── Build and save ────────────────────────────────────────────────────
        doc_dir.mkdir(parents=True, exist_ok=True)
        emb_dir.mkdir(parents=True, exist_ok=True)

        doc_records = []
        texts       = []

        for fpath, dtype in [(cv_path, "cv"), (cl_path, "cover_letter")]:
            if not fpath:
                continue
            dest_name = f"{sid}_{dtype}{fpath.suffix.lower()}"
            dest      = doc_dir / dest_name
            shutil.copy(fpath, dest)
            doc_records.append({
                "type":          dtype,
                "filename":      dest_name,
                "ingested_date": TODAY,
            })
            try:
                texts.append(parse_file(dest))
            except Exception as e:
                _row_warn(console, i, sid, f"Could not parse {dtype}: {e}")

        # Embed combined text
        emb_path = ""
        if texts:
            try:
                vector   = embed_text("\n\n".join(texts))
                emb_file = emb_dir / f"{sid}.npy"
                save_embedding(vector, emb_file)
                emb_path = str(emb_file)
            except Exception as e:
                _row_warn(console, i, sid, f"Embedding failed: {e}")

        # Load existing meta to preserve reassignment history etc.
        if sid in existing:
            meta = load_json("students", sid)
            old_files = [d["filename"] for d in meta.get("documents", [])]
            # Delete old files
            for fname in old_files:
                old = doc_dir / fname
                if old.exists():
                    old.unlink()
            meta["documents"]      = doc_records
            meta["embedding_file"] = emb_path
            meta["program"]        = code
            meta["linkedin_url"]   = linkedin
            meta["portfolio_urls"] = portfolio_urls
            if emb_path:
                meta["embedding_file"] = emb_path
            action = "replace"
        else:
            meta = {
                "student_number":       sid,
                "name":                 name,
                "email":                email,
                "program":              code,
                "semester_start":       semester,
                "hours_available":      default_hours,
                "status":               "active",
                "linkedin_url":         linkedin,
                "portfolio_urls":       portfolio_urls,
                "reassignment_history": [],
                "documents":            doc_records,
                "embedding_file":       emb_path,
                "notes":                "",
            }
            action = "ingest"

        save_json("students", sid, meta)
        audit_log(action, "students", sid,
                  files=[r["filename"] for r in doc_records],
                  program=code, program_confidence=confidence)

        _row_ok(console, i, sid, name, code, confidence, cv_path, cl_path)
        results.append((sid, name, code, confidence, action, ""))

    _print_student_summary(results, console)


# ── Project import ────────────────────────────────────────────────────────────

def _import_projects(
    csv_path: Path,
    raw_dir: Path,
    semester: str,
    dry_run: bool,
    console: Console,
) -> None:
    from src.store import list_ids, load_json, save_json, PATHS
    from src.ingest import (
        _parse_and_embed, _save_documents, _save_emb,
        _slugify, _extract_email, _canonical_filename,
    )
    from src.language import detect_language
    from src.audit import log as audit_log

    desc_dir = raw_dir / "Desc"
    rows = _read_csv(csv_path)
    if not rows:
        console.print("  [red]projects.csv is empty or unreadable.[/red]")
        return

    console.print(f"\n  [bold]Projects[/bold]  ({len(rows)} rows in CSV)\n")

    results = []

    for i, row in enumerate(rows, 1):
        lead_name  = _get(row, _PCOL_NAME)
        lead_email = _get(row, _PCOL_EMAIL)
        client_raw = _get(row, _PCOL_CLIENT)
        title      = _get(row, _PCOL_TITLE)
        desc       = _get(row, _PCOL_DESC)
        tasks_raw  = _get(row, _PCOL_TASKS)
        more_raw   = _get(row, _PCOL_MORE).lower()
        more_docs  = more_raw.startswith("oui") or more_raw.startswith("yes")

        if not title:
            _row_warn(console, i, "—", "No project title — skipped")
            results.append(("?", f"row {i}", "skipped", "no title"))
            continue

        console.print(f"  [dim]Row {i}[/dim]  [bold]{title}[/bold]")

        # ── Company resolution ────────────────────────────────────────────────
        company_id, company_name = _resolve_company(
            client_raw, lead_name, lead_email, dry_run, console
        )

        # ── Task extraction ───────────────────────────────────────────────────
        extracted_tasks = _extract_tasks(tasks_raw)
        tasks = _confirm_tasks(extracted_tasks, tasks_raw, dry_run, console)

        if tasks is None:
            results.append((title, company_name, "skipped", "tasks aborted"))
            continue

        total_hours = sum(t["hours"] for t in tasks)

        # ── Language detection ────────────────────────────────────────────────
        language = detect_language(desc or tasks_raw)

        # ── Find additional documents ─────────────────────────────────────────
        extra_files = []
        if desc_dir.exists():
            for f in desc_dir.iterdir():
                if f.is_file() and _title_matches_file(title, f.name):
                    extra_files.append(f)

        # ── Build full text for embedding ─────────────────────────────────────
        full_text = "\n\n".join(filter(None, [desc, tasks_raw,
                                              client_raw,
                                              *[f.read_text(errors="replace")
                                                if f.suffix in {".txt",".md"}
                                                else "" for f in extra_files]]))

        # Deduplicate: if title slug starts with company_id slug, don't repeat it
        from src.ingest import _slugify
        from src.semester import parse as _parse_sem
        _sem = _parse_sem(semester)
        sem_short    = _sem.to_short() if _sem else semester.replace(" ", "")
        title_slug   = _slugify(title)
        company_slug = _slugify(company_id)
        if title_slug.startswith(company_slug + "_") or title_slug == company_slug:
            raw_id = f"{title_slug}_{sem_short}"
        else:
            raw_id = f"{company_slug}_{title_slug}_{sem_short}"
        project_id = raw_id[:64]

        if dry_run:
            console.print(
                f"    [dim]company:[/dim]  {company_name}  [{company_id}]\n"
                f"    [dim]tasks:[/dim]    {len(tasks)} task(s), {total_hours}h total\n"
                f"    [dim]language:[/dim] {language}\n"
                f"    [dim]extra docs:[/dim] {len(extra_files)}\n"
                f"    [yellow]→ would create project {project_id}[/yellow]\n"
            )
            results.append((title, company_name, "would ingest", ""))
            continue

        # ── Embed ─────────────────────────────────────────────────────────────
        from src.embed import embed_text, save_embedding
        from src.store import PATHS
        from pathlib import Path as _P

        emb_path = ""
        if full_text.strip():
            try:
                from src.embed import embed_text as _et, save_embedding as _se
                vec      = _et(full_text)
                emb_dir  = _P(PATHS["embeddings"]) / "projects"
                emb_dir.mkdir(parents=True, exist_ok=True)
                emb_file = emb_dir / f"{project_id}.npy"
                _se(vec, emb_file)
                emb_path = str(emb_file)
            except Exception as e:
                console.print(f"    [yellow]⚠ Embedding failed: {e}[/yellow]")

        # ── Save description as a text document ───────────────────────────────
        doc_records = []
        doc_dir_p   = _P(PATHS["documents"]) / "projects"
        doc_dir_p.mkdir(parents=True, exist_ok=True)

        if full_text.strip():
            text_filename = f"{project_id}_project_proposal.txt"
            (doc_dir_p / text_filename).write_text(full_text, encoding="utf-8")
            doc_records.append({
                "type":          "project_proposal",
                "filename":      text_filename,
                "ingested_date": TODAY,
            })

        for ef in extra_files:
            dest_name = _canonical_filename(project_id, "project_proposal", ef)
            shutil.copy(ef, doc_dir_p / dest_name)
            doc_records.append({
                "type":          "project_proposal",
                "filename":      dest_name,
                "ingested_date": TODAY,
            })

        # ── Ensure company exists ─────────────────────────────────────────────
        _ensure_company(company_id, company_name, lead_name, lead_email,
                        language, semester)

        meta = {
            "project_id":      project_id,
            "company_id":      company_id,
            "title":           title,
            "status":          "active",
            "semester":        semester,
            "language":        language,
            "capacity": {
                "total_hours": total_hours,
                "tasks":       tasks,
            },
            "lead_name":       lead_name,
            "lead_email":      lead_email,
            "renewal_history": [],
            "documents":       doc_records,
            "coordinators":    [],
            "embedding_file":  emb_path,
            "notes":           f"Contact: {_get(row, _PCOL_CONTACT)}",
        }
        save_json("projects", project_id, meta)
        audit_log("ingest", "projects", project_id,
                  company=company_id,
                  files=[r["filename"] for r in doc_records])

        console.print(
            f"    [green]✓[/green]  {project_id}\n"
            f"    [dim]{len(tasks)} task(s)  ·  {total_hours}h  ·  {language}[/dim]\n"
        )
        results.append((title, company_name, "ingested", ""))

    _print_project_summary(results, console)


# ── Task extraction ───────────────────────────────────────────────────────────

_TOTAL_PAT = re.compile(
    r"^\s*(total|sous.total|subtotal|grand.total|estimé|estimated)\b",
    re.IGNORECASE,
)

# Line format: "Label : 40 h" or "Label — 40h" or "• Label : 30-50h"
_LINE_PAT = re.compile(
    r"^[•\-*\d.]*\s*"           # optional bullet / number
    r"(.+?)"                     # label (non-greedy)
    r"\s*[:–—-]+\s*"             # separator
    r"(\d+)"                     # lower bound of hours
    r"(?:\s*[-–à]\s*\d+)?"       # optional upper bound (range) — ignored
    r"\s*(?:h\b|heures?|hours?)",
    re.IGNORECASE,
)

# Inline format: "label text (50 h)" — one per line or comma-separated
_INLINE_PAT = re.compile(
    r"([A-ZÀ-Ÿa-zà-ÿ][^(]{3,60}?)"   # label — starts with letter, not too long
    r"\s*\((\d+)\s*h\)",               # (N h)
    re.IGNORECASE,
)


def _extract_tasks(raw: str) -> list[dict]:
    """
    Try to extract (label, hours) pairs from a freeform task breakdown string.
    Handles three formats:
      - Line format:   "Label : 40 h"  or  "• Label — 30-50h"
      - Inline format: "conception UX/UI (50 h), analyse (40 h)"
    Returns list of task dicts. Empty list if nothing extractable.
    """
    tasks = []
    seen  = set()

    def _add(label: str, hours: int) -> None:
        label = re.sub(r"\s+", " ", label).strip("•*-–— :,.()")
        label = re.sub(r"^[\d.]+\s*", "", label)   # strip leading numbering
        if not label or hours <= 0 or hours > 500:
            return
        if _TOTAL_PAT.match(label):
            return
        key = label.lower()[:30]
        if key in seen:
            return
        seen.add(key)
        tasks.append({"label": label, "hours": hours})

    # ── Try line format first ─────────────────────────────────────────────────
    for line in raw.splitlines():
        m = _LINE_PAT.match(line.strip())
        if m:
            _add(m.group(1).strip(), int(m.group(2)))

    # ── Try inline format if line format found nothing ─────────────────────────
    if not tasks:
        for m in _INLINE_PAT.finditer(raw):
            _add(m.group(1).strip(), int(m.group(2)))

    # Assign task IDs
    for i, t in enumerate(tasks, 1):
        t["task_id"]     = f"t{i}"
        t["description"] = ""

    # ── Sanity check: discard results with garbled labels ─────────────────────
    # True garbled labels (from dense prose) start mid-word — a lowercase
    # letter that is clearly not the beginning of a proper word.
    # We flag a label as garbled if it starts with a lowercase ASCII letter
    # (accented capitals like À are fine). If >40% are garbled, the text is
    # too dense to parse reliably — return empty and prompt manually.
    if tasks:
        garbled = sum(1 for t in tasks if t["label"] and t["label"][0].islower())
        if garbled / len(tasks) > 0.40:
            return []

    return tasks


def _confirm_tasks(
    extracted: list[dict],
    raw: str,
    dry_run: bool,
    console: Console,
) -> list[dict] | None:
    """
    Show extracted tasks for confirmation, or fall back to manual entry.
    Returns confirmed task list, or None if user aborts.
    """
    from src.ingest import _prompt_tasks, _print_task_table

    if extracted:
        total = sum(t["hours"] for t in extracted)
        console.print(
            f"    [dim]Extracted {len(extracted)} task(s), {total}h total:[/dim]"
        )
        _print_task_table(extracted, console)

        if dry_run:
            return extracted

        answer = input("    Confirm tasks? [Y/n/edit]: ").strip().lower()
        if answer in ("", "y"):
            return extracted
        if answer == "n":
            return None
        # edit → fall through to manual
        console.print("    [dim]Entering manual task definition…[/dim]")

    else:
        if raw.strip():
            console.print(
                f"    [yellow]⚠ Could not extract structured tasks from:[/yellow]\n"
                f"    [dim]{raw[:200]}{'…' if len(raw)>200 else ''}[/dim]"
            )
        else:
            console.print("    [dim]No task breakdown provided.[/dim]")

        if dry_run:
            return []

        answer = input("    Define tasks manually? [Y/n]: ").strip().lower()
        if answer not in ("", "y"):
            return None

    if not dry_run:
        return _prompt_tasks(console)
    return []


# ── Company resolution ────────────────────────────────────────────────────────

_KNOWN_INSTITUTIONS = {
    "lcieducation":   "LCI Éducation",
    "collegelasalle": "Collège LaSalle",
    "lasallecollege": "Collège LaSalle",
}

_GENERIC_DOMAINS = {"gmail","hotmail","yahoo","outlook","icloud","proton","protonmail"}


def _resolve_company(
    client_desc: str,
    lead_name: str,
    lead_email: str,
    dry_run: bool,
    console: Console,
) -> tuple[str, str]:
    """
    Try to infer a company name from the client description field and email domain.
    Prompts for confirmation. Returns (company_id, company_name).
    """
    from src.ingest import _slugify

    guessed = ""

    # 1. Known institutional domains → canonical name
    m = re.search(r"@([\w\-]+)\.", lead_email)
    if m:
        domain = m.group(1).lower().replace("-", "")
        if domain in _KNOWN_INSTITUTIONS:
            guessed = _KNOWN_INSTITUTIONS[domain]
        elif domain not in _GENERIC_DOMAINS:
            guessed = domain.replace("-", " ").title()

    # 2. Short client description (likely a company name)
    if not guessed and client_desc and len(client_desc) < 60 and "\n" not in client_desc:
        guessed = client_desc

    if dry_run:
        name = guessed or f"(from {lead_name})"
        return _slugify(name)[:40], name

    if guessed:
        answer = input(
            f"    Company: [bold]{guessed}[/bold] — confirm or edit [Y/e]: "
        ).strip().lower()
        if answer in ("", "y"):
            name = guessed
        else:
            name = input("    Company name: ").strip() or guessed
    else:
        console.print(
            f"    [yellow]Could not infer company name from:[/yellow] {client_desc[:80]}"
        )
        name = input("    Company name: ").strip() or lead_name

    return _slugify(name)[:40], name


def _ensure_company(
    company_id: str,
    name: str,
    lead_name: str,
    lead_email: str,
    language: str,
    semester: str,
) -> None:
    """Create company JSON if it doesn't exist yet."""
    from src.store import list_ids, save_json
    if company_id in list_ids("companies"):
        return
    save_json("companies", company_id, {
        "company_id":         company_id,
        "name":               name,
        "status":             "active",
        "language":           language,
        "contact_name":       lead_name,
        "contact_email":      lead_email,
        "activation_history": [{"semester": semester,
                                "activated_date": TODAY,
                                "deactivated_date": None}],
        "documents":          [],
        "embedding_file":     "",
        "notes":              "",
    })


# ── Utilities ─────────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> list[dict]:
    """Read a semicolon-delimited CSV, try common encodings."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f, delimiter=";"))
        except Exception:
            continue
    return []


def _get(row: dict, key: str) -> str:
    """
    Case- and whitespace-tolerant dict lookup for MS Forms CSV headers.
    Handles trailing spaces, non-breaking spaces, and apostrophe variants.
    """
    # Direct hit first
    v = row.get(key)
    if v is not None:
        return v.strip()
    # Normalise both key and all row keys
    def _norm(s: str) -> str:
        return s.lower().strip().replace("\xa0", " ").replace("'", "'").replace("'", "'")
    key_n = _norm(key)
    for k, val in row.items():
        if _norm(k) == key_n:
            return (val or "").strip()
    return ""


def _name_from_forms_filename(raw: str) -> str:
    """
    MS Forms appends '_Firstname Lastname (truncated)' to uploaded filenames.
    Works on both plain filenames and SharePoint URLs.
    """
    if not raw:
        return ""
    from urllib.parse import unquote
    if raw.startswith("http"):
        raw = raw.split("/")[-1].split("?")[0]
    stem = Path(unquote(raw)).stem
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        return parts[1].strip()
    return ""


def _filename_stem_from_forms(raw: str) -> str:
    """
    Extract a matchable filename stem from either:
      - a plain filename:  "AkramCVvvv (1)_Akram Boughlala.pdf"
      - a SharePoint URL:  "https://.../%20Akram%20Boughlala.pdf"
    Returns the decoded stem (without extension), or empty string.
    """
    if not raw:
        return ""
    from urllib.parse import unquote
    # If it looks like a URL, take everything after the last /
    if raw.startswith("http"):
        raw = raw.split("/")[-1].split("?")[0]
    raw = unquote(raw)
    return Path(raw).stem


def _find_file(folder: Path, forms_value: str) -> Path | None:
    """
    Find the actual file in folder whose stem best matches the Forms value.
    forms_value may be a plain filename or a SharePoint URL.
    """
    if not folder.exists() or not forms_value:
        return None

    target_stem = _filename_stem_from_forms(forms_value).lower()
    if not target_stem:
        return None

    # Exact stem match
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.stem.lower() == target_stem:
            return f

    # Partial match — target stem contained in file stem, or vice versa
    # MS Forms appends "_Firstname Lastname" so the original name is the prefix
    for f in folder.iterdir():
        if not f.is_file():
            continue
        fs = f.stem.lower()
        if target_stem in fs or fs in target_stem:
            return f

    # Last resort: match on the name portion after the last underscore
    # e.g. "AkramCVvvv_Akram Boughlala" → match on "akram boughlala"
    if "_" in target_stem:
        name_part = target_stem.rsplit("_", 1)[-1].strip()
        if name_part:
            for f in folder.iterdir():
                if not f.is_file():
                    continue
                if name_part in f.stem.lower():
                    return f

    return None


def _title_matches_file(title: str, filename: str) -> bool:
    """Check if a Desc/ file seems related to a project title."""
    title_words = set(re.findall(r"\w{4,}", title.lower()))
    file_words  = set(re.findall(r"\w{4,}", filename.lower()))
    return len(title_words & file_words) >= 2


# ── Summary printers ──────────────────────────────────────────────────────────

def _row_ok(console, i, sid, name, code, confidence, cv, cl, dry_run=False):
    verb   = "→" if dry_run else "✓"
    color  = "yellow" if dry_run else "green"
    conf_c = "dim" if confidence in ("exact","fuzzy") else "yellow"
    cv_s   = "✓" if cv else "[dim]—[/dim]"
    cl_s   = "✓" if cl else "[dim]—[/dim]"
    console.print(
        f"  [{color}]{verb}[/{color}]  [dim]{i:>2}[/dim]"
        f"  [cyan]{sid}[/cyan]"
        f"  {name or '[dim]name pending[/dim]'}"
        f"  [bold]{code}[/bold] [{conf_c}]({confidence})[/{conf_c}]"
        f"  CV:{cv_s} CL:{cl_s}"
    )


def _row_warn(console, i, sid, msg):
    console.print(f"  [yellow]⚠[/yellow]  [dim]{i:>2}[/dim]  [cyan]{sid}[/cyan]  {msg}")


def _print_student_summary(results: list, console: Console) -> None:
    ingested = sum(1 for r in results if r[4] in ("ingest","replace","would ingest","would update"))
    pending  = sum(1 for r in results if r[2] in ("420.B0", "570.??"))
    skipped  = sum(1 for r in results if r[4] == "skipped")
    errors   = sum(1 for r in results if r[4] == "error")

    console.print(
        f"\n  [bold]Students summary:[/bold]"
        f"  [green]{ingested} ingested[/green]"
        + (f"  [yellow]{pending} pending program[/yellow]" if pending else "")
        + (f"  [dim]{skipped} skipped[/dim]" if skipped else "")
        + (f"  [red]{errors} error(s)[/red]" if errors else "")
    )
    if pending:
        console.print(
            "  [dim]Run [bold]innovhub list students --pending-program[/bold]"
            " to see all pending confirmations.[/dim]"
        )
    console.print()


def _print_project_summary(results: list, console: Console) -> None:
    ingested = sum(1 for r in results if "ingest" in r[2])
    skipped  = sum(1 for r in results if r[2] == "skipped")
    console.print(
        f"  [bold]Projects summary:[/bold]"
        f"  [green]{ingested} ingested[/green]"
        + (f"  [dim]{skipped} skipped[/dim]" if skipped else "")
    )
    console.print()
