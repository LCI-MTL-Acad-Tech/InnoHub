"""
match.py — rank students against projects (or vice versa), with optional
TF-IDF explanation of why a pair scores the way it does.
"""
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np

from src.models import Explanation, TermWeight


# ── Matching ──────────────────────────────────────────────────────────────────

def run(args):
    """
    match command dispatcher.
    --student <number>  : rank projects by similarity to this student
    --search <query>    : find student by name/email regex, then match
    --company <name>    : rank students by similarity to this company's projects
    """
    if getattr(args, "search", None):
        _match_student_search(args)
    elif args.student:
        _match_student(args.student, args)
    elif args.company:
        _match_company(args.company, args)


# ── Student → Projects matching ───────────────────────────────────────────────

def _match_student(student_number: str, args) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import load_json, list_ids, load_assignments, project_fill
    from src.embed import load_embedding, cosine_similarity
    from src.semester import parse as parse_sem
    import types

    console = Console()

    try:
        student_meta = load_json("students", student_number)
    except FileNotFoundError:
        console.print(f"  [red]Student '{student_number}' not found.[/red]")
        return

    if student_meta.get("status") != "active":
        console.print(
            f"  [yellow]{student_meta['name']} is {student_meta['status']}.[/yellow]"
        )
        if not getattr(args, "inactive", False):
            return

    emb_path = student_meta.get("embedding_file", "")
    if not emb_path or not Path(emb_path).exists():
        console.print(
            f"  [red]No embedding found for {student_number}. "
            f"Re-ingest their documents.[/red]"
        )
        return

    s_vec = load_embedding(emb_path)

    # ── Load state once into memory ───────────────────────────────────────────
    rows             = load_assignments()
    hours_available  = int(student_meta.get("hours_available", 0))

    # Semester filter (constant across loop)
    sem_filter_str = None
    if getattr(args, "semester", None):
        sem_obj = parse_sem(args.semester)
        if sem_obj:
            sem_filter_str = sem_obj.to_storage()

    # ── Assignment loop ───────────────────────────────────────────────────────
    while True:
        # Recompute from in-memory rows each iteration
        hours_committed = sum(
            int(r.get("hours_planned", 0)) for r in rows
            if r["student_number"] == student_number
            and r["status"] in {"proposed", "confirmed"}
        )
        hours_remaining = hours_available - hours_committed

        assigned_projects = {
            r["project_id"] for r in rows
            if r["student_number"] == student_number
            and r["status"] in {"proposed", "confirmed"}
        }
        past_projects = {
            r["project_id"] for r in rows
            if r["student_number"] == student_number
            and r["status"] in {"completed", "cancelled"}
        }

        if hours_remaining <= 0:
            console.print(
                f"  [dim]{student_meta['name']} has no hours remaining.[/dim]\n"
            )
            break

        # Rank eligible projects
        results = []
        for pid in list_ids("projects"):
            if pid in assigned_projects:
                continue
            try:
                pmeta = load_json("projects", pid)
            except Exception:
                continue
            if pmeta.get("status") != "active" and not getattr(args, "inactive", False):
                continue
            if sem_filter_str and pmeta.get("semester") != sem_filter_str:
                continue
            p_emb = pmeta.get("embedding_file", "")
            if not p_emb or not Path(p_emb).exists():
                continue
            try:
                cmeta = load_json("companies", pmeta["company_id"])
                if cmeta.get("status") != "active" and not getattr(args, "inactive", False):
                    continue
                company_name = cmeta.get("name", pmeta["company_id"])
            except Exception:
                company_name = pmeta.get("company_id", "")
            fill = project_fill(pmeta, rows)
            if not fill["has_open_slot"] and not getattr(args, "inactive", False):
                continue
            score = cosine_similarity(s_vec, load_embedding(p_emb))
            results.append((score, pmeta, company_name, fill))

        results.sort(key=lambda x: x[0], reverse=True)
        n    = getattr(args, "n", 5) if not getattr(args, "all", False) else len(results)
        shown = results[:n]

        if not shown:
            console.print(f"\n  No eligible projects found for {student_meta['name']}.\n")
            break

        # Build per-project breakdown for the header
        committed_by_project: dict[str, int] = {}
        for r in rows:
            if (r["student_number"] == student_number
                    and r["status"] in {"proposed", "confirmed"}):
                pid = r["project_id"]
                committed_by_project[pid] = (
                    committed_by_project.get(pid, 0)
                    + int(r.get("hours_planned", 0))
                )

        console.print(
            f"\n  [bold]{student_meta['name']}[/bold]"
            f"  {student_meta['program']}  ·  {student_meta['semester_start']}"
            f"  ·  [green]{hours_remaining}h remaining[/green]"
            f"  [dim]({hours_committed}h committed / {hours_available}h total)[/dim]\n"
        )
        if committed_by_project:
            for pid, h in sorted(committed_by_project.items()):
                try:
                    ptitle = load_json("projects", pid).get("title", pid)[:40]
                except Exception:
                    ptitle = pid
                console.print(f"  [dim]  · {ptitle}: {h}h[/dim]")
            console.print()

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Rank",    style="dim",    width=5,  justify="right")
        table.add_column("Score",   style="green",  width=6,  justify="right")
        table.add_column("Project", style="white",  min_width=28)
        table.add_column("Company", style="dim",    min_width=16)
        table.add_column("Fill",    style="cyan",   justify="right", width=14)
        table.add_column("Status",  style="yellow", width=10)

        for i, (score, pmeta, company, fill) in enumerate(shown, 1):
            status_colour = "green" if pmeta["status"] == "active" else "dim"
            n_teams = fill["n_teams"]
            if n_teams > 1:
                team_parts = []
                for label, td in sorted(fill["teams"].items()):
                    col = "green" if td["remaining"] > 0 else "dim"
                    team_parts.append(f"[{col}]{label}:{td['filled']}/{fill['total_hours']}h[/{col}]")
                fill_str = " ".join(team_parts)
            else:
                td = next(iter(fill["teams"].values()))
                fill_str = f"{td['filled']}/{fill['total_hours']}h"
            table.add_row(
                str(i), f"{score:.2f}", pmeta["title"], company,
                fill_str,
                f"[{status_colour}]{pmeta['status']}[/{status_colour}]",
            )

        console.print(table)

        total_matches = len(results)
        if total_matches > n:
            console.print(
                f"  [dim]Showing {n} of {total_matches} matches. "
                f"Use --n {n*2} or --all to see more.[/dim]"
            )

        if past_projects:
            names = []
            for pid in list(past_projects)[:3]:
                try:
                    names.append(load_json("projects", pid)["title"])
                except Exception:
                    names.append(pid)
            console.print(
                f"  [dim]Past assignments (excluded): "
                f"{', '.join(names)}"
                f"{'…' if len(past_projects) > 3 else ''}[/dim]"
            )

        console.print()

        raw = input(
            "  Assign to a project? Enter rank number, or press Enter to finish: "
        ).strip()
        if not raw:
            break

        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(shown):
                raise ValueError
        except ValueError:
            console.print("  Invalid selection.")
            continue

        _, pmeta, _, _ = shown[idx]
        sem_str = pmeta.get("semester", "")
        if not sem_str:
            from src.semester import prompt as prompt_sem
            sem_str = prompt_sem(types.SimpleNamespace(semester=None)).to_storage()

        # Run assignment — returns new rows to merge into memory
        from src.assign import run_assign
        assign_args = types.SimpleNamespace(
            student_number = student_number,
            project_id     = pmeta["project_id"],
            semester       = sem_str,
        )
        run_assign(assign_args)

        # Reload assignments from disk after writing — single source of truth
        rows = load_assignments()



