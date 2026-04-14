"""
assign.py — create, confirm, edit, and cancel assignment rows.
All mutations go through store.rewrite_assignments() to keep the CSV consistent.
"""
import uuid
from datetime import date
from rich.console import Console
from rich.table import Table
from rich import box

from src.store import (
    load_json, save_json, load_assignments, rewrite_assignments,
    append_assignment_rows,
)
from src.email_template import render_email
from src.audit import log as audit_log

TODAY = date.today().isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_rows_for_student(student_number: str, rows: list[dict]) -> list[dict]:
    return [
        r for r in rows
        if r["student_number"] == student_number
        and r["status"] in {"proposed", "confirmed"}
    ]


def _hours_committed(student_number: str, rows: list[dict]) -> int:
    return sum(
        int(r.get("hours_planned", 0))
        for r in rows
        if r["student_number"] == student_number
        and r["status"] in {"proposed", "confirmed"}
    )


def _select_team(
    project_id: str,
    n_teams: int,
    rows: list[dict],
    console: Console,
) -> str | None:
    """
    For a single-team project, return "".
    For a multi-team project, show current team fill and ask which team.
    Returns the chosen team label ("A", "B", …) or None to abort.
    """
    if n_teams <= 1:
        return ""

    letters = [chr(ord("A") + i) for i in range(n_teams)]

    # Count assigned students per team
    team_counts: dict[str, int] = {L: 0 for L in letters}
    for r in rows:
        if r["project_id"] == project_id and r["status"] in {"proposed", "confirmed"}:
            t = r.get("team", "")
            if t in team_counts:
                team_counts[t] += 1

    console.print(f"\n  [bold]{n_teams} competing teams[/bold]  — current members:")
    for L in letters:
        console.print(f"    {L}  {team_counts[L]} student(s)")

    raw = input(
        f"  Assign to which team? [{'/'.join(letters)}] "
        f"(or 'new' to open a new team, blank to abort): "
    ).strip().upper()

    if not raw:
        console.print("  Aborted.")
        return None

    if raw == "NEW":
        # Open the next unused letter
        next_letter = chr(ord("A") + n_teams)
        console.print(f"  Opening team {next_letter}.")
        return next_letter

    if raw in letters:
        return raw

    console.print(f"  [red]'{raw}' is not a valid team label.[/red]")
    return None


def _print_email_draft(draft: dict, console: Console) -> None:
    lang = draft["language"].upper()
    console.print(f"\n  [dim]── Email draft ({lang}) ────────────────────────────────[/dim]")
    for addr in draft["to"]:
        console.print(f"  [bold]TO:[/bold]  {addr}")
    for addr in draft.get("cc", []):
        console.print(f"  [bold]CC:[/bold]  {addr}")
    console.print(f"  [bold]Subject:[/bold]  {draft['subject']}\n")
    for line in draft["body"].splitlines():
        console.print(f"  {line}")
    console.print(f"  [dim]──────────────────────────────────────────────────────[/dim]\n")


def _print_task_selection(tasks: list[dict], filled: dict[str, int], console: Console) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("#",         style="dim",   width=3)
    table.add_column("Task",      style="white", min_width=28)
    table.add_column("Total",     style="dim",   justify="right", width=7)
    table.add_column("Filled",    style="cyan",  justify="right", width=7)
    table.add_column("Remaining", style="green", justify="right", width=10)

    for i, t in enumerate(tasks, 1):
        tid       = t["task_id"]
        f         = filled.get(tid, 0)
        remaining = t["hours"] - f
        rem_style = "green" if remaining > 0 else "dim"
        table.add_row(
            str(i),
            t["label"],
            f"{t['hours']}h",
            f"{f}h",
            f"[{rem_style}]{remaining}h[/{rem_style}]",
        )
    console.print(table)


# ── run_assign ────────────────────────────────────────────────────────────────

