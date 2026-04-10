"""
dashboard_cli.py — terminal dashboard using rich.
Supports filtering, grouping and sorting via CLI flags.
All filtering is applied at run time — rerun with different flags for new views.
"""
from rich.console import Console
from rich.table import Table
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from rich import box


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    from src.store import (
        list_ids, load_json, load_assignments, load_programs, default_coordinator
    )
    from src.semester import parse as parse_sem, group_by_calendar, group_by_academic
    from src.fuzzy import ranked_matches

    console  = Console()
    programs = load_programs()

    # ── Parse filters ─────────────────────────────────────────────────────────
    f = _Filters(args)

    # ── Load all entities ─────────────────────────────────────────────────────
    all_students  = _safe_load_all("students")
    all_projects  = _safe_load_all("projects")
    all_companies = _safe_load_all("companies")
    all_coords    = _safe_load_all("coordinators")
    rows          = load_assignments()

    # ── Resolve company/coordinator filter names to IDs ───────────────────────
    company_ids_filter: set[str] | None = None
    if f.companies:
        company_ids_filter = set()
        all_cnames = {c["company_id"]: c.get("name","") for c in all_companies}
        for q in f.companies:
            matches = ranked_matches(q, list(all_cnames.values()), limit=3)
            for name, score in matches:
                if score >= 70:
                    cid = next((k for k,v in all_cnames.items() if v == name), None)
                    if cid:
                        company_ids_filter.add(cid)

    coord_emails_filter: set[str] | None = None
    if f.coordinators:
        coord_emails_filter = set()
        for q in f.coordinators:
            for c in all_coords:
                from src.fuzzy import best_match
                name_score = best_match(q, [c.get("name","")])[1]
                email_score = best_match(q, [c.get("email","")])[1]
                if max(name_score, email_score) >= 70:
                    coord_emails_filter.add(c["email"])

    # ── Filter entities ───────────────────────────────────────────────────────
    students = _filter_students(all_students, f, rows)
    projects = _filter_projects(all_projects, f, rows, company_ids_filter, coord_emails_filter)
    companies_active = [c for c in all_companies if c.get("status") == "active"]

    # Filter rows to match filtered students/projects
    student_ids  = {s["student_number"] for s in students}
    project_ids  = {p["project_id"]     for p in projects}
    rows_filtered = [
        r for r in rows
        if r.get("student_number") in student_ids
        or r.get("project_id")     in project_ids
    ]
    if f.statuses:
        rows_filtered = [r for r in rows_filtered if r.get("status") in f.statuses]

    # ── Active subsets ────────────────────────────────────────────────────────
    active_students = [s for s in students if s.get("status") == "active"]
    active_projects = [p for p in projects if p.get("status") == "active"]

    placed_students: set[str] = {
        r["student_number"] for r in rows
        if r.get("status") in {"confirmed", "completed"}
        and r["student_number"] in student_ids
    }

    asgn_counts: dict[str, int] = {}
    for r in rows_filtered:
        s = r.get("status", "?")
        asgn_counts[s] = asgn_counts.get(s, 0) + 1

    # ── Header ────────────────────────────────────────────────────────────────
    active_filters = f.describe()
    header_line = f"[bold]◈ Innovation Hub — Dashboard[/bold]"
    if active_filters:
        header_line += f"  [dim]Filters: {active_filters}[/dim]"
    console.print(f"\n{header_line}\n")

    # ── Summary panels ────────────────────────────────────────────────────────
    def _panel(value: int, label: str, color: str = "green") -> Panel:
        t = Text(str(value), style=f"bold {color}", justify="center")
        t.append(f"\n{label}", style="dim")
        return Panel(t, expand=True, border_style="dim")

    console.print(Columns([
        _panel(len(active_students),   "active students",  "green"),
        _panel(len(active_projects),   "active projects",  "cyan"),
        _panel(len(companies_active),  "active companies", "cyan"),
        _panel(len(all_coords),        "coordinators",     "blue"),
        _panel(asgn_counts.get("confirmed", 0), "confirmed", "green"),
        _panel(asgn_counts.get("proposed",  0), "proposed",  "yellow"),
    ], equal=True, expand=True))
    console.print()

    # ── Semester / year breakdown ─────────────────────────────────────────────
    group_by = (getattr(args, "group_by", None) or "calendar").split(",")[0].strip()
    all_sem_strings = {r.get("semester","") for r in load_assignments() if r.get("semester")}
    all_sems = [s for s in (parse_sem(x) for x in all_sem_strings) if s]

    if all_sems and not f.semesters and not f.years:
        if group_by == "academic-year":
            groups = group_by_academic(all_sems)
            group_label = "academic year"
        else:
            groups = group_by_calendar(all_sems)
            group_label = "calendar year"

        sem_table = Table(
            box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
            title=f"Semesters by {group_label}",
            title_style="bold", title_justify="left",
        )
        sem_table.add_column("Group",    style="cyan",  min_width=14)
        sem_table.add_column("Semester", style="white", min_width=14)
        sem_table.add_column("Students", justify="right", style="green")
        sem_table.add_column("Projects", justify="right", style="cyan")

        for group_key, group_sems in groups.items():
            for i, sem in enumerate(group_sems):
                s_count = sum(1 for s in all_students
                              if parse_sem(s.get("semester_start","")) == sem)
                p_count = sum(1 for p in all_projects
                              if parse_sem(p.get("semester","")) == sem)
                sem_table.add_row(
                    str(group_key) if i == 0 else "",
                    str(sem), str(s_count), str(p_count),
                )
        console.print(sem_table)
        console.print()

    # ── Placement by program ──────────────────────────────────────────────────
    prog_labels = {p["code"]: p.get("label_fr", p["code"]) for p in programs}
    prog_total:  dict[str, int] = {}
    prog_placed: dict[str, int] = {}

    for s in active_students:
        code = s.get("program", "?")
        prog_total[code] = prog_total.get(code, 0) + 1

    for s in active_students:
        if s["student_number"] in placed_students:
            code = s.get("program", "?")
            prog_placed[code] = prog_placed.get(code, 0) + 1

    if prog_total:
        prog_table = Table(
            box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
            title="Placement by program",
            title_style="bold", title_justify="left",
        )
        prog_table.add_column("Code",    style="cyan",  width=8)
        prog_table.add_column("Program", style="white", min_width=36)
        prog_table.add_column("Placed",  justify="right", style="green")
        prog_table.add_column("Total",   justify="right", style="dim")
        prog_table.add_column("Rate",    min_width=24)

        sort_by = getattr(args, "sort_by", None)
        codes   = sorted(prog_total.keys())
        if sort_by == "fill-rate":
            codes = sorted(codes, key=lambda c: prog_placed.get(c,0)/prog_total[c] if prog_total[c] else 0, reverse=True)

        for code in codes:
            total  = prog_total[code]
            placed = prog_placed.get(code, 0)
            rate   = placed / total if total else 0
            prog_table.add_row(
                code,
                prog_labels.get(code, code),
                str(placed), str(total),
                _rate_bar(rate, width=16),
            )
        console.print(prog_table)
        console.print()

    # ── Project fill status ───────────────────────────────────────────────────
    display_projects = active_projects
    if f.unfilled:
        display_projects = [
            p for p in display_projects
            if _hours_remaining(p, rows) > 0
        ]

    if display_projects:
        # Determine grouping keys
        group_keys = [g.strip() for g in (getattr(args, "group_by", None) or "company").split(",")]
        _render_project_table(display_projects, rows, group_keys, args, console)

    # ── Unplaced students ─────────────────────────────────────────────────────
    unplaced = [
        s for s in active_students
        if s["student_number"] not in placed_students
    ]
    if f.unplaced:
        display_unplaced = unplaced
    else:
        display_unplaced = unplaced

    if display_unplaced:
        unplaced_table = Table(
            box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
            title="Unplaced students",
            title_style="bold yellow", title_justify="left",
        )
        unplaced_table.add_column("Student #", style="cyan mono", width=10)
        unplaced_table.add_column("Name",      style="white",     min_width=22)
        unplaced_table.add_column("Program",   style="cyan",      width=8)
        unplaced_table.add_column("Semester",  style="dim",       width=12)
        unplaced_table.add_column("Hours",     style="green",     justify="right", width=8)

        sort_by = getattr(args, "sort_by", None)
        if sort_by == "program":
            display_unplaced = sorted(display_unplaced, key=lambda s: (s.get("program",""), s.get("name","")))
        elif sort_by == "semester":
            display_unplaced = sorted(display_unplaced, key=lambda s: s.get("semester_start",""))
        elif sort_by == "hours":
            display_unplaced = sorted(display_unplaced, key=lambda s: s.get("hours_available",0), reverse=True)
        else:
            display_unplaced = sorted(display_unplaced, key=lambda s: (s.get("program",""), s.get("name","")))

        for s in display_unplaced:
            unplaced_table.add_row(
                s["student_number"],
                s.get("name", ""),
                s.get("program", ""),
                s.get("semester_start", ""),
                f"{s.get('hours_available', 0)}h",
            )
        console.print(unplaced_table)
        console.print()
    elif not f.unplaced:
        console.print(
            "  [bold green]✓[/bold green]  "
            "[dim]All active students have at least one confirmed assignment.[/dim]\n"
        )

    # ── Projects with no coordinator ──────────────────────────────────────────
    if f.no_coordinator:
        no_coord = [p for p in active_projects if not p.get("coordinators")]
        if no_coord:
            nc_table = Table(
                box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                title="Projects with no coordinator",
                title_style="bold yellow", title_justify="left",
            )
            nc_table.add_column("Project",  style="white", min_width=28)
            nc_table.add_column("Company",  style="dim",   min_width=18)
            nc_table.add_column("Semester", style="cyan",  width=12)
            for p in sorted(no_coord, key=lambda x: x.get("title","")):
                try:
                    from src.store import load_json
                    company = load_json("companies", p["company_id"]).get("name", p["company_id"])
                except Exception:
                    company = p.get("company_id", "")
                nc_table.add_row(p.get("title",""), company, p.get("semester",""))
            console.print(nc_table)
            console.print()
        else:
            console.print(
                "  [bold green]✓[/bold green]  "
                "[dim]All active projects have a coordinator assigned.[/dim]\n"
            )

    # ── Default coordinator reminder ──────────────────────────────────────────
    dc = default_coordinator()
    if dc:
        console.print(
            f"  [dim]Default coordinator: "
            f"{dc.get('name','')}  <{dc.get('email','')}>[/dim]\n"
        )