def _match_student_search(args) -> None:
    """Resolve student by name/email regex, then match."""
    import re
    from rich.console import Console
    from src.store import list_ids, load_json

    console = Console()
    query   = args.search

    matches = []
    for sid in list_ids("students"):
        try:
            meta = load_json("students", sid)
            if (re.search(query, meta.get("name", ""), re.IGNORECASE)
                    or re.search(query, meta.get("email", ""), re.IGNORECASE)):
                matches.append(meta)
        except Exception:
            pass

    if not matches:
        console.print(f"  [red]No students found matching '{query}'.[/red]")
        return

    if len(matches) == 1:
        _match_student(matches[0]["student_number"], args)
        return

    console.print(f"\n  Students matching '{query}':")
    for i, m in enumerate(matches, 1):
        console.print(
            f"    {i}  {m['student_number']}  {m['name']}"
            f"  {m['program']}  [{m['status']}]"
        )
    raw = input("  Which student? Enter number: ").strip()
    try:
        meta = matches[int(raw) - 1]
        _match_student(meta["student_number"], args)
    except (ValueError, IndexError):
        console.print("  Aborted.")


# ── Company → Students matching ───────────────────────────────────────────────

def _match_company(company_name: str, args) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import list_ids, load_json, load_assignments
    from src.embed import load_embedding, cosine_similarity
    from src.fuzzy import ranked_matches
    from src.semester import parse as parse_sem

    console = Console()

    # Resolve company by fuzzy name
    all_ids    = list_ids("companies")
    names      = {cid: load_json("companies", cid)["name"] for cid in all_ids}
    matches    = ranked_matches(company_name, list(names.values()), limit=3)

    if not matches or matches[0][1] < 60:
        console.print(f"  [red]No company found matching '{company_name}'.[/red]")
        return

    if matches[0][1] < 90 or len([m for m in matches if m[1] > 60]) > 1:
        console.print(f"\n  Companies matching '{company_name}':")
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

    company_id   = next(cid for cid, n in names.items() if n == selected_name)
    company_meta = load_json("companies", company_id)

    # Find active projects for this company
    project_id_filter = getattr(args, "project", None)
    sem_filter_str    = None
    if getattr(args, "semester", None):
        sem_obj = parse_sem(args.semester)
        if sem_obj:
            sem_filter_str = sem_obj.to_storage()

    projects = []
    for pid in list_ids("projects"):
        try:
            pmeta = load_json("projects", pid)
        except Exception:
            continue
        if pmeta.get("company_id") != company_id:
            continue
        if project_id_filter and pid != project_id_filter:
            continue
        if pmeta.get("status") != "active" and not getattr(args, "inactive", False):
            continue
        if sem_filter_str and pmeta.get("semester") != sem_filter_str:
            continue
        emb = pmeta.get("embedding_file", "")
        if not emb or not Path(emb).exists():
            continue
        projects.append(pmeta)

    if not projects:
        console.print(
            f"  [yellow]No active projects with embeddings found for {selected_name}.[/yellow]"
        )
        return

    rows = load_assignments()
    n    = getattr(args, "n", 5) if not getattr(args, "all", False) else None

    for pmeta in projects:
        p_vec = load_embedding(pmeta["embedding_file"])
        fill  = project_fill(pmeta, rows)

        # Students assigned to any team of this project
        assigned_students = {
            r["student_number"] for r in rows
            if r["project_id"] == pmeta["project_id"]
            and r["status"] in {"proposed", "confirmed"}
        }

        results = []
        for sid in list_ids("students"):
            try:
                smeta = load_json("students", sid)
            except Exception:
                continue
            if smeta.get("status") != "active" and not getattr(args, "inactive", False):
                continue

            s_emb = smeta.get("embedding_file", "")
            if not s_emb or not Path(s_emb).exists():
                continue

            hours_committed = sum(
                int(r.get("hours_planned", 0)) for r in rows
                if r["student_number"] == sid
                and r["status"] in {"proposed", "confirmed"}
            )
            hours_remaining = int(smeta.get("hours_available", 0)) - hours_committed
            if hours_remaining <= 0 and not getattr(args, "inactive", False):
                continue

            score = cosine_similarity(p_vec, load_embedding(s_emb))
            results.append((score, smeta, hours_remaining))

        results.sort(key=lambda x: x[0], reverse=True)
        shown = results[:n] if n else results

        # Fill summary line
        n_teams = fill["n_teams"]
        if n_teams > 1:
            team_parts = [
                f"{lbl}:{td['filled']}/{fill['total_hours']}h"
                for lbl, td in sorted(fill["teams"].items())
            ]
            fill_str = "  ".join(team_parts)
        else:
            td = next(iter(fill["teams"].values()))
            fill_str = f"{td['filled']}/{fill['total_hours']}h"

        console.print(
            f"\n  [bold]{pmeta['title']}[/bold]"
            f"  ·  {pmeta.get('semester', '')}"
            f"  ·  {fill_str} filled\n"
        )

        if not shown:
            console.print("  [dim]No eligible students found.[/dim]\n")
            continue

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Rank",     style="dim",   width=5,  justify="right")
        table.add_column("Score",    style="green", width=6,  justify="right")
        table.add_column("Student",  style="white", min_width=22)
        table.add_column("Program",  style="cyan",  width=8)
        table.add_column("Semester", style="dim",   width=10)
        table.add_column("Avail.",   style="green", justify="right", width=8)

        for i, (score, smeta, hrs) in enumerate(shown, 1):
            table.add_row(
                str(i),
                f"{score:.2f}",
                smeta["name"],
                smeta.get("program", ""),
                smeta.get("semester_start", ""),
                f"{hrs}h",
            )
        console.print(table)

        if n and len(results) > n:
            console.print(
                f"  [dim]Showing {n} of {len(results)} matches. "
                f"Use --n {n*2} or --all to see more.[/dim]"
            )
        console.print()


