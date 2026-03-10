"""
ingest.py — parse, embed, and store incoming documents.
Handles duplicate detection, program validation, and task definition for projects.
"""
import re
import uuid
from datetime import date
from pathlib import Path

import tomllib

from src.store import (
    load_json, save_json, list_ids,
    load_programs, save_programs, load_assignments,
)
from src.parse import parse_file
from src.embed import embed_text, save_embedding, cosine_similarity, load_embedding
from src.language import detect_language
from src.fuzzy import detect_program_typo, ranked_matches
from src.audit import log as audit_log

TODAY = date.today().isoformat()

with open("config.toml", "rb") as f:
    _CFG = tomllib.load(f)

DEDUP_THRESHOLD = _CFG["matching"]["dedup_threshold"]
PATHS           = _CFG["paths"]


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    kind  = _resolve_type(args.type)
    files = [Path(f) for f in args.files]

    # Coordinators may be ingested with no documents
    if kind != "coordinator" and not files:
        print("  At least one file is required for this document type.")
        return

    for f in files:
        if not f.exists():
            print(f"  File not found: {f}")
            return

    if kind == "student":
        _ingest_student(files, args)
    elif kind == "company":
        _ingest_company(files, args)
    elif kind == "project":
        _ingest_project(files, args)
    elif kind == "coordinator":
        _ingest_coordinator(files, args)


def _resolve_type(t: str) -> str:
    return {"s": "student", "c": "company", "p": "project",
            "coord": "coordinator"}.get(t, t)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _parse_and_embed(files: list[Path]) -> tuple[str, object]:
    """Concatenate text from all files, return (text, vector)."""
    text = "\n\n".join(parse_file(f) for f in files)
    return text, embed_text(text)


def _find_similar(kind: str, vector, exclude_id: str = "") -> list[tuple[str, float]]:
    """Return [(entity_id, score)] above dedup threshold, sorted descending."""
    results = []
    for eid in list_ids(kind):
        if eid == exclude_id:
            continue
        try:
            meta = load_json(kind, eid)
            emb_path = meta.get("embedding_file", "")
            if not emb_path or not Path(emb_path).exists():
                continue
            score = cosine_similarity(vector, load_embedding(emb_path))
            if score >= DEDUP_THRESHOLD:
                results.append((eid, score))
        except Exception:
            continue
    return sorted(results, key=lambda x: x[1], reverse=True)


def _canonical_filename(entity_id: str, doc_type: str, original: Path) -> str:
    """
    Build a canonical filename: <entity_id_safe>_<doc_type_safe><ext>
    - entity_id: student number, company slug, or coordinator email-safe slug
    - doc_type:  cv, cover_letter, company_description, project_proposal
    - ext:       preserved from original file
    """
    safe_id   = entity_id.replace("@", "_").replace(".", "_")
    safe_type = doc_type.replace(" ", "_").lower()
    return f"{safe_id}_{safe_type}{original.suffix.lower()}"


def _save_documents(
    kind: str,
    entity_id: str,
    files: list[Path],
    doc_type: str,
    old_filenames: list[str] | None = None,
) -> list[dict]:
    """
    Copy source files into data/documents/<kind>/ with canonical names.
    Deletes old files if old_filenames is provided (replace flow).
    Returns document records.
    """
    import shutil
    doc_dir = Path(PATHS["documents"]) / kind
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Remove old files if replacing
    if old_filenames:
        for fname in old_filenames:
            old_path = doc_dir / fname
            if old_path.exists():
                old_path.unlink()

    records = []
    for f in files:
        dest_name = _canonical_filename(entity_id, doc_type, f)
        dest      = doc_dir / dest_name
        if dest.resolve() != f.resolve():
            shutil.copy(f, dest)
        records.append({
            "type":          doc_type,
            "filename":      dest_name,
            "ingested_date": TODAY,
        })
    return records


def _save_emb(kind: str, entity_id: str, vector) -> str:
    """Persist embedding, return relative path string."""
    emb_path = Path(PATHS["embeddings"]) / kind / f"{entity_id}.npy"
    save_embedding(vector, emb_path)
    return str(emb_path)


