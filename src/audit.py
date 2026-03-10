"""
audit.py — append-only transaction log.

Writes one JSON object per line to data/audit.log.
Every entry has: ts, action, entity, id, user — plus action-specific fields.

Actions logged:
  ingest      — document(s) added for an entity
  replace     — document(s) replaced for an entity (old files listed)
  purge       — documents deleted from disk (complete/close operations)
  assign      — student assigned to project
  confirm     — assignment confirmed
  cancel      — assignment cancelled
  edit        — assignment hours edited
  activate    — entity reactivated
  deactivate  — entity deactivated
  close       — project closed
  complete    — student marked completed
  reassign    — student moved to new semester
  add_program — new program code added
  add_coordinator — coordinator ingested
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _log_path() -> Path:
    import tomllib
    try:
        with open("config.toml", "rb") as f:
            cfg = tomllib.load(f)
        return Path(cfg["paths"]["data"]) / "audit.log"
    except Exception:
        return Path("data/audit.log")


def _user() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(action: str, entity: str, entity_id: str, **kwargs) -> None:
    """
    Append one log entry. Extra kwargs are merged into the record.
    Safe to call even if the log file or data/ directory doesn't exist yet.
    """
    entry = {
        "ts":     _ts(),
        "action": action,
        "entity": entity,
        "id":     entity_id,
        "user":   _user(),
        **kwargs,
    }
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never let logging break normal operation


def load_log(
    action: str | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
) -> list[dict]:
    """
    Read and optionally filter the audit log.
    Returns list of entry dicts, newest last.
    """
    path = _log_path()
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if action    and entry.get("action") != action:
                continue
            if entity    and entry.get("entity") != entity:
                continue
            if entity_id and entry.get("id")     != entity_id:
                continue
            entries.append(entry)
    return entries