# ── run_list ──────────────────────────────────────────────────────────────────

def run_list(args) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import list_ids, load_json, load_assignments
    from src.semester import parse as parse_sem

    console  = Console()
    what     = args.what
    inactive = getattr(args, "inactive", False)

    sem_filter_str = None
    if getattr(args, "semester", None):
        sem_obj = parse_sem(args.semester)
        if sem_obj:
            sem_filter_str = sem_obj.to_storage()

    rows = load_assignments()

    if what == "students":
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                      title="Students", title_style="bold", title_justify="left")
        table.add_column("ID",       style="cyan", width=10)
        table.add_column("Name",     style="white",     min_width=22)
        table.add_column("Program",  style="cyan",      width=8)
        table.add_column("Semester", style="dim",       width=10)
        table.add_column("Hours",    style="green",     justify="right", width=10)
        table.add_column("Assigned", style="dim",       justify="right", width=8)
        table.add_column("Status",   style="yellow",    width=10)

        pending_only = getattr(args, "pending_program", False)

        for sid in sorted(list_ids("students")):
            try:
                m = load_json("students", sid)
            except Exception:
                continue
            if not inactive and m.get("status") not in {"active"}:
                continue
            if sem_filter_str and m.get("semester_start") != sem_filter_str:
                continue
            prog = m.get("program", "")
            is_pending = prog in {"570.??"}  # only interior design DEC/AEC is truly pending
            if pending_only and not is_pending:
                continue
            committed = sum(
                int(r.get("hours_planned", 0)) for r in rows
                if r["student_number"] == sid
                and r["status"] in {"proposed", "confirmed"}
            )
            remaining  = int(m.get("hours_available", 0)) - committed
            n_assigned = len({r["assignment_id"] for r in rows
                               if r["student_number"] == sid
                               and r["status"] in {"proposed", "confirmed"}})
            status_c = {"active": "green", "inactive": "yellow",
                        "completed": "dim"}.get(m.get("status", ""), "white")
            prog_display = f"[bold yellow]{prog} ⚠[/bold yellow]" if is_pending else prog
            table.add_row(
                sid,
                m.get("name", ""),
                prog_display,
                m.get("semester_start", ""),
                f"{remaining}h left",
                str(n_assigned),
                f"[{status_c}]{m.get('status','')}[/{status_c}]",
            )
        console.print(table)
        if pending_only:
            console.print(
                "  [dim]Use [bold]innovhub ingest --type s --id <N>[/bold] "
                "to re-ingest with a corrected program code.[/dim]\n"
            )

    elif what == "projects":
        from src.store import project_fill as _pf

        # ── --no-tasks filter ─────────────────────────────────────────────────
        if getattr(args, "no_tasks", False):
            from pathlib import Path
            from src.store import PATHS
            flagged = []
            for pid in sorted(list_ids("projects")):
                try:
                    m = load_json("projects", pid)
                except Exception:
                    continue
                if sem_filter_str and m.get("semester") != sem_filter_str:
                    continue
                if not m.get("capacity", {}).get("tasks"):
                    flagged.append((pid, m.get("title", pid),
                                    m.get("semester", "")))
            if not flagged:
                console.print("  [green]All projects have tasks defined.[/green]\n")
                return
            console.print(
                f"\n  [yellow]{len(flagged)} project(s) with no tasks:[/yellow]\n"
            )
            for pid, title, sem in flagged:
                console.print(f"    [dim]{pid}[/dim]  {title}  [dim]{sem}[/dim]")
            if getattr(args, "requeue", False):
                console.print()
                for pid, title, _ in flagged:
                    path = Path(PATHS["projects"]) / f"{pid}.json"
                    path.unlink(missing_ok=True)
                    console.print(f"  [dim]deleted[/dim]  {pid}")
                console.print(
                    f"\n  [green]✓[/green]  {len(flagged)} project(s) requeued."
                    f" Run [bold]import[/bold] to re-process.\n"
                )
            return
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                      title="Projects", title_style="bold", title_justify="left")
        table.add_column("ID",       style="dim",  min_width=24)
        table.add_column("Title",    style="white",     min_width=26)
        table.add_column("Company",  style="dim",       min_width=16)
        table.add_column("Semester", style="cyan",      width=10)
        table.add_column("Teams",    style="dim",       width=6,  justify="right")
        table.add_column("Fill",     style="green",     justify="right", width=12)
        table.add_column("Status",   style="yellow",    width=10)

        for pid in sorted(list_ids("projects")):
            try:
                m = load_json("projects", pid)
            except Exception:
                continue
            if not inactive and m.get("status") not in {"active"}:
                continue
            if sem_filter_str and m.get("semester") != sem_filter_str:
                continue
            try:
                company = load_json("companies", m["company_id"]).get("name", m["company_id"])
            except Exception:
                company = m.get("company_id", "")
            fill     = _pf(m, rows)
            n_teams  = fill["n_teams"]
            td       = next(iter(fill["teams"].values()))
            fill_str = f"{fill['filled_total']}/{fill['capacity_total']}h"
            status_c = {"active": "green", "inactive": "yellow",
                        "closed": "red"}.get(m.get("status", ""), "white")
            table.add_row(
                pid,
                m.get("title", ""),
                company,
                m.get("semester", ""),
                str(n_teams),
                fill_str,
                f"[{status_c}]{m.get('status','')}[/{status_c}]",
            )
        console.print(table)

    elif what == "coordinators":
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                      title="Coordinators", title_style="bold", title_justify="left")
        table.add_column("Email",    style="cyan", min_width=28)
        table.add_column("Name",     style="white",     min_width=22)
        table.add_column("Programs", style="dim",       min_width=16)
        table.add_column("CV",       style="dim",       width=5)
        table.add_column("Status",   style="yellow",    width=10)

        for cid in sorted(list_ids("coordinators")):
            try:
                m = load_json("coordinators", cid)
            except Exception:
                continue
            if not inactive and m.get("status") != "active":
                continue
            progs    = ", ".join(m.get("programs", [])) or "all"
            has_cv   = "✓" if m.get("embedding_file") else "—"
            status_c = "green" if m.get("status") == "active" else "yellow"
            table.add_row(
                cid,
                m.get("name", ""),
                progs,
                has_cv,
                f"[{status_c}]{m.get('status','')}[/{status_c}]",
            )
        console.print(table)

    elif what == "companies":
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                      title="Companies", title_style="bold", title_justify="left")
        table.add_column("ID",       style="dim",  min_width=16)
        table.add_column("Name",     style="white",     min_width=20)
        table.add_column("Contact",  style="dim",       min_width=20)
        table.add_column("Lang",     style="cyan",      width=6)
        table.add_column("Projects", style="dim",       justify="right", width=9)
        table.add_column("Status",   style="yellow",    width=10)

        for cid in sorted(list_ids("companies")):
            try:
                m = load_json("companies", cid)
            except Exception:
                continue
            if not inactive and m.get("status") != "active":
                continue
            n_projects = sum(
                1 for pid in list_ids("projects")
                if load_json("projects", pid).get("company_id") == cid
                and load_json("projects", pid).get("status") == "active"
            )
            status_c = "green" if m.get("status") == "active" else "yellow"
            table.add_row(
                cid,
                m.get("name", ""),
                f"{m.get('contact_name','')}  <{m.get('contact_email','')}>",
                m.get("language", ""),
                str(n_projects),
                f"[{status_c}]{m.get('status','')}[/{status_c}]",
            )
        console.print(table)


