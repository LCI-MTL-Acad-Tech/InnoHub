"""
bulk_import.py — batch import from a raw/ folder produced by MS Forms exports.

Expected structure:
  raw/
    students.csv  or  students.xlsx   — MS Forms export
    projects.csv  or  projects.xlsx   — MS Forms export (optional)
    CV/            — one file per student CV (PDF, DOCX, or image)
    CL/            — one file per student cover letter (PDF, DOCX, or image; optional)
    Desc/          — additional project documents (optional)

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

# Hours already completed in class before the placement begins.
# Subtract from the program's total internship hours to get placement hours.
COURSE_DURATION = 15

TODAY = date.today().isoformat()

# MS Forms column names — students
_COL_ID         = "Votre numéro d'étudiant / Your student ID number"
_COL_EMAIL      = "Votre adresse e-mail LCI / Your college email"
_COL_PROGRAM    = "Nom de votre programme d'études / Your study program name "
_COL_CV         = "Votre CV d'une page à jour / Your up-to-date one-page CV"
_COL_CL         = "Lettre de motivation / Cover letter"
_COL_LINKEDIN   = "URL de votre profil LinkedIn / Your LinkedIn profile URL"
_COL_PORTFOLIO  = "URL(s) de portfolio(s) / Your portfolio URL(s)"
_COL_LANG       = "Langue(s) de travail"
_COL_LEADERSHIP = "Leadership"
_COL_LIAISON    = "Liaison"

# MS Forms column names — projects
_PCOL_NAME    = "Votre nom / Your name"
_PCOL_EMAIL   = "Adresse e-mail / Contact email"
_PCOL_CLIENT  = "Description du client / Client description"
_PCOL_TITLE   = "Titre du projet / Project title"
_PCOL_DESC    = "Description du projet / Project description"
_PCOL_TASKS   = "Décomposition des tâches / Task breakdown"
_PCOL_CONTACT = "Veuillez décrire votre mode et fréquence de contact préférés pendant le travail sur le projet.\n\nPlease describe your preferred mode and frequency of contact during the project work.\n"
_PCOL_MORE    = "Envoyez-vous plus d'informations ? / Are you sending more information?"


# ── Profile resolvers ─────────────────────────────────────────────────────────

def _resolve_language_profile(raw: str) -> str:
    """
    Map a Langue(s) de travail freetext answer to a short code.

    Codes:
      bilingual_helper  — fully bilingual, happy to help others
      bilingual_passive — fully bilingual, not much help to others
      strong_fr         — French strong, working on English
      strong_en         — English strong, working on French
      en_only           — English only
      fr_only           — French only (or French dominant / English elementary)
      trilingual        — three or more languages
      undefined         — could not resolve
    """
    if not raw:
        return "undefined"
    r = raw.lower()
    if "espagnol" in r or "spanish" in r or "trois" in r or "three" in r or "3 lang" in r:
        return "trilingual"
    if "bilingue" in r or "bilingual" in r:
        if "aide" in r or "help" in r or "heureuse" in r or "happy" in r:
            return "bilingual_helper"
        return "bilingual_passive"
    if "français" in r and ("élevé" in r or "elementaire" in r or "élémentaire" in r):
        return "fr_only"
    if "français" in r and ("fort" in r or "bon" in r or "strong" in r or "french is strong" in r):
        return "strong_fr"
    if "anglais" in r and ("fort" in r or "bon" in r or "strong" in r or "english is strong" in r):
        return "strong_en"
    if "anglais" in r and ("seulement" in r or "only" in r or "vraiment capable" in r or "really only" in r):
        return "en_only"
    if "français" in r and ("seulement" in r or "only" in r):
        return "fr_only"
    return "undefined"


def _resolve_leadership(raw: str) -> str:
    """
    Map a Leadership freetext answer to a short code.

    Codes:
      lead     — already a team leader
      willing  — would like to be
      no       — not yet leadership material
      undefined
    """
    if not raw:
        return "undefined"
    r = raw.lower()
    if "déjà" in r or "already" in r:
        return "lead"
    if "souhaiterais" in r or "would like" in r:
        return "willing"
    if "pas encore" in r or "not yet" in r:
        return "no"
    return "undefined"


def _resolve_liaison(raw: str) -> bool | None:
    """
    Map a Liaison freetext answer to True (willing) or False (not willing).
    Returns None if the column was absent / unresolvable.
    """
    if not raw:
        return None
    r = raw.lower()
    if "souhaiterais" in r or "would like" in r:
        return True
    if "ne serai pas" in r or "won't be" in r or "trop" in r or "too much" in r:
        return False
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    console    = Console()
    teams_file = getattr(args, "teams_file", None)
    dry_run    = getattr(args, "dry_run", False)

    # ── Teams profile patch (--teams-file) ────────────────────────────────────
    if teams_file:
        _patch_student_profiles(Path(teams_file), dry_run, console)
        return

    raw_dir   = Path(args.dir)
    semester  = args.semester
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
    students_file = _find_tabular(raw_dir, "students")
    if students_file:
        console.print(f"  [dim]Students file: {students_file.name}[/dim]")
        _import_students(students_file, raw_dir, semester_str, default_h,
                         dry_run, console)
    else:
        console.print("  [dim]No students.csv / students.xlsx found — skipping students.[/dim]")

    # ── Projects ──────────────────────────────────────────────────────────────
    projects_file = _find_tabular(raw_dir, "projects")
    if projects_file:
        console.print(f"  [dim]Projects file: {projects_file.name}[/dim]")
        _import_projects(projects_file, raw_dir, semester_str, dry_run, console)
    else:
        console.print("  [dim]No projects.csv / projects.xlsx found — skipping projects.[/dim]")


# ── Teams profile patch ───────────────────────────────────────────────────────

def _patch_student_profiles(teams_path: Path, dry_run: bool, console: Console) -> None:
    """
    Read a teams form export (XLSX or CSV) and patch language_profile,
    leadership, and liaison_willing into existing student JSONs.
    Matched by email address. No re-embedding.
    """
    from src.store import list_ids, load_json, save_json

    if not teams_path.exists():
        console.print(f"  [red]Teams file not found: {teams_path}[/red]")
        return

    rows = _read_tabular(teams_path)
    if not rows:
        console.print(f"  [red]Teams file is empty or unreadable: {teams_path.name}[/red]")
        return

    console.print(
        f"\n  [bold]◈ Innovation Hub — Teams Profile Patch[/bold]"
        f"  [dim]{teams_path.name}  ·  {len(rows)} row(s)[/dim]"
        + ("  [yellow bold][DRY RUN][/yellow bold]" if dry_run else "")
        + "\n"
    )

    # Build email → student_number index
    email_index: dict[str, str] = {}
    for sid in list_ids("students"):
        try:
            m = load_json("students", sid)
            e = (m.get("email") or "").strip().lower()
            if e:
                email_index[e] = sid
        except Exception:
            pass

    updated = 0
    unmatched = 0

    for i, row in enumerate(rows, 1):
        # Email column in the teams form
        email_raw = (_get(row, "Email") or _get(row, "Courriel") or "").strip().lower()
        if not email_raw:
            continue

        sid = email_index.get(email_raw)
        if not sid:
            console.print(f"  [dim]Row {i}[/dim]  [yellow]{email_raw}[/yellow]  [dim]no match — skipped[/dim]")
            unmatched += 1
            continue

        lang_raw       = _get(row, _COL_LANG)
        leadership_raw = _get(row, _COL_LEADERSHIP)
        liaison_raw    = _get(row, _COL_LIAISON)

        language_profile = _resolve_language_profile(lang_raw)
        leadership       = _resolve_leadership(leadership_raw)
        liaison_willing  = _resolve_liaison(liaison_raw)

        if dry_run:
            console.print(
                f"  [yellow]→[/yellow]  [dim]{i:>2}[/dim]  [cyan]{sid}[/cyan]  {email_raw}"
                f"  lang=[bold]{language_profile}[/bold]"
                f"  leadership=[bold]{leadership}[/bold]"
                f"  liaison=[bold]{liaison_willing}[/bold]"
            )
            updated += 1
            continue

        try:
            meta = load_json("students", sid)
            meta["language_profile"] = language_profile
            meta["leadership"]       = leadership
            meta["liaison_willing"]  = liaison_willing
            # Ensure new fields exist with defaults if not already present
            meta.setdefault("team_lead_project", None)
            meta.setdefault("liaison_project",   None)
            save_json("students", sid, meta)
            console.print(
                f"  [green]✓[/green]  [dim]{i:>2}[/dim]  [cyan]{sid}[/cyan]  {email_raw}"
                f"  lang=[bold]{language_profile}[/bold]"
                f"  leadership=[bold]{leadership}[/bold]"
                f"  liaison=[bold]{liaison_willing}[/bold]"
            )
            updated += 1
        except Exception as e:
            console.print(f"  [red]✗[/red]  [dim]{i:>2}[/dim]  [cyan]{sid}[/cyan]  {e}")

    verb = "would update" if dry_run else "updated"
    console.print(
        f"\n  [bold]Done:[/bold]  [green]{updated} student(s) {verb}[/green]"
        + (f"  [yellow]{unmatched} unmatched[/yellow]" if unmatched else "")
        + "\n"
    )


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
    from src.program_resolver import resolve
    from src.embed import embed_text, save_embedding
    from src.parse import parse_file
    from src.audit import log as audit_log

    cv_dir  = raw_dir / "CV"
    cl_dir  = raw_dir / "CL"
    doc_dir = Path(PATHS["documents"]) / "students"
    emb_dir = Path(PATHS["embeddings"]) / "students"

    programs = load_programs()
    existing = set(list_ids("students"))

    rows = _read_tabular(csv_path)
    if not rows:
        try:
            _read_tabular(csv_path)
        except Exception as e:
            console.print(f"  [red]students file error: {e}[/red]")
            return
        console.print(f"  [red]students file is empty or has no readable rows: {csv_path.name}[/red]")
        return

    console.print(f"  [bold]Students[/bold]  ({len(rows)} rows)\n")

    # Detect whether the form included language/leadership/liaison columns
    first = rows[0] if rows else {}
    has_lang       = bool(_get(first, _COL_LANG))
    has_leadership = bool(_get(first, _COL_LEADERSHIP))
    has_liaison    = bool(_get(first, _COL_LIAISON))

    # Diagnostic: show actual column names if expected ones are missing
    if rows:
        missing = [c for c in [_COL_ID, _COL_EMAIL, _COL_PROGRAM, _COL_CV]
                   if not _get(first, c) and _get(first, c) == ""]
        if all(not _get(first, c) for c in [_COL_ID, _COL_EMAIL]):
            console.print("  [yellow]⚠ Expected columns not found. Actual columns:[/yellow]")
            for col in list(first.keys())[:8]:
                console.print(f"    [dim]{repr(col)}[/dim]")
            return

    results = []

    for i, row in enumerate(rows, 1):
        sid   = _get(row, _COL_ID)
        email = _get(row, _COL_EMAIL)
        prog_raw = _get(row, _COL_PROGRAM)
        cv_fname  = _get(row, _COL_CV)
        cl_fname  = _get(row, _COL_CL)
        linkedin  = _get(row, _COL_LINKEDIN)
        portfolio_raw = _get(row, _COL_PORTFOLIO)
        portfolio_urls = [u.strip() for u in re.split(r"[,\n]", portfolio_raw) if u.strip()]

        # Language / leadership / liaison — resolved from form if columns present
        lang_raw       = _get(row, _COL_LANG)       if has_lang       else ""
        leadership_raw = _get(row, _COL_LEADERSHIP) if has_leadership else ""
        liaison_raw    = _get(row, _COL_LIAISON)    if has_liaison    else ""

        language_profile = _resolve_language_profile(lang_raw)
        leadership       = _resolve_leadership(leadership_raw)
        liaison_willing  = _resolve_liaison(liaison_raw)

        if lang_raw and language_profile == "undefined":
            _row_warn(console, i, sid or "?",
                      f"Could not resolve language profile: '{lang_raw[:60]}'")
        if leadership_raw and leadership == "undefined":
            _row_warn(console, i, sid or "?",
                      f"Could not resolve leadership: '{leadership_raw[:60]}'")

        # ── Validate student ID ───────────────────────────────────────────────
        if not sid:
            _row_warn(console, i, "—", "No student ID — skipped")
            results.append(("?", f"row {i}", "?", "?", "skipped", "no student ID"))
            continue

        # ── Early skip if already ingested ────────────────────────────────────
        if sid in existing and not dry_run:
            console.print(
                f"  [dim]Row {i}[/dim]  {sid}  "
                f"[dim]already ingested — skipped[/dim]"
            )
            results.append((sid, "", "", "", "skipped", "already exists"))
            continue

        # ── Extract name from CV filename, then email ─────────────────────────
        name = (_name_from_forms_filename(cv_fname)
                or _name_from_forms_filename(cl_fname)
                or _name_from_email(email)
                or "")

        # ── Resolve program ───────────────────────────────────────────────────
        code, confidence = resolve(prog_raw, programs, interactive=not dry_run)

        if code == "420.B0":
            _row_warn(console, i, sid,
                      f"IT stream unclear from form field — will infer from CV")
        elif code == "570.??":
            _row_warn(console, i, sid, f"Interior design DEC/AEC unclear for '{prog_raw}' — stored as 570.??")
            if not dry_run:
                interior_codes = [p for p in programs
                                  if p["code"] in ("570.E0", "NTA.21")
                                  and p.get("active", "true") == "true"]
                print(f"\n  Interior design — DEC or AEC?")
                for j, p in enumerate(interior_codes, 1):
                    print(f"    {j}  {p['code']}  —  {p.get('label_fr', '')}")
                raw_choice = input("  Enter number (or blank to keep as 570.??): ").strip()
                try:
                    resolved = interior_codes[int(raw_choice) - 1]["code"]
                    code = resolved
                    confidence = "manual"
                except (ValueError, IndexError):
                    pass
        elif code == "???":
            _row_warn(console, i, sid, f"Could not resolve program '{prog_raw}'")

        # ── Hours available ───────────────────────────────────────────────────
        from src.store import semester_program_info
        sp_info = semester_program_info(semester, code)
        if sp_info:
            hours = sp_info["hours"] - COURSE_DURATION
        else:
            hours = default_hours - COURSE_DURATION
            if not dry_run and code not in ("???", "570.??"):
                _row_warn(console, i, sid,
                          f"No internship data for {code} / {semester} — using {hours}h default")

        cv_path = _find_file(cv_dir, cv_fname) if cv_fname else None
        cl_path = _find_file(cl_dir, cl_fname) if cl_fname else None

        if not cv_path:
            _row_warn(console, i, sid, f"CV file not found: '{cv_fname}'")

        # ── Skip if already exists and no new CV to update ───────────────────
        if sid in existing and not cv_path and not dry_run:
            console.print(
                f"  [dim]Row {i}[/dim]  {sid}  "
                f"[dim]Already ingested, no new CV — skipped[/dim]"
            )
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
                if code == "420.B0":
                    from src.program_resolver import refine_it_stream
                    cv_text = texts[0] if texts else ""
                    refined = refine_it_stream(cv_text)
                    if refined:
                        _row_info(console, i, sid,
                                  f"IT stream inferred from CV: [bold]{refined}[/bold] (was 420.B0)")
                        code = refined
                    else:
                        _row_warn(console, i, sid,
                                  "IT stream unclear from CV — stored as 420.B0")

                vector   = embed_text("\n\n".join(texts))
                emb_file = emb_dir / f"{sid}.npy"
                save_embedding(vector, emb_file)
                emb_path = str(emb_file)
            except Exception as e:
                _row_warn(console, i, sid, f"Embedding failed: {e}")

        if sid in existing:
            meta = load_json("students", sid)
            old_files = [d["filename"] for d in meta.get("documents", [])]
            for fname in old_files:
                old = doc_dir / fname
                if old.exists():
                    old.unlink()
            meta["documents"]        = doc_records
            meta["embedding_file"]   = emb_path
            meta["program"]          = code
            meta["linkedin_url"]     = linkedin
            meta["portfolio_urls"]   = portfolio_urls
            meta["hours_available"]  = hours
            # Only overwrite profile fields if the column was present in the form
            if has_lang:
                meta["language_profile"] = language_profile
            if has_leadership:
                meta["leadership"] = leadership
            # liaison_willing on its own doesn't set liaison_project —
            # that is set by the `lead` command. We only store the preference.
            if has_liaison:
                meta["liaison_willing"] = liaison_willing
            action = "replace"
        else:
            meta = {
                "student_number":       sid,
                "name":                 name,
                "email":                email,
                "program":              code,
                "semester_start":       semester,
                "hours_available":      hours,
                "status":               "active",
                "linkedin_url":         linkedin,
                "portfolio_urls":       portfolio_urls,
                "language_profile":     language_profile,
                "leadership":           leadership,
                "liaison_willing":      liaison_willing,  # True/False/None
                "team_lead_project":    None,
                "liaison_project":      None,
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
    rows = _read_tabular(csv_path)
    if not rows:
        try:
            _read_tabular(csv_path)
        except Exception as e:
            console.print(f"  [red]projects file error: {e}[/red]")
            return
        console.print(f"  [red]projects file is empty or has no readable rows: {csv_path.name}[/red]")
        return

    existing_projects = set(list_ids("projects"))
    force = getattr(args if hasattr(args, 'force') else type('', (), {})(), 'force', False) \
        if 'args' in dir() else False

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

        if not dry_run:
            from src.ingest import _slugify as _sl
            from src.semester import parse as _ps
            _s = _ps(semester)
            _ss = _s.to_short() if _s else semester.replace(" ", "")
            _title_slug = _sl(title)
            def _strip_prefix(pid: str) -> str:
                for sep in ["_"]:
                    parts = pid.split(sep)
                    for skip in (1, 2):
                        rest = sep.join(parts[skip:])
                        if _title_slug[:20] in rest:
                            return rest
                return pid
            _already = any(
                (_title_slug in pid and pid.endswith(_ss))
                or _title_slug[:30] in _strip_prefix(pid)
                for pid in existing_projects
            )
            if _already:
                console.print(
                    f"  [dim]Row {i}[/dim]  {title}  "
                    f"[dim]already ingested — skipped[/dim]"
                )
                results.append((title, "", "skipped", "already exists"))
                continue

        console.print(f"  [dim]Row {i}[/dim]  [bold]{title}[/bold]")

        n_teams = 1

        company_id, company_name = _resolve_company(
            client_raw, lead_name, lead_email, dry_run, console
        )

        extracted_tasks = _extract_tasks(tasks_raw)
        tasks = _confirm_tasks(extracted_tasks, tasks_raw, dry_run, console,
                               description=desc)

        if tasks is None:
            results.append((title, company_name, "skipped", "tasks aborted"))
            continue

        total_hours = sum(t["hours"] for t in tasks)

        language = detect_language(desc or tasks_raw)

        extra_files = []
        if desc_dir.exists():
            for f in desc_dir.iterdir():
                if f.is_file() and _title_matches_file(title, f.name):
                    extra_files.append(f)

        full_text = "\n\n".join(filter(None, [desc, tasks_raw,
                                              client_raw,
                                              *[f.read_text(errors="replace")
                                                if f.suffix in {".txt",".md"}
                                                else "" for f in extra_files]]))

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

        if project_id in existing_projects and not dry_run:
            console.print(
                f"    [dim]Already ingested — skipped. "
                f"Delete data/projects/{project_id}.json to re-import.[/dim]\n"
            )
            results.append((title, company_name, "skipped", "already exists"))
            continue

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

        emb_path = ""
        if full_text.strip():
            try:
                from src.embed import embed_text as _et, save_embedding as _se
                from src.store import PATHS
                from pathlib import Path as _P
                vec      = _et(full_text)
                emb_dir  = _P(PATHS["embeddings"]) / "projects"
                emb_dir.mkdir(parents=True, exist_ok=True)
                emb_file = emb_dir / f"{project_id}.npy"
                _se(vec, emb_file)
                emb_path = str(emb_file)
            except Exception as e:
                console.print(f"    [yellow]⚠ Embedding failed: {e}[/yellow]")

        doc_records = []
        from src.store import PATHS
        from pathlib import Path as _P
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

        from src.ingest import _canonical_filename
        for ef in extra_files:
            dest_name = _canonical_filename(project_id, "project_proposal", ef)
            shutil.copy(ef, doc_dir_p / dest_name)
            doc_records.append({
                "type":          "project_proposal",
                "filename":      dest_name,
                "ingested_date": TODAY,
            })

        _ensure_company(company_id, company_name, lead_name, lead_email,
                        language, semester)

        meta = {
            "project_id":      project_id,
            "company_id":      company_id,
            "title":           title,
            "status":          "active",
            "semester":        semester,
            "language":        language,
            "teams":           n_teams,
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

def _parse_hours(s: str) -> int | None:
    s = re.sub(r"\s*(h|hours?|heures?)\s*$", "", s.strip(), flags=re.IGNORECASE)
    try:
        return int(s)
    except ValueError:
        return None


_TOTAL_PAT = re.compile(
    r"^\s*(total|sous.total|subtotal|grand.total|estimé|estimated)\b",
    re.IGNORECASE,
)

_LINE_HOURS_FIRST_PAT = re.compile(
    r"^(\d+)\s*(?:h\b|heures?|hours?)\s+"
    r"([^:\n]+?)"
    r"\s*:\s*",
    re.IGNORECASE,
)

_LINE_HOURS_DASH_PAT = re.compile(
    r"^(\d+)\s*(?:h\b|heures?|hours?)"
    r"\s*[–—]\s*"
    r"(.+)$",
    re.IGNORECASE,
)

_LINE_PAT = re.compile(
    r"^[•\-*\d.]*\s*"
    r"(.+?)"
    r"\s*[:–—-]+\s*"
    r"(\d+)"
    r"(?:\s*[-–à]\s*\d+)?"
    r"\s*(?:h\b|heures?|hours?)",
    re.IGNORECASE,
)

_INLINE_PAT = re.compile(
    r"(?:^|(?<=[,;:\n(])|(?<=\bincluan[t])\s+|(?<=\bet\s)\s*)"
    r"\s*"
    r"([A-ZÀ-Ÿa-zà-ÿ/][^(,;:\n]{2,60}?)"
    r"\s*\(\s*(\d+)\s*(?:h\b|heures?|hours?)\s*\)",
    re.IGNORECASE | re.MULTILINE,
)

_PROSE_PAT = re.compile(
    r"((?:\w[\w/'' -]{1,50}?))"
    r"\s*\(\s*(\d+)\s*(?:h\b|heures?|hours?)\s*\)",
    re.IGNORECASE | re.UNICODE,
)

_POUR_HEURES_ANCHOR = re.compile(
    r"\s+pour\s+(\d+)\s*heures?\b",
    re.IGNORECASE,
)

def _extract_pour_heures(raw: str) -> list[tuple[str, int]]:
    results = []
    parts = re.split(r"\s+pour\s+(\d+)\s*heures?\b", raw, flags=re.IGNORECASE)
    for idx in range(1, len(parts), 2):
        hours_str = parts[idx]
        preceding = parts[idx - 1]
        preceding_clean = re.sub(r"\([^)]*\)", "", preceding).strip()
        segments = [s.strip() for s in preceding_clean.split(",") if s.strip()]
        if not segments:
            continue
        _CONJ = re.compile(
            r"^(ou|et|ainsi|mais|donc|car|nor|or|and|but)\b",
            re.IGNORECASE
        )
        while len(segments) > 1 and _CONJ.match(segments[-1]):
            merged = segments[-2] + ", " + segments[-1]
            segments = segments[:-2] + [merged]
        label = segments[-1]
        label = re.sub(
            r"^(?:incluant|notamment|ainsi\s+que|et\s+)?(?:l[ae'']|les|le|la|un|une|des|du)?\s+",
            "", label, flags=re.IGNORECASE
        ).strip()
        if re.search(r"\b(total|environ\s+\d+|réparti)", label, re.IGNORECASE):
            continue
        if len(label) < 4:
            continue
        try:
            results.append((label, int(hours_str)))
        except ValueError:
            pass
    return results

_LABEL_ONLY_PAT = re.compile(
    r"^"
    r"(?:[•\-*]\s*|(?:\d+[.)]\s*))?"
    r"([A-ZÀ-Ÿa-zà-ÿ][^\n]{5,120})"
    r"\s*$",
    re.MULTILINE,
)


def _extract_tasks(raw: str) -> list[dict]:
    tasks = []
    seen  = set()

    def _clean(label: str) -> str:
        label = re.sub(r"\s+", " ", label).strip("\u2022*-\u2013\u2014 :,().\"\n\t")
        label = re.sub(r"^[\d.]+\s*", "", label)
        _STRIP = re.compile(
            r"^(?:incluant|including|notamment|dont"
            r"|tels?\s+que|such\s+as"
            r"|et\s+|and\s+"
            r"|les\s+|le\s+|la\s+|l[\u2019\u2018\']\s*"
            r"|des\s+|du\s+|de\s+la\s+|de\s+l[\u2019\u2018\']\s*|de\s+"
            r"|un\s+|une\s+"
            r")\s*",
            re.IGNORECASE
        )
        for _ in range(6):
            stripped = _STRIP.sub("", label)
            if stripped == label:
                break
            label = stripped
        return label.strip("\u2022*-\u2013\u2014 :,().\"\n\t")

    def _add(label: str, hours: int) -> None:
        label = _clean(label)
        if not label or hours <= 0 or hours > 500:
            return
        if _TOTAL_PAT.match(label):
            return
        key = label.lower()[:30]
        if key in seen:
            return
        seen.add(key)
        tasks.append({"label": label, "hours": hours})

    prev_tech: list[str] = []
    blocks = re.split(r"\n{2,}", raw.strip())
    tab_tasks = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        parts = re.split(r"\t|   +", block, maxsplit=2)
        if len(parts) < 2:
            continue
        hours_part = parts[0].strip()
        if not re.match(r"^\d{1,3}h$", hours_part, re.IGNORECASE):
            continue
        hours = int(hours_part[:-1])
        desc_part = parts[1].strip() if len(parts) > 1 else ""
        tech_part = parts[2].strip() if len(parts) > 2 else ""
        if "—" in desc_part:
            title, detail = desc_part.split("—", 1)
            label = title.strip()
        else:
            label = desc_part
        if tech_part.strip().lower() == "same as above":
            tech = prev_tech[:]
        else:
            tech = [t.strip() for t in tech_part.split(",") if t.strip()]
            if tech:
                prev_tech = tech[:]
        tab_tasks.append({"label": label, "hours": hours,
                          "description": "", "tech": tech})
    if tab_tasks:
        for i, t in enumerate(tab_tasks, 1):
            t["task_id"] = f"t{i}"
            if "description" not in t:
                t["description"] = ""
        return tab_tasks

    for line in raw.splitlines():
        m = _LINE_PAT.match(line.strip())
        if m:
            _add(m.group(1).strip(), int(m.group(2)))

    if not tasks:
        for line in raw.splitlines():
            m = _LINE_HOURS_FIRST_PAT.match(line.strip())
            if m:
                _add(m.group(2).strip(), int(m.group(1)))

    dash_tasks: list[dict] = []
    seen_dash: set[str] = set()
    for line in raw.splitlines():
        m = _LINE_HOURS_DASH_PAT.match(line.strip())
        if m:
            label = m.group(2).strip()
            hours = int(m.group(1))
            if label and hours > 0 and not _TOTAL_PAT.match(label):
                key = label.lower()[:30]
                if key not in seen_dash:
                    seen_dash.add(key)
                    dash_tasks.append({"label": label, "hours": hours,
                                       "description": ""})
    if dash_tasks and len(dash_tasks) > len(tasks):
        tasks = dash_tasks

    if not tasks:
        for m in _INLINE_PAT.finditer(raw):
            _add(m.group(1), int(m.group(2)))

    if not tasks:
        for m in _PROSE_PAT.finditer(raw):
            _add(m.group(1), int(m.group(2)))

    if not tasks:
        for label, hours in _extract_pour_heures(raw):
            _add(label, hours)

    if not tasks:
        candidates = []
        for m in _LABEL_ONLY_PAT.finditer(raw):
            raw_label = m.group(1).strip()
            if not raw_label or len(raw_label) < 5:
                continue
            if _TOTAL_PAT.match(raw_label):
                continue
            if raw_label.count(",") > 3 or len(raw_label.split()) > 16:
                continue
            key = _clean(raw_label).lower()[:30]
            if not key or key in seen:
                continue
            seen.add(key)
            display = re.sub(r"^[•\-*\d.)]+\s*", "", raw_label).strip()
            candidates.append({"label": display, "hours": 0, "description": ""})
        if candidates:
            tasks = candidates

    for i, t in enumerate(tasks, 1):
        t["task_id"]     = f"t{i}"
        if "description" not in t:
            t["description"] = ""

    if tasks:
        garbled = sum(
            1 for t in tasks
            if len(t["label"]) <= 2
            or re.search(r"\.\s+[a-z]", t["label"])
        )
        if len(tasks) > 0 and garbled / len(tasks) > 0.60:
            return []

    return tasks


def _confirm_tasks(
    extracted: list[dict],
    raw: str,
    dry_run: bool,
    console: Console,
    description: str = "",
) -> list[dict] | None:
    from src.ingest import _prompt_tasks, _print_task_table

    def _show_description() -> None:
        import pydoc
        text = (description + "\n\n" + raw).strip() if description else raw.strip()
        if text:
            pydoc.pager(text)

    labels_only = extracted and all(t["hours"] == 0 for t in extracted)

    if labels_only:
        console.print(
            f"    [dim]Found {len(extracted)} task label(s) — hours not specified.[/dim]"
        )
        for t in extracted:
            console.print(f"    [dim]·[/dim] {t['label']}")
        console.print()

        if dry_run:
            return extracted

        _show_description()
        console.print(
            "    Enter hours for each task "
            "(or blank to skip that task, 0 to abort):\n"
        )
        result = []
        for t in extracted:
            raw_h = input(f"    {t['label']}: ").strip()
            if raw_h in ("0", "0h"):
                console.print("    Aborted.")
                return None
            if not raw_h:
                continue
            h = _parse_hours(raw_h)
            if h is None:
                console.print(f"    Invalid hours — skipping '{t['label']}'.")
                continue
            result.append({**t, "hours": h})
        if not result:
            console.print("    No tasks with hours — aborted.")
            return None
        total = sum(t["hours"] for t in result)
        _print_task_table(result, console)
        answer = input(f"\n    Confirm tasks? [{total}h total] [Y/n]: ").strip().lower()
        return result if answer in ("", "y") else None

    elif extracted:
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
        _show_description()
        console.print("    [dim]Entering manual task definition…[/dim]")

    else:
        if raw.strip() or description.strip():
            console.print(
                f"    [yellow]⚠ Could not extract structured tasks.[/yellow]"
            )
        else:
            console.print("    [dim]No task breakdown provided.[/dim]")

        if dry_run:
            return []

        answer = input("    Define tasks manually? [Y/n]: ").strip().lower()
        if answer not in ("", "y"):
            return None

        _show_description()

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
    from src.ingest import _slugify

    guessed = ""

    m = re.search(r"@([\w\-]+)\.", lead_email)
    if m:
        domain = m.group(1).lower().replace("-", "")
        if domain in _KNOWN_INSTITUTIONS:
            guessed = _KNOWN_INSTITUTIONS[domain]
        elif domain not in _GENERIC_DOMAINS:
            guessed = domain.replace("-", " ").title()

    if not guessed and client_desc and len(client_desc) < 60 and "\n" not in client_desc:
        guessed = client_desc

    if dry_run:
        name = guessed or f"(from {lead_name})"
        return _slugify(name)[:40], name

    if guessed:
        console.print(f"    Company: [bold]{guessed}[/bold]")
        answer = input("    Confirm or edit? [Y/e]: ").strip().lower()
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

def _read_tabular(path: Path) -> list[dict]:
    suffix = path.suffix.lower()

    if suffix == ".xlsx":
        try:
            import pandas as pd
        except ImportError:
            print("  ERROR: pandas not installed. Run: ./bin/pip install pandas")
            return []
        try:
            df = pd.read_excel(path, dtype=str)
            df = df.fillna("")
            return df.to_dict(orient="records")
        except Exception as e:
            print(f"  ERROR reading {path.name}: {e}")
            return []

    for delimiter in (";", ","):
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(path, newline="", encoding=enc) as f:
                    rows = list(csv.DictReader(f, delimiter=delimiter))
                    if rows:
                        return rows
            except Exception:
                continue
    return []


def _find_tabular(folder: Path, stem: str) -> Path | None:
    for ext in (".csv", ".xlsx"):
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    for ext in (".xlsx", ".csv"):
        matches = sorted(folder.glob(f"{stem}*{ext}"))
        if matches:
            return matches[0]
    for ext in (".xlsx", ".csv"):
        matches = sorted(f for f in folder.glob(f"*{ext}")
                         if stem.lower() in f.name.lower())
        if matches:
            return matches[0]
    return None


def _get(row: dict, key: str) -> str:
    import unicodedata

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFC", s)
        return s.lower().strip().replace("\xa0", " ").replace("'", "'").replace("\u2019", "'")

    v = row.get(key)
    if v is not None:
        return (v or "").strip()

    key_n = _norm(key)

    for k, val in row.items():
        if _norm(k) == key_n:
            return (val or "").strip()

    for k, val in row.items():
        k_n = _norm(k)
        if k_n.startswith(key_n) or key_n.startswith(k_n):
            return (val or "").strip()

    return ""


def _name_from_email(email: str) -> str:
    if not email or "@" not in email:
        return ""
    local = email.split("@")[0]
    local = re.sub(r"\d+$", "", local)
    parts = re.split(r"[._-]", local)
    return " ".join(p.capitalize() for p in parts if p)


def _name_from_forms_filename(raw: str) -> str:
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
    if not raw:
        return ""
    import unicodedata
    from urllib.parse import unquote
    if raw.startswith("http"):
        raw = raw.split("/")[-1].split("?")[0]
    raw = unquote(raw)
    return unicodedata.normalize("NFC", Path(raw).stem)


def _nfc_lower(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s).lower()


_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


def _ascii_only(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", nfkd.lower())


def _find_file(folder: Path, forms_value: str) -> Path | None:
    if not folder.exists() or not forms_value:
        return None

    target_stem = _nfc_lower(_filename_stem_from_forms(forms_value))
    if not target_stem:
        return None

    candidates = [f for f in folder.iterdir()
                  if f.is_file() and f.suffix.lower() in _DOC_EXTENSIONS]

    for f in candidates:
        if _nfc_lower(f.stem) == target_stem:
            return f

    for f in candidates:
        fs = _nfc_lower(f.stem)
        if target_stem in fs or fs in target_stem:
            return f

    if "_" in target_stem:
        name_part = target_stem.rsplit("_", 1)[-1].strip()
        if name_part:
            for f in candidates:
                if name_part in _nfc_lower(f.stem):
                    return f

    target_ascii = _ascii_only(target_stem)
    if target_ascii:
        for f in candidates:
            if _ascii_only(f.stem) == target_ascii:
                return f
        for f in candidates:
            fa = _ascii_only(f.stem)
            if target_ascii in fa or fa in target_ascii:
                return f

    return None


def _title_matches_file(title: str, filename: str) -> bool:
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


def _row_info(console, i, sid, msg):
    console.print(f"  [blue]ℹ[/blue]  [dim]{i:>2}[/dim]  [cyan]{sid}[/cyan]  {msg}")


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
