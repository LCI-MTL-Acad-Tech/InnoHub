"""
main.py — Innovation Hub CLI entry point.
All commands are defined here; logic lives in src/.
"""
import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="innovhub",
        description="Innovation Hub — student-project matching tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--generate-man", action="store_true",
        help="Generate groff man pages into man/ and exit."
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── ingest ────────────────────────────────────────────────────────────────
    p_ingest = sub.add_parser("ingest", help="Add documents to the system.")
    p_ingest.add_argument("files", nargs="+", metavar="FILE")
    p_ingest.add_argument(
        "--type", "-t", required=True,
        choices=["s", "student", "c", "company", "p", "project"],
        metavar="TYPE", help="s|student  c|company  p|project"
    )
    p_ingest.add_argument("--id",      metavar="STUDENT_NUMBER", help="Student number (required for --type s).")
    p_ingest.add_argument("--program", "--p", metavar="CODE",    help="Program code (required for --type s).")
    p_ingest.add_argument("--company", metavar="COMPANY_ID",     help="Company ID (required for --type p).")
    p_ingest.add_argument("--semester", metavar="TAG",           help="e.g. 2025-H")

    # ── match ─────────────────────────────────────────────────────────────────
    p_match = sub.add_parser("match", help="Find best matches for a student or project.")
    target = p_match.add_mutually_exclusive_group(required=True)
    target.add_argument("--student",  metavar="STUDENT_NUMBER")
    target.add_argument("--company",  metavar="NAME")
    p_match.add_argument("--project", metavar="PROJECT_ID")
    p_match.add_argument("--search",  metavar="QUERY", help="Regex search on student name or email.")
    p_match.add_argument("--n",       metavar="N",   type=int, default=5)
    p_match.add_argument("--all",     action="store_true")
    p_match.add_argument("--semester",metavar="TAG")
    p_match.add_argument("--inactive",action="store_true")

    # ── assign ────────────────────────────────────────────────────────────────
    p_assign = sub.add_parser("assign", help="Assign a student to a project (interactive).")
    p_assign.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_assign.add_argument("project_id",     metavar="PROJECT_ID")
    p_assign.add_argument("--semester",     metavar="TAG", required=True)

    # ── confirm ───────────────────────────────────────────────────────────────
    p_confirm = sub.add_parser("confirm", help="Confirm a proposed assignment.")
    p_confirm.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_confirm.add_argument("--project",      metavar="PROJECT_ID")

    # ── edit-assignment ───────────────────────────────────────────────────────
    p_edit = sub.add_parser("edit-assignment", help="Edit hours for a specific task assignment.")
    p_edit.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_edit.add_argument("--project",      metavar="PROJECT_ID", required=True)
    p_edit.add_argument("--task",         metavar="TASK_ID",    required=True)

    # ── remove-assignment ─────────────────────────────────────────────────────
    p_remove = sub.add_parser("remove-assignment", help="Remove a student from a project (all or one task).")
    p_remove.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_remove.add_argument("--project",      metavar="PROJECT_ID", required=True)
    p_remove.add_argument("--task",         metavar="TASK_ID")

    # ── status ────────────────────────────────────────────────────────────────
    p_status = sub.add_parser("status", help="Show status of a student, project, or company.")
    s_target = p_status.add_mutually_exclusive_group(required=True)
    s_target.add_argument("--student",  metavar="STUDENT_NUMBER")
    s_target.add_argument("--project",  metavar="PROJECT_ID")
    s_target.add_argument("--company",  metavar="NAME")
    s_target.add_argument("--all",      action="store_true")
    p_status.add_argument("--search",   metavar="QUERY")

    # ── list ──────────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List students, projects, or companies.")
    p_list.add_argument("what", choices=["students", "projects", "companies"])
    p_list.add_argument("--semester", metavar="TAG")
    p_list.add_argument("--inactive", action="store_true")

    # ── activate / deactivate ─────────────────────────────────────────────────
    for cmd in ("activate", "deactivate"):
        p_act = sub.add_parser(cmd, help=f"{cmd.capitalize()} a student, project, or company.")
        a_target = p_act.add_mutually_exclusive_group(required=True)
        a_target.add_argument("--student", metavar="STUDENT_NUMBER")
        a_target.add_argument("--project", metavar="PROJECT_ID")
        a_target.add_argument("--company", metavar="NAME")
        p_act.add_argument("--semester",   metavar="TAG")

    # ── close-project ─────────────────────────────────────────────────────────
    p_close = sub.add_parser("close-project", help="Close a project (purges documents, retains CSV history).")
    p_close.add_argument("project_id", metavar="PROJECT_ID")

    # ── complete ──────────────────────────────────────────────────────────────
    p_complete = sub.add_parser("complete", help="Mark a student as completed (purges documents).")
    p_complete.add_argument("student_number", metavar="STUDENT_NUMBER")

    # ── reassign ──────────────────────────────────────────────────────────────
    p_reassign = sub.add_parser("reassign", help="Move a student to a different semester.")
    p_reassign.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_reassign.add_argument("--semester",     metavar="TAG", required=True)

    # ── explain ───────────────────────────────────────────────────────────────
    p_explain = sub.add_parser(
        "explain",
        help="Show TF-IDF explanation for a student–project match."
    )
    p_explain.add_argument("student_number", metavar="STUDENT_NUMBER")
    p_explain.add_argument("--project",      metavar="PROJECT_ID", required=True)
    p_explain.add_argument("--top-n",        metavar="N", type=int, default=10,
                           help="Number of shared terms to show. Default: 10.")

    # ── dashboard ─────────────────────────────────────────────────────────────
    p_dash = sub.add_parser("dashboard", help="Show CLI dashboard.")
    p_dash.add_argument("--semester", metavar="TAG")

    # ── dashboard-web ─────────────────────────────────────────────────────────
    p_web = sub.add_parser("dashboard-web", help="Start local web dashboard (http://127.0.0.1:8080).")
    p_web.add_argument("--port",     metavar="PORT", type=int, default=8080)
    p_web.add_argument("--semester", metavar="TAG")

    return parser


