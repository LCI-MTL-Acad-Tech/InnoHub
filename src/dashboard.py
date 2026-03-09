"""
dashboard.py — CLI dashboard for placement rates and project fill status.

Reads directly from assignments.csv and metadata JSONs — no embeddings needed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from models import AssignmentStatus, ProjectStatus, StudentStatus
from store import (
    load_assignments,
    load_programs,
    load_project,
    list_projects,
    list_students,
)


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def _is_placed(student_id: str, assignments: list, semester: Optional[str]) -> bool:
    for a in assignments:
        if a.student_id != student_id:
            continue
        if semester and a.semester != semester:
            continue
        if a.status in (AssignmentStatus.PROPOSED, AssignmentStatus.CONFIRMED,
                        AssignmentStatus.COMPLETED):
            return True
    return False


def placement_by_program(semester: Optional[str] = None) -> list[dict]:
    """
    Return placement stats grouped by program code.
    Each row: {program, total, placed, rate_pct}
    """
    students = list_students()
    assignments = load_assignments()
    programs = {p.code: p for p in load_programs()}

    # Filter students by semester if requested
    if semester:
        students = [s for s in students if s.semester == semester]

    counts: dict[str, dict] = defaultdict(lambda: {"total": 0, "placed": 0})

    for student in students:
        if student.status == StudentStatus.COMPLETED:
            # Count completed students as placed
            counts[student.program]["total"] += 1
            counts[student.program]["placed"] += 1
            continue
        if student.status == StudentStatus.INACTIVE:
            # Inactive students are excluded from placement rate
            continue
        counts[student.program]["total"] += 1
        if _is_placed(student.student_id, assignments, semester):
            counts[student.program]["placed"] += 1

    rows = []
    for code in sorted(counts.keys()):
        total = counts[code]["total"]
        placed = counts[code]["placed"]
        rate = (placed / total * 100) if total > 0 else 0.0
        prog = programs.get(code)
        rows.append({
            "program":  code,
            "label":    prog.label_fr if prog else code,
            "total":    total,
            "placed":   placed,
            "unplaced": total - placed,
            "rate_pct": round(rate, 1),
        })
    return rows


def project_fill_status(semester: Optional[str] = None) -> list[dict]:
    """
    Return fill status for each active project.
    Each row: {project_id, title, company_id, semester, capacity, used, pct}
    """
    from store import get_slots_used, load_company

    rows = []
    for project in list_projects():
        if project.status != ProjectStatus.ACTIVE:
            continue
        if semester and project.semester != semester:
            continue
        used = get_slots_used(project.project_id)
        cap  = project.capacity
        pct  = round(used / cap * 100) if cap > 0 else 0
        company = load_company(project.company_id)
        rows.append({
            "project_id":   project.project_id,
            "title":        project.title,
            "company_name": company.name if company else project.company_id,
            "semester":     project.semester,
            "capacity":     cap,
            "used":         used,
            "pct":          pct,
        })
    rows.sort(key=lambda r: r["pct"])
    return rows


def unplaced_students(semester: Optional[str] = None) -> list[dict]:
    """Return active students with no current assignment."""
    students = list_students()
    assignments = load_assignments()

    rows = []
    for student in students:
        if student.status != StudentStatus.ACTIVE:
            continue
        if semester and student.semester != semester:
            continue
        if not _is_placed(student.student_id, assignments, semester):
            rows.append({
                "student_id": student.student_id,
                "name":       student.name,
                "program":    student.program,
                "semester":   student.semester,
                "email":      student.email,
            })
    rows.sort(key=lambda r: (r["program"], r["name"]))
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render_dashboard(semester: Optional[str] = None) -> str:
    header = f"  InnovHub — Placement Dashboard"
    if semester:
        header += f"  [{semester}]"
    lines = [
        "",
        header,
        "═" * 70,
        "",
        "PLACEMENT RATE BY PROGRAM",
        "─" * 70,
        f"  {'PROGRAM':<8}  {'LABEL':<35}  {'PLACED':>6}  {'TOTAL':>5}  {'RATE':>6}",
        "─" * 70,
    ]

    for row in placement_by_program(semester):
        bar = _bar(row["rate_pct"], width=15)
        lines.append(
            f"  {row['program']:<8}  {row['label']:<35}  "
            f"{row['placed']:>3}/{row['total']:<3}  "
            f"{row['rate_pct']:>5.1f}%  {bar}"
        )

    lines += [
        "",
        "PROJECT FILL STATUS (active projects)",
        "─" * 70,
        f"  {'SLOTS':<7}  {'PROJECT':<35}  {'COMPANY':<20}  SEM",
        "─" * 70,
    ]

    for row in project_fill_status(semester):
        slots_str = f"{row['used']}/{row['capacity']}"
        bar = _bar(row["pct"], width=10)
        lines.append(
            f"  {slots_str:<7}  {row['title'][:35]:<35}  "
            f"{row['company_name'][:20]:<20}  {row['semester']}"
            f"  {bar}"
        )

    unplaced = unplaced_students(semester)
    lines += [
        "",
        f"UNPLACED STUDENTS ({len(unplaced)})",
        "─" * 70,
        f"  {'ID':<10}  {'NAME':<25}  {'PROGRAM':<8}  {'EMAIL'}",
        "─" * 70,
    ]
    if unplaced:
        for row in unplaced:
            lines.append(
                f"  {row['student_id']:<10}  {row['name'][:25]:<25}  "
                f"{row['program']:<8}  {row['email']}"
            )
    else:
        lines.append("  All active students have been placed.")

    lines.append("")
    return "\n".join(lines)
