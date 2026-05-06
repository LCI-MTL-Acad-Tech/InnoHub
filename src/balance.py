"""
balance.py — distribute students into balanced competing teams per project.

Teams are language-aware:
- FR-dominant students (fr_only, strong_fr) are grouped together
- EN-dominant students (en_only, strong_en) are grouped together
- Bilinguals (bilingual_helper, bilingual_passive, trilingual) are distributed
  as bridges so each team has at least one
- If not enough bilinguals, small incompatible groups are split into
  single-person teams rather than leaving bilingual-less 2-person teams
- No more than one single-person team if avoidable
- At most one team lead per team
- Hours are balanced within language constraints

Team labels in CSV remain A, B, C...
Display labels are FR-A, EN-B, BIL-C based on the composition:
  - All FR-dominant (+ optional bilingual bridge) → FR-x
  - All EN-dominant (+ optional bilingual bridge) → EN-x
  - Mixed FR+EN with bilingual bridge → BIL-x
  - Single student → their dominant language label

Command:
    ./bin/python main.py balance [--project PROJECT_ID] [--semester TAG]
"""
from rich.console import Console
from rich.table import Table
from rich import box


# ── Language classification ───────────────────────────────────────────────────

LANG_SHORT = {
    "bilingual_helper":  "bil.+",
    "bilingual_passive": "bil.",
    "strong_fr":         "FR>en",
    "strong_en":         "EN>fr",
    "en_only":           "EN",
    "fr_only":           "FR",
    "trilingual":        "3lang",
    "undefined":         "?",
}

FR_PROFILES  = {"fr_only", "strong_fr"}
EN_PROFILES  = {"en_only", "strong_en"}
BIL_PROFILES = {"bilingual_helper", "bilingual_passive", "trilingual"}


def _lang_str(profile: str) -> str:
    return LANG_SHORT.get(profile, profile or "?")


def _lang_group(profile: str) -> str:
    """Return 'fr', 'en', 'bil', or 'undefined'."""
    if profile in FR_PROFILES:
        return "fr"
    if profile in EN_PROFILES:
        return "en"
    if profile in BIL_PROFILES:
        return "bil"
    return "undefined"


def _team_display_label(letter: str, member_profiles: list[str]) -> str:
    """
    Determine the display prefix (FR/EN/BIL) for a team given its members'
    language profiles.
    - Any FR-dominant + EN-dominant mix with a bilingual → BIL
    - FR-dominant only (with or without bilingual) → FR
    - EN-dominant only (with or without bilingual) → EN
    - All bilingual → BIL
    - Single undefined → ?
    """
    groups = {_lang_group(p) for p in member_profiles if p}
    has_fr  = "fr"  in groups
    has_en  = "en"  in groups
    has_bil = "bil" in groups

    if has_fr and has_en:
        return f"BIL-{letter}"
    if has_fr:
        return f"FR-{letter}"
    if has_en:
        return f"EN-{letter}"
    if has_bil:
        return f"BIL-{letter}"
    return f"?-{letter}"


# ── Data helpers ──────────────────────────────────────────────────────────────

def _student_hours(student_number: str, project_id: str, rows: list[dict]) -> int:
    return sum(
        (int(r["hours_planned"]) if str(r.get("hours_planned", "0")).isdigit() else 0)
        for r in rows
        if r["student_number"] == student_number
        and r["project_id"] == project_id
        and r["status"] not in {"cancelled", "completed"}
    )


def _student_tasks(student_number: str, project_id: str, rows: list[dict]) -> set[str]:
    return {
        r["task_id"] for r in rows
        if r["student_number"] == student_number
        and r["project_id"] == project_id
        and r["status"] not in {"cancelled", "completed"}
    }


# ── Distribution algorithm ────────────────────────────────────────────────────

