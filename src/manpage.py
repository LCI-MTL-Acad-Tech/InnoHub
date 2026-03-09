"""
manpage.py — generate groff .1 man pages from the argparse definitions in main.py.
Called via:  python main.py --generate-man
Writes:
    man/innovhub.1                  — top-level overview, all commands listed
    man/innovhub-<command>.1        — one page per subcommand
After generation, run install.sh to install them to ~/.local/share/man/man1/.
"""
import argparse
import textwrap
from datetime import date
from pathlib import Path


# ── Per-command enrichment ────────────────────────────────────────────────────
# Anything not derivable from argparse alone (description, examples, see-also).

_ENRICHMENT: dict[str, dict] = {
    "__main__": {
        "description": (
            "Innovation Hub is a fully offline CLI tool for matching students "
            "to company projects based on semantic similarity of their documents. "
            "All data stays on the local machine; no document is ever uploaded "
            "to an external service."
        ),
        "examples": [
            ("python main.py ingest cv.pdf --type s --id 2134567 --p GDIM",
             "Ingest a student CV."),
            ("python main.py match --student 2134567",
             "Find the best project matches for a student."),
            ("python main.py dashboard",
             "Open the CLI dashboard."),
            ("python main.py dashboard-web",
             "Start the local web dashboard at http://127.0.0.1:8080."),
        ],
    },
    "ingest": {
        "description": (
            "Parse, embed, and store one or more documents. "
            "For students (--type s), both --id and --p are required. "
            "For projects (--type p), --company is required. "
            "Duplicate detection runs automatically: similar companies prompt "
            "a merge-or-keep choice; similar projects from the same company "
            "prompt an update-or-keep choice; a re-uploaded student prompts "
            "confirmation before replacing existing documents."
        ),
        "examples": [
            ("python main.py ingest cv.pdf lettre.pdf --type s --id 2134567 --p GDIM --semester 2025-H",
             "Ingest a student CV and cover letter."),
            ("python main.py ingest studio_noko.pdf --type c",
             "Ingest a company description."),
            ("python main.py ingest projet.pdf --type p --company studio_noko --semester 2025-H",
             "Ingest a project proposal."),
        ],
        "see_also": ["innovhub-match", "innovhub-status"],
    },
    "match": {
        "description": (
            "Compute cosine similarity between the target's embedding and all "
            "eligible counterparts. Results are filtered to active entities with "
            "remaining hours before ranking. "
            "Students are identified by their student number; use --search for "
            "regex lookup by name or email. "
            "Companies are resolved by fuzzy name match; an ambiguous query "
            "prompts for clarification. "
            "Inactive projects and students are hidden by default; use --inactive "
            "to include them."
        ),
        "examples": [
            ("python main.py match --student 2134567",
             "Show top 5 project matches for a student."),
            ("python main.py match --student 2134567 --n 20 --semester 2025-H",
             "Show top 20 matches filtered to a specific semester."),
            ("python main.py match --student --search dupont",
             "Find student by name, then show matches."),
            ("python main.py match --company \"studio noko\"",
             "Show best student matches for all projects at a company."),
        ],
        "see_also": ["innovhub-explain", "innovhub-assign", "innovhub-status"],
    },
    "assign": {
        "description": (
            "Interactively assign a student to a project. "
            "Presents the project's task list with hours; the operator selects "
            "which tasks to assign and may fine-tune the hours for each. "
            "On confirmation, a draft notification email is generated in the "
            "detected language of the project documents (Canadian French or "
            "Canadian English) and printed to stdout for copy-paste into Outlook. "
            "The assignment is created with status 'proposed' until confirmed "
            "with the confirm command."
        ),
        "examples": [
            ("python main.py assign 2134567 studio_noko_refonte_ui_2025H --semester 2025-H",
             "Assign a student to a project interactively."),
        ],
        "see_also": ["innovhub-confirm", "innovhub-remove-assignment", "innovhub-match"],
    },
    "confirm": {
        "description": (
            "Move a proposed assignment to confirmed status. "
            "If the student has only one proposed assignment, --project may be "
            "omitted and it resolves automatically. "
            "If multiple proposed assignments exist, --project is required."
        ),
        "examples": [
            ("python main.py confirm 2134567 --project studio_noko_refonte_ui_2025H",
             "Confirm a specific proposed assignment."),
            ("python main.py confirm 2134567",
             "Confirm the only open proposed assignment for this student."),
        ],
        "see_also": ["innovhub-assign", "innovhub-remove-assignment"],
    },
    "edit-assignment": {
        "description": (
            "Edit the planned hours for a specific task within an existing assignment. "
            "Both --project and --task are required to uniquely identify the row. "
            "The student's hours_available is adjusted to reflect the difference."
        ),
        "examples": [
            ("python main.py edit-assignment 2134567 --project studio_noko_refonte_ui_2025H --task t2",
             "Interactively edit hours for task t2."),
        ],
        "see_also": ["innovhub-remove-assignment", "innovhub-assign"],
    },
    "remove-assignment": {
        "description": (
            "Cancel a student's assignment to a project. "
            "Without --task, all task rows for that student–project pair are cancelled "
            "and the full committed hours are returned to the student. "
            "With --task, only that one task row is cancelled. "
            "Cancelled rows are retained in assignments.csv for audit purposes."
        ),
        "examples": [
            ("python main.py remove-assignment 2134567 --project studio_noko_refonte_ui_2025H",
             "Remove student from all tasks on a project."),
            ("python main.py remove-assignment 2134567 --project studio_noko_refonte_ui_2025H --task t2",
             "Remove student from one specific task only."),
        ],
        "see_also": ["innovhub-assign", "innovhub-edit-assignment"],
    },
    "status": {
        "description": (
            "Show detailed status for a student, project, or company. "
            "For projects: a per-task table of total, filled, and remaining hours "
            "with assigned student numbers. "
            "For students: committed hours, projects, and current status. "
            "For companies: active projects and overall fill rate. "
            "--all shows a summary across every entity."
        ),
        "examples": [
            ("python main.py status --project studio_noko_refonte_ui_2025H",
             "Show task-level fill status for a project."),
            ("python main.py status --student 2134567",
             "Show assignment summary for a student."),
            ("python main.py status --company \"studio noko\"",
             "Show all projects and fill rates for a company."),
            ("python main.py status --all",
             "Show summary for every entity in the system."),
        ],
        "see_also": ["innovhub-dashboard", "innovhub-list"],
    },
    "list": {
        "description": (
            "Print a tabulated list of all students, projects, or companies. "
            "Use --semester to restrict to a specific semester. "
            "Inactive entities are hidden by default; use --inactive to include them."
        ),
        "examples": [
            ("python main.py list students --semester 2025-H",
             "List all active students for a semester."),
            ("python main.py list projects --inactive",
             "List all projects including inactive ones."),
        ],
        "see_also": ["innovhub-status", "innovhub-dashboard"],
    },
    "activate": {
        "description": (
            "Set a student, project, or company to active status. "
            "Reactivating a company makes the company itself active but does not "
            "automatically reactivate its individual projects — each project must "
            "be activated separately. "
            "--semester is optional and used for logging purposes."
        ),
        "examples": [
            ("python main.py activate --company \"studio noko\" --semester 2025-H",
             "Reactivate a company for a new semester."),
            ("python main.py activate --project studio_noko_refonte_ui_2025H",
             "Activate a specific project."),
        ],
        "see_also": ["innovhub-deactivate"],
    },
    "deactivate": {
        "description": (
            "Set a student, project, or company to inactive status. "
            "If active assignments exist, a confirmation prompt lists them and "
            "asks whether to proceed; confirming cancels all affected assignments. "
            "Deactivating a company hides all its projects from matching."
        ),
        "examples": [
            ("python main.py deactivate --student 2134567",
             "Deactivate a student (cancels open assignments after confirmation)."),
            ("python main.py deactivate --company \"studio noko\"",
             "Deactivate a company and all its projects."),
        ],
        "see_also": ["innovhub-activate", "innovhub-complete"],
    },
    "complete": {
        "description": (
            "Mark a student as completed. "
            "Their source documents and embedding file are permanently deleted "
            "from disk. All rows in assignments.csv are retained as a historical "
            "log. This action cannot be undone; a confirmation prompt is shown "
            "before any files are removed."
        ),
        "examples": [
            ("python main.py complete 2134567",
             "Mark a student as completed and purge their documents."),
        ],
        "see_also": ["innovhub-deactivate", "innovhub-reassign"],
    },
    "reassign": {
        "description": (
            "Move a student to a different semester. "
            "Updates semester_start in the student metadata and appends an entry "
            "to their reassignment_history. Does not affect existing assignment rows."
        ),
        "examples": [
            ("python main.py reassign 2134567 --semester 2025-A",
             "Move a student to the autumn 2025 semester."),
        ],
        "see_also": ["innovhub-activate", "innovhub-complete"],
    },
    "explain": {
        "description": (
            "Show a TF-IDF explanation for why a student matches a specific project. "
            "Fits a TfidfVectorizer across the full document corpus so that IDF "
            "weights reflect global term rarity. "
            "Outputs a table of the top shared terms (by product of both TF-IDF "
            "weights), followed by terms present in the student's documents only "
            "and terms present in the project documents only. "
            "The latter two columns help identify gaps between what the student "
            "offers and what the project requires."
        ),
        "examples": [
            ("python main.py explain 2134567 --project studio_noko_refonte_ui_2025H",
             "Show top 10 shared terms for a student–project pair."),
            ("python main.py explain 2134567 --project studio_noko_refonte_ui_2025H --top-n 20",
             "Show top 20 shared terms."),
        ],
        "see_also": ["innovhub-match", "innovhub-assign"],
    },
    "dashboard": {
        "description": (
            "Display a CLI dashboard using rich. "
            "Shows: overall placement counts, placement rate per program "
            "(assigned / total with percentage), project fill status per company, "
            "and a list of unplaced students grouped by program. "
            "Use --semester to scope all panels to a specific semester; "
            "omit it to show all-time data."
        ),
        "examples": [
            ("python main.py dashboard",
             "Show the full dashboard across all semesters."),
            ("python main.py dashboard --semester 2025-H",
             "Show the dashboard scoped to hiver 2025."),
        ],
        "see_also": ["innovhub-dashboard-web", "innovhub-status", "innovhub-list"],
    },
    "dashboard-web": {
        "description": (
            "Start a local HTTP server and open an interactive web dashboard. "
            "The server binds to 127.0.0.1 only and is never reachable from "
            "outside the local machine. "
            "The dashboard reads live data from disk on every request. "
            "Source documents can be previewed or downloaded directly from the "
            "student, project, and company detail cards. "
            "Press Ctrl+C to stop the server."
        ),
        "examples": [
            ("python main.py dashboard-web",
             "Start the web dashboard on the default port (8080)."),
            ("python main.py dashboard-web --port 9090 --semester 2025-H",
             "Start on a custom port, scoped to a specific semester."),
        ],
        "see_also": ["innovhub-dashboard"],
    },
}