# ── Student ingest ────────────────────────────────────────────────────────────

def _ingest_student(files: list[Path], args) -> None:
    from rich.console import Console
    console = Console()

    student_number = getattr(args, "id", None)
    program_code   = getattr(args, "program", None)

    if not student_number:
        student_number = input("  Student number: ").strip()
    if not program_code:
        program_code = input("  Program code: ").strip()

    program_code = program_code.upper()

    # ── Program validation ────────────────────────────────────────────────────
    programs     = load_programs()
    known_codes  = [p["code"] for p in programs if p.get("active", "true") == "true"]
    program_meta = None

    if program_code not in known_codes:
        suggestion, score = detect_program_typo(program_code, known_codes)
        if suggestion and score >= 70:
            answer = input(
                f"  '{program_code}' not recognised. Did you mean '{suggestion}'? [Y/n/add]: "
            ).strip().lower()
            if answer in ("", "y"):
                program_code = suggestion
            elif answer == "add":
                program_code = _add_new_program(program_code, programs)
            else:
                console.print("  Aborted.")
                return
        else:
            answer = input(
                f"  '{program_code}' is not in the known program list. Add it? [Y/n]: "
            ).strip().lower()
            if answer in ("", "y"):
                program_code = _add_new_program(program_code, programs)
            else:
                console.print("  Aborted.")
                return

    # ── Duplicate / re-upload check ───────────────────────────────────────────
    existing_ids = list_ids("students")
    if student_number in existing_ids:
        meta = load_json("students", student_number)
        answer = input(
            f"  Student {student_number} ({meta['name']}) already exists. "
            f"Replace documents? [y/N]: "
        ).strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return
        _replace_student_docs(student_number, files, meta, console)
        return

    # ── Parse, embed, check for similar ──────────────────────────────────────
    console.print("  Parsing and embedding...", end=" ", flush=True)
    text, vector = _parse_and_embed(files)
    console.print("✓")

    # ── Extract email ─────────────────────────────────────────────────────────
    email = _extract_email(text)
    if not email:
        email = input("  Email address not found in document. Enter manually: ").strip()

    # ── Extract name ──────────────────────────────────────────────────────────
    name = input(f"  Student full name: ").strip()

    # ── Hours available ───────────────────────────────────────────────────────
    hours_str = input("  Hours available: ").strip()
    try:
        hours_available = int(hours_str)
    except ValueError:
        hours_available = 135
        console.print(f"  Invalid input — defaulting to {hours_available}h.")

    semester = _prompt_semester(args)

    # ── Determine document type and save ─────────────────────────────────────
    doc_records = []
    for i, f in enumerate(files):
        dtype = "cv" if i == 0 else "cover_letter"
        doc_records += _save_documents("students", student_number, [f], dtype)

    emb_path = _save_emb("students", student_number, vector)

    meta = {
        "student_number":       student_number,
        "name":                 name,
        "email":                email,
        "program":              program_code,
        "semester_start":       semester,
        "hours_available":      hours_available,
        "status":               "active",
        "reassignment_history": [],
        "documents":            doc_records,
        "embedding_file":       emb_path,
        "notes":                "",
    }
    save_json("students", student_number, meta)
    audit_log("ingest", "students", student_number,
              files=[r["filename"] for r in doc_records])
    console.print(f"  ✓ Student {student_number} ({name}) ingested.")


def _replace_student_docs(student_number: str, files: list[Path], meta: dict, console) -> None:
    """Re-embed and replace documents for an existing student."""
    console.print("  Parsing and embedding...", end=" ", flush=True)
    text, vector = _parse_and_embed(files)
    console.print("✓")

    old_filenames = [d["filename"] for d in meta.get("documents", [])]

    doc_records = []
    for i, f in enumerate(files):
        dtype = "cv" if i == 0 else "cover_letter"
        # Pass old_filenames only on first iteration to avoid double-delete
        old = old_filenames if i == 0 else None
        doc_records += _save_documents("students", student_number, [f], dtype,
                                       old_filenames=old)

    emb_path = _save_emb("students", student_number, vector)
    meta["documents"]      = doc_records
    meta["embedding_file"] = emb_path
    save_json("students", student_number, meta)
    audit_log("replace", "students", student_number,
              old_files=old_filenames,
              new_files=[r["filename"] for r in doc_records])
    console.print(f"  ✓ Documents updated for {meta['name']}.")