def run_assign(args) -> None:
    console = Console()

    student_number = args.student_number
    project_id     = args.project_id
    semester       = args.semester

    from src.semester import parse as parse_semester, prompt as prompt_semester
    sem_obj = parse_semester(semester)
    if not sem_obj:
        print(
            f"  Could not parse semester '{semester}'. "
            f"Please re-enter (e.g. Fall 2024, H2025)."
        )
        import types
        sem_obj = prompt_semester(types.SimpleNamespace(semester=None))
    semester = sem_obj.to_storage()

    # ── Load entities ─────────────────────────────────────────────────────────
    try:
        student_meta = load_json("students", student_number)
    except FileNotFoundError:
        console.print(f"  [red]Student '{student_number}' not found.[/red]")
        return

    try:
        project_meta = load_json("projects", project_id)
    except FileNotFoundError:
        console.print(f"  [red]Project '{project_id}' not found.[/red]")
        return

    try:
        company_meta = load_json("companies", project_meta["company_id"])
    except FileNotFoundError:
        company_meta = {"name": project_meta["company_id"]}

    if student_meta.get("status") != "active":
        console.print(f"  [yellow]Student {student_number} is {student_meta['status']} — cannot assign.[/yellow]")
        return

    if project_meta.get("status") != "active":
        console.print(f"  [yellow]Project '{project_id}' is {project_meta['status']} — cannot assign.[/yellow]")
        return

    rows            = load_assignments()
    hours_committed = _hours_committed(student_number, rows)
    hours_available = int(student_meta.get("hours_available", 0))
    hours_remaining = hours_available - hours_committed

    # ── Team selection ────────────────────────────────────────────────────────
    n_teams = int(project_meta.get("teams", 1))
    team    = _select_team(project_id, n_teams, rows, console)
    if team is None:
        return

    # Check not already assigned to this project+team
    already = [
        r for r in rows
        if r["student_number"] == student_number
        and r["project_id"] == project_id
        and r.get("team", "") == team
        and r["status"] in {"proposed", "confirmed"}
    ]
    if already:
        team_label = f" team {team}" if team else ""
        console.print(
            f"  [yellow]{student_meta['name']} is already assigned to "
            f"'{project_meta['title']}'{team_label} ({already[0]['status']}).[/yellow]"
        )
        return

    team_label = f"  [dim]Team {team}[/dim]" if team else ""
    console.print(
        f"\n  [bold]{student_meta['name']}[/bold]"
        f"  {student_meta['program']}  ·  {semester}"
        f"  ·  [green]{hours_remaining}h available[/green]"
    )
    console.print(
        f"  → [bold]{project_meta['title']}[/bold]"
        f"  ({company_meta['name']}){team_label}\n"
    )

    # ── Task fill state (per team) ────────────────────────────────────────────
    tasks  = project_meta["capacity"]["tasks"]
    filled: dict[str, int] = {}
    for r in rows:
        if (r["project_id"] == project_id
                and r.get("team", "") == team
                and r["status"] in {"proposed", "confirmed"}):
            tid = r["task_id"]
            filled[tid] = filled.get(tid, 0) + int(r.get("hours_planned", 0))

    _print_task_selection(tasks, filled, console)

    # ── Task selection ────────────────────────────────────────────────────────
    raw = input(
        '  Assign to which tasks? (comma-separated numbers, or "all"): '
    ).strip().lower()

    if raw == "all":
        selected_indices = list(range(len(tasks)))
    else:
        selected_indices = []
        for token in raw.split(","):
            token = token.strip()
            try:
                idx = int(token) - 1
                if 0 <= idx < len(tasks):
                    selected_indices.append(idx)
            except ValueError:
                pass

    if not selected_indices:
        console.print("  No valid tasks selected. Aborted.")
        return

    # ── Hour fine-tuning ──────────────────────────────────────────────────────
    task_assignments: list[tuple[dict, int]] = []  # (task, hours)
    for idx in selected_indices:
        task = tasks[idx]
        remaining_on_task = task["hours"] - filled.get(task["task_id"], 0)
        default_hours     = min(remaining_on_task, hours_remaining)
        raw_hours = input(
            f"  Hours for '{task['label']}' [{default_hours}]: "
        ).strip()
        try:
            hours = int(raw_hours) if raw_hours else default_hours
        except ValueError:
            hours = default_hours
        task_assignments.append((task, hours))

    total_committed = sum(h for _, h in task_assignments)

    # ── Over-commitment check ─────────────────────────────────────────────────
    if total_committed > hours_remaining:
        console.print(
            f"\n  [yellow]Warning: this assignment commits {total_committed}h "
            f"but the student only has {hours_remaining}h remaining.[/yellow]"
        )
        answer = input("  Proceed anyway? [y/N]: ").strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    summary = Table(box=box.SIMPLE_HEAD, show_header=False)
    summary.add_column("Task",  style="white", min_width=28)
    summary.add_column("Hours", style="cyan",  justify="right")
    for task, hours in task_assignments:
        summary.add_row(task["label"], f"{hours}h")
    summary.add_section()
    summary.add_row(
        "[bold]Total committed[/bold]",
        f"[bold]{total_committed}h[/bold]",
    )
    summary.add_row(
        "[dim]Student hours remaining after[/dim]",
        f"[dim]{hours_remaining - total_committed}h[/dim]",
    )
    console.print(summary)
    console.print()

    answer = input("  Confirm? [Y/n]: ").strip().lower()
    if answer not in ("", "y"):
        console.print("  Aborted.")
        return

    # ── Build CSV rows ────────────────────────────────────────────────────────
    assignment_id = str(uuid.uuid4())[:8]
    new_rows = []
    for task, hours in task_assignments:
        new_rows.append({
            "assignment_id":      assignment_id,
            "student_number":     student_number,
            "student_email":      student_meta.get("email", ""),
            "student_program":    student_meta.get("program", ""),
            "project_id":         project_id,
            "project_lead_email": project_meta.get("lead_email", ""),
            "semester":           semester,
            "team":               team,
            "task_id":            task["task_id"],
            "task_label":         task["label"],
            "hours_planned":      hours,
            "hours_committed":    hours,
            "status":             "proposed",
            "assigned_date":      TODAY,
            "confirmed_date":     "",
            "completed_date":     "",
            "notes":              "",
        })

    append_assignment_rows(new_rows)
    audit_log("assign", "students", student_number,
              assignment_id=assignment_id,
              project=project_id,
              semester=semester,
              tasks=[{"task_id": t["task_id"], "label": t["label"], "hours": h}
                     for t, h in task_assignments])

    console.print(
        f"  [bold green]✓[/bold green]"
        f"  Assignment created  [dim](id: {assignment_id})[/dim]"
        f"  ·  status: [yellow]proposed[/yellow]"
    )

    # ── Email draft ───────────────────────────────────────────────────────────
    # Collect coordinator emails from the project
    coordinator_emails = []
    for coord_email in project_meta.get("coordinators", []):
        try:
            from src.store import load_json as _lj
            coord_meta = _lj("coordinators", coord_email)
            coordinator_emails.append(coord_meta.get("email", coord_email))
        except Exception:
            coordinator_emails.append(coord_email)

    draft = render_email(
        language           = project_meta.get("language", "fr"),
        student_name       = student_meta.get("name", ""),
        student_email      = student_meta.get("email", ""),
        lead_name          = project_meta.get("lead_name", ""),
        lead_email         = project_meta.get("lead_email", ""),
        project_title      = project_meta.get("title", ""),
        company_name       = company_meta.get("name", ""),
        semester           = semester,
        coordinator_emails = coordinator_emails,
        linkedin_url       = student_meta.get("linkedin_url", ""),
        portfolio_urls     = student_meta.get("portfolio_urls", []),
    )
    _print_email_draft(draft, console)

    # ── Loop if student still has hours ───────────────────────────────────────
    new_remaining = hours_remaining - total_committed
    if new_remaining > 0:
        console.print(
            f"  [green]{student_meta['name']} still has {new_remaining}h available.[/green]"
        )


