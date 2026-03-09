"""
manpage.py — render groff .1 man pages from the argparse command definitions.
Called via: python main.py --generate-man
Writes one .1 file per subcommand into man/, plus a top-level innovhub.1.
"""
import argparse
import textwrap
from datetime import date
from pathlib import Path
import tomllib

with open("config.toml", "rb") as f:
    _CFG = tomllib.load(f)

MAN_DIR  = Path(_CFG["paths"]["man"])
DATE_STR = date.today().strftime("%Y-%m-%d")
SECTION  = "1"

# ── Per-command enrichment ────────────────────────────────────────────────────

_DESCRIPTIONS = {
    "innovhub": (
        "Innovation Hub is a fully offline CLI tool for matching students to "
        "company projects. Documents are parsed and embedded locally using a "
        "multilingual sentence-transformer model. No data is ever sent to the cloud."
    ),
    "ingest": (
        "Parse one or more documents (PDF, DOCX, HTML, or plaintext) and add them "
        "to the system. The document type must be specified with --type. "
        "For students, --id and --program (or --p) are required. "
        "For projects, --company is required. "
        "Duplicate detection runs automatically: similar companies prompt for "
        "merge-or-keep; similar projects from the same company prompt for "
        "update-or-keep. Program codes are validated against the known list; "
        "unrecognised codes trigger a typo check and offer to add a new entry."
    ),
    "match": (
        "Compute cosine similarity between the target's embedding and all eligible "
        "counterparts. Results are filtered by active status and available hours "
        "before ranking. Already-assigned pairs are excluded from results but noted "
        "at the bottom of the output. "
        "Use --search to identify a student by name or email regex instead of "
        "student number."
    ),
    "assign": (
        "Interactively assign a student to a project. After selecting the project, "
        "you are prompted to choose which tasks to assign the student to and to "
        "optionally fine-tune the hours per task. "
        "On confirmation a draft notification email is printed in the language of "
        "the project document (Canadian French or Canadian English) with both the "
        "student and the project lead on TO. The assignment is created with status "
        "proposed until confirmed."
    ),
    "confirm": (
        "Move a proposed assignment to confirmed status. If the student has only one "
        "proposed assignment, --project is optional and the single open assignment "
        "is confirmed automatically."
    ),
    "edit": (
        "Edit the planned hours for a specific task within an existing assignment. "
        "Both --project and --task are required to identify the row unambiguously."
    ),
    "remove": (
        "Cancel a student's assignment. Without --task, all task rows for that "
        "student on that project are cancelled and the assignment is fully removed. "
        "With --task, only the specified task row is cancelled. "
        "Cancelled rows are retained in assignments.csv as an audit trail; "
        "hours are freed on the student record."
    ),
    "status": (
        "Show the fill state of a project's tasks (total / filled / remaining hours "
        "per task, with assigned student numbers), or a summary for a student "
        "(hours committed, projects, status), or an overview of a company "
        "(active projects and fill rate). Use --all for a global overview."
    ),
    "list": (
        "Tabulate all students, projects, or companies. Filter by semester with "
        "--semester. Include inactive records with --inactive."
    ),
    "activate": (
        "Set a student, project, or company to active status. "
        "Reactivating a company does not automatically reactivate its projects; "
        "each project must be activated individually."
    ),
    "deactivate": (
        "Set a student, project, or company to inactive status. "
        "If any active assignments exist, a confirmation is required before "
        "proceeding; all affected assignments are cancelled. "
        "Deactivating a company hides all its projects from matching."
    ),
    "complete": (
        "Mark a student as completed. Purges documents and embeddings, retains "
        "history. If any active assignments exist, a confirmation is required "
        "before proceeding; all affected assignments are cancelled."
    ),
    "close": (
        "Mark a project as closed. Purges documents and embeddings, retains "
        "history. Closed projects are excluded from matching. "
        "If any active assignments exist, a confirmation is required before "
        "proceeding; all affected assignments are cancelled."
    ),
    "reassign": (
        "Move a student to a different semester. If the student has active "
        "assignments, you are asked whether this is an extension (keep current "
        "assignments and move to the new semester) or a reset (cancel all current "
        "assignments and start fresh). The choice and the semester change are both "
        "logged in the student's reassignment_history."
    ),
    "explain": (
        "Show a TF-IDF explanation for why a student matches a project. "
        "A TfidfVectorizer is fitted across the full document corpus so that IDF "
        "weights reflect global term rarity. The output table shows the top shared "
        "terms by product weight, plus terms unique to each side. "
        "Terms present in the project but absent from the student's documents "
        "indicate skills the project needs that the student has not mentioned."
    ),
    "dashboard": (
        "Display a live terminal dashboard showing placement rates per study "
        "program, project fill status per company, and the list of unplaced "
        "students. Filter to a specific semester with --semester."
    ),
    "web": (
        "Start a local HTTP server (bound to 127.0.0.1 only) and open a visual "
        "dashboard in the browser. The dashboard queries live JSON endpoints and "
        "allows browsing student, project, and company records with links to their "
        "source documents. No data leaves the machine."
    ),
}