def _add_new_program(code: str, programs: list[dict]) -> str:
    label_fr = input(f"  French label for '{code}': ").strip()
    label_en = input(f"  English label for '{code}': ").strip()
    programs.append({
        "code":     code,
        "label_fr": label_fr,
        "label_en": label_en,
        "active":   "true",
    })
    save_programs(programs)
    audit_log("add_program", "programs", code,
              label_fr=label_fr, label_en=label_en)
    print(f"  ✓ Program '{code}' added.")
    return code


# ── Company ingest ────────────────────────────────────────────────────────────

def _ingest_company(files: list[Path], args) -> None:
    from rich.console import Console
    console = Console()

    console.print("  Parsing and embedding...", end=" ", flush=True)
    text, vector = _parse_and_embed(files)
    console.print("✓")

    language = detect_language(text)

    # ── Duplicate detection ───────────────────────────────────────────────────
    similar = _find_similar("companies", vector)
    if similar:
        top_id, top_score = similar[0]
        top_meta = load_json("companies", top_id)
        console.print(
            f"\n  Similar company already exists: "
            f"[bold]{top_meta['name']}[/bold] (score: {top_score:.2f})"
        )
        answer = input("  Merge into existing, or keep as separate? [m/K]: ").strip().lower()
        if answer == "m":
            _merge_company(top_id, top_meta, files, vector, console)
            return

    # ── New company ───────────────────────────────────────────────────────────
    name         = input("  Company name: ").strip()
    company_id   = _slugify(name)
    contact_name  = input("  Contact name: ").strip()
    contact_email = input("  Contact email: ").strip()

    doc_records = _save_documents("companies", company_id, files, "company_description")
    emb_path    = _save_emb("companies", company_id, vector)

    meta = {
        "company_id":         company_id,
        "name":               name,
        "status":             "active",
        "language":           language,
        "contact_name":       contact_name,
        "contact_email":      contact_email,
        "activation_history": [{"semester": getattr(args, "semester", "") or "",
                                "activated_date": TODAY, "deactivated_date": None}],
        "documents":          doc_records,
        "embedding_file":     emb_path,
        "notes":              "",
    }
    save_json("companies", company_id, meta)
    audit_log("ingest", "companies", company_id,
              files=[r["filename"] for r in doc_records])
    console.print(f"  ✓ Company '{name}' ingested (id: {company_id}).")


def _merge_company(company_id: str, meta: dict, files: list[Path], vector, console) -> None:
    """Add new documents to an existing company and re-embed."""
    new_docs    = _save_documents("companies", company_id, files, "company_description")
    emb_path    = _save_emb("companies", company_id, vector)
    meta["documents"].extend(new_docs)
    meta["embedding_file"] = emb_path
    save_json("companies", company_id, meta)
    audit_log("replace", "companies", company_id,
              files=[r["filename"] for r in new_docs])
    console.print(f"  ✓ Merged into '{meta['name']}' — documents updated.")


# ── Project ingest ────────────────────────────────────────────────────────────

