"""
dashboard_web.py — local HTTP server for the visual dashboard.
Binds to 127.0.0.1 only. No external dependencies beyond Python stdlib.
"""
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


# ── API data builders ─────────────────────────────────────────────────────────

def _build_status(filters: dict) -> dict:
    from src.store import list_ids, load_json, load_assignments
    rows = load_assignments()
    if filters["semesters"]:
        rows = [r for r in rows if r.get("semester") in filters["semesters"]]

    students  = list_ids("students")
    projects  = list_ids("projects")
    companies = list_ids("companies")
    coords    = list_ids("coordinators")

    active_students  = sum(1 for s in students  if load_json("students",  s).get("status") == "active")
    active_projects  = sum(1 for p in projects  if load_json("projects",  p).get("status") == "active")
    active_companies = sum(1 for c in companies if load_json("companies", c).get("status") == "active")

    by_status: dict[str, int] = {}
    for r in rows:
        s = r.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "students":  {"total": len(students),  "active": active_students},
        "projects":  {"total": len(projects),  "active": active_projects},
        "companies": {"total": len(companies), "active": active_companies},
        "coordinators": {"total": len(coords)},
        "assignments": by_status,
    }


def _build_programs(filters: dict) -> list[dict]:
    from src.store import list_ids, load_json, load_assignments, load_programs
    rows     = load_assignments()
    programs = load_programs()
    if filters["semesters"]:
        rows = [r for r in rows if r.get("semester") in filters["semesters"]]

    code_totals:  dict[str, int] = {}
    code_placed:  dict[str, int] = {}

    for sid in list_ids("students"):
        meta = load_json("students", sid)
        if meta.get("status") == "completed":
            continue
        code = meta.get("program", "?")
        code_totals[code] = code_totals.get(code, 0) + 1

    for r in rows:
        if r.get("status") in {"confirmed", "completed"}:
            code = r.get("student_program", "?")
            code_placed[code] = code_placed.get(code, 0) + 1

    prog_labels = {p["code"]: p.get("label_fr", p["code"]) for p in programs}

    result = []
    for code, total in sorted(code_totals.items()):
        placed = code_placed.get(code, 0)
        result.append({
            "code":    code,
            "label":   prog_labels.get(code, code),
            "total":   total,
            "placed":  min(placed, total),
            "rate":    round(min(placed, total) / total, 3) if total else 0,
        })
    return result


def _safe_load(kind: str, eid: str, default=None):
    from src.store import load_json
    try:
        return load_json(kind, eid)
    except Exception:
        return default if default is not None else {}


def _build_students(filters: dict) -> list[dict]:
    from src.store import list_ids, load_json, load_assignments
    rows   = load_assignments()
    result = []
    for sid in list_ids("students"):
        try:
            meta = load_json("students", sid)
        except Exception:
            continue
        if filters["semesters"] and meta.get("semester_start","") not in filters["semesters"]:
            continue
        if filters["programs"] and meta.get("program","") not in filters["programs"]:
            continue
        if filters["statuses"] and meta.get("status","") not in filters["statuses"]:
            continue
        active_rows = [
            r for r in rows
            if r["student_number"] == sid
            and r["status"] in {"proposed", "confirmed"}
        ]
        hours_committed = sum(int(r.get("hours_planned", 0)) for r in active_rows)
        placed = any(r["status"] in {"confirmed","completed"} for r in rows if r["student_number"] == sid)
        if filters.get("unplaced") and placed:
            continue
        result.append({
            "student_number":  sid,
            "name":            meta.get("name", ""),
            "email":           meta.get("email", ""),
            "program":         meta.get("program", ""),
            "semester":        meta.get("semester_start", ""),
            "hours_available": meta.get("hours_available", 0),
            "hours_committed": hours_committed,
            "hours_remaining": meta.get("hours_available", 0) - hours_committed,
            "status":          meta.get("status", ""),
            "assignments":     len({r["assignment_id"] for r in active_rows}),
            "placed":          placed,
        })
    result.sort(key=lambda x: x["name"])
    return result


def _build_companies(filters: dict) -> list[dict]:
    from src.store import list_ids, load_json
    result = []
    for cid in list_ids("companies"):
        try:
            meta = load_json("companies", cid)
        except Exception:
            continue
        if filters["language"] and meta.get("language","") != filters["language"]:
            continue
        if filters["statuses"] and meta.get("status","") not in filters["statuses"]:
            continue
        projects = [
            _safe_load("projects", pid)
            for pid in list_ids("projects")
            if _safe_load("projects", pid, {}).get("company_id") == cid
            and (not filters["semesters"] or _safe_load("projects", pid, {}).get("semester","") in filters["semesters"])
            and _safe_load("projects", pid, {}).get("status") != "closed"
        ]
        result.append({
            "company_id":    cid,
            "name":          meta.get("name", ""),
            "status":        meta.get("status", ""),
            "language":      meta.get("language", ""),
            "contact_name":  meta.get("contact_name", ""),
            "contact_email": meta.get("contact_email", ""),
            "project_count":   len(projects),
            "active_projects": sum(1 for p in projects if p.get("status") == "active"),
        })
    result.sort(key=lambda x: x["name"])
    return result