# ── run_confirm ───────────────────────────────────────────────────────────────

def run_confirm(args) -> None:
    console = Console()

    student_number = args.student_number
    project_id     = getattr(args, "project", None)

    try:
        student_meta = load_json("students", student_number)
    except FileNotFoundError:
        console.print(f"  [red]Student '{student_number}' not found.[/red]")
        return

    rows     = load_assignments()
    proposed = [
        r for r in rows
        if r["student_number"] == student_number
        and r["status"] == "proposed"
        and (not project_id or r["project_id"] == project_id)
    ]

    if not proposed:
        console.print(
            f"  [yellow]No proposed assignments found for {student_meta['name']}"
            + (f" on project '{project_id}'" if project_id else "") + ".[/yellow]"
        )
        return

    # Group by assignment_id
    by_assignment: dict[str, list[dict]] = {}
    for r in proposed:
        by_assignment.setdefault(r["assignment_id"], []).append(r)

    # Auto-resolve if only one assignment
    if len(by_assignment) == 1:
        aid     = list(by_assignment.keys())[0]
        a_rows  = by_assignment[aid]
        pid     = a_rows[0]["project_id"]
        try:
            project_meta = load_json("projects", pid)
            title = project_meta["title"]
        except Exception:
            title = pid
        console.print(
            f"\n  Assignment [dim]{aid}[/dim]"
            f" — [bold]{title}[/bold] / {student_meta['name']}"
            f"  ·  status: proposed → confirmed"
        )
    else:
        # Multiple — let the user pick
        console.print(f"\n  Multiple proposed assignments for {student_meta['name']}:")
        aids = list(by_assignment.keys())
        for i, aid in enumerate(aids, 1):
            r   = by_assignment[aid][0]
            try:
                title = load_json("projects", r["project_id"])["title"]
            except Exception:
                title = r["project_id"]
            total_h = sum(int(x["hours_planned"]) for x in by_assignment[aid])
            console.print(f"    {i}  {title}  {total_h}h  [dim]{aid}[/dim]")
        choice = input("  Which assignment? Enter number: ").strip()
        try:
            aid = aids[int(choice) - 1]
        except (ValueError, IndexError):
            console.print("  Aborted.")
            return
        a_rows = by_assignment[aid]

    answer = input("  Confirm? [Y/n]: ").strip().lower()
    if answer not in ("", "y"):
        console.print("  Aborted.")
        return

    # Update all rows for this assignment_id
    for r in rows:
        if r["assignment_id"] == aid:
            r["status"]         = "confirmed"
            r["confirmed_date"] = TODAY

    rewrite_assignments(rows)
    audit_log("confirm", "assignments", aid,
              student=student_number,
              project=a_rows[0]["project_id"])
    console.print(
        f"  [bold green]✓[/bold green]"
        f"  Status: proposed → [green]confirmed[/green]"
        f"  ·  confirmed_date: {TODAY}"
    )