def run_status(args):
    """
    status command dispatcher — routes to the appropriate view based on flags.
      --student  : hours summary, assigned tasks, remaining availability
      --project  : per-task fill table (total / filled / remaining / students)
      --company  : status of all active projects for that company
      --all      : global overview (stub — implemented in dashboard_cli)
    """
    if args.student:
        _status_student(args.student)
    elif args.project:
        _status_project(args.project)
    elif args.company:
        _status_company(args.company)
    elif getattr(args, "coordinator", None):
        _status_coordinator(args.coordinator)
    elif args.all:
        from src.dashboard_cli import run as run_dash
        run_dash(args)


def _status_student(student_number: str) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import load_json, load_assignments

    console = Console()
    meta    = load_json("students", student_number)
    rows    = load_assignments()

    active_rows = [
        r for r in rows
        if r["student_number"] == student_number
        and r["status"] in {"proposed", "confirmed"}
    ]

    hours_committed = sum(int(r["hours_planned"]) for r in active_rows)
    hours_available = int(meta["hours_available"])
    hours_remaining = hours_available - hours_committed

    status_colour = {
        "active": "green", "inactive": "yellow", "completed": "dim"
    }.get(meta["status"], "white")

    console.print(
        f"\n  [bold]{meta['name']}[/bold]"
        f"  {meta['program']}  ·  {meta['semester_start']}"
        f"  ·  [{status_colour}]{meta['status']}[/{status_colour}]"
    )
    console.print(
        f"  Hours: [cyan]{hours_committed}h committed[/cyan]"
        f" / [green]{hours_remaining}h remaining[/green]"
        f" / {hours_available}h total\n"
    )

    if active_rows:
        # Group by project
        by_project: dict[str, list[dict]] = {}
        for r in active_rows:
            by_project.setdefault(r["project_id"], []).append(r)

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Project",  style="white",   min_width=30)
        table.add_column("Task",     style="white",   min_width=20)
        table.add_column("Hours",    style="cyan",    justify="right")
        table.add_column("Status",   style="yellow",  justify="center")

        for project_id, task_rows in by_project.items():
            try:
                pmeta = load_json("projects", project_id)
                title = pmeta["title"]
            except Exception:
                title = project_id
            for i, r in enumerate(task_rows):
                table.add_row(
                    title if i == 0 else "",
                    r["task_label"],
                    f"{r['hours_planned']}h",
                    r["status"],
                )
        console.print(table)
    else:
        console.print("  [dim]No active assignments.[/dim]\n")