def _build_projects(filters: dict) -> list[dict]:
    from src.store import list_ids, load_json, load_assignments, project_fill as _pf
    rows   = load_assignments()
    result = []
    for pid in list_ids("projects"):
        try:
            meta = load_json("projects", pid)
        except Exception:
            continue
        if filters["semesters"] and meta.get("semester","") not in filters["semesters"]:
            continue
        if filters["companies"] and meta.get("company_id","") not in filters["companies"]:
            continue
        if filters["coordinators"] and not any(
            e in meta.get("coordinators",[]) for e in filters["coordinators"]
        ):
            continue
        if filters["language"] and meta.get("language","") != filters["language"]:
            continue
        if filters["statuses"] and meta.get("status","") not in filters["statuses"]:
            continue
        if filters.get("no_coord") and meta.get("coordinators"):
            continue
        try:
            company = load_json("companies", meta["company_id"]).get("name", meta["company_id"])
        except Exception:
            company = meta.get("company_id", "")

        fill = _pf(meta, rows)

        if filters.get("unfilled") and not fill["has_open_slot"]:
            continue

        coord_ids   = meta.get("coordinators", [])
        coord_names = []
        for cid in coord_ids:
            try:
                coord_names.append(load_json("coordinators", cid).get("name", cid))
            except Exception:
                pass

        # Per-team summary for the web UI
        teams_data = {
            label: {
                "filled":    td["filled"],
                "remaining": td["remaining"],
                "students":  td["students"],
            }
            for label, td in fill["teams"].items()
        }

        result.append({
            "project_id":      pid,
            "title":           meta.get("title", ""),
            "company":         company,
            "company_id":      meta.get("company_id", ""),
            "semester":        meta.get("semester", ""),
            "status":          meta.get("status", ""),
            "language":        meta.get("language", ""),
            "n_teams":         fill["n_teams"],
            "total_hours":     fill["total_hours"],
            "filled_hours":    fill["filled_total"],
            "capacity_total":  fill["capacity_total"],
            "fill_pct":        round(fill["fill_pct"], 3),
            "has_open_slot":   fill["has_open_slot"],
            "teams":           teams_data,
            "tasks":           meta.get("capacity", {}).get("tasks", []),
            "lead_name":       meta.get("lead_name", ""),
            "lead_email":      meta.get("lead_email", ""),
            "coordinators":    coord_names,
            "has_coordinator": bool(coord_ids),
        })
    result.sort(key=lambda x: (x["company"], x["title"]))
    return result


def _build_semesters(group_by: str = "calendar") -> list[dict]:
    """Return all known semesters grouped and sorted."""
    from src.store import load_assignments
    from src.semester import parse as parse_sem, group_by_calendar, group_by_academic

    all_strings = {r.get("semester", "") for r in load_assignments() if r.get("semester")}
    all_sems    = [s for s in (parse_sem(x) for x in all_strings) if s]

    groups = group_by_academic(all_sems) if group_by == "academic" \
             else group_by_calendar(all_sems)

    result = []
    for group_key, sems in groups.items():
        result.append({
            "group":     str(group_key),
            "semesters": [{"term": s.term, "year": s.year,
                           "label": s.to_storage(), "short": s.to_short()} for s in sems],
        })
    return result


def _build_assignments(filters: dict) -> list[dict]:
    from src.store import load_assignments
    rows = load_assignments()
    if filters["semesters"]:
        rows = [r for r in rows if r.get("semester","") in filters["semesters"]]
    if filters["programs"]:
        rows = [r for r in rows if r.get("student_program","") in filters["programs"]]
    if filters["statuses"]:
        rows = [r for r in rows if r.get("status","") in filters["statuses"]]
    return rows


def _serve_document(kind: str, entity_id: str):
    """Return (bytes, mime_type) for a source document, or (None, None)."""
    from src.store import load_json, PATHS
    try:
        meta = load_json(kind, entity_id)
        docs = meta.get("documents", [])
        if not docs:
            return None, None
        doc_path = Path(PATHS["documents"]) / kind / docs[0]["filename"]
        if not doc_path.exists():
            return None, None
        mime = mimetypes.guess_type(str(doc_path))[0] or "application/octet-stream"
        return doc_path.read_bytes(), mime
    except Exception:
        return None, None