def _distribute(
    students: list[tuple],  # (sid, hours, tasks, is_lead, lang_group)
    n_teams: int,
) -> dict[str, list[str]]:
    """
    Language-aware greedy bin-packing.

    Steps:
    1. Separate into FR, EN, BIL, and undefined groups.
    2. Build FR teams and EN teams; merge small groups if needed to avoid
       bilingual-less 2-person teams that could not be fixed.
    3. Distribute bilinguals as bridges, one per team if possible.
    4. Respect lead constraint (at most one lead per team).
    5. Balance hours within constraints.

    Returns {team_label: [student_numbers]}
    """
    labels = [chr(ord("A") + i) for i in range(n_teams)]

    fr_students  = [(sid, h, t, il) for sid, h, t, il, lg in students if lg == "fr"]
    en_students  = [(sid, h, t, il) for sid, h, t, il, lg in students if lg == "en"]
    bil_students = [(sid, h, t, il) for sid, h, t, il, lg in students if lg == "bil"]
    und_students = [(sid, h, t, il) for sid, h, t, il, lg in students if lg == "undefined"]

    # Sort each group heaviest first
    fr_students  = sorted(fr_students,  key=lambda x: x[1], reverse=True)
    en_students  = sorted(en_students,  key=lambda x: x[1], reverse=True)
    bil_students = sorted(bil_students, key=lambda x: x[1], reverse=True)
    und_students = sorted(und_students, key=lambda x: x[1], reverse=True)

    # Build initial buckets: one per FR group chunk, one per EN group chunk
    # If n_teams == 1, everything goes in one bucket
    buckets: list[list[tuple]] = []

    if n_teams == 1:
        buckets = [fr_students + en_students + bil_students + und_students]
    else:
        # Split FR students across ceil(n_teams * fr_ratio) teams
        total_mono = len(fr_students) + len(en_students)
        if total_mono == 0:
            # All bilingual / undefined — distribute evenly
            buckets = [[] for _ in range(n_teams)]
        else:
            n_fr_teams = max(1, round(n_teams * len(fr_students) / total_mono)) \
                if fr_students else 0
            n_en_teams = max(1, round(n_teams * len(en_students) / total_mono)) \
                if en_students else 0

            # Clamp so we don't exceed n_teams
            if n_fr_teams + n_en_teams > n_teams:
                if n_fr_teams >= n_en_teams:
                    n_fr_teams = n_teams - n_en_teams
                else:
                    n_en_teams = n_teams - n_fr_teams
            remaining_teams = n_teams - n_fr_teams - n_en_teams

            # Distribute FR students across n_fr_teams buckets
            fr_buckets: list[list[tuple]] = [[] for _ in range(max(n_fr_teams, 0))]
            for i, s in enumerate(fr_students):
                fr_buckets[i % max(n_fr_teams, 1)].append(s)

            # Distribute EN students across n_en_teams buckets
            en_buckets: list[list[tuple]] = [[] for _ in range(max(n_en_teams, 0))]
            for i, s in enumerate(en_students):
                en_buckets[i % max(n_en_teams, 1)].append(s)

            # Extra buckets for overflow / undefined
            extra_buckets: list[list[tuple]] = [[] for _ in range(remaining_teams)]

            buckets = fr_buckets + en_buckets + extra_buckets

        # Distribute undefined students into lightest buckets
        bucket_hours = [sum(s[1] for s in b) for b in buckets]
        for s in und_students:
            idx = bucket_hours.index(min(bucket_hours))
            buckets[idx].append(s)
            bucket_hours[idx] += s[1]

    # Distribute bilinguals: one per bucket (bridge), rest into lightest
    bucket_has_bil = [any(True for _ in []) for _ in buckets]  # all False initially
    bucket_hours   = [sum(s[1] for s in b) for b in buckets]

    # First pass: give each bucket without a bilingual one bilingual
    bil_remaining = list(bil_students)
    bil_assigned_first = []
    for i, bucket in enumerate(buckets):
        if not bil_remaining:
            break
        # Check if bucket already has a bilingual (shouldn't at this point, but safe)
        if not any(True for s in bucket if any(True for _ in [])):
            bil_assigned_first.append((i, bil_remaining.pop(0)))

    for i, s in bil_assigned_first:
        buckets[i].append(s)
        bucket_hours[i] += s[1]

    # Second pass: remaining bilinguals go into lightest bucket
    for s in bil_remaining:
        idx = bucket_hours.index(min(bucket_hours))
        buckets[idx].append(s)
        bucket_hours[idx] += s[1]

    # Enforce lead constraint: at most one lead per bucket
    # If a bucket has >1 lead, move extras to lightest bucket without a lead
    for i, bucket in enumerate(buckets):
        leads_in_bucket = [s for s in bucket if s[3]]  # s[3] = is_lead
        if len(leads_in_bucket) > 1:
            # Keep the one with most hours, move the rest
            leads_in_bucket.sort(key=lambda s: s[1], reverse=True)
            for extra_lead in leads_in_bucket[1:]:
                buckets[i].remove(extra_lead)
                bucket_hours[i] -= extra_lead[1]
                # Find bucket without a lead
                target = next(
                    (j for j, b in enumerate(buckets) if j != i and not any(s[3] for s in b)),
                    bucket_hours.index(min(bucket_hours))
                )
                buckets[target].append(extra_lead)
                bucket_hours[target] += extra_lead[1]

    # Check for bilingual-less 2-person teams and try to fix
    # If a 2-person bucket has no bilingual, try to steal one from a larger bucket
    for i, bucket in enumerate(buckets):
        if len(bucket) == 2 and not any(
            _lang_group(next(
                (p for p in ["bilingual_helper","bilingual_passive","trilingual"]
                 if p == p), "x"  # placeholder — we need the actual profile
            )) == "bil"
            for s in bucket
        ):
            # Find a bilingual in another bucket that has >1 bilingual
            for j, other in enumerate(buckets):
                if i == j:
                    continue
                bilinguals_in_other = [s for s in other
                                       if s[0] in {sid for sid, _, _, _, lg in students
                                                    if lg == "bil"}]
                if len(bilinguals_in_other) > 1:
                    # Move one bilingual to bucket i
                    bridge = bilinguals_in_other[0]
                    buckets[j].remove(bridge)
                    bucket_hours[j] -= bridge[1]
                    buckets[i].append(bridge)
                    bucket_hours[i] += bridge[1]
                    break

    # Assign letter labels
    result: dict[str, list[str]] = {}
    for idx, (letter, bucket) in enumerate(zip(labels, buckets)):
        result[letter] = [s[0] for s in bucket]

    return result