# ── Filters dataclass ─────────────────────────────────────────────────────────

class _Filters:
    def __init__(self, args):
        from src.semester import parse as parse_sem

        raw_sems      = getattr(args, "semester",       None) or []
        self.semesters = [s for s in (parse_sem(x) for x in raw_sems) if s]
        self.years     = set(getattr(args, "year",        None) or [])
        self.companies = getattr(args, "company",    None) or []
        self.programs  = set(getattr(args, "program",     None) or [])
        self.coordinators = getattr(args, "coordinator", None) or []
        self.language  = getattr(args, "language",   None)
        self.statuses  = set(getattr(args, "status",      None) or [])
        self.unplaced      = getattr(args, "unplaced",       False)
        self.unfilled      = getattr(args, "unfilled",       False)
        self.no_coordinator = getattr(args, "no_coordinator", False)

    def describe(self) -> str:
        parts = []
        if self.semesters:   parts.append("semester=" + ",".join(str(s) for s in self.semesters))
        if self.years:       parts.append("year="     + ",".join(str(y) for y in sorted(self.years)))
        if self.companies:   parts.append("company="  + ",".join(self.companies))
        if self.programs:    parts.append("program="  + ",".join(sorted(self.programs)))
        if self.coordinators: parts.append("coordinator=" + ",".join(self.coordinators))
        if self.language:    parts.append(f"language={self.language}")
        if self.statuses:    parts.append("status="   + ",".join(sorted(self.statuses)))
        if self.unplaced:    parts.append("unplaced")
        if self.unfilled:    parts.append("unfilled")
        if self.no_coordinator: parts.append("no-coordinator")
        return "  ".join(parts)