# ── HTTP handler ──────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):

    semester_filter: str | None = None   # stored as canonical "Fall 2024" string
    group_by: str = "calendar"           # "calendar" or "academic"

    def log_message(self, format, *args):
        pass  # suppress default access log

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed  = urlparse(self.path)
        path    = parsed.path.rstrip("/")
        qs      = parse_qs(parsed.query)
        filters = _parse_request_filters(qs, self.__class__.semester_filter)

        try:
            if path in ("", "/"):
                self._html(_DASHBOARD_HTML.encode())
            elif path == "/api/status":
                data = _build_status(filters)
                data["group_by"] = qs.get("group_by", [self.__class__.group_by])[0]
                data["semester"] = self.__class__.semester_filter or ""
                self._json(data)
            elif path == "/api/programs":
                self._json(_build_programs(filters))
            elif path == "/api/students":
                self._json(_build_students(filters))
            elif path == "/api/companies":
                self._json(_build_companies(filters))
            elif path == "/api/projects":
                self._json(_build_projects(filters))
            elif path == "/api/semesters":
                group_by = qs.get("group_by", [self.__class__.group_by])[0]
                self._json(_build_semesters(group_by))
            elif path == "/api/assignments":
                self._json(_build_assignments(filters))
            elif path == "/api/filters":
                self._json(_build_filter_options())
            elif path.startswith("/api/document/"):
                parts = path.split("/")  # ['','api','document',kind,entity_id]
                if len(parts) == 5:
                    data, mime = _serve_document(parts[3], parts[4])
                    if data:
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self._not_found()
                else:
                    self._not_found()
            else:
                self._not_found()
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)


def _parse_request_filters(qs: dict, server_semester: str | None) -> dict:
    """
    Build a normalised filter dict from URL query string parameters.
    All values are lists for consistency.
    """
    from src.semester import parse as parse_sem

    semesters = []
    for s in qs.get("semester", []):
        sem = parse_sem(s)
        if sem:
            semesters.append(sem.to_storage())
    if server_semester and not semesters:
        semesters = [server_semester]

    return {
        "semesters":    semesters,
        "years":        [int(y) for y in qs.get("year", []) if y.isdigit()],
        "companies":    qs.get("company",     []),
        "programs":     qs.get("program",     []),
        "coordinators": qs.get("coordinator", []),
        "language":     qs.get("language",    [None])[0],
        "statuses":     qs.get("status",      []),
        "unplaced":     qs.get("unplaced",    [""])[0] == "1",
        "unfilled":     qs.get("unfilled",    [""])[0] == "1",
        "no_coord":     qs.get("no_coord",    [""])[0] == "1",
        "sort_by":      qs.get("sort_by",     [None])[0],
        "group_by":     qs.get("group_by",    [None])[0],
    }


def _apply_filters(items: list[dict], filters: dict, kind: str) -> list[dict]:
    """Apply shared filters to a list of entity dicts."""
    from src.semester import parse as parse_sem

    result = items

    if filters["semesters"]:
        if kind == "students":
            result = [x for x in result if x.get("semester_start","") in filters["semesters"]]
        else:
            result = [x for x in result if x.get("semester","") in filters["semesters"]]

    if filters["years"]:
        def _year(x):
            key = "semester_start" if kind == "students" else "semester"
            s = parse_sem(x.get(key, ""))
            return s.year if s else None
        result = [x for x in result if _year(x) in filters["years"]]

    if filters["programs"] and kind == "students":
        result = [x for x in result if x.get("program","") in filters["programs"]]

    if filters["companies"] and kind == "projects":
        result = [x for x in result if x.get("company_id","") in filters["companies"]]

    if filters["coordinators"] and kind == "projects":
        result = [x for x in result
                  if any(e in x.get("coordinators",[]) for e in filters["coordinators"])]

    if filters["language"] and kind in ("projects", "companies"):
        result = [x for x in result if x.get("language","") == filters["language"]]

    if filters["no_coord"] and kind == "projects":
        result = [x for x in result if not x.get("coordinators")]

    return result


def _build_filter_options() -> dict:
    """Return all available values for each filter dimension."""
    from src.store import list_ids, load_json, load_assignments, load_programs

    semesters = set()
    years     = set()
    companies = {}
    programs  = set()
    coords    = {}
    languages = set()

    for sid in list_ids("students"):
        try:
            m = load_json("students", sid)
            sem = m.get("semester_start","")
            if sem:
                semesters.add(sem)
                from src.semester import parse as ps
                s = ps(sem)
                if s: years.add(s.year)
            if m.get("program"):
                programs.add(m["program"])
        except Exception:
            pass

    for pid in list_ids("projects"):
        try:
            m = load_json("projects", pid)
            sem = m.get("semester","")
            if sem:
                semesters.add(sem)
                from src.semester import parse as ps
                s = ps(sem)
                if s: years.add(s.year)
            if m.get("language"):
                languages.add(m["language"])
        except Exception:
            pass

    for cid in list_ids("companies"):
        try:
            m = load_json("companies", cid)
            companies[cid] = m.get("name", cid)
        except Exception:
            pass

    for email in list_ids("coordinators"):
        try:
            m = load_json("coordinators", email)
            coords[email] = m.get("name", email)
        except Exception:
            pass

    prog_labels = {p["code"]: p.get("label_fr", p["code"])
                   for p in load_programs()}

    return {
        "semesters":    sorted(semesters),
        "years":        sorted(years),
        "companies":    [{"id": k, "name": v} for k, v in sorted(companies.items(), key=lambda x: x[1])],
        "programs":     [{"code": c, "label": prog_labels.get(c, c)} for c in sorted(programs)],
        "coordinators": [{"email": k, "name": v} for k, v in sorted(coords.items(), key=lambda x: x[1])],
        "languages":    sorted(languages),
    }


