"""
bootstrap.py — ensure the full data folder structure and seed files exist.
Called at startup from main.py before any command is dispatched.
Idempotent: safe to run on every invocation.
"""
import csv
from pathlib import Path


DIRS = [
    "data/students",
    "data/companies",
    "data/projects",
    "data/coordinators",
    "data/documents/students",
    "data/documents/companies",
    "data/documents/projects",
    "data/documents/coordinators",
    "data/embeddings/students",
    "data/embeddings/companies",
    "data/embeddings/projects",
    "data/embeddings/coordinators",
    "man",
    "tests",
]

ASSIGNMENTS_HEADER = [
    "assignment_id", "student_number", "student_email", "student_program",
    "project_id", "project_lead_email", "semester", "task_id", "task_label",
    "hours_planned", "hours_committed", "status", "assigned_date",
    "confirmed_date", "completed_date", "notes",
]

PROGRAMS_HEADER = ["code", "label_fr", "label_en", "active"]

SCHEMA_COORDINATOR = {
    "_comment": "Reference schema — one file per coordinator: data/coordinators/<email>.json",
    "coordinator_id": "prenom.nom@college-lasalle.qc.ca",
    "name": "Prénom Nom",
    "email": "prenom.nom@college-lasalle.qc.ca",
    "programs": [],
    "status": "active",
    "documents": [],
    "embedding_file": "",
    "notes": ""
}


SCHEMA_STUDENT = {
    "_comment": "Reference schema — one file per student: data/students/<student_number>.json",
    "student_number": "2134567",
    "name": "Prénom Nom",
    "email": "prenom.nom@college-lasalle.qc.ca",
    "program": "GDIM",
    "semester_start": "Fall 2025",
    "hours_available": 135,
    "status": "active",
    "reassignment_history": [],
    "documents": [],
    "embedding_file": "",
    "notes": ""
}

SCHEMA_COMPANY = {
    "_comment": "Reference schema — one file per company: data/companies/<company_id>.json",
    "company_id": "nom_entreprise",
    "name": "Nom de l'entreprise",
    "status": "active",
    "language": "fr",
    "contact_name": "Prénom Nom",
    "contact_email": "contact@entreprise.ca",
    "activation_history": [],
    "documents": [],
    "embedding_file": "",
    "notes": ""
}

SCHEMA_PROJECT = {
    "_comment": "Reference schema — one file per project: data/projects/<project_id>.json",
    "project_id": "entreprise_titre_2025H",
    "company_id": "nom_entreprise",
    "title": "Titre du projet",
    "status": "active",
    "semester": "Fall 2025",
    "language": "fr",
    "capacity": {
        "total_hours": 0,
        "tasks": [
            {"task_id": "t1", "label": "Tâche exemple", "hours": 0}
        ]
    },
    "lead_name": "Prénom Nom",
    "lead_email": "lead@entreprise.ca",
    "renewal_history": [],
    "documents": [],
    "embedding_file": "",
    "notes": ""
}


def bootstrap(verbose: bool = False) -> None:
    """Create missing directories and seed files. Never overwrites existing data."""

    def log(msg: str) -> None:
        if verbose:
            print(f"  {msg}")

    # ── Directories ───────────────────────────────────────────────────────────
    for d in DIRS:
        path = Path(d)
        if not path.exists():
            path.mkdir(parents=True)
            log(f"created  {d}/")

    # ── assignments.csv ───────────────────────────────────────────────────────
    assignments = Path("data/assignments.csv")
    if not assignments.exists() or assignments.stat().st_size == 0:
        with open(assignments, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=ASSIGNMENTS_HEADER).writeheader()
        log("created  data/assignments.csv")

    # ── programs.csv ─────────────────────────────────────────────────────────
    programs = Path("data/programs.csv")
    if not programs.exists():
        with open(programs, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=PROGRAMS_HEADER).writeheader()
        log("created  data/programs.csv")

    # ── audit.log ─────────────────────────────────────────────────────────────
    audit = Path("data/audit.log")
    if not audit.exists():
        audit.touch()
        log("created  data/audit.log")
    import json
    schemas = {
        "data/students/SCHEMA.json":     SCHEMA_STUDENT,
        "data/companies/SCHEMA.json":    SCHEMA_COMPANY,
        "data/projects/SCHEMA.json":     SCHEMA_PROJECT,
        "data/coordinators/SCHEMA.json": SCHEMA_COORDINATOR,
    }
    for path_str, schema in schemas.items():
        path = Path(path_str)
        if not path.exists():
            path.write_text(json.dumps(schema, indent=2, ensure_ascii=False))
            log(f"created  {path_str}")



def run_reset(args) -> None:
    """
    Reset data files so you can start a fresh import.

    Without --hard:  clears JSON entities, assignments.csv, and audit.log.
                     Documents and embeddings are preserved.
    With --hard:     also deletes documents and embeddings.

    Reminder of what to recreate:
      innovhub import --dir raw/ --semester "Winter 2026"
    """
    import shutil
    from rich.console import Console
    console = Console()
    hard = getattr(args, "hard", False)

    console.print("\n  [bold red]◈ Innovation Hub — Reset[/bold red]\n")
    if hard:
        console.print("  This will delete ALL data including documents and embeddings.")
    else:
        console.print("  This will delete entity JSON files, assignments.csv, and audit.log.")
        console.print("  Documents and embeddings will be preserved.")
    console.print()

    confirm = input("  Type YES to confirm: ").strip()
    if confirm != "YES":
        console.print("  Aborted.")
        return

    removed = 0

    # JSON entities
    for kind in ("students", "companies", "projects", "coordinators"):
        folder = Path(f"data/{kind}")
        for f in folder.glob("*.json"):
            if f.stem == "SCHEMA":
                continue
            f.unlink()
            removed += 1

    # CSV and log
    for fname in ("data/assignments.csv", "data/audit.log"):
        p = Path(fname)
        if p.exists():
            p.unlink()
            removed += 1

    if hard:
        # Documents
        doc_root = Path("data/documents")
        for f in doc_root.rglob("*"):
            if f.is_file():
                f.unlink()
                removed += 1
        # Embeddings
        emb_root = Path("data/embeddings")
        for f in emb_root.rglob("*.npy"):
            f.unlink()
            removed += 1

    # Recreate empty CSV and log
    Path("data/assignments.csv").touch()
    Path("data/audit.log").touch()

    console.print(f"\n  [green]✓[/green]  {removed} file(s) removed.")
    console.print(
        "  [dim]To reimport: [bold]innovhub import --dir raw/ --semester \"Winter 2026\"[/bold][/dim]\n"
    )