def _ingest_project(files: list[Path], args) -> None:
    from rich.console import Console
    console = Console()

    company_id = getattr(args, "company", None)
    if not company_id:
        company_id = input("  Company ID: ").strip()

    try:
        company_meta = load_json("companies", company_id)
    except FileNotFoundError:
        console.print(f"  [red]Company '{company_id}' not found. Ingest the company first.[/red]")
        return

    console.print("  Parsing and embedding...", end=" ", flush=True)
    text, vector = _parse_and_embed(files)
    console.print("✓")

    language = detect_language(text)

    # ── Duplicate detection within same company ───────────────────────────────
    company_projects = [
        pid for pid in list_ids("projects")
        if load_json("projects", pid).get("company_id") == company_id
    ]
    similar = [
        (pid, score)
        for pid, score in _find_similar("projects", vector)
        if pid in company_projects
    ]

    if similar:
        top_id, top_score = similar[0]
        top_meta = load_json("projects", top_id)
        console.print(
            f"\n  Similar project from {company_meta['name']} already exists:\n"
            f"  [bold]{top_meta['title']}[/bold] (score: {top_score:.2f})"
        )
        answer = input(
            "  Update/renew existing project, or keep as separate? [u/K]: "
        ).strip().lower()
        if answer == "u":
            _renew_project(top_id, top_meta, files, vector, args, console)
            return

    # ── Extract project metadata ──────────────────────────────────────────────
    title      = input("  Project title: ").strip()
    lead_name  = _extract_lead_name(text)
    lead_email = _extract_email(text)

    if not lead_email:
        lead_email = input("  Project lead email (not found in document): ").strip()
    else:
        confirm = input(f"  Project lead email detected: {lead_email}  Correct? [Y/n]: ").strip().lower()
        if confirm == "n":
            lead_email = input("  Enter correct email: ").strip()

    if not lead_name:
        lead_name = input("  Project lead name: ").strip()

    semester = _prompt_semester(args)

    # ── Task definition ───────────────────────────────────────────────────────
    tasks = _define_tasks(args, console)
    if tasks is None:
        console.print("  Aborted.")
        return

    total_hours = sum(t["hours"] for t in tasks)
    project_id  = _slugify(f"{company_id}_{title}_{semester}")

    # ── Coordinator assignment ────────────────────────────────────────────────
    # Temporarily save the project so coordinator.py can load its embedding
    doc_records = _save_documents("projects", project_id, files, "project_proposal")
    emb_path    = _save_emb("projects", project_id, vector)

    _temp_meta = {
        "project_id": project_id, "company_id": company_id,
        "title": title, "status": "active", "semester": semester,
        "language": language,
        "capacity": {"total_hours": total_hours, "tasks": tasks},
        "lead_name": lead_name, "lead_email": lead_email,
        "renewal_history": [], "documents": doc_records,
        "coordinators": [], "embedding_file": emb_path, "notes": "",
    }
    save_json("projects", project_id, _temp_meta)

    from src.coordinator import coordinator_setup_flow
    coordinator_ids = coordinator_setup_flow(project_id, console)

    meta = {**_temp_meta, "coordinators": coordinator_ids}
    save_json("projects", project_id, meta)
    audit_log("ingest", "projects", project_id,
              files=[r["filename"] for r in doc_records],
              company=company_id)
    console.print(f"  ✓ Project '{title}' ingested (id: {project_id}).")


def _renew_project(
    project_id: str, meta: dict, files: list[Path],
    vector, args, console
) -> None:
    """Replace documents and re-embed an existing project; log renewal history."""
    old_docs = [d["filename"] for d in meta.get("documents", [])]
    new_docs = _save_documents("projects", project_id, files, "project_proposal",
                               old_filenames=old_docs)
    emb_path = _save_emb("projects", project_id, vector)

    meta.setdefault("renewal_history", []).append({
        "date": TODAY,
        "previous_documents": old_docs,
    })
    meta["documents"]      = new_docs
    meta["embedding_file"] = emb_path
    meta["status"]         = "active"

    # Offer to redefine tasks
    answer = input("  Redefine tasks for the renewed project? [y/N]: ").strip().lower()
    if answer == "y":
        from rich.console import Console
        tasks = _define_tasks(args, Console())
        if tasks:
            meta["capacity"] = {
                "total_hours": sum(t["hours"] for t in tasks),
                "tasks":       tasks,
            }

    save_json("projects", project_id, meta)
    audit_log("replace", "projects", project_id,
              old_files=old_docs,
              new_files=[r["filename"] for r in new_docs])
    console.print(f"  ✓ Project '{meta['title']}' renewed.")