# ── Filtering helpers ─────────────────────────────────────────────────────────

def _filter_students(students: list[dict], f: "_Filters", rows: list[dict]) -> list[dict]:
    from src.semester import parse as parse_sem
    result = students
    if f.semesters:
        result = [s for s in result if parse_sem(s.get("semester_start","")) in f.semesters]
    if f.years:
        result = [s for s in result
                  if _year_of(s.get("semester_start","")) in f.years]
    if f.programs:
        result = [s for s in result if s.get("program","") in f.programs]
    if "unassigned" in f.statuses:
        placed = {r["student_number"] for r in rows if r.get("status") in {"confirmed","completed"}}
        result = [s for s in result if s["student_number"] not in placed]
    student_statuses = f.statuses - {"proposed","confirmed","unassigned","closed"}
    if student_statuses:
        result = [s for s in result if s.get("status","") in student_statuses]
    return result


def _filter_projects(
    projects: list[dict], f: "_Filters", rows: list[dict],
    company_ids: set[str] | None, coord_emails: set[str] | None,
) -> list[dict]:
    from src.semester import parse as parse_sem
    result = projects
    if f.semesters:
        result = [p for p in result if parse_sem(p.get("semester","")) in f.semesters]
    if f.years:
        result = [p for p in result if _year_of(p.get("semester","")) in f.years]
    if company_ids is not None:
        result = [p for p in result if p.get("company_id","") in company_ids]
    if coord_emails is not None:
        result = [p for p in result
                  if any(e in p.get("coordinators",[]) for e in coord_emails)]
    if f.language:
        result = [p for p in result if p.get("language","") == f.language]
    project_statuses = f.statuses - {"proposed","confirmed","unassigned","completed"}
    if project_statuses:
        result = [p for p in result if p.get("status","") in project_statuses]
    return result


