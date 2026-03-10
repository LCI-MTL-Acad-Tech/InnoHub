"""
coordinator.py — coordinator resolution, recommendation, and assignment.
Used by ingest.py (at project ingest time) and by the coord command.

Coordinator IDs are the email address — globally unique and human-readable.
Coordinator–project relationships are stored in the project JSON (coordinators list).
Past assignment history is derived by checking which projects a coordinator
appears on, not via a CSV column.
"""
from pathlib import Path

from src.store import load_json, save_json, list_ids, load_coordinators
from src.fuzzy import ranked_matches
from src.embed import cosine_similarity, load_embedding


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve_coordinator(query: str) -> dict | None:
    """
    Fuzzy-match query against coordinator names and emails.
    Email is the unique ID so exact email matches resolve immediately.
    Returns a single coordinator dict, or None if the user aborts.
    """
    from rich.console import Console
    console = Console()

    coords = load_coordinators()
    if not coords:
        console.print("  [yellow]No coordinators in the system yet.[/yellow]")
        return None

    # Exact email match — unambiguous
    query_lower = query.strip().lower()
    for c in coords:
        if c["email"].lower() == query_lower:
            return c

    # Fuzzy match against names and emails
    name_map  = {c["email"]: c["name"]  for c in coords}
    display   = {c["email"]: f"{c['name']}  <{c['email']}>" for c in coords}

    name_scores  = ranked_matches(query, list(name_map.values()),  limit=5)
    email_scores = ranked_matches(query, list(c["email"] for c in coords), limit=5)

    scores: dict[str, float] = {}
    for email in name_map:
        ns = next((s for n, s in name_scores  if n == name_map[email]), 0)
        es = next((s for e, s in email_scores if e == email),            0)
        scores[email] = max(ns, es)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ranked = [(email, s) for email, s in ranked if s >= 50]

    if not ranked:
        console.print(f"  [red]No coordinator found matching '{query}'.[/red]")
        return None

    if ranked[0][1] >= 90 and (len(ranked) < 2 or ranked[1][1] < 80):
        return load_json("coordinators", ranked[0][0])

    # Ambiguous — prompt
    console.print(f"\n  Coordinators matching '{query}':")
    for i, (email, score) in enumerate(ranked[:5], 1):
        console.print(f"    {i}  {display[email]}  ({score:.0f}%)")
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

    selected: list[dict]  = []
    seen_emails: set[str] = set()

    while True:
        query = input(f"  {prompt} (name or email, blank to finish): ").strip()
        if not query:
            break
        coord = resolve_coordinator(query)
        if coord is None:
            continue
        email = coord["email"]
        if email in seen_emails:
            console.print(f"  [yellow]{coord['name']} is already in the list.[/yellow]")
            continue
        selected.append(coord)
        seen_emails.add(email)
        console.print(f"  ✓ Added: {coord['name']} <{email}>")

    return selected


# ── Past-project helpers (uses project JSON, not CSV column) ──────────────────

def _projects_for_coordinator(coordinator_email: str) -> list[str]:
    """Return project_ids where this coordinator is assigned."""
    result = []
    for pid in list_ids("projects"):
        try:
            pmeta = load_json("projects", pid)
            if coordinator_email in pmeta.get("coordinators", []):
                result.append(pid)
        except Exception:
            pass
    return result


def _has_past_projects(coordinator_email: str) -> bool:
    return bool(_projects_for_coordinator(coordinator_email))