# ── Task definition ───────────────────────────────────────────────────────────

def _define_tasks(args, console) -> list[dict] | None:
    """
    Load tasks from a TOML sidecar if --tasks is given,
    otherwise prompt interactively. Returns list of task dicts or None on abort.
    """
    tasks_file = getattr(args, "tasks", None)
    if tasks_file:
        return _load_tasks_toml(tasks_file, console)
    return _prompt_tasks(console)


def _load_tasks_toml(path_str: str, console) -> list[dict] | None:
    """Parse a TOML task sidecar file."""
    path = Path(path_str)
    if not path.exists():
        console.print(f"  [red]Tasks file not found: {path}[/red]")
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        raw_tasks = data.get("task", [])
        if not raw_tasks:
            console.print("  [red]No [[task]] entries found in TOML file.[/red]")
            return None
        tasks = []
        for i, t in enumerate(raw_tasks, 1):
            if "title" not in t or "hours" not in t:
                console.print(f"  [red]Task {i} missing 'title' or 'hours'.[/red]")
                return None
            tasks.append({
                "task_id":     f"t{i}",
                "label":       t["title"],
                "hours":       int(t["hours"]),
                "description": t.get("description", ""),
            })
        _print_task_table(tasks, console)
        answer = input("  Confirm tasks? [Y/n]: ").strip().lower()
        return tasks if answer in ("", "y") else None
    except Exception as e:
        console.print(f"  [red]Failed to parse tasks file: {e}[/red]")
        return None


def _prompt_tasks(console) -> list[dict] | None:
    """Interactively prompt for task definitions."""
    from rich.table import Table
    from rich import box

    console.print("\n  Define project tasks (leave title blank to finish):\n")
    tasks = []
    i = 1
    while True:
        title = input(f"  Task {i} title: ").strip()
        if not title:
            if not tasks:
                console.print("  At least one task is required.")
                continue
            break
        hours_str = input(f"  Task {i} hours: ").strip()
        try:
            hours = int(hours_str)
        except ValueError:
            console.print("  Invalid hours — skipping task.")
            continue
        description = input(f"  Task {i} description (optional): ").strip()
        tasks.append({
            "task_id":     f"t{i}",
            "label":       title,
            "hours":       hours,
            "description": description,
        })
        i += 1

    _print_task_table(tasks, console)
    answer = input("\n  Confirm tasks? [Y/n]: ").strip().lower()
    return tasks if answer in ("", "y") else None


def _print_task_table(tasks: list[dict], console) -> None:
    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("ID",          style="dim",   width=4)
    table.add_column("Title",       style="white", min_width=28)
    table.add_column("Hours",       style="cyan",  justify="right")
    table.add_column("Description", style="dim",   min_width=30)

    total = 0
    for t in tasks:
        table.add_row(
            t["task_id"],
            t["label"],
            f"{t['hours']}h",
            t.get("description", ""),
        )
        total += t["hours"]

    table.add_section()
    table.add_row("", "[bold]TOTAL[/bold]", f"[bold]{total}h[/bold]", "")
    console.print(table)