# ── Embedded dashboard HTML ───────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Innovation Hub</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

  :root {
    --bg:       #0f1117;
    --surface:  #181c27;
    --border:   #252b3b;
    --accent:   #4fffb0;
    --accent2:  #4b8eff;
    --warn:     #ffb84b;
    --danger:   #ff5e5e;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --mono:     'DM Mono', monospace;
    --sans:     'DM Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    min-height: 100vh;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 32px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  header h1 {
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 500;
    letter-spacing: .08em;
    color: var(--accent);
  }

  #sem-badge {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
  }

  nav {
    display: flex;
    gap: 2px;
    padding: 12px 32px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  nav button {
    background: none;
    border: none;
    color: var(--muted);
    font-family: var(--sans);
    font-size: 13px;
    font-weight: 500;
    padding: 6px 14px;
    border-radius: 4px;
    cursor: pointer;
    transition: all .15s;
  }

  nav button:hover  { color: var(--text); background: var(--border); }
  nav button.active { color: var(--accent); background: rgba(79,255,176,.08); }

  main { padding: 28px 32px; }

  /* stat cards */
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 14px;
    margin-bottom: 28px;
  }

  .stat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
  }

  .stat .val {
    font-family: var(--mono);
    font-size: 32px;
    font-weight: 500;
    color: var(--accent);
    line-height: 1;
  }

  .stat .lbl {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .1em;
    color: var(--muted);
    margin-top: 6px;
  }

  /* tables */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 20px;
  }

  .card-header {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .1em;
    color: var(--muted);
    font-weight: 600;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th {
    text-align: left;
    padding: 10px 16px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    font-weight: 500;
  }

  td {
    padding: 10px 16px;
    border-bottom: 1px solid rgba(37,43,59,.6);
    font-size: 13px;
    vertical-align: middle;
  }

  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,.02); }

  .mono { font-family: var(--mono); font-size: 12px; }

  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 500;
    font-family: var(--mono);
  }

  .badge-active   { background: rgba(79,255,176,.12);  color: var(--accent); }
  .badge-inactive { background: rgba(255,184,75,.12);  color: var(--warn); }
  .badge-closed   { background: rgba(255,94,94,.12);   color: var(--danger); }
  .badge-completed{ background: rgba(100,116,139,.15); color: var(--muted); }
  .badge-proposed { background: rgba(75,142,255,.12);  color: var(--accent2); }
  .badge-confirmed{ background: rgba(79,255,176,.12);  color: var(--accent); }

  /* fill bar */
  .fill-wrap { display:flex; align-items:center; gap:10px; min-width:120px; }
  .fill-bar  { flex:1; height:4px; background:var(--border); border-radius:2px; overflow:hidden; }
  .fill-bar-inner { height:100%; border-radius:2px; background:var(--accent); transition:width .4s; }
  .fill-pct  { font-family:var(--mono); font-size:11px; color:var(--muted); min-width:32px; text-align:right; }

  /* program placement bars */
  .prog-row { display:flex; align-items:center; gap:14px; padding:12px 20px; border-bottom:1px solid rgba(37,43,59,.6); }
  .prog-row:last-child { border-bottom:none; }
  .prog-code { font-family:var(--mono); font-size:12px; min-width:72px; }
  .prog-label{ color:var(--muted); font-size:12px; flex:1; }
  .prog-bar  { width:180px; height:6px; background:var(--border); border-radius:3px; overflow:hidden; }
  .prog-bar-inner { height:100%; border-radius:3px; background:var(--accent); transition:width .4s; }
  .prog-pct  { font-family:var(--mono); font-size:12px; min-width:44px; text-align:right; }
  .prog-nums { font-family:var(--mono); font-size:11px; color:var(--muted); min-width:56px; text-align:right; }

  .doc-link {
    color: var(--accent2);
    text-decoration: none;
    font-size: 11px;
    font-family: var(--mono);
  }
  .doc-link:hover { text-decoration: underline; }

  .hidden { display: none; }

  .empty { padding: 28px 20px; color: var(--muted); font-size: 13px; }

  .loading { padding: 48px; text-align:center; color:var(--muted); font-family:var(--mono); font-size:13px; }

  /* Filter bar */
  #filter-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 32px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
    gap: 12px;
    flex-wrap: wrap;
  }

  #filter-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  #filter-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  #filter-bar select {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--sans);
    font-size: 12px;
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    max-width: 140px;
  }

  #filter-bar select:focus { outline: none; border-color: var(--accent2); }

  #filter-bar select.active-filter {
    border-color: var(--accent);
    color: var(--accent);
  }

  .filter-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    white-space: nowrap;
  }

  .filter-toggle input { accent-color: var(--accent); cursor: pointer; }
  .filter-toggle:has(input:checked) { color: var(--accent); }

  #active-filter-count {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--accent);
    background: rgba(79,255,176,.1);
    padding: 2px 8px;
    border-radius: 3px;
  }

  #clear-filters-btn, #groupby-btn {
    background: none;
    border: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
  }

  #clear-filters-btn:hover { border-color: var(--danger); color: var(--danger); }
  #groupby-btn:hover { border-color: var(--accent2); color: var(--accent2); }
