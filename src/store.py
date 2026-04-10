"""
store.py — read/write JSON metadata and assignments.csv.
Single source of truth for all persistence.
"""
import csv
import json
from pathlib import Path
import tomllib

with open("config.toml", "rb") as f:
    _CFG = tomllib.load(f)

PATHS = _CFG["paths"]

def _json_path(kind: str, entity_id: str) -> Path:
    return Path(PATHS[kind]) / f"{entity_id}.json"

def load_json(kind: str, entity_id: str) -> dict:
    return json.loads(_json_path(kind, entity_id).read_text())

def save_json(kind: str, entity_id: str, data: dict) -> None:
    path = _json_path(kind, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def list_ids(kind: str) -> list[str]:
    folder = Path(PATHS[kind])
    if not folder.exists():
        return []
    return [p.stem for p in folder.glob("*.json") if p.stem != "SCHEMA"]

def load_programs() -> list[dict]:
    with open(PATHS["programs"], newline="") as f:
        return list(csv.DictReader(f))

def save_programs(rows: list[dict]) -> None:
    with open(PATHS["programs"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "label_fr", "label_en", "active"])
        w.writeheader()
        w.writerows(rows)

def load_assignments() -> list[dict]:
    with open(PATHS["assignments"], newline="") as f:
        return list(csv.DictReader(f))

def append_assignment_rows(rows: list[dict]) -> None:
    path = Path(PATHS["assignments"])
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        fieldnames = [
            "assignment_id", "student_number", "student_email", "student_program",
            "project_id", "project_lead_email", "semester", "team", "task_id", "task_label",
            "hours_planned", "hours_committed", "status", "assigned_date",
            "confirmed_date", "completed_date", "notes"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerows(rows)

def rewrite_assignments(rows: list[dict]) -> None:
    """Overwrite the entire CSV — used for edits and cancellations."""
    path = Path(PATHS["assignments"])
    fieldnames = [
        "assignment_id", "student_number", "student_email", "student_program",
        "project_id", "project_lead_email", "semester", "team", "task_id", "task_label",
        "hours_planned", "hours_committed", "status", "assigned_date",
        "confirmed_date", "completed_date", "notes"
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def load_coordinators() -> list[dict]:
    """Return all coordinator metadata dicts, sorted by name."""
    ids = list_ids("coordinators")
    coords = []
    for cid in ids:
        try:
            coords.append(load_json("coordinators", cid))
        except Exception:
            pass
    return sorted(coords, key=lambda c: c.get("name", ""))

def default_coordinator() -> dict:
    """Return the default coordinator from config, or empty dict if not set."""
    import tomllib
    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)
    coord = cfg.get("coordinator", {})
    return coord if coord.get("name") or coord.get("email") else {}

def project_fill(project_meta: dict, rows: list[dict]) -> dict:
    """
    Compute fill state for a project, aware of competing teams.

    Returns:
      {
        "n_teams":       int,          # 1 for single-team
        "total_hours":   int,          # per-team capacity
        "teams": {
            "A": {"filled": int, "remaining": int, "students": [str, ...]},
            ...
        },
        "filled_total":  int,          # sum across all teams
        "capacity_total":int,          # total_hours × n_teams
        "fill_pct":      float,        # 0–1 across all teams
        "has_open_slot": bool,         # any team has remaining hours
      }
    """
    pid        = project_meta["project_id"]
    n_teams    = int(project_meta.get("teams", 1))
    total_hrs  = project_meta.get("capacity", {}).get("total_hours", 0)

    # Collect active rows for this project
    active = [
        r for r in rows
        if r["project_id"] == pid
        and r["status"] in {"proposed", "confirmed"}
    ]

    # Determine which team labels are actually in use
    if n_teams <= 1:
        labels = [""]
    else:
        used_labels = sorted({r.get("team", "A") for r in active})
        std_labels  = [chr(ord("A") + i) for i in range(n_teams)]
        # Ensure at least the standard labels are present
        labels = sorted(set(std_labels) | set(used_labels))

    teams: dict[str, dict] = {}
    for label in labels:
        team_rows = [r for r in active if r.get("team", "") == label]
        filled    = sum(int(r.get("hours_planned", 0)) for r in team_rows)
        students  = list({r["student_number"] for r in team_rows})
        teams[label] = {
            "filled":    filled,
            "remaining": max(0, total_hrs - filled),
            "students":  students,
        }

    filled_total   = sum(t["filled"]    for t in teams.values())
    capacity_total = total_hrs * max(1, n_teams)

    return {
        "n_teams":        n_teams,
        "total_hours":    total_hrs,
        "teams":          teams,
        "filled_total":   filled_total,
        "capacity_total": capacity_total,
        "fill_pct":       filled_total / capacity_total if capacity_total else 0,
        "has_open_slot":  any(t["remaining"] > 0 for t in teams.values()),
    }


def validate_semester(tag: str) -> bool:
    """Return True if tag can be parsed as a valid semester. See semester.py."""
    from src.semester import parse
    return parse(tag) is not None
