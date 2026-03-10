"""
lifecycle.py — activate, deactivate, close, complete, and reassign entities.
Handles cascading cancellation of active assignments where needed.
"""
from datetime import date
from pathlib import Path

from src.store import (
    load_json, save_json, list_ids,
    load_assignments, rewrite_assignments,
)
from src.models import PROJECT_STATUSES, STUDENT_STATUSES, COMPANY_STATUSES
from src.audit import log as audit_log


TODAY = date.today().isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_assignments_for(entity_id: str, rows: list[dict]) -> list[dict]:
    """Return assignment rows in active states for a student or project."""
    active = {"proposed", "confirmed"}
    return [
        r for r in rows
        if r["status"] in active
        and (r["student_number"] == entity_id or r["project_id"] == entity_id)
    ]


def _cancel_rows(rows: list[dict], ids_to_cancel: set[str]) -> list[dict]:
    """Mark matching assignment_ids as cancelled; return full updated list."""
    for r in rows:
        if r["assignment_id"] in ids_to_cancel:
            r["status"] = "cancelled"
    return rows


def _confirm_cascade(label: str, affected: list[dict]) -> bool:
    """Print affected assignments and ask for confirmation. Returns True to proceed."""
    from rich.console import Console
    console = Console()
    console.print(f"\n  [yellow]{label} has {len(affected)} active assignment(s):[/yellow]")
    for r in affected:
        console.print(
            f"    [dim]─[/dim] {r['project_id']}  task {r['task_id']}"
            f"  [{r['status']}]"
        )
    answer = input("\n  This will cancel these assignments. Proceed? [y/N]: ").strip().lower()
    return answer == "y"


def _purge_documents(kind: str, entity_id: str) -> int:
    """Delete source documents and embedding for an entity. Returns file count removed."""
    from src.store import PATHS
    removed = 0

    meta = load_json(kind, entity_id)

    # Source documents
    doc_dir = Path(PATHS["documents"]) / kind
    for doc in meta.get("documents", []):
        fpath = doc_dir / doc["filename"]
        if fpath.exists():
            fpath.unlink()
            removed += 1

    # Embedding
    emb = meta.get("embedding_file", "")
    if emb:
        ep = Path(emb)
        if ep.exists():
            ep.unlink()
            removed += 1

    # Clear references in metadata
    meta["documents"] = []
    meta["embedding_file"] = ""
    save_json(kind, entity_id, meta)

    return removed


# ── activate / deactivate ─────────────────────────────────────────────────────

def run(args):
    """Entry point for both activate and deactivate commands."""
    cmd = args.command  # "activate" or "deactivate"

    if args.student:
        _set_student_status(args.student, "active" if cmd == "activate" else "inactive")
    elif args.project:
        _set_project_status(args.project, "active" if cmd == "activate" else "inactive")
    elif args.company:
        _set_company_status(args.company, "active" if cmd == "activate" else "inactive", args)


def _set_student_status(student_number: str, new_status: str) -> None:
    from rich.console import Console
    console = Console()

    meta       = load_json("students", student_number)
    old_status = meta["status"]

    if new_status == "active" and old_status == "completed":
        console.print(
            f"  [yellow]{meta['name']} is marked completed — "
            f"documents have been purged. Re-ingest documents before activating.[/yellow]"
        )
        return

    if new_status == "inactive" and old_status == "active":
        rows = load_assignments()
        affected = _active_assignments_for(student_number, rows)
        if affected and not _confirm_cascade(meta["name"], affected):
            console.print("  Aborted.")
            return
        if affected:
            ids = {r["assignment_id"] for r in affected}
            rewrite_assignments(_cancel_rows(rows, ids))
            console.print(f"  ✓ {len(ids)} assignment(s) cancelled")

    meta["status"] = new_status
    save_json("students", student_number, meta)
    audit_log(new_status, "students", student_number)
    console.print(f"  ✓ {meta['name']} → [bold]{new_status}[/bold]")


