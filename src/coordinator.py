"""
coordinator.py — coordinator resolution, recommendation, and assignment.
Used by ingest.py (at project ingest time) and by the assign-coordinator command.
"""
from pathlib import Path

from src.store import load_json, save_json, list_ids, load_coordinators
from src.fuzzy import ranked_matches
from src.embed import cosine_similarity, load_embedding


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve_coordinator(query: str) -> dict | None:
    """
    Fuzzy-match query against coordinator names and emails.
    Returns a single coordinator dict, or None if the user aborts.
    Prompts for selection if ambiguous.
    """
    from rich.console import Console
    console = Console()

    coords = load_coordinators()
    if not coords:
        console.print("  [yellow]No coordinators in the system yet.[/yellow]")
        return None

    # Match against both name and email fields
    name_map   = {c["coordinator_id"]: c["name"]  for c in coords}
    email_map  = {c["coordinator_id"]: c["email"] for c in coords}
    display    = {c["coordinator_id"]: f"{c['name']}  <{c['email']}>" for c in coords}

    # Score against names and emails separately, take the best per coordinator
    name_scores  = ranked_matches(query, list(name_map.values()),  limit=5)
    email_scores = ranked_matches(query, list(email_map.values()), limit=5)

    scores: dict[str, float] = {}
    for cid in name_map:
        ns = next((s for n, s in name_scores  if n == name_map[cid]),  0)
        es = next((s for e, s in email_scores if e == email_map[cid]), 0)
        scores[cid] = max(ns, es)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ranked = [(cid, s) for cid, s in ranked if s >= 50]

    if not ranked:
        console.print(f"  [red]No coordinator found matching '{query}'.[/red]")
        return None

    if ranked[0][1] >= 90 and (len(ranked) < 2 or ranked[1][1] < 80):
        return load_json("coordinators", ranked[0][0])

    # Ambiguous — prompt
    console.print(f"\n  Coordinators matching '{query}':")
    for i, (cid, score) in enumerate(ranked[:5], 1):
        console.print(f"    {i}  {display[cid]}  ({score:.0f}%)")
    choice = input("  Which coordinator? Enter number (or 0 to skip): ").strip()
    try:
        idx = int(choice) - 1
        if idx < 0:
            return None
        return load_json("coordinators", ranked[idx][0])
    except (ValueError, IndexError):
        return None


def pick_coordinators(prompt: str = "Add coordinator") -> list[dict]:
    """
    Repeatedly resolve coordinators by name/email until the user stops.
    Returns list of coordinator dicts.
    """
    from rich.console import Console
    console = Console()

    selected: list[dict] = []
    seen_ids: set[str]   = set()

    while True:
        query = input(f"  {prompt} (name or email, blank to finish): ").strip()
        if not query:
            break
        coord = resolve_coordinator(query)
        if coord is None:
            continue
        cid = coord["coordinator_id"]
        if cid in seen_ids:
            console.print(f"  [yellow]{coord['name']} is already in the list.[/yellow]")
            continue
        selected.append(coord)
        seen_ids.add(cid)
        console.print(f"  ✓ Added: {coord['name']} <{coord['email']}>")

    return selected


# ── Recommendation ────────────────────────────────────────────────────────────

def recommend_coordinators(
    project_id: str,
    top_n: int = 5,
) -> list[tuple[dict, float, str]]:
    """
    Rank all active coordinators by relevance to a project.
    Returns list of (coordinator_dict, score, signal_used).

    Signal priority:
      1. CV embedding similarity to project embedding
      2. Past project embedding similarity (average of assigned projects)
      3. Program overlap (weak signal, score capped at 0.60)
    """
    from src.store import load_assignments
    import numpy as np

    project_meta = load_json("projects", project_id)
    proj_emb_path = project_meta.get("embedding_file", "")
    if not proj_emb_path or not Path(proj_emb_path).exists():
        return []

    proj_vec   = load_embedding(proj_emb_path)
    proj_progs = {project_meta.get("company_id", "")}  # weak signal fallback

    all_assignments = load_assignments()
    results = []

    for coord in load_coordinators():
        if coord.get("status") != "active":
            continue

        cid   = coord["coordinator_id"]
        score = 0.0
        signal = "none"

        # ── Signal 1: CV embedding ────────────────────────────────────────────
        emb_path = coord.get("embedding_file", "")
        if emb_path and Path(emb_path).exists():
            cv_vec = load_embedding(emb_path)
            score  = cosine_similarity(proj_vec, cv_vec)
            signal = "cv"

        # ── Signal 2: Past project similarity ─────────────────────────────────
        elif _has_past_assignments(cid, all_assignments):
            past_scores = _past_project_scores(cid, proj_vec, all_assignments)
            if past_scores:
                score  = float(np.mean(past_scores))
                signal = "history"

        # ── Signal 3: Program overlap (weak) ──────────────────────────────────
        else:
            coord_progs = set(coord.get("programs", []))
            proj_prog   = project_meta.get("semester", "")  # best proxy available
            if not coord_progs:  # empty = all programs
                score  = 0.40
                signal = "programs (all)"
            elif coord_progs & proj_progs:
                score  = 0.40
                signal = "programs"
            else:
                score  = 0.10
                signal = "programs (none)"

        results.append((coord, round(score, 2), signal))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_n]