# ── run_edit ──────────────────────────────────────────────────────────────────

def run_edit(args) -> None:
    console = Console()

    student_number = args.student_number
    project_id     = args.project
    task_id        = args.task

    rows = load_assignments()
    matches = [
        r for r in rows
        if r["student_number"] == student_number
        and r["project_id"]    == project_id
        and r["task_id"]       == task_id
        and r["status"] in {"proposed", "confirmed"}
    ]

    if not matches:
        console.print(
            f"  [red]No active assignment found for student {student_number}"
            f" on project '{project_id}' task '{task_id}'.[/red]"
        )
        return

    row = matches[0]
    current_hours = int(row["hours_planned"])

    console.print(
        f"\n  [bold]{row['task_label']}[/bold]"
        f"  —  {current_hours}h  [{row['status']}]"
    )

    raw = input(f"  New hours [{current_hours}]: ").strip()
    try:
        new_hours = int(raw) if raw else current_hours
    except ValueError:
        console.print("  Invalid input. Aborted.")
        return

    if new_hours == current_hours:
        console.print("  No change.")
        return

    for r in rows:
        if (r["student_number"] == student_number
                and r["project_id"] == project_id
                and r["task_id"]    == task_id
                and r["status"] in {"proposed", "confirmed"}):
            r["hours_planned"]   = new_hours
            r["hours_committed"] = new_hours

    rewrite_assignments(rows)
    audit_log("edit", "assignments", row["assignment_id"],
              task_id=task_id,
              old_hours=current_hours,
              new_hours=new_hours)
    console.print(
        f"  [bold green]✓[/bold green]"
        f"  {row['task_label']}: [dim]{current_hours}h[/dim] → [cyan]{new_hours}h[/cyan]"
    )