def _status_project(project_id: str) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import load_json, load_assignments, project_fill as _pf

    console = Console()
    meta    = load_json("projects", project_id)
    rows    = load_assignments()
    fill    = _pf(meta, rows)

    status_colour = {
        "active": "green", "inactive": "yellow", "closed": "red"
    }.get(meta["status"], "white")
    n_teams = fill["n_teams"]

    console.print(
        f"\n  [bold]{meta['title']}[/bold]"
        f"  ·  {meta['semester']}"
        + (f"  ·  [cyan]{n_teams} teams[/cyan]" if n_teams > 1 else "")
        + f"  ·  [{status_colour}]{meta['status']}[/{status_colour}]"
    )
    try:
        company = load_json("companies", meta["company_id"])
        console.print(f"  {company['name']}  ·  Lead: {meta['lead_name']} <{meta['lead_email']}>\n")
    except Exception:
        console.print(f"  Lead: {meta['lead_name']} <{meta['lead_email']}>\n")

    tasks = meta["capacity"]["tasks"]
    active_rows = [
        r for r in rows
        if r["project_id"] == project_id
        and r["status"] in {"proposed", "confirmed"}
    ]

    if n_teams <= 1:
        # ── Single team — original task table ────────────────────────────────
        filled: dict[str, int] = {}
        students_on_task: dict[str, list[str]] = {}
        for r in active_rows:
            tid = r["task_id"]
            filled[tid] = filled.get(tid, 0) + int(r["hours_planned"])
            students_on_task.setdefault(tid, []).append(r["student_number"])

        total_hours  = fill["total_hours"]
        total_filled = fill["filled_total"]
        total_remain = total_hours - total_filled

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Task",      style="white", min_width=28)
        table.add_column("Total",     style="white", justify="right")
        table.add_column("Filled",    style="cyan",  justify="right")
        table.add_column("Remaining", style="green", justify="right")
        table.add_column("Students",  style="dim",   min_width=16)

        for t in tasks:
            tid   = t["task_id"]
            f     = filled.get(tid, 0)
            rem   = t["hours"] - f
            studs = ", ".join(students_on_task.get(tid, [])) or "—"
            table.add_row(
                t["label"], f"{t['hours']}h", f"{f}h",
                f"[{'green' if rem > 0 else 'dim'}]{rem}h[/{'green' if rem > 0 else 'dim'}]",
                studs,
            )
        table.add_section()
        table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{total_hours}h[/bold]",
            f"[bold]{total_filled}h[/bold]",
            f"[bold]{total_remain}h[/bold]", "",
        )
        console.print(table)

    else:
        # ── Multi-team — one section per team ─────────────────────────────────
        for team_label, td in sorted(fill["teams"].items()):
            team_rows = [r for r in active_rows if r.get("team", "") == team_label]

            filled_t: dict[str, int] = {}
            students_t: dict[str, list[str]] = {}
            for r in team_rows:
                tid = r["task_id"]
                filled_t[tid] = filled_t.get(tid, 0) + int(r["hours_planned"])
                students_t.setdefault(tid, []).append(r["student_number"])

            total_filled_t = td["filled"]
            total_remain_t = td["remaining"]

            console.print(
                f"  [bold cyan]Team {team_label}[/bold cyan]"
                f"  {total_filled_t}/{fill['total_hours']}h filled"
                f"  ({len(td['students'])} student(s))"
            )
            table = Table(box=box.SIMPLE_HEAD, show_header=True,
                          header_style="bold", show_footer=False)
            table.add_column("Task",      style="white", min_width=28)
            table.add_column("Total",     style="white", justify="right")
            table.add_column("Filled",    style="cyan",  justify="right")
            table.add_column("Remaining", style="green", justify="right")
            table.add_column("Students",  style="dim",   min_width=16)

            for t in tasks:
                tid   = t["task_id"]
                f     = filled_t.get(tid, 0)
                rem   = t["hours"] - f
                studs = ", ".join(students_t.get(tid, [])) or "—"
                table.add_row(
                    t["label"], f"{t['hours']}h", f"{f}h",
                    f"[{'green' if rem > 0 else 'dim'}]{rem}h[/{'green' if rem > 0 else 'dim'}]",
                    studs,
                )
            console.print(table)
            console.print()

    confirmed = len({r["assignment_id"] for r in active_rows if r["status"] == "confirmed"})
    proposed  = len({r["assignment_id"] for r in active_rows if r["status"] == "proposed"})
    console.print(
        f"  Assignments: [green]{confirmed} confirmed[/green]"
        f"  [yellow]{proposed} proposed[/yellow]\n"
    )

    # Coordinators
    coord_ids = meta.get("coordinators", [])
    if coord_ids:
        names = []
        for cid in coord_ids:
            try:
                c = load_json("coordinators", cid)
                names.append(f"{c['name']} <{c['email']}>")
            except Exception:
                names.append(cid)
        console.print("  [bold]Coordinators:[/bold]  " + "  ·  ".join(names))
    else:
        from src.store import default_coordinator
        dc = default_coordinator()
        if dc:
            console.print(
                f"  [bold]Coordinator:[/bold]  [dim](none assigned — de facto: "
                f"{dc['name']} <{dc['email']}>)[/dim]"
            )
    console.print()


