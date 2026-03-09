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
    Main match command dispatcher.
    Full implementation will cover:
      - loading all embeddings of the target type into memory
      - computing cosine_similarity against the query embedding
      - filtering by status == active and hours_available > 0
      - excluding already-assigned pairs (active rows in assignments.csv)
      - returning a ranked list with scores, capacity info, and student hours
      - dispatching to explain() if --explain is passed
    """
    pass


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
    from src.store import load_json, load_assignments

    console = Console()
    meta    = load_json("projects", project_id)
    rows    = load_assignments()

    active_rows = [
        r for r in rows
        if r["project_id"] == project_id
        and r["status"] in {"proposed", "confirmed"}
    ]

    # Hours filled per task
    filled: dict[str, int] = {}
    students_on_task: dict[str, list[str]] = {}
    for r in active_rows:
        tid = r["task_id"]
        filled[tid]             = filled.get(tid, 0) + int(r["hours_planned"])
        students_on_task.setdefault(tid, []).append(r["student_number"])

    status_colour = {
        "active": "green", "inactive": "yellow", "closed": "red"
    }.get(meta["status"], "white")

    console.print(
        f"\n  [bold]{meta['title']}[/bold]"
        f"  ·  {meta['semester']}"
        f"  ·  [{status_colour}]{meta['status']}[/{status_colour}]"
    )
    try:
        company = load_json("companies", meta["company_id"])
        console.print(f"  {company['name']}  ·  Lead: {meta['lead_name']} <{meta['lead_email']}>\n")
    except Exception:
        console.print(f"  Lead: {meta['lead_name']} <{meta['lead_email']}>\n")

    tasks = meta["capacity"]["tasks"]
    total_hours   = sum(t["hours"] for t in tasks)
    total_filled  = sum(filled.get(t["task_id"], 0) for t in tasks)
    total_remain  = total_hours - total_filled

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Task",      style="white",   min_width=28)
    table.add_column("Total",     style="white",   justify="right")
    table.add_column("Filled",    style="cyan",    justify="right")
    table.add_column("Remaining", style="green",   justify="right")
    table.add_column("Students",  style="dim",     min_width=16)

    for t in tasks:
        tid       = t["task_id"]
        f         = filled.get(tid, 0)
        remain    = t["hours"] - f
        studs     = ", ".join(students_on_task.get(tid, [])) or "—"
        remain_colour = "green" if remain > 0 else "dim"
        table.add_row(
            t["label"],
            f"{t['hours']}h",
            f"{f}h",
            f"[{remain_colour}]{remain}h[/{remain_colour}]",
            studs,
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_hours}h[/bold]",
        f"[bold]{total_filled}h[/bold]",
        f"[bold]{total_remain}h[/bold]",
        "",
    )
    console.print(table)

    confirmed = len({r["assignment_id"] for r in active_rows if r["status"] == "confirmed"})
    proposed  = len({r["assignment_id"] for r in active_rows if r["status"] == "proposed"})
    console.print(
        f"  Assignments: [green]{confirmed} confirmed[/green]"
        f"  [yellow]{proposed} proposed[/yellow]\n"
    )


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


def run_list(args):
    """
    list command — tabulate students, projects, or companies.
    Full implementation will cover:
      - filtering by semester and active/inactive flag
      - rich table output
    """
    pass


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