# ── run_remove ────────────────────────────────────────────────────────────────

def run_remove(args) -> None:
    console = Console()

    student_number = args.student_number
    project_id     = args.project
    task_id        = getattr(args, "task", None)

    try:
        student_meta = load_json("students", student_number)
    except FileNotFoundError:
        console.print(f"  [red]Student '{student_number}' not found.[/red]")
        return

    try:
        project_meta = load_json("projects", project_id)
    except FileNotFoundError:
        console.print(f"  [red]Project '{project_id}' not found.[/red]")
        return

    rows = load_assignments()

    if task_id:
        # Single task removal
        targets = [
            r for r in rows
            if r["student_number"] == student_number
            and r["project_id"]    == project_id
            and r["task_id"]       == task_id
            and r["status"] in {"proposed", "confirmed"}
        ]
        if not targets:
            console.print(
                f"  [yellow]No active assignment for task '{task_id}'.[/yellow]"
            )
            return

        t = targets[0]
        console.print(
            f"\n  Remove [bold]{student_meta['name']}[/bold]"
            f" from [bold]{t['task_label']}[/bold]"
            f" ({project_meta['title']})?"
        )
        answer = input("  [y/N]: ").strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return

        hours_freed = int(t["hours_planned"])
        for r in rows:
            if (r["student_number"] == student_number
                    and r["project_id"] == project_id
                    and r["task_id"]    == task_id
                    and r["status"] in {"proposed", "confirmed"}):
                r["status"] = "cancelled"

        rewrite_assignments(rows)
        audit_log("cancel", "assignments", t["assignment_id"],
                  student=student_number,
                  project=project_id,
                  task_id=task_id)
        console.print(
            f"  [bold green]✓[/bold green]  Task row cancelled."
            f"  [green]+{hours_freed}h freed[/green]"
        )

    else:
        # Remove from all tasks on this project
        targets = [
            r for r in rows
            if r["student_number"] == student_number
            and r["project_id"]    == project_id
            and r["status"] in {"proposed", "confirmed"}
        ]
        if not targets:
            console.print(
                f"  [yellow]{student_meta['name']} has no active assignments"
                f" on '{project_meta['title']}'.[/yellow]"
            )
            return

        console.print(
            f"\n  [bold]{student_meta['name']}[/bold]"
            f" is assigned to {len(targets)} task(s)"
            f" on [bold]{project_meta['title']}[/bold]:"
        )
        table = Table(box=box.SIMPLE_HEAD, show_header=False)
        table.add_column("Task",   style="white", min_width=28)
        table.add_column("Hours",  style="cyan",  justify="right")
        table.add_column("Status", style="yellow")
        for r in targets:
            table.add_row(r["task_label"], f"{r['hours_planned']}h", r["status"])
        console.print(table)

        answer = input("\n  Remove from all tasks? [y/N]: ").strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return

        hours_freed   = sum(int(r["hours_planned"]) for r in targets)
        assignment_id = targets[0]["assignment_id"]
        ids_to_cancel = {r["assignment_id"] for r in targets}

        for r in rows:
            if r["assignment_id"] in ids_to_cancel and r["student_number"] == student_number:
                r["status"] = "cancelled"

        rewrite_assignments(rows)
        audit_log("cancel", "assignments", assignment_id,
                  student=student_number,
                  project=project_id)
        console.print(
            f"  [bold green]✓[/bold green]"
            f"  {len(targets)} task row(s) cancelled"
            f"  [dim](assignment {assignment_id} fully cancelled)[/dim]"
            f"  ·  [green]+{hours_freed}h freed[/green]"
        )