def _status_coordinator(query: str) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from src.store import load_json, list_ids
    from src.coordinator import resolve_coordinator

    console = Console()

    coord = resolve_coordinator(query)
    if coord is None:
        return

    cid    = coord["email"]   # email is the unique ID
    progs  = ", ".join(coord.get("programs", [])) or "all programs"
    status_colour = "green" if coord.get("status") == "active" else "yellow"

    console.print(
        f"\n  [bold]{coord['name']}[/bold]  <{coord['email']}>"
        f"  ·  [{status_colour}]{coord.get('status', 'active')}[/{status_colour}]"
        f"  ·  Programs: {progs}\n"
    )

    # Find all projects assigned to this coordinator
    assigned = [
        pid for pid in list_ids("projects")
        if cid in load_json("projects", pid).get("coordinators", [])
    ]

    if not assigned:
        console.print("  [dim]No projects assigned.[/dim]\n")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Project",   style="white",  min_width=30)
    table.add_column("Company",   style="dim",    min_width=18)
    table.add_column("Semester",  style="cyan",   width=10)
    table.add_column("Status",    style="yellow", width=10)
    table.add_column("Fill",      style="green",  justify="right")

    for pid in assigned:
        try:
            pmeta   = load_json("projects", pid)
            cmeta   = load_json("companies", pmeta["company_id"])
            company = cmeta["name"]
        except Exception:
            company = pmeta.get("company_id", "—")

        total   = pmeta["capacity"]["total_hours"]
        from src.store import load_assignments
        rows    = load_assignments()
        filled  = sum(
            int(r["hours_planned"]) for r in rows
            if r["project_id"] == pid
            and r["status"] in {"proposed", "confirmed"}
        )
        table.add_row(
            pmeta["title"],
            company,
            pmeta["semester"],
            pmeta["status"],
            f"{filled}/{total}h",
        )

    console.print(table)