</style>
</head>
<body>

<header>
  <h1>◈ INNOVATION HUB</h1>
  <span id="sem-badge">loading…</span>
</header>

<!-- FILTER BAR -->
<div id="filter-bar">
  <div id="filter-controls">
    <select id="f-semester" onchange="applyFilters()" title="Semester">
      <option value="">All semesters</option>
    </select>
    <select id="f-year" onchange="applyFilters()" title="Year">
      <option value="">All years</option>
    </select>
    <select id="f-company" onchange="applyFilters()" title="Company">
      <option value="">All companies</option>
    </select>
    <select id="f-program" onchange="applyFilters()" title="Program">
      <option value="">All programs</option>
    </select>
    <select id="f-coordinator" onchange="applyFilters()" title="Coordinator">
      <option value="">All coordinators</option>
    </select>
    <select id="f-language" onchange="applyFilters()" title="Language">
      <option value="">All languages</option>
      <option value="fr">French</option>
      <option value="en">English</option>
    </select>
    <select id="f-status" onchange="applyFilters()" title="Status">
      <option value="">All statuses</option>
      <option value="active">Active</option>
      <option value="inactive">Inactive</option>
      <option value="confirmed">Confirmed</option>
      <option value="proposed">Proposed</option>
      <option value="unassigned">Unassigned</option>
      <option value="closed">Closed</option>
      <option value="completed">Completed</option>
    </select>
    <label class="filter-toggle" title="Unplaced students only">
      <input type="checkbox" id="f-unplaced" onchange="applyFilters()"> Unplaced
    </label>
    <label class="filter-toggle" title="Unfilled projects only">
      <input type="checkbox" id="f-unfilled" onchange="applyFilters()"> Unfilled
    </label>
    <label class="filter-toggle" title="Projects with no coordinator">
      <input type="checkbox" id="f-no-coord" onchange="applyFilters()"> No coord
    </label>
  </div>
  <div id="filter-right">
    <span id="active-filter-count" class="hidden"></span>
    <button id="clear-filters-btn" class="hidden" onclick="clearFilters()">✕ Clear</button>
    <button id="groupby-btn" onclick="toggleGroupBy()">group: calendar</button>
  </div>
</div>

<nav>
  <button class="active" onclick="show('overview')">Overview</button>
  <button onclick="show('programs')">Programs</button>
  <button onclick="show('students')">Students</button>
  <button onclick="show('projects')">Projects</button>
  <button onclick="show('companies')">Companies</button>
  <button onclick="show('assignments')">Assignments</button>
</nav>

<main>

<!-- OVERVIEW -->
<div id="view-overview">
  <div class="stats" id="stat-cards">
    <div class="loading">Loading…</div>
  </div>
  <div class="card" style="margin-bottom:20px">
    <div class="card-header">Semesters</div>
    <div id="semester-groups"><div class="loading">Loading…</div></div>
  </div>
  <div class="card">
    <div class="card-header">Placement by program</div>
    <div id="prog-summary"><div class="loading">Loading…</div></div>
  </div>
</div>

<!-- PROGRAMS -->
<div id="view-programs" class="hidden">
  <div class="card">
    <div class="card-header">Program placement rates</div>
    <div id="prog-detail"><div class="loading">Loading…</div></div>
  </div>
</div>

<!-- STUDENTS -->
<div id="view-students" class="hidden">
  <div class="card">
    <div class="card-header">Students</div>
    <div id="students-table"><div class="loading">Loading…</div></div>
  </div>
</div>

<!-- PROJECTS -->
<div id="view-projects" class="hidden">
  <div class="card">
    <div class="card-header">Projects</div>
    <div id="projects-table"><div class="loading">Loading…</div></div>
  </div>
</div>

<!-- COMPANIES -->
<div id="view-companies" class="hidden">
  <div class="card">
    <div class="card-header">Companies</div>
    <div id="companies-table"><div class="loading">Loading…</div></div>
  </div>