# ── Groff rendering helpers ───────────────────────────────────────────────────

def _groff_escape(text: str) -> str:
    """Escape backslashes and hyphens for groff."""
    return text.replace("\\", "\\\\").replace("-", "\\-")


def _render_option(action: argparse.Action) -> str:
    """Render one argparse action as a groff .TP block."""
    if not action.option_strings and action.dest == argparse.SUPPRESS:
        return ""

    lines = []
    if action.option_strings:
        opts = ", ".join(
            f"\\fB{_groff_escape(o)}\\fR" +
            (f" \\fI{action.metavar}\\fR" if action.metavar else "")
            for o in action.option_strings
        )
    else:
        opts = f"\\fI{_groff_escape(action.metavar or action.dest.upper())}\\fR"

    lines.append(".TP")
    lines.append(opts)

    help_text = action.help or ""
    if action.default and action.default is not argparse.SUPPRESS:
        help_text += f" Default: {action.default}."
    if action.choices:
        help_text += f" Choices: {', '.join(str(c) for c in action.choices)}."

    lines.append(_groff_escape(help_text))
    return "\n".join(lines)


def _render_page(
    name: str,
    section: int,
    date_str: str,
    synopsis: str,
    description: str,
    options_block: str,
    examples: list[tuple[str, str]],
    see_also: list[str],
) -> str:
    """Assemble a complete groff man page string."""

    parts = []

    # Header
    parts.append(
        f'.TH "{name.upper()}" {section} "{date_str}" '
        f'"Innovation Hub" "Innovation Hub CLI"'
    )

    # Name
    parts.append(".SH NAME")
    short_desc = _ENRICHMENT.get(
        name.removeprefix("innovhub-") or "__main__", {}
    ).get("description", "")
    short_desc = short_desc.split(".")[0]  # first sentence only
    parts.append(f"{_groff_escape(name)} \\- {_groff_escape(short_desc)}")

    # Synopsis
    parts.append(".SH SYNOPSIS")
    parts.append(".nf")
    parts.append(_groff_escape(synopsis))
    parts.append(".fi")

    # Description
    parts.append(".SH DESCRIPTION")
    for para in description.strip().split("\n\n"):
        parts.append(".PP")
        parts.append(_groff_escape(" ".join(para.split())))

    # Options
    if options_block.strip():
        parts.append(".SH OPTIONS")
        parts.append(options_block)

    # Examples
    if examples:
        parts.append(".SH EXAMPLES")
        for cmd, desc in examples:
            parts.append(".TP")
            parts.append(f"\\fB{_groff_escape(cmd)}\\fR")
            parts.append(_groff_escape(desc))

    # See also
    if see_also:
        parts.append(".SH SEE ALSO")
        refs = ", ".join(
            f"\\fB{_groff_escape(r)}\\fR(1)" for r in see_also
        )
        parts.append(refs)

    return "\n".join(parts) + "\n"