_EXAMPLES = {
    "ingest": [
        ("ingest cv.pdf --type s --id 2134567 --p GDIM --semester 2025-H",
         "Ingest a student CV."),
        ("ingest cover.pdf --type s --id 2134567 --p GDIM",
         "Add a cover letter to the same student."),
        ("ingest studio_noko_desc.pdf --type c",
         "Ingest a company description."),
        ("ingest refonte_ui.pdf --type p --company studio_noko --semester 2025-H",
         "Ingest a project proposal."),
    ],
    "match": [
        ("match --student 2134567",
         "Show top 5 project matches for a student."),
        ("match --student 2134567 --n 10 --semester 2025-H",
         "Show top 10 matches restricted to 2025-H projects."),
        ("match --student --search dupont",
         "Find student(s) named Dupont, then match."),
        ("match --company \"studio noko\"",
         "Show top student matches for all active Studio Noko projects."),
        ("match --company \"studio noko\" --project studio_noko_refonte_ui_2025H",
         "Match against a specific project only."),
    ],
    "assign": [
        ("assign 2134567 studio_noko_refonte_ui_2025H --semester 2025-H",
         "Interactively assign a student to a project."),
    ],
    "confirm": [
        ("confirm 2134567",
         "Confirm the single proposed assignment for this student."),
        ("confirm 2134567 --project studio_noko_refonte_ui_2025H",
         "Confirm a specific proposed assignment."),
    ],
    "edit": [
        ("edit 2134567 --project studio_noko_refonte_ui_2025H --task t2",
         "Edit hours for a specific task."),
    ],
    "remove": [
        ("remove 2134567 --project studio_noko_refonte_ui_2025H",
         "Remove student from all tasks on a project."),
        ("remove 2134567 --project studio_noko_refonte_ui_2025H --task t2",
         "Remove student from one task only."),
    ],
    "status": [
        ("status --project studio_noko_refonte_ui_2025H",
         "Show task fill state for a project."),
        ("status --student 2134567",
         "Show assignment summary for a student."),
        ("status --company \"studio noko\"",
         "Show project overview for a company."),
        ("status --all",
         "Global overview of all entities."),
    ],
    "list": [
        ("list students --semester 2025-H",
         "List all students in the 2025-H cohort."),
        ("list projects --inactive",
         "List all projects including inactive ones."),
        ("list companies",
         "List all companies."),
    ],
    "activate": [
        ("activate --company \"studio noko\"",
         "Reactivate a company for a new semester."),
        ("activate --project studio_noko_refonte_ui_2025H",
         "Activate a specific project."),
    ],
    "deactivate": [
        ("deactivate --student 2134567",
         "Deactivate a student (cancels open assignments after confirmation)."),
        ("deactivate --company \"studio noko\"",
         "Deactivate a company and hide all its projects from matching."),
    ],
    "complete": [
        ("complete 2134567",
         "Mark student as completed and purge their documents."),
    ],
    "close": [
        ("close studio_noko_refonte_ui_2025H",
         "Close a project, purge its documents, cancel active assignments."),
    ],
    "reassign": [
        ("reassign 2134567 --semester 2025-A",
         "Move student to fall 2025; prompts extension or reset if assignments exist."),
    ],
    "explain": [
        ("explain 2134567 --project studio_noko_refonte_ui_2025H",
         "Show top 10 shared TF-IDF terms for this pair."),
        ("explain 2134567 --project studio_noko_refonte_ui_2025H --top-n 20",
         "Show top 20 shared terms."),
    ],
    "dashboard": [
        ("dashboard",
         "Show live terminal dashboard across all semesters."),
        ("dashboard --semester 2025-H",
         "Dashboard filtered to the 2025-H cohort."),
    ],
    "web": [
        ("web",
         "Start web dashboard on default port 8080."),
        ("web --port 9090 --semester 2025-H",
         "Start on a custom port, scoped to 2025-H."),
    ],
}

_SEE_ALSO = {
    "ingest":            ["innovhub-match(1)", "innovhub-status(1)"],
    "match":             ["innovhub-ingest(1)", "innovhub-assign(1)", "innovhub-explain(1)"],
    "assign":            ["innovhub-match(1)", "innovhub-confirm(1)", "innovhub-remove(1)"],
    "confirm":           ["innovhub-assign(1)"],
    "edit":   ["innovhub-assign(1)", "innovhub-remove(1)"],
    "remove": ["innovhub-assign(1)", "innovhub-edit(1)"],
    "status":            ["innovhub-list(1)", "innovhub-dashboard(1)"],
    "list":              ["innovhub-status(1)", "innovhub-dashboard(1)"],
    "activate":          ["innovhub-deactivate(1)"],
    "deactivate":        ["innovhub-activate(1)", "innovhub-complete(1)"],
    "complete":          ["innovhub-deactivate(1)"],
    "close":     ["innovhub-deactivate(1)", "innovhub-complete(1)"],
    "reassign":          ["innovhub-status(1)", "innovhub-remove(1)"],
    "explain":           ["innovhub-match(1)"],
    "dashboard":         ["innovhub-web(1)", "innovhub-status(1)"],
    "web":     ["innovhub-dashboard(1)"],
}