def main():
    from src.setup_wizard import needs_setup, run_wizard
    from src.bootstrap import bootstrap

    # First-run: no arguments and data/ absent → offer guided setup
    if len(sys.argv) == 1 and needs_setup():
        run_wizard()
        return

    bootstrap()

    parser = build_parser()
    args   = parser.parse_args()

    if args.command is None and not getattr(args, "generate_man", False):
        parser.print_help()
        sys.exit(0)

    if getattr(args, "generate_man", False):
        from src.manpage import generate_all
        generate_all(parser)
        return

    # Dispatch
    cmd = args.command
    if   cmd == "ingest":            from src.ingest        import run;           run(args)
    elif cmd == "match":             from src.match         import run;           run(args)
    elif cmd == "assign":            from src.assign        import run_assign;    run_assign(args)
    elif cmd == "confirm":           from src.assign        import run_confirm;   run_confirm(args)
    elif cmd == "edit-assignment":   from src.assign        import run_edit;      run_edit(args)
    elif cmd == "remove-assignment": from src.assign        import run_remove;    run_remove(args)
    elif cmd == "status":            from src.match         import run_status;    run_status(args)
    elif cmd == "list":              from src.match         import run_list;      run_list(args)
    elif cmd in ("activate",
                 "deactivate"):      from src.lifecycle     import run;           run(args)
    elif cmd == "close-project":     from src.lifecycle     import run_close;     run_close(args)
    elif cmd == "complete":          from src.lifecycle     import run_complete;  run_complete(args)
    elif cmd == "reassign":          from src.lifecycle     import run_reassign;  run_reassign(args)
    elif cmd == "explain":           from src.match         import run_explain;   run_explain(args)
    elif cmd == "dashboard-web":     from src.dashboard_web import run;           run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