def _status_company(company_name: str) -> None:
    from rich.console import Console
    from src.store import load_json, list_ids
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
    cmeta      = load_json("companies", company_id)

    status_colour = "green" if cmeta["status"] == "active" else "yellow"
    console.print(
        f"\n  [bold]{cmeta['name']}[/bold]"
        f"  ·  [{status_colour}]{cmeta['status']}[/{status_colour}]\n"
    )

    # Show status for every non-closed project
    project_ids = [
        pid for pid in list_ids("projects")
        if load_json("projects", pid).get("company_id") == company_id
        and load_json("projects", pid).get("status") != "closed"
    ]

    if not project_ids:
        console.print("  [dim]No active or inactive projects.[/dim]\n")
        return

    for pid in project_ids:
        _status_project(pid)


# ── Explanation ───────────────────────────────────────────────────────────────

def explain(
    student_number: str,
    project_id: str,
    score: float,
    top_n: int = 10,
) -> Explanation:
    """
    Build a TF-IDF explanation for why student matches project.

    Strategy:
      1. Load the raw text for the student and the project from
         data/documents/.
      2. Fit a TfidfVectorizer across ALL documents in the corpus so that
         IDF weights reflect term rarity globally, not just in this pair.
      3. Extract the top_n terms by shared weight (product of both TF-IDF
         scores), then separate terms present in only one document.

    Returns an Explanation dataclass ready for rendering.
    """
    from src.store import load_json, list_ids
    from src.parse import parse_file

    # ── Collect corpus ────────────────────────────────────────────────────────
    corpus_texts: dict[str, str] = {}

    for kind in ("students", "companies", "projects"):
        for eid in list_ids(kind):
            meta = load_json(kind, eid)
            docs = meta.get("documents", [])
            if not docs:
                continue
            doc_dir = Path(f"data/documents/{kind}")
            texts = []
            for d in docs:
                fpath = doc_dir / d["filename"]
                if fpath.exists():
                    texts.append(parse_file(fpath))
            if texts:
                corpus_texts[eid] = "\n".join(texts)

    if student_number not in corpus_texts:
        raise ValueError(f"No documents found for student {student_number}")
    if project_id not in corpus_texts:
        raise ValueError(f"No documents found for project {project_id}")

    ids = list(corpus_texts.keys())
    texts = [corpus_texts[i] for i in ids]

    # ── Fit TF-IDF across full corpus ─────────────────────────────────────────
    vec = TfidfVectorizer(
        ngram_range=(1, 3),        # unigrams, bigrams, trigrams
        min_df=1,
        max_features=20_000,
        sublinear_tf=True,
        strip_accents=None,        # preserve French accents
    )
    tfidf_matrix = vec.fit_transform(texts)
    feature_names = np.array(vec.get_feature_names_out())

    s_idx = ids.index(student_number)
    p_idx = ids.index(project_id)

    s_vec = tfidf_matrix[s_idx].toarray().flatten()
    p_vec = tfidf_matrix[p_idx].toarray().flatten()

    # ── Shared terms — product of both weights, non-zero in both ─────────────
    both_nonzero = (s_vec > 0) & (p_vec > 0)
    shared_weight = s_vec * p_vec
    shared_weight[~both_nonzero] = 0

    top_indices = np.argsort(shared_weight)[::-1][:top_n]
    shared_terms = [
        TermWeight(
            term=feature_names[i],
            student_weight=round(float(s_vec[i]), 2),
            project_weight=round(float(p_vec[i]), 2),
            shared_weight=round(float(shared_weight[i]), 2),
        )
        for i in top_indices
        if shared_weight[i] > 0
    ]

    # ── Terms present in student only ─────────────────────────────────────────
    student_only_mask = (s_vec > 0) & (p_vec == 0)
    student_only_indices = np.argsort(s_vec * student_only_mask)[::-1][:8]
    student_only = [
        feature_names[i]
        for i in student_only_indices
        if s_vec[i] > 0 and p_vec[i] == 0
    ]

    # ── Terms present in project only ─────────────────────────────────────────
    project_only_mask = (p_vec > 0) & (s_vec == 0)
    project_only_indices = np.argsort(p_vec * project_only_mask)[::-1][:8]
    project_only = [
        feature_names[i]
        for i in project_only_indices
        if p_vec[i] > 0 and s_vec[i] == 0
    ]

    return Explanation(
        student_number=student_number,
        project_id=project_id,
        score=round(score, 2),
        shared_terms=shared_terms,
        student_only_terms=student_only,
        project_only_terms=project_only,
    )