def _has_past_assignments(coordinator_id: str, assignments: list[dict]) -> bool:
    return any(
        r.get("coordinator_id") == coordinator_id
        for r in assignments
        if r.get("status") in {"confirmed", "completed"}
    )


def _past_project_scores(
    coordinator_id: str,
    proj_vec,
    assignments: list[dict],
) -> list[float]:
    seen_projects: set[str] = set()
    scores = []
    for r in assignments:
        if r.get("coordinator_id") != coordinator_id:
            continue
        if r.get("status") not in {"confirmed", "completed"}:
            continue
        pid = r["project_id"]
        if pid in seen_projects:
            continue
        seen_projects.add(pid)
        try:
            pmeta = load_json("projects", pid)
            ep    = pmeta.get("embedding_file", "")
            if ep and Path(ep).exists():
                scores.append(cosine_similarity(proj_vec, load_embedding(ep)))
        except Exception:
            pass
    return scores


def render_recommendations(
    recs: list[tuple[dict, float, str]],
    console,
) -> None:
    from rich.table import Table
    from rich import box

    if not recs:
        console.print("  [dim]No coordinator recommendations available.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Rank",      style="dim",     width=4,  justify="right")
    table.add_column("Name",      style="white",   min_width=22)
    table.add_column("Email",     style="dim",     min_width=28)
    table.add_column("Score",     style="green",   width=6,  justify="right")
    table.add_column("Signal",    style="cyan",    min_width=14)
    table.add_column("Programs",  style="dim",     min_width=16)

    for i, (coord, score, signal) in enumerate(recs, 1):
        progs = ", ".join(coord.get("programs", [])) or "all"
        table.add_row(
            str(i),
            coord["name"],
            coord["email"],
            f"{score:.2f}",
            signal,
            progs,
        )
    console.print(table)


# ── Coordinator setup flow (called from ingest) ───────────────────────────────

def coordinator_setup_flow(project_id: str, console) -> list[str]:
    """
    Offer three options at project ingest time:
      s — skip (return empty list)
      m — manual pick
      r — system recommendation
    Returns list of coordinator_ids to store on the project.
    """
    console.print("\n  [bold]Coordinator assignment[/bold]")
    console.print("    s  Skip — assign coordinators later")
    console.print("    m  Manual — search by name or email")
    console.print("    r  Recommend — rank by subject-matter relevance")
    choice = input("\n  Choice [s/m/r]: ").strip().lower()

    if choice == "m":
        selected = pick_coordinators()
        return [c["coordinator_id"] for c in selected]

    elif choice == "r":
        recs = recommend_coordinators(project_id)
        if not recs:
            console.print(
                "  [yellow]Not enough data for recommendations yet "
                "(no coordinator CVs or past assignments). "
                "Falling back to manual.[/yellow]"
            )
            selected = pick_coordinators()
            return [c["coordinator_id"] for c in selected]

        render_recommendations(recs, console)
        console.print(
            "\n  Enter rank numbers to assign (comma-separated), "
            "or blank to skip: "
        )
        raw = input("  ").strip()
        if not raw:
            return []
        selected_ids = []
        for token in raw.split(","):
            token = token.strip()
            try:
                idx = int(token) - 1
                coord, _, _ = recs[idx]
                selected_ids.append(coord["coordinator_id"])
                console.print(f"  ✓ {coord['name']}")
            except (ValueError, IndexError):
                pass
        return selected_ids

    return []  # skip


# ── assign-coordinator command ────────────────────────────────────────────────

def run_assign_coordinator(args) -> None:
    from rich.console import Console
    console = Console()

    try:
        meta = load_json("projects", args.project_id)
    except FileNotFoundError:
        console.print(f"  [red]Project '{args.project_id}' not found.[/red]")
        return

    coordinator_ids: list[str] = meta.get("coordinators", [])

    if args.add:
        coord = resolve_coordinator(args.add)
        if coord is None:
            return
        cid = coord["coordinator_id"]
        if cid in coordinator_ids:
            console.print(f"  [yellow]{coord['name']} is already assigned.[/yellow]")
            return
        coordinator_ids.append(cid)
        meta["coordinators"] = coordinator_ids
        save_json("projects", args.project_id, meta)
        console.print(f"  ✓ {coord['name']} assigned to '{meta['title']}'.")

    elif args.remove:
        coord = resolve_coordinator(args.remove)
        if coord is None:
            return
        cid = coord["coordinator_id"]
        if cid not in coordinator_ids:
            console.print(f"  [yellow]{coord['name']} is not assigned to this project.[/yellow]")
            return
        coordinator_ids.remove(cid)
        meta["coordinators"] = coordinator_ids
        save_json("projects", args.project_id, meta)
        console.print(f"  ✓ {coord['name']} removed from '{meta['title']}'.")

    else:
        # No flag — show current coordinators
        console.print(f"\n  [bold]{meta['title']}[/bold] — coordinators:")
        if not coordinator_ids:
            from src.store import default_coordinator
            dc = default_coordinator()
            if dc:
                console.print(
                    f"  [dim](none assigned — de facto: "
                    f"{dc['name']} <{dc['email']}>)[/dim]"
                )
            else:
                console.print("  [dim](none assigned)[/dim]")
        else:
            for cid in coordinator_ids:
                try:
                    c = load_json("coordinators", cid)
                    console.print(f"    {c['name']}  <{c['email']}>")
                except Exception:
                    console.print(f"    [dim]{cid} (not found)[/dim]")