</div>

<!-- ASSIGNMENTS -->
<div id="view-assignments" class="hidden">
  <div class="card">
    <div class="card-header">Assignment log</div>
    <div id="assignments-table"><div class="loading">Loading…</div></div>
  </div>
</div>

</main>

<script>
let _groupBy   = 'calendar';
let _activeFilters = {};
let _currentView   = 'overview';
let _cache         = {};

function _qs() {
  const f = _activeFilters;
  const p = new URLSearchParams();
  if (f.semester)    p.set('semester', f.semester);
  if (f.year)        p.set('year', f.year);
  if (f.company)     p.set('company', f.company);
  if (f.program)     p.set('program', f.program);
  if (f.coordinator) p.set('coordinator', f.coordinator);
  if (f.language)    p.set('language', f.language);
  if (f.status)      p.set('status', f.status);
  if (f.unplaced)    p.set('unplaced', '1');
  if (f.unfilled)    p.set('unfilled', '1');
  if (f.no_coord)    p.set('no_coord', '1');
  p.set('group_by', _groupBy);
  const s = p.toString();
  return s ? '?' + s : '';
}

function _readFilters() {
  return {
    semester:    document.getElementById('f-semester').value,
    year:        document.getElementById('f-year').value,
    company:     document.getElementById('f-company').value,
    program:     document.getElementById('f-program').value,
    coordinator: document.getElementById('f-coordinator').value,
    language:    document.getElementById('f-language').value,
    status:      document.getElementById('f-status').value,
    unplaced:    document.getElementById('f-unplaced').checked,
    unfilled:    document.getElementById('f-unfilled').checked,
    no_coord:    document.getElementById('f-no-coord').checked,
  };
}

function _markActiveSelects(f) {
  ['semester','year','company','program','coordinator','language','status'].forEach(key => {
    const el = document.getElementById('f-' + key);
    if (el) el.classList.toggle('active-filter', !!f[key]);
  });
  const count = [f.semester,f.year,f.company,f.program,f.coordinator,
                 f.language,f.status,f.unplaced,f.unfilled,f.no_coord]
    .filter(Boolean).length;
  const badge = document.getElementById('active-filter-count');
  const clear  = document.getElementById('clear-filters-btn');
  if (count > 0) {
    badge.textContent = count + ' filter' + (count > 1 ? 's' : '');
    badge.classList.remove('hidden');
    clear.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
    clear.classList.add('hidden');
  }
}

async function applyFilters() {
  _activeFilters = _readFilters();
  _markActiveSelects(_activeFilters);
  _cache = {};
  document.getElementById('sem-badge').textContent =
    _activeFilters.semester ? 'semester: ' + _activeFilters.semester : 'all semesters';
  await render(_currentView);
}

function clearFilters() {
  ['semester','year','company','program','coordinator','language','status'].forEach(key => {
    const el = document.getElementById('f-' + key);
    if (el) el.value = '';
  });
  ['unplaced','unfilled','no-coord'].forEach(key => {
    const el = document.getElementById('f-' + key);
    if (el) el.checked = false;
  });
  applyFilters();
}

async function api(endpoint) {
  const qs  = _qs();
  const sep = endpoint.includes('?') ? '&' : qs;
  const url = endpoint + sep;
  if (_cache[url]) return _cache[url];
  const r = await fetch(url);
  const d = await r.json();
  _cache[url] = d;
  return d;
}

async function populateFilterOptions() {
  try {
    const opts = await fetch('/api/filters').then(r => r.json());
    const adds = [
      ['f-semester',    opts.semesters,    s => ({v:s, t:s})],
      ['f-year',        opts.years,        y => ({v:y, t:y})],
      ['f-company',     opts.companies,    c => ({v:c.id, t:c.name})],
      ['f-program',     opts.programs,     p => ({v:p.code, t:p.code + ' — ' + p.label})],
      ['f-coordinator', opts.coordinators, c => ({v:c.email, t:c.name})],
    ];
    adds.forEach(([id, items, fn]) => {
      const sel = document.getElementById(id);
      items.forEach(item => {
        const {v,t} = fn(item);
        const o = document.createElement('option');
        o.value = v; o.textContent = t;
        sel.appendChild(o);
      });
    });
  } catch(e) { console.warn('Filter options error:', e); }
}

async function toggleGroupBy() {
  _groupBy = _groupBy === 'calendar' ? 'academic' : 'calendar';
  document.getElementById('groupby-btn').textContent = 'group: ' + _groupBy;
  _cache = {};
  await render(_currentView);
}

