"""
suggest_teams.py — analyse student supply vs project capacity and suggest
a replica (competing team) count per project.

Algorithm
---------
For each active project in the target semester:

  1.  Build a competency profile for each of its tasks by embedding the
      task label + the competency text of every program whose internship
      skills overlap with that task (cosine similarity ≥ COMP_THRESHOLD).

  2.  For each active student, compute the max cosine similarity between
      their document embedding and any of the project's task embeddings.
      A student is "relevant" to the project if that score ≥ MATCH_THRESHOLD.

  3.  Relevant student hours are summed.  If relevant_hours == 0 the project
      is flagged as UNPLACEABLE.

  4.  suggested_teams = max(1, ceil(relevant_hours / project_total_hours))
      capped at MAX_TEAMS.

  5.  Display a table for approval.  For each project the coordinator can
      accept the suggestion, enter a different number, or type 0 to mark
      it unplaceable.

  6.  On approval, write teams: N to each project JSON.

Usage
-----
  innovhub suggest-teams --semester "Winter 2026"
  innovhub suggest-teams --semester "Winter 2026" --dry-run
"""
import math
from pathlib import Path
from rich.console import Console
from rich.table   import Table
from rich         import box

# Similarity threshold for a student to count as relevant to a project task
MATCH_THRESHOLD = 0.35   # intentionally low — we cast a wide net for supply
# Threshold above which a program's competency text is considered relevant
# to a task label (used to weight programs toward tasks)
COMP_THRESHOLD  = 0.30
# Hard cap on suggested teams (sanity limit)
MAX_TEAMS       = 8


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    from src.store import (
        list_ids, load_json, load_programs,
        program_competency_text, semester_program_info,
    )
    from src.embed  import embed_text, cosine_similarity, load_embedding
    from src.semester import parse as parse_sem

    console  = Console()
    semester = args.semester
    dry_run  = getattr(args, "dry_run", False)

    sem_obj = parse_sem(semester)
    if not sem_obj:
        console.print(f"  [red]Could not parse semester '{semester}'.[/red]")
        return
    semester_str = sem_obj.to_storage()

    console.print(f"\n  [bold]Team suggestion — {semester_str}[/bold]"
                  + ("  [dim][DRY RUN][/dim]" if dry_run else "") + "\n")

    # ── Load active students for the semester ─────────────────────────────────
    students = []
    no_emb = 0
    for sid in list_ids("students"):
        try:
            m = load_json("students", sid)
        except Exception:
            continue
        if m.get("status") != "active":
            continue
        if m.get("semester_start") != semester_str:
            continue
        emb_file = m.get("embedding_file", "")
        if emb_file and Path(emb_file).exists():
            vec = load_embedding(emb_file)
        else:
            # No CV embedding — fall back to the program competency text
            prog = m.get("program", "")
            prog_text = program_competency_text(prog, lang="fr")
            if not prog_text:
                no_emb += 1
                continue
            vec = embed_text(prog_text)
            no_emb += 1
        students.append({
            "id":      sid,
            "program": m.get("program", ""),
            "hours":   int(m.get("hours_available", 0)),
            "vec":     vec,
        })

    if not students:
        console.print(f"  [yellow]No active students found for {semester_str}.[/yellow]")
        return

    if no_emb:
        console.print(
            f"  {len(students)} active student(s) for {semester_str} "
            f"[dim]({no_emb} without CV embedding — using program profile)[/dim]."
        )
    else:
        console.print(f"  {len(students)} active student(s) for {semester_str}.")

    # ── Pre-embed competency profiles per program ─────────────────────────────
    programs    = load_programs()
    prog_codes  = [p["code"] for p in programs if p.get("active", "true") == "true"]
    console.print(f"  Embedding competency profiles for {len(prog_codes)} programs…", end="")
    prog_vecs: dict[str, object] = {}
    for code in prog_codes:
        text = program_competency_text(code, lang="fr")
        if text.strip():
            prog_vecs[code] = embed_text(text)
    console.print(" done.")

    # ── Load active projects for the semester ─────────────────────────────────
    projects = []
    for pid in list_ids("projects"):
        try:
            m = load_json("projects", pid)
        except Exception:
            continue
        if m.get("status") != "active":
            continue
        if m.get("semester") != semester_str:
            continue
        emb_file = m.get("embedding_file", "")
        if not emb_file or not Path(emb_file).exists():
            continue
        projects.append(m)

    if not projects:
        console.print(f"  [yellow]No active projects found for {semester_str}.[/yellow]")
        return

    console.print(f"  {len(projects)} active project(s).\n")

    # ── Analyse each project ──────────────────────────────────────────────────
    results = []   # (project_meta, suggestion, relevant_hours, relevant_students)

    for pmeta in projects:
        pid         = pmeta["project_id"]
        tasks       = pmeta.get("capacity", {}).get("tasks", [])
        total_hours = pmeta.get("capacity", {}).get("total_hours", 0)
        if not total_hours:
            continue

        p_vec = load_embedding(pmeta["embedding_file"])

        # Build per-task embeddings: task label + relevant program competency text
        task_vecs = []
        for task in tasks:
            label_text  = task["label"]
            # Find programs whose competency profile aligns with this task label
            task_label_vec = embed_text(label_text)
            supporting_texts = [label_text]
            for code, pvec in prog_vecs.items():
                if cosine_similarity(task_label_vec, pvec) >= COMP_THRESHOLD:
                    ct = program_competency_text(code, lang="fr")
                    if ct:
                        supporting_texts.append(ct)
            combined_vec = embed_text("\n".join(supporting_texts))
            task_vecs.append(combined_vec)

        # Fall back to project-level embedding if no tasks
        if not task_vecs:
            task_vecs = [p_vec]

        # Compute relevance of each student to this project
        relevant_hours    = 0
        relevant_students = []
        for s in students:
            # Student is relevant if they score above threshold on any task
            max_score = max(
                cosine_similarity(s["vec"], tv) for tv in task_vecs
            )
            if max_score >= MATCH_THRESHOLD:
                relevant_hours += s["hours"]
                relevant_students.append(s["id"])

        if relevant_hours == 0 or not relevant_students:
            suggestion = 0
            students_per_team = 0
        else:
            avg_hours = relevant_hours / len(relevant_students)
            students_per_team = max(1, math.ceil(total_hours / avg_hours))
            suggestion = min(MAX_TEAMS, max(1, math.floor(len(relevant_students) / students_per_team)))

        results.append({
            "meta":              pmeta,
            "suggestion":        suggestion,
            "relevant_hours":    relevant_hours,
            "n_relevant":        len(relevant_students),
            "students_per_team": students_per_team,
            "total_hours":       total_hours,
            "current_teams":     int(pmeta.get("teams", 1)),
        })

    # ── Display results table ─────────────────────────────────────────────────
    results.sort(key=lambda r: (r["suggestion"] == 0, -r["relevant_hours"]))

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold",
                  title="Suggested team counts", title_style="bold",
                  title_justify="left")
    table.add_column("#",            style="dim",   width=3,  justify="right")
    table.add_column("Project",      style="white", min_width=30)
    table.add_column("Capacity",     style="dim",   width=9,  justify="right")
    table.add_column("Relevant",     style="cyan",  width=9,  justify="right")
    table.add_column("Per team",     style="dim",   width=9,  justify="right")
    table.add_column("Current",      style="dim",   width=8,  justify="right")
    table.add_column("Suggested",    style="green", width=9,  justify="right")

    for i, r in enumerate(results, 1):
        title        = r["meta"].get("title", r["meta"]["project_id"])[:38]
        cap          = f"{r['total_hours']}h"
        relevant     = str(r["n_relevant"])
        per_team     = str(r.get("students_per_team", "—"))
        current      = str(r["current_teams"])

        if r["suggestion"] == 0:
            suggested = "[red]unplaceable[/red]"
        else:
            suggested = f"[bold green]{r['suggestion']}[/bold green]"

        table.add_row(str(i), title, cap, relevant, per_team, current, suggested)

    console.print(table)

    if dry_run:
        console.print("  [dim]Dry run — no changes written.[/dim]\n")
        return

    # ── Interactive approval ──────────────────────────────────────────────────
    console.print(
        "  For each project, press Enter to accept the suggestion,\n"
        "  enter a number to override, or 0 to mark unplaceable.\n"
    )

    approved: list[tuple[dict, int]] = []   # (pmeta, final_teams)

    for i, r in enumerate(results, 1):
        title     = r["meta"].get("title", r["meta"]["project_id"])[:50]
        suggested = r["suggestion"]
        sup_str   = "unplaceable" if suggested == 0 else str(suggested)
        prompt    = (
            f"  [{i}] {title}\n"
            f"      suggested={sup_str}"
            f"      suggested={sup_str}"
            f"  ({r['n_relevant']} relevant, ~{r['students_per_team']} per team)"
            f"  — accept? [Enter={sup_str} / number / 0=skip]: "
            f"  — accept? [Enter={sup_str} / number / 0=skip]: "
        )
        raw = input(prompt).strip()
        if raw == "":
            final = suggested
        else:
            try:
                final = max(0, int(raw))
            except ValueError:
                final = suggested
        approved.append((r["meta"], final))

    # ── Write to project JSON files ───────────────────────────────────────────
    from src.store import save_json, load_json as _lj
    written = 0
    for pmeta, final_teams in approved:
        pid  = pmeta["project_id"]
        data = _lj("projects", pid)
        data["teams"] = final_teams
        save_json("projects", pid, data)
        written += 1

    console.print(f"\n  [green]✓[/green]  Updated {written} project(s).\n")
    console.print(
        "  [dim]Run [bold]innovhub suggest-teams[/bold] again after ingesting"
        " more students to refine the counts.[/dim]\n"
    )