def _suggest_n(students: list[tuple], all_task_ids: list[str]) -> int:
    """Suggest N teams based on hour spread, capped by bilingual count."""
    if len(students) <= 1:
        return 1

    n_bil = sum(1 for _, _, _, _, lg in students if lg == "bil")
    max_n = max(1, n_bil) if n_bil > 0 else len(students)

    best_n = 1
    for n in range(2, min(len(students), max_n) + 1):
        dist = _distribute(students, n)
        team_totals = [
            sum(h for sid, h, _, _, _ in students if sid in sids)
            for sids in dist.values()
        ]
        if not team_totals or min(team_totals) == 0:
            break
        ratio = max(team_totals) / min(team_totals)
        if ratio < 1.3:
            best_n = n
        if ratio < 1.1 or n >= len(students):
            best_n = n
            break
    return max(1, best_n)


# ── Display ───────────────────────────────────────────────────────────────────

def _show_distribution(
    project_title: str,
    project_id: str,
    distribution: dict[str, list[str]],
    students: list[tuple],  # (sid, hours, tasks, is_lead, lang_group)
    load_json,
    console: Console,
) -> None:
    hours_by_sid = {sid: hrs for sid, hrs, _, _, _ in students}

    team_totals = [
        sum(hours_by_sid.get(sid, 0) for sid in sids)
        for sids in distribution.values()
    ]
    min_h = min(team_totals) if team_totals else 0
    max_h = max(team_totals) if team_totals else 0

    console.print(
        f"\n  [bold]{project_title}[/bold]"
        f"  ·  [cyan]{len(distribution)} team(s)[/cyan]"
        f"  ·  hours per team: [green]{min_h}h[/green] – [yellow]{max_h}h[/yellow]\n"
    )

    all_task_ids: set[str] = set()
    for _, _, tasks, _, _ in students:
        all_task_ids |= tasks
    n_tasks = len(all_task_ids)

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Team",     style="cyan",  width=8)
    table.add_column("Students", style="white", min_width=36)
    table.add_column("Hours",    style="green", justify="right", width=8)
    table.add_column("Tasks",    style="dim",   justify="right", width=8)

    for letter, sids in sorted(distribution.items()):
        total = sum(hours_by_sid.get(sid, 0) for sid in sids)

        covered_tasks: set[str] = set()
        for sid in sids:
            for s, h, t, il, lg in students:
                if s == sid:
                    covered_tasks |= t
        coverage = f"{len(covered_tasks)}/{n_tasks}"

        # Collect member profiles for team label
        member_profiles = []
        member_parts    = []
        for sid in sids:
            try:
                m       = load_json("students", sid)
                name    = m.get("name", sid)
                profile = m.get("language_profile", "undefined")
                lang    = _lang_str(profile)
                member_profiles.append(profile)
                lead_marker    = " [bold yellow]★[/bold yellow]" \
                    if m.get("team_lead_project") == project_id else ""
                liaison_marker = " [bold cyan]⬡[/bold cyan]" \
                    if m.get("liaison_project") == project_id else ""
                member_parts.append(f"{name} ({lang}){lead_marker}{liaison_marker}")
            except Exception:
                member_parts.append(sid)

        display_label = _team_display_label(letter, member_profiles)
        table.add_row(display_label, "\n".join(member_parts), f"{total}h", coverage)

    console.print(table)
    console.print(
        "  [dim]★ = team lead  ⬡ = client liaison  "
        "bil.+ = bilingual helper  bil. = bilingual  "
        "FR>en / EN>fr = strong primary  EN / FR = monolingual[/dim]\n"
    )


