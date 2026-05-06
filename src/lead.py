"""
lead.py — assign or remove team lead (and optionally liaison) roles.

A student can be team lead for at most one project.
If a student is removed as lead, their liaison role is also cleared.
The liaison prompt only appears if the project has no liaison yet.

Commands:
    ./bin/python main.py lead --student <NUMBER> [--project <PROJECT_ID>]
    ./bin/python main.py lead --student <NUMBER> --remove
"""
from rich.console import Console
from rich.table import Table
from rich import box


def _find_liaison(project_id: str, all_student_ids: list[str], load_json) -> str | None:
    """Return student_number of current liaison for this project, or None."""
    for sid in all_student_ids:
        try:
            m = load_json("students", sid)
            if m.get("liaison_project") == project_id:
                return sid
        except Exception:
            pass
    return None


def run(args) -> None:
    from src.store import load_json, save_json, list_ids, load_assignments

    console = Console()
    student_number = args.student_number
    remove         = getattr(args, "remove", False)
    project_id     = getattr(args, "project", None)

    try:
        student_meta = load_json("students", student_number)
    except FileNotFoundError:
        console.print(f"  [red]Student '{student_number}' not found.[/red]")
        return

    name = student_meta.get("name", student_number)

    # ── Remove ────────────────────────────────────────────────────────────────
    if remove:
        old_lead    = student_meta.get("team_lead_project")
        old_liaison = student_meta.get("liaison_project")
        if not old_lead and not old_liaison:
            console.print(f"  [dim]{name} has no lead or liaison role to remove.[/dim]")
            return
        console.print(
            f"\n  Remove lead role from [bold]{name}[/bold]"
            + (f" (project: {old_lead})" if old_lead else "")
            + ("  +  liaison role" if old_liaison else "")
            + "?"
        )
        answer = input("  [y/N]: ").strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return
        student_meta["team_lead_project"] = None
        student_meta["liaison_project"]   = None
        save_json("students", student_number, student_meta)
        console.print(f"  [bold green]✓[/bold green]  Lead and liaison roles removed from {name}.")
        return

    # ── Assign ────────────────────────────────────────────────────────────────
    # Find projects this student is assigned to
    rows = load_assignments()
    assigned_pids = sorted({
        r["project_id"] for r in rows
        if r["student_number"] == student_number
        and r["status"] not in {"cancelled", "completed"}
    })

    if not assigned_pids:
        console.print(f"  [yellow]{name} has no active project assignments.[/yellow]")
        return

    # Resolve project
    if project_id:
        if project_id not in assigned_pids:
            console.print(
                f"  [red]'{project_id}' is not an active assignment for {name}.[/red]"
            )
            return
    else:
        # Show list and let user pick
        console.print(f"\n  Projects assigned to [bold]{name}[/bold]:\n")
        table = Table(box=box.SIMPLE_HEAD, show_header=False)
        table.add_column("#",       style="dim",   width=3)
        table.add_column("Project", style="white", min_width=30)
        table.add_column("Current lead?", style="cyan", width=14)

        all_sids = list_ids("students")
        project_metas = []
        for i, pid in enumerate(assigned_pids, 1):
            try:
                pmeta = load_json("projects", pid)
                title = pmeta.get("title", pid)
            except Exception:
                title = pid
            current_lead = None
            for sid in all_sids:
                try:
                    m = load_json("students", sid)
                    if m.get("team_lead_project") == pid:
                        current_lead = m.get("name", sid)
                        break
                except Exception:
                    pass
            lead_str = f"[dim]{current_lead}[/dim]" if current_lead else "[dim]none[/dim]"
            table.add_row(str(i), title, lead_str)
            project_metas.append((pid, title))

        console.print(table)
        raw = input("  Which project? Enter number: ").strip()
        try:
            project_id = project_metas[int(raw) - 1][0]
        except (ValueError, IndexError):
            console.print("  Aborted.")
            return

    # Load project title for display
    try:
        pmeta = load_json("projects", project_id)
        project_title = pmeta.get("title", project_id)
    except Exception:
        project_title = project_id

    # Warn if student is already lead of a different project
    current_lead_project = student_meta.get("team_lead_project")
    if current_lead_project and current_lead_project != project_id:
        console.print(
            f"\n  [yellow]{name} is already team lead for '{current_lead_project}'.[/yellow]"
        )
        answer = input("  Replace with new project? [y/N]: ").strip().lower()
        if answer != "y":
            console.print("  Aborted.")
            return
        # Clear old liaison if it was for the old project
        if student_meta.get("liaison_project") == current_lead_project:
            student_meta["liaison_project"] = None

    # Assign lead
    student_meta["team_lead_project"] = project_id
    if student_meta.get("leadership") not in {"lead", "willing"}:
        student_meta["leadership"] = "lead"

    # ── Liaison check ─────────────────────────────────────────────────────────
    all_sids       = list_ids("students")
    existing_liaison = _find_liaison(project_id, all_sids, load_json)

    if existing_liaison and existing_liaison != student_number:
        try:
            liaison_name = load_json("students", existing_liaison).get("name", existing_liaison)
        except Exception:
            liaison_name = existing_liaison
        console.print(
            f"\n  [dim]Project already has a liaison: {liaison_name}.[/dim]"
        )
    elif not existing_liaison:
        console.print(
            f"\n  No liaison is currently assigned to [bold]{project_title}[/bold]."
        )
        answer = input(f"  Make {name} the client liaison as well? [y/N]: ").strip().lower()
        if answer == "y":
            student_meta["liaison_project"] = project_id
        else:
            # Only clear if they were liaison for a different project via this assignment
            pass

    save_json("students", student_number, student_meta)

    liaison_note = ""
    if student_meta.get("liaison_project") == project_id:
        liaison_note = "  +  [cyan]client liaison[/cyan]"

    console.print(
        f"\n  [bold green]✓[/bold green]"
        f"  [bold]{name}[/bold] is now team lead for [bold]{project_title}[/bold]."
        + liaison_note
        + "\n"
    )