def _year_of(sem_str: str) -> int | None:
    from src.semester import parse as parse_sem
    s = parse_sem(sem_str)
    return s.year if s else None


def _hours_remaining(project: dict, rows: list[dict]) -> int:
    from src.store import project_fill
    fill = project_fill(project, rows)
    # A slot is open if any team still has capacity
    return fill["capacity_total"] - fill["filled_total"]


# ── Project table renderer ────────────────────────────────────────────────────

def _render_project_table(
    projects: list[dict],
    rows: list[dict],
    group_keys: list[str],
    args,
    console: Console,
) -> None:
    from src.store import load_json

    sort_by = getattr(args, "sort_by", None)

    # Sort
    if sort_by == "fill-rate":
        from src.store import project_fill as _pf
        projects = sorted(projects,
                          key=lambda p: _pf(p, rows)["fill_pct"],
                          reverse=True)
    elif sort_by == "semester":
        projects = sorted(projects, key=lambda p: p.get("semester",""))
    elif sort_by == "company":
        projects = sorted(projects, key=lambda p: p.get("company_id",""))
    elif sort_by == "name":
        projects = sorted(projects, key=lambda p: p.get("title",""))
    else:
        projects = sorted(projects, key=lambda p: (p.get("company_id",""), p.get("title","")))

    # Determine primary grouping
    primary_group = group_keys[0] if group_keys else "company"

    def _group_key(p: dict) -> str:
        if primary_group in ("year", "calendar"):
            year = _year_of(p.get("semester",""))
            return str(year) if year else "?"
        if primary_group == "academic-year":
            from src.semester import parse as parse_sem
            s = parse_sem(p.get("semester",""))
            return s.academic_year_label() if s else "?"
        if primary_group == "semester":
            return p.get("semester","?")
        if primary_group == "coordinator":
            coords = p.get("coordinators",[])
            if not coords:
                return "(none)"
            try:
                return load_json("coordinators", coords[0]).get("name", coords[0])
            except Exception:
                return coords[0]
        # default: company
        try:
            return load_json("companies", p.get("company_id","")).get("name", p.get("company_id","?"))
        except Exception:
            return p.get("company_id","?")

    # Build grouped dict preserving sort order
    grouped: dict[str, list[dict]] = {}
    for p in projects:
        k = _group_key(p)
        grouped.setdefault(k, []).append(p)

    title_suffix = f" (grouped by {primary_group})" if primary_group != "company" else ""

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
        title=f"Project fill status{title_suffix}",
        title_style="bold", title_justify="left",
    )
    table.add_column("Group",    style="dim",   min_width=18)
    table.add_column("Project",  style="white", min_width=28)
    table.add_column("Sem",      style="cyan",  width=12)
    table.add_column("Teams",    style="dim",   width=6,  justify="right")
    table.add_column("Fill",     min_width=22)
    table.add_column("Coords",   style="dim",   min_width=14)

    for group_name, group_projects in grouped.items():
        for i, p in enumerate(group_projects):
            from src.store import project_fill as _pf
            fill     = _pf(p, rows)
            n_teams  = fill["n_teams"]
            fill_bar_str = _rate_bar(fill["fill_pct"], width=14)

            coord_ids   = p.get("coordinators", [])
            coord_names = []
            for cid in coord_ids:
                try:
                    coord_names.append(
                        load_json("coordinators", cid).get("name", cid).split()[0]
                    )
                except Exception:
                    pass
            coords_str = ", ".join(coord_names) if coord_names else "[dim]—[/dim]"

            table.add_row(
                group_name if i == 0 else "",
                p.get("title", p["project_id"]),
                p.get("semester", ""),
                str(n_teams) if n_teams > 1 else "",
                fill_bar_str,
                coords_str,
            )

    console.print(table)
    console.print()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_load_all(kind: str) -> list[dict]:
    from src.store import list_ids, load_json
    result = []
    for eid in list_ids(kind):
        try:
            result.append(load_json(kind, eid))
        except Exception:
            pass
    return result


def _rate_bar(rate: float, width: int = 16) -> str:
    filled = round(rate * width)
    empty  = width - filled
    if rate >= 1.0:
        color = "yellow"
    elif rate >= 0.6:
        color = "green"
    else:
        color = "cyan"
    return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * empty}[/dim] [dim]{round(rate * 100):3d}%[/dim]"