def render_explanation(exp: Explanation) -> None:
    """Print an Explanation to the terminal using rich."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    console.print(
        f"\n── Match explanation: "
        f"[bold]{exp.student_number}[/bold] ↔ "
        f"[bold]{exp.project_id}[/bold] ──",
        style="cyan",
    )
    console.print(f"\n  Score: [bold green]{exp.score:.2f}[/bold green]\n")

    if exp.shared_terms:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Term",           style="white",  min_width=24)
        table.add_column("Student",        style="cyan",   justify="right")
        table.add_column("Project",        style="magenta",justify="right")
        table.add_column("Shared weight",  style="green",  justify="right")

        for t in exp.shared_terms:
            table.add_row(
                t.term,
                f"{t.student_weight:.2f}",
                f"{t.project_weight:.2f}",
                f"{t.shared_weight:.2f}",
            )
        console.print(table)
    else:
        console.print("  [dim]No significant shared terms found.[/dim]\n")

    if exp.student_only_terms:
        console.print(
            "  [cyan]In student only:[/cyan]  "
            + ", ".join(exp.student_only_terms)
        )
    if exp.project_only_terms:
        console.print(
            "  [magenta]In project only:[/magenta]  "
            + ", ".join(exp.project_only_terms)
        )
    console.print()


def run_explain(args):
    """
    explain subcommand entry point.
    Resolves student number and project ID, calls explain(), renders result.
    """
    from src.embed import cosine_similarity, load_embedding
    from src.store import load_json

    student_meta = load_json("students", args.student)
    project_meta = load_json("projects", args.project)

    s_vec = load_embedding(student_meta["embedding_file"])
    p_vec = load_embedding(project_meta["embedding_file"])
    score = cosine_similarity(s_vec, p_vec)

    exp = explain(
        student_number=args.student,
        project_id=args.project,
        score=score,
        top_n=getattr(args, "top_n", 10),
    )
    render_explanation(exp)
