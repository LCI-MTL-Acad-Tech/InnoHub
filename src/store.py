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
            "project_id", "project_lead_email", "semester", "task_id", "task_label",
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
        "project_id", "project_lead_email", "semester", "task_id", "task_label",
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

def validate_semester(tag: str) -> bool:
    """Return True if tag matches expected format: YYYY-H or YYYY-A."""
    import re
    return bool(re.fullmatch(r"\d{4}-[HA]", tag.strip()))