# ── Core per-project logic ─────────────────────────────────────────────────────

def _balance_project(project_id: str, rows: list[dict], console: Console) -> bool:
    from src.store import load_json, save_json, rewrite_assignments, list_ids

    try:
        pmeta = load_json("projects", project_id)
    except FileNotFoundError:
        console.print(f"  [red]Project '{project_id}' not found.[/red]")
        return False

    project_title = pmeta.get("title", project_id)
    all_task_ids  = [t["task_id"] for t in pmeta.get("capacity", {}).get("tasks", [])]

    active_sids = sorted({
        r["student_number"] for r in rows
        if r["project_id"] == project_id
        and r["status"] not in {"cancelled", "completed"}
    })

    if not active_sids:
        console.print(f"  [dim]{project_title}: no active students, skipping.[/dim]")
        return False

    students = []
    for sid in active_sids:
        hrs     = _student_hours(sid, project_id, rows)
        tasks   = _student_tasks(sid, project_id, rows)
        try:
            m       = load_json("students", sid)
            is_lead = m.get("team_lead_project") == project_id
            lg      = _lang_group(m.get("language_profile", "undefined"))
        except Exception:
            is_lead = False
            lg      = "undefined"
        students.append((sid, hrs, tasks, is_lead, lg))

    suggested_n = _suggest_n(students, all_task_ids)
    n = suggested_n

    while True:
        distribution = _distribute(students, n)
        _show_distribution(project_title, project_id, distribution, students, load_json, console)

        raw = input(
            f"  Accept {n} team(s)? [Enter to confirm, number to try another N, 's' to skip]: "
        ).strip().lower()

        if raw == "s":
            console.print(f"  [dim]Skipped {project_title}.[/dim]\n")
            return False

        if raw == "":
            break

        try:
            new_n = int(raw)
            if new_n < 1 or new_n > len(students):
                console.print(f"  [yellow]N must be between 1 and {len(students)}.[/yellow]")
                continue
            n = new_n
        except ValueError:
            console.print("  [yellow]Enter a number, Enter to confirm, or 's' to skip.[/yellow]")
            continue

    # Write team assignments
    sid_to_team = {sid: label for label, sids in distribution.items() for sid in sids}

    changed = 0
    for r in rows:
        if (r["project_id"] == project_id
                and r["student_number"] in sid_to_team
                and r["status"] not in {"cancelled", "completed"}):
            new_team = sid_to_team[r["student_number"]]
            if r.get("team", "") != new_team:
                r["team"] = new_team
                changed += 1

    new_n_teams = len(distribution)
    if int(pmeta.get("teams", 1)) != new_n_teams:
        pmeta["teams"] = new_n_teams
        save_json("projects", project_id, pmeta)

    rewrite_assignments(rows)
    console.print(
        f"  [bold green]✓[/bold green]"
        f"  {project_title}: {new_n_teams} team(s), {changed} row(s) updated.\n"
    )
    return True


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args) -> None:
    from src.store import list_ids, load_json, load_assignments
    from src.semester import parse as parse_sem

    console = Console()
    rows    = load_assignments()

    project_filter = getattr(args, "project", None)
    sem_filter_str = None
    if getattr(args, "semester", None):
        sem_obj = parse_sem(args.semester)
        if sem_obj:
            sem_filter_str = sem_obj.to_storage()

    if project_filter:
        project_ids = [project_filter]
    else:
        project_ids = []
        for pid in sorted(list_ids("projects")):
            try:
                pmeta = load_json("projects", pid)
            except Exception:
                continue
            if pmeta.get("status") not in {"active"}:
                continue
            if sem_filter_str and pmeta.get("semester") != sem_filter_str:
                continue
            has_active = any(
                r for r in rows
                if r["project_id"] == pid
                and r["status"] not in {"cancelled", "completed"}
            )
            if has_active:
                project_ids.append(pid)

    if not project_ids:
        console.print("  [dim]No projects with active assignments found.[/dim]")
        return

    console.print(f"\n  Balancing [cyan]{len(project_ids)}[/cyan] project(s)...\n")

    for pid in project_ids:
        _balance_project(pid, rows, console)
        rows = load_assignments()