# ── groff helpers ─────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape backslashes and hyphens for groff."""
    return text.replace("\\", "\\\\").replace("-", "\\-")


def _wrap(text: str, width: int = 72) -> str:
    return "\n".join(textwrap.wrap(text, width))


def _extract_options(sub: argparse.ArgumentParser) -> list[tuple[str, str]]:
    options = []
    for action in sub._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            flag = ", ".join(action.option_strings)
            if action.metavar:
                flag += f" {action.metavar}"
            options.append((flag, action.help or ""))
        else:
            label = action.metavar or action.dest.upper()
            options.append((label, action.help or ""))
    return options


def _synopsis(name: str, sub: argparse.ArgumentParser) -> str:
    parts = [f"innovhub {name}"]
    for action in sub._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            flag = action.option_strings[0]
            if action.metavar:
                flag += f" {action.metavar}"
            required = getattr(action, "required", False)
            parts.append(flag if required else f"[{flag}]")
        else:
            parts.append(action.metavar or action.dest.upper())
    return " ".join(parts)


# ── Page renderers ────────────────────────────────────────────────────────────

def _render_subcommand(
    name: str,
    sub: argparse.ArgumentParser,
) -> str:
    lines = [
        f'.TH "{_esc("innovhub-" + name).upper()}" {SECTION} "{DATE_STR}" "Innovation Hub" "Innovation Hub CLI"',
        "",
        ".SH NAME",
        f"{_esc('innovhub-' + name)} \\- {_esc(_DESCRIPTIONS.get(name, '')[:60])}",
        "",
        ".SH SYNOPSIS",
        f".B {_esc(_synopsis(name, sub))}",
        "",
        ".SH DESCRIPTION",
    ]

    for para in _DESCRIPTIONS.get(name, "").split("\n\n"):
        lines += [".PP", _esc(_wrap(para)), ""]

    options = _extract_options(sub)
    if options:
        lines.append(".SH OPTIONS")
        for flag, help_text in options:
            lines += [".TP", f".B {_esc(flag)}", _esc(help_text) or "\\&", ""]

    examples = _EXAMPLES.get(name, [])
    if examples:
        lines.append(".SH EXAMPLES")
        for cmd, desc in examples:
            lines += [".PP", f".B innovhub {_esc(cmd)}", ".br", _esc(desc), ""]

    see_also = _SEE_ALSO.get(name, ["innovhub(1)"])
    lines += [
        ".SH SEE ALSO",
        ", ".join(_esc(s) for s in see_also),
        "",
    ]

    return "\n".join(lines)


def _render_toplevel(subcommand_map: dict) -> str:
    lines = [
        f'.TH "INNOVHUB" {SECTION} "{DATE_STR}" "Innovation Hub" "Innovation Hub CLI"',
        "",
        ".SH NAME",
        "innovhub \\- offline student\\-project matching tool",
        "",
        ".SH SYNOPSIS",
        ".B innovhub",
        ".I COMMAND",
        ".RI [ OPTIONS ]",
        "",
        ".SH DESCRIPTION",
        ".PP",
        _esc(_wrap(_DESCRIPTIONS["innovhub"])),
        "",
        ".SH COMMANDS",
    ]
    for name in subcommand_map:
        lines += [
            ".TP",
            f".B {_esc(name)}",
            _esc(_DESCRIPTIONS.get(name, "")),
            "",
        ]
    lines += [
        ".SH SEE ALSO",
        ", ".join(f"innovhub\\-{_esc(n)}(1)" for n in subcommand_map),
        "",
    ]
    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_all(parser: argparse.ArgumentParser) -> None:
    """
    Generate one .1 file per subcommand plus innovhub.1.
    Called by: python main.py --generate-man
    """
    MAN_DIR.mkdir(parents=True, exist_ok=True)

    subparsers_action = next(
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    subcommand_map = subparsers_action.choices  # {name: ArgumentParser}

    # Top-level page
    (MAN_DIR / "innovhub.1").write_text(_render_toplevel(subcommand_map))
    print("  wrote  man/innovhub.1")

    # One page per subcommand
    for name, sub in subcommand_map.items():
        content = _render_subcommand(name, sub)
        filename = f"innovhub-{name}.1"
        (MAN_DIR / filename).write_text(content)
        print(f"  wrote  man/{filename}")

    total = 1 + len(subcommand_map)
    print(f"\n  {total} man pages written to {MAN_DIR}/")
    print("  Run install.sh to install them system-wide.")