async function renderSemesters() {
  const data = await fetch('/api/semesters?group_by=' + _groupBy).then(r => r.json());
  if (!data.length) {
    document.getElementById('semester-groups').innerHTML = '<div class="empty">No semester data yet.</div>';
    return;
  }
  let html = '<table><tr><th>Group</th><th>Semester</th><th>Short</th></tr>';
  for (const group of data) {
    group.semesters.forEach((s, i) => {
      html += '<tr><td class="mono">' + (i === 0 ? group.group : '') + '</td><td>' + s.label + '</td><td class="mono" style="color:var(--muted)">' + s.short + '</td></tr>';
    });
  }
  html += '</table>';
  document.getElementById('semester-groups').innerHTML = html;
}

function show(name) {
  const VIEWS = ['overview','programs','students','projects','companies','assignments'];
  VIEWS.forEach(v => {
    document.getElementById('view-' + v).classList.toggle('hidden', v !== name);
  });
  document.querySelectorAll('nav button').forEach((b, i) => {
    b.classList.toggle('active', VIEWS[i] === name);
  });
  _currentView = name;
  render(name);
}

function badge(status) {
  const cls = {
    active:'badge-active', inactive:'badge-inactive', closed:'badge-closed',
    completed:'badge-completed', proposed:'badge-proposed', confirmed:'badge-confirmed'
  }[status] || '';
  return '<span class="badge ' + cls + '">' + status + '</span>';
}

function fillBar(filled, total) {
  const pct = total ? Math.round(filled / total * 100) : 0;
  const color = pct >= 100 ? 'var(--warn)' : pct >= 60 ? 'var(--accent)' : 'var(--accent2)';
  return '<div class="fill-wrap"><div class="fill-bar"><div class="fill-bar-inner" style="width:' + Math.min(pct,100) + '%;background:' + color + '"></div></div><span class="fill-pct">' + pct + '%</span></div>';
}