# ── Top-level innovhub.1 ──────────────────────────────────────────────────────

def _generate_main_page(
    parser: argparse.ArgumentParser,
    date_str: str,
    out_dir: Path,
) -> None:
    enrich = _ENRICHMENT["__main__"]

    synopsis_lines = ["innovhub COMMAND [OPTIONS]", "innovhub --help"]
    synopsis = "\n".join(synopsis_lines)

    # Build a .TP list of subcommands
    options_lines = []
    for action in parser._subparsers._group_actions:  # type: ignore[attr-defined]
        if hasattr(action, "_name_parser_map"):
            for sub_name, sub_parser in action._name_parser_map.items():
                options_lines.append(".TP")
                options_lines.append(f"\\fB{_groff_escape(sub_name)}\\fR")
                options_lines.append(_groff_escape(sub_parser.description or sub_parser._description or ""))
    options_block = "\n".join(options_lines)

    page = _render_page(
        name="innovhub",
        section=1,
        date_str=date_str,
        synopsis=synopsis,
        description=enrich["description"],
        options_block=options_block,
        examples=enrich.get("examples", []),
        see_also=[f"innovhub-{c}" for c in [
            "ingest", "match", "assign", "confirm", "explain",
            "status", "list", "dashboard", "dashboard-web",
        ]],
    )
    (out_dir / "innovhub.1").write_text(page)