def _ingest_coordinator(files: list[Path], args) -> None:
    from rich.console import Console
    console = Console()

    # Optional CV documents — embed if provided, skip embedding if not
    has_docs = bool(files) and all(f.exists() for f in files)

    if has_docs:
        console.print("  Parsing and embedding...", end=" ", flush=True)
        text, vector = _parse_and_embed(files)
        console.print("✓")
    else:
        text, vector = "", None

    # ── Gather metadata ───────────────────────────────────────────────────────
    name  = input("  Coordinator full name: ").strip()
    email = _extract_email(text) if has_docs else ""
    if not email:
        email = input("  Email address: ").strip()
    else:
        confirm = input(f"  Email detected: {email}  Correct? [Y/n]: ").strip().lower()
        if confirm == "n":
            email = input("  Enter correct email: ").strip()

    # Programs (optional)
    programs_raw = input(
        "  Associated programs (comma-separated codes, blank = all): "
    ).strip()
    programs = [p.strip().upper() for p in programs_raw.split(",") if p.strip()] \
               if programs_raw else []

    coordinator_id = email   # email is the unique key

    # ── Duplicate detection ───────────────────────────────────────────────────
    existing = list_ids("coordinators")
    if existing:
        # Exact email match → always update
        if email in existing:
            answer = input(
                f"  Coordinator with email {email} already exists. "
                f"Update? [Y/n]: "
            ).strip().lower()
            if answer not in ("", "y"):
                console.print("  Aborted.")
                return
            _update_coordinator(email, files, vector, has_docs, programs, console)
            return

        # Fuzzy name match → offer merge
        from src.fuzzy import ranked_matches
        existing_names = {
            cid: load_json("coordinators", cid)["name"]
            for cid in existing
        }
        matches = ranked_matches(name, list(existing_names.values()), limit=3)
        if matches and matches[0][1] >= 85:
            top_name = matches[0][0]
            top_id   = next(k for k, v in existing_names.items() if v == top_name)
            answer = input(
                f"  Similar coordinator already exists: {top_name} ({top_id}). "
                f"Update or keep separate? [u/K]: "
            ).strip().lower()
            if answer == "u":
                _update_coordinator(top_id, files, vector, has_docs, programs, console)
                return

    # ── Save ──────────────────────────────────────────────────────────────────
    doc_records = _save_documents("coordinators", coordinator_id, files, "cv") \
                  if has_docs else []
    emb_path    = _save_emb("coordinators", coordinator_id, vector) \
                  if has_docs and vector is not None else ""

    meta = {
        "coordinator_id": coordinator_id,
        "name":           name,
        "email":          email,
        "programs":       programs,
        "status":         "active",
        "documents":      doc_records,
        "embedding_file": emb_path,
        "notes":          "",
    }
    save_json("coordinators", coordinator_id, meta)
    audit_log("add_coordinator", "coordinators", coordinator_id,
              name=name,
              files=[r["filename"] for r in doc_records])
    console.print(f"  ✓ Coordinator '{name}' ingested (id: {coordinator_id}).")


def _update_coordinator(
    coordinator_id: str, files: list[Path], vector,
    has_docs: bool, programs: list[str], console,
) -> None:
    meta = load_json("coordinators", coordinator_id)
    if has_docs and vector is not None:
        doc_records          = _save_documents("coordinators", coordinator_id, files, "cv")
        emb_path             = _save_emb("coordinators", coordinator_id, vector)
        meta["documents"]    = doc_records
        meta["embedding_file"] = emb_path
    if programs:
        meta["programs"] = programs
    save_json("coordinators", coordinator_id, meta)
    audit_log("replace", "coordinators", coordinator_id,
              files=[r["filename"] for r in (meta.get("documents") or [])])
    console.print(f"  ✓ Coordinator '{meta['name']}' updated.")


# ── Utility ───────────────────────────────────────────────────────────────────

def _prompt_semester(args) -> str:
    """Prompt for a semester, normalise it, return canonical storage string."""
    from src.semester import prompt
    return prompt(args).to_storage()

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text


def _extract_email(text: str) -> str:
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def _extract_lead_name(text: str) -> str:
    """
    Heuristic: look for a name near 'responsable', 'lead', 'contact', or
    'chargé de projet' within the first 3000 characters.
    Returns empty string if nothing found — caller will prompt.
    """
    patterns = [
        r"(?:responsable|lead|contact|charg[ée] de projet)[^\n:]*:\s*([A-ZÀÂÄÉÈÊËÏÎÔÙÛÜ][a-zàâäéèêëïîôùûü]+(?:\s+[A-ZÀÂÄÉÈÊËÏÎÔÙÛÜ][a-zàâäéèêëïîôùûü]+)+)",
    ]
    snippet = text[:3000]
    for pattern in patterns:
        match = re.search(pattern, snippet, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""