async function render(view) {
  if (view === 'overview') {
    const [st, pr] = await Promise.all([api('/api/status'), api('/api/programs')]);
    await renderSemesters();
    const asgn = st.assignments || {};
    document.getElementById('stat-cards').innerHTML = [
      [st.students.active,  'Active students'],
      [st.projects.active,  'Active projects'],
      [st.companies.active, 'Active companies'],
      [st.coordinators.total, 'Coordinators'],
      [asgn.confirmed || 0, 'Confirmed'],
      [asgn.proposed  || 0, 'Proposed'],
    ].map(([v,l]) => '<div class="stat"><div class="val">' + v + '</div><div class="lbl">' + l + '</div></div>').join('');
    if (!pr.length) {
      document.getElementById('prog-summary').innerHTML = '<div class="empty">No program data yet.</div>';
      return;
    }
    document.getElementById('prog-summary').innerHTML = pr.map(p =>
      '<div class="prog-row"><span class="prog-code">' + p.code + '</span><span class="prog-label">' + p.label + '</span><div class="prog-bar"><div class="prog-bar-inner" style="width:' + Math.round(p.rate*100) + '%"></div></div><span class="prog-pct">' + Math.round(p.rate*100) + '%</span><span class="prog-nums">' + p.placed + '/' + p.total + '</span></div>'
    ).join('');
  }
  else if (view === 'programs') {
    const pr = await api('/api/programs');
    if (!pr.length) { document.getElementById('prog-detail').innerHTML = '<div class="empty">No program data yet.</div>'; return; }
    document.getElementById('prog-detail').innerHTML = pr.map(p =>
      '<div class="prog-row"><span class="prog-code">' + p.code + '</span><span class="prog-label">' + p.label + '</span><div class="prog-bar"><div class="prog-bar-inner" style="width:' + Math.round(p.rate*100) + '%"></div></div><span class="prog-pct">' + Math.round(p.rate*100) + '%</span><span class="prog-nums">' + p.placed + '/' + p.total + '</span></div>'
    ).join('');
  }
  else if (view === 'students') {
    const data = await api('/api/students');
    if (!data.length) { document.getElementById('students-table').innerHTML = '<div class="empty">No students yet.</div>'; return; }
    document.getElementById('students-table').innerHTML = '<table><tr><th>Name</th><th>Program</th><th>Semester</th><th>Hours</th><th>Placed</th><th>Status</th><th>Doc</th></tr>' +
      data.map(s => '<tr><td>' + s.name + '<br><span class="mono" style="color:var(--muted)">' + s.email + '</span></td><td class="mono">' + s.program + '</td><td class="mono">' + s.semester + '</td><td>' + fillBar(s.hours_committed, s.hours_available) + '<span style="font-size:11px;color:var(--muted)">' + s.hours_committed + '/' + s.hours_available + 'h</span></td><td>' + (s.placed ? '<span style="color:var(--accent)">✓</span>' : '<span style="color:var(--warn)">—</span>') + '</td><td>' + badge(s.status) + '</td><td><a class="doc-link" href="/api/document/students/' + s.student_number + '" target="_blank">view</a></td></tr>'
      ).join('') + '</table>';
  }
  else if (view === 'projects') {
    const data = await api('/api/projects');
    if (!data.length) { document.getElementById('projects-table').innerHTML = '<div class="empty">No projects yet.</div>'; return; }
    document.getElementById('projects-table').innerHTML = '<table><tr><th>Title</th><th>Company</th><th>Semester</th><th>Teams</th><th>Fill</th><th>Coordinators</th><th>Lang</th><th>Status</th><th>Doc</th></tr>' +
      data.map(p => {
        const teamsCell = p.n_teams > 1
          ? Object.entries(p.teams).sort().map(([l,t]) => `<span title="Team ${l}">${l}:${t.filled}/${p.total_hours}h</span>`).join(' ')
          : fillBar(p.filled_hours, p.total_hours) + '<span style="font-size:11px;color:var(--muted)">' + p.filled_hours + '/' + p.total_hours + 'h</span>';
        return '<tr><td>' + p.title + '<br><span style="font-size:11px;color:var(--muted)">' + p.lead_name + '</span></td><td>' + p.company + '</td><td class="mono">' + p.semester + '</td><td class="mono" style="font-size:11px">' + (p.n_teams > 1 ? p.n_teams + ' teams' : '—') + '</td><td>' + teamsCell + '</td><td style="font-size:12px;color:var(--muted)">' + (p.coordinators.join(', ') || '—') + '</td><td class="mono">' + (p.language || '') + '</td><td>' + badge(p.status) + '</td><td><a class="doc-link" href="/api/document/projects/' + p.project_id + '" target="_blank">view</a></td></tr>';
      }).join('') + '</table>';
  }
  else if (view === 'companies') {
    const data = await api('/api/companies');
    if (!data.length) { document.getElementById('companies-table').innerHTML = '<div class="empty">No companies yet.</div>'; return; }
    document.getElementById('companies-table').innerHTML = '<table><tr><th>Name</th><th>Contact</th><th>Language</th><th>Projects</th><th>Status</th><th>Doc</th></tr>' +
      data.map(c => '<tr><td>' + c.name + '</td><td>' + c.contact_name + '<br><span class="mono" style="color:var(--muted)">' + c.contact_email + '</span></td><td class="mono">' + c.language + '</td><td class="mono">' + c.active_projects + ' active / ' + c.project_count + ' total</td><td>' + badge(c.status) + '</td><td><a class="doc-link" href="/api/document/companies/' + c.company_id + '" target="_blank">view</a></td></tr>'
      ).join('') + '</table>';
  }
  else if (view === 'assignments') {
    const data = await api('/api/assignments');
    if (!data.length) { document.getElementById('assignments-table').innerHTML = '<div class="empty">No assignments yet.</div>'; return; }
    document.getElementById('assignments-table').innerHTML = '<table><tr><th>Student</th><th>Program</th><th>Project</th><th>Task</th><th>Hours</th><th>Semester</th><th>Status</th><th>Assigned</th></tr>' +
      data.map(r => '<tr><td class="mono">' + r.student_number + '<br><span style="font-size:11px;color:var(--muted)">' + r.student_email + '</span></td><td class="mono">' + r.student_program + '</td><td style="font-size:12px">' + r.project_id + '</td><td style="font-size:12px">' + r.task_label + '</td><td class="mono">' + r.hours_planned + 'h</td><td class="mono">' + r.semester + '</td><td>' + badge(r.status) + '</td><td class="mono" style="font-size:11px">' + r.assigned_date + '</td></tr>'
      ).join('') + '</table>';
  }
}

// Init
(async () => {
  await populateFilterOptions();
  const st = await fetch('/api/status').then(r => r.json());
  _groupBy = st.group_by || 'calendar';
  document.getElementById('groupby-btn').textContent = 'group: ' + _groupBy;
  const initSem = st.semester || new URLSearchParams(window.location.search).get('semester') || '';
  if (initSem) {
    document.getElementById('f-semester').value = initSem;
    _activeFilters = _readFilters();
    _markActiveSelects(_activeFilters);
    document.getElementById('sem-badge').textContent = 'semester: ' + initSem;
  } else {
    document.getElementById('sem-badge').textContent = 'all semesters';
    _activeFilters = _readFilters();
  }
  render('overview');
})();
</script>
</body>
</html>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    import tomllib
    from src.semester import parse as parse_sem

    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)

    port     = getattr(args, "port", None) or cfg["server"]["port"]
    host     = cfg["server"]["host"]
    semester = getattr(args, "semester", None)
    group_by = getattr(args, "group_by", "calendar")

    # Normalise semester to storage string
    sem_obj = parse_sem(semester) if semester else None
    DashboardHandler.semester_filter = sem_obj.to_storage() if sem_obj else None
    DashboardHandler.group_by        = group_by

    server = HTTPServer((host, port), DashboardHandler)

    sem_label = f"  semester: {semester}" if semester else "  all semesters"
    print(f"\n  Innovation Hub dashboard")
    print(f"  {sem_label}")
    print(f"\n  http://{host}:{port}\n")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()