def _past_project_scores(coordinator_email: str, proj_vec) -> list[float]:
    scores = []
    for pid in _projects_for_coordinator(coordinator_email):
        try:
            pmeta = load_json("projects", pid)
            ep    = pmeta.get("embedding_file", "")
            if ep and Path(ep).exists():
                scores.append(cosine_similarity(proj_vec, load_embedding(ep)))
        except Exception:
            pass
    return scores


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
      2. Past project embedding similarity (average across assigned projects)
      3. Program overlap (weak signal)
    """
    import numpy as np

    project_meta  = load_json("projects", project_id)
    proj_emb_path = project_meta.get("embedding_file", "")
    if not proj_emb_path or not Path(proj_emb_path).exists():
        return []

    proj_vec    = load_embedding(proj_emb_path)
    proj_company = project_meta.get("company_id", "")

    results = []

    for coord in load_coordinators():
        if coord.get("status") != "active":
            continue

        email  = coord["email"]
        score  = 0.0
        signal = "none"

        # Signal 1: CV embedding
        emb_path = coord.get("embedding_file", "")
        if emb_path and Path(emb_path).exists():
            score  = cosine_similarity(proj_vec, load_embedding(emb_path))
            signal = "cv"

        # Signal 2: Past project similarity
        elif _has_past_projects(email):
            past = _past_project_scores(email, proj_vec)
            if past:
                score  = float(np.mean(past))
                signal = "history"

        # Signal 3: Program overlap (weak)
        else:
            coord_progs = set(coord.get("programs", []))
            if not coord_progs:
                score  = 0.40
                signal = "programs (all)"
            else:
                score  = 0.10
                signal = "programs (none)"

        results.append((coord, round(score, 2), signal))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_n]


def render_recommendations(recs: list[tuple[dict, float, str]], console) -> None:
    from rich.table import Table
    from rich import box

    if not recs:
        console.print("  [dim]No coordinator recommendations available.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Rank",     style="dim",   width=4,  justify="right")
    table.add_column("Name",     style="white", min_width=22)
    table.add_column("Email",    style="dim",   min_width=28)
    table.add_column("Score",    style="green", width=6,  justify="right")
    table.add_column("Signal",   style="cyan",  min_width=14)
    table.add_column("Programs", style="dim",   min_width=16)

    for i, (coord, score, signal) in enumerate(recs, 1):
        progs = ", ".join(coord.get("programs", [])) or "all"
        table.add_row(
            str(i), coord["name"], coord["email"],
            f"{score:.2f}", signal, progs,
        )
    console.print(table)


# ── Coordinator setup flow (called from ingest) ───────────────────────────────

def coordinator_setup_flow(project_id: str, console) -> list[str]:
    """
    Offer three options at project ingest time.
    Returns list of coordinator emails (used as IDs) to store on the project.
    """
    console.print("\n  [bold]Coordinator assignment[/bold]")
    console.print("    s  Skip — assign coordinators later")
    console.print("    m  Manual — search by name or email")
    console.print("    r  Recommend — rank by subject-matter relevance")
    choice = input("\n  Choice [s/m/r]: ").strip().lower()

    if choice == "m":
        return [c["email"] for c in pick_coordinators()]

    elif choice == "r":
        recs = recommend_coordinators(project_id)
        if not recs:
            console.print(
                "  [yellow]Not enough data for recommendations yet. "
                "Falling back to manual.[/yellow]"
            )
            return [c["email"] for c in pick_coordinators()]

        render_recommendations(recs, console)
        console.print(
            "\n  Enter rank numbers to assign (comma-separated), "
            "or blank to skip: "
        )
        raw = input("  ").strip()
        if not raw:
            return []
        selected = []
        for token in raw.split(","):
            try:
                idx = int(token.strip()) - 1
                coord, _, _ = recs[idx]
                selected.append(coord["email"])
                console.print(f"  ✓ {coord['name']}")
            except (ValueError, IndexError):
                pass
        return selected

    return []  # skip


# ── coord command ─────────────────────────────────────────────────────────────

def run_assign_coordinator(args) -> None:
    from rich.console import Console
    console = Console()

    try:
        meta = load_json("projects", args.project_id)
    except FileNotFoundError:
        console.print(f"  [red]Project '{args.project_id}' not found.[/red]")
        return

    coordinator_emails: list[str] = meta.get("coordinators", [])

    if getattr(args, "add", None):
        coord = resolve_coordinator(args.add)
        if coord is None:
            return
        email = coord["email"]
        if email in coordinator_emails:
            console.print(f"  [yellow]{coord['name']} is already assigned.[/yellow]")
            return
        coordinator_emails.append(email)
        meta["coordinators"] = coordinator_emails
        save_json("projects", args.project_id, meta)
        console.print(f"  ✓ {coord['name']} assigned to '{meta['title']}'.")

    elif getattr(args, "remove", None):
        coord = resolve_coordinator(args.remove)
        if coord is None:
            return
        email = coord["email"]
        if email not in coordinator_emails:
            console.print(f"  [yellow]{coord['name']} is not assigned to this project.[/yellow]")
            return
        coordinator_emails.remove(email)
        meta["coordinators"] = coordinator_emails
        save_json("projects", args.project_id, meta)
        console.print(f"  ✓ {coord['name']} removed from '{meta['title']}'.")

    else:
        # Show current coordinators
        console.print(f"\n  [bold]{meta['title']}[/bold] — coordinators:")
        if not coordinator_emails:
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
            for email in coordinator_emails:
                try:
                    c = load_json("coordinators", email)
                    console.print(f"    {c['name']}  <{email}>")
                except Exception:
                    console.print(f"    [dim]{email} (not found)[/dim]")