def _set_project_status(project_id: str, new_status: str) -> None:
    from rich.console import Console
    console = Console()

    meta = load_json("projects", project_id)
    old_status = meta["status"]

    if new_status == "inactive" and old_status == "active":
        rows = load_assignments()
        affected = _active_assignments_for(project_id, rows)
        if affected and not _confirm_cascade(meta["title"], affected):
            console.print("  Aborted.")
            return
        if affected:
            ids = {r["assignment_id"] for r in affected}
            rewrite_assignments(_cancel_rows(rows, ids))
            console.print(f"  ✓ {len(ids)} assignment(s) cancelled")

    meta["status"] = new_status
    save_json("projects", project_id, meta)
    audit_log(new_status, "projects", project_id)
    console.print(f"  ✓ {meta['title']} → [bold]{new_status}[/bold]")


def _set_company_status(company_name: str, new_status: str, args=None) -> None:
    from rich.console import Console
    from src.fuzzy import ranked_matches
    console = Console()

    # Resolve company by fuzzy name
    all_ids = list_ids("companies")
    names   = {cid: load_json("companies", cid)["name"] for cid in all_ids}
    matches = ranked_matches(company_name, list(names.values()), limit=3)

    if not matches or matches[0][1] < 60:
        console.print(f"  [red]No company found matching '{company_name}'[/red]")
        return

    if matches[0][1] < 90 or len([m for m in matches if m[1] > 60]) > 1:
        console.print("\n  Matching companies:")
        for i, (name, score) in enumerate(matches, 1):
            console.print(f"    {i}  {name}  ({score:.0f}%)")
        choice = input("  Which company? Enter number: ").strip()
        try:
            selected_name = matches[int(choice) - 1][0]
        except (ValueError, IndexError):
            console.print("  Aborted.")
            return
    else:
        selected_name = matches[0][0]

    company_id = next(cid for cid, n in names.items() if n == selected_name)
    meta = load_json("companies", company_id)

    if new_status == "inactive" and meta["status"] == "active":
        # Find all active projects for this company and their assignments
        project_ids = [
            pid for pid in list_ids("projects")
            if load_json("projects", pid).get("company_id") == company_id
            and load_json("projects", pid).get("status") == "active"
        ]
        rows = load_assignments()
        affected = [
            r for pid in project_ids
            for r in _active_assignments_for(pid, rows)
        ]
        if affected and not _confirm_cascade(meta["name"], affected):
            console.print("  Aborted.")
            return
        if affected:
            ids = {r["assignment_id"] for r in affected}
            rewrite_assignments(_cancel_rows(rows, ids))
            console.print(f"  ✓ {len(ids)} assignment(s) cancelled")
        # Deactivate all active projects
        for pid in project_ids:
            pmeta = load_json("projects", pid)
            pmeta["status"] = "inactive"
            save_json("projects", pid, pmeta)
        console.print(f"  ✓ {len(project_ids)} project(s) set to inactive")

    # Log activation history
    history = meta.get("activation_history", [])
    if new_status == "active":
        history.append({
            "semester":         getattr(args, "semester", "") or "",
            "activated_date":   TODAY,
            "deactivated_date": None,
        })
    elif new_status == "inactive" and history:
        history[-1]["deactivated_date"] = TODAY

    meta["activation_history"] = history
    meta["status"] = new_status
    save_json("companies", company_id, meta)
    audit_log(new_status, "companies", company_id)
    console.print(f"  ✓ {meta['name']} → [bold]{new_status}[/bold]")


# ── close project ─────────────────────────────────────────────────────────────

def run_close(args):
    """
    close-project — mark a project as closed, purge its documents.
    Closed projects are excluded from matching. CSV history is preserved.
    """
    from rich.console import Console
    console = Console()

    meta = load_json("projects", args.project)

    if meta["status"] == "closed":
        console.print(f"  [yellow]{meta['title']} is already closed.[/yellow]")
        return

    rows     = load_assignments()
    affected = _active_assignments_for(args.project, rows)

    console.print(f"\n  [bold]{meta['title']}[/bold] ({meta['semester']})")

    if affected:
        if not _confirm_cascade(meta["title"], affected):
            console.print("  Aborted.")
            return
        ids = {r["assignment_id"] for r in affected}
        rewrite_assignments(_cancel_rows(rows, ids))
        console.print(f"  ✓ {len(ids)} assignment(s) cancelled")

    removed = _purge_documents("projects", args.project)

    meta["status"] = "closed"
    save_json("projects", args.project, meta)

    audit_log("close", "projects", args.project,
              files_purged=removed)
    console.print(f"  ✓ {removed} document(s) purged")
    console.print(f"  ✓ {meta['title']} → [bold]closed[/bold]")
    console.print(f"  [dim]Assignment history retained in CSV.[/dim]")