# ── Per-subcommand pages ──────────────────────────────────────────────────────

def _generate_sub_page(
    name: str,
    sub_parser: argparse.ArgumentParser,
    date_str: str,
    out_dir: Path,
) -> None:
    enrich = _ENRICHMENT.get(name, {})
    page_name = f"innovhub-{name}"

    # Synopsis: reconstruct from argparse
    synopsis_parts = [f"innovhub {name}"]
    for action in sub_parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            opt = action.option_strings[0]
            if action.required:
                synopsis_parts.append(
                    f"{opt} {action.metavar or action.dest.upper()}"
                )
            else:
                metavar = f" {action.metavar}" if action.metavar else ""
                synopsis_parts.append(f"[{opt}{metavar}]")
        else:
            synopsis_parts.append(
                action.metavar or action.dest.upper()
            )
    synopsis = " ".join(synopsis_parts)

    # Options block
    option_parts = []
    for action in sub_parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        rendered = _render_option(action)
        if rendered:
            option_parts.append(rendered)
    options_block = "\n".join(option_parts)

    description = enrich.get("description", sub_parser.description or "")
    examples    = enrich.get("examples", [])
    see_also    = enrich.get("see_also", ["innovhub"])

    page = _render_page(
        name=page_name,
        section=1,
        date_str=date_str,
        synopsis=synopsis,
        description=description,
        options_block=options_block,
        examples=examples,
        see_also=see_also,
    )
    (out_dir / f"{page_name}.1").write_text(page)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_all(parser: argparse.ArgumentParser) -> None:
    """Generate all man pages into the man/ directory."""
    out_dir = Path("man")
    out_dir.mkdir(exist_ok=True)

    date_str = date.today().strftime("%Y\\-%m\\-%d")

    _generate_main_page(parser, date_str, out_dir)

    for action in parser._subparsers._group_actions:  # type: ignore[attr-defined]
        if hasattr(action, "_name_parser_map"):
            for sub_name, sub_parser in action._name_parser_map.items():
                _generate_sub_page(sub_name, sub_parser, date_str, out_dir)

    pages = sorted(out_dir.glob("*.1"))
    print(f"Generated {len(pages)} man pages in {out_dir}/:")
    for p in pages:
        print(f"  {p.name}")
    print("\nRun install.sh to install them.")