# ── complete student ──────────────────────────────────────────────────────────

def run_complete(args):
    """
    complete — mark student as completed, purge their documents.
    CSV assignment history is preserved.
    """
    from rich.console import Console
    console = Console()

    meta     = load_json("students", args.student_number)
    rows     = load_assignments()
    affected = _active_assignments_for(args.student_number, rows)

    console.print(f"\n  [bold]{meta['name']}[/bold] — {meta['program']}, {meta['semester_start']}")

    active_count = len([
        r for r in rows
        if r["student_number"] == args.student_number
        and r["status"] in {"proposed", "confirmed", "completed"}
    ])
    console.print(f"  {active_count} assignment(s) on record.")

    if affected:
        if not _confirm_cascade(meta["name"], affected):
            console.print("  Aborted.")
            return
        ids = {r["assignment_id"] for r in affected}
        rewrite_assignments(_cancel_rows(rows, ids))
        console.print(f"  ✓ {len(ids)} open assignment(s) cancelled")

    answer = input(
        "\n  This will purge all documents and embeddings for this student.\n"
        "  Their assignment history will be retained. Proceed? [y/N]: "
    ).strip().lower()
    if answer != "y":
        console.print("  Aborted.")
        return

    removed = _purge_documents("students", args.student_number)
    meta["status"] = "completed"
    save_json("students", args.student_number, meta)

    audit_log("complete", "students", args.student_number,
              files_purged=removed)
    console.print(f"  ✓ {removed} document(s) and embedding purged")
    console.print(f"  ✓ {meta['name']} → [bold]completed[/bold]")
    console.print(f"  [dim]Assignment history retained in CSV.[/dim]")


# ── reassign student ──────────────────────────────────────────────────────────

def run_reassign(args):
    """
    reassign — move a student to a different semester.
    Asks whether this is an extension (keep assignments) or a reset (cancel them).
    """
    from rich.console import Console
    console = Console()

    meta        = load_json("students", args.student_number)
    old_semester = meta["semester_start"]
    from src.semester import parse as parse_semester, prompt as prompt_semester
    import types
    sem_obj = parse_semester(args.semester) or prompt_semester(
        types.SimpleNamespace(semester=args.semester)
    )
    new_semester = sem_obj.to_storage()

    if old_semester == new_semester:
        console.print(f"  [yellow]{meta['name']} is already in {new_semester}.[/yellow]")
        return

    rows     = load_assignments()
    active   = _active_assignments_for(args.student_number, rows)

    console.print(f"\n  [bold]{meta['name']}[/bold]  {old_semester} → {new_semester}")

    if active:
        console.print(f"\n  This student has {len(active)} active assignment(s):")
        for r in active:
            console.print(
                f"    [dim]─[/dim] {r['project_id']}  task {r['task_id']}"
                f"  {r['hours_planned']}h  [{r['status']}]"
            )
        console.print(
            "\n  [bold]Extension[/bold] — keep current assignments and move to new semester."
            "\n  [bold]Reset[/bold]     — cancel all current assignments and start fresh."
        )
        answer = input("\n  Extension or reset? [e/r]: ").strip().lower()
        if answer not in {"e", "r"}:
            console.print("  Aborted.")
            return
        if answer == "r":
            ids = {r["assignment_id"] for r in active}
            rewrite_assignments(_cancel_rows(rows, ids))
            console.print(f"  ✓ {len(ids)} assignment(s) cancelled")
            kind = "reset"
        else:
            kind = "extension"
    else:
        kind = "reassignment"

    meta.setdefault("reassignment_history", []).append({
        "from_semester": old_semester,
        "to_semester":   new_semester,
        "date":          TODAY,
        "kind":          kind,
    })
    meta["semester_start"] = new_semester
    save_json("students", args.student_number, meta)

    audit_log("reassign", "students", args.student_number,
              from_semester=old_semester,
              to_semester=new_semester,
              kind=kind)
    console.print(f"  ✓ {meta['name']} → [bold]{new_semester}[/bold]  ({kind})")
