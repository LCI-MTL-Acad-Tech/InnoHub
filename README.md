# Innovation Hub — Student-Project Matching CLI

Fully offline CLI tool for placing students in company projects.  
No document ever leaves your machine.

## What it does

- Ingests student CVs, company descriptions, and project proposals (PDF, DOCX, HTML, plaintext)
- Embeds all documents locally using a multilingual sentence-transformer model
- Ranks students against projects (and vice versa) by semantic similarity
- Tracks assignments, hours, task breakdowns, confirmations, and cancellations
- Supports coordinators, program codes, semester management, and lifecycle operations
- Provides a CLI dashboard and a local web dashboard for visual exploration
- Logs every action to an append-only audit log

## Requirements

- Ubuntu 22.04+ (or any Linux with Python 3.11+)
- ~500 MB disk for the embedding model (downloaded once, then fully offline)

## First run

```bash
pip install -r requirements.txt
python main.py
```

The first run with no arguments launches the setup wizard, which:
- Generates `config.toml` from the template (asks for your name and email)
- Creates the full data directory structure
- Optionally downloads the embedding model
- Optionally installs man pages
- Optionally adds an `innovhub` shell alias

## Commands

```
ingest        Add documents (student CV, company description, project proposal, coordinator CV)
match         Find best-matching projects for a student, or students for a company
assign        Assign a student to a project (interactive, with email draft)
confirm       Confirm a proposed assignment
edit          Edit hours on a specific task assignment
remove        Remove a student from a project (one task or all)
status        Show fill status for a project, student, company, or coordinator
list          List students, projects, companies, or coordinators
activate      Reactivate a student, project, or company
deactivate    Deactivate (suspends matching; cancels open assignments after confirmation)
close         Close a project (purges documents, retains history)
complete      Mark a student as completed (purges documents, retains history)
reassign      Move a student to a different semester (extension or reset)
explain       Show TF-IDF term breakdown for a student–project match
coord         Attach or detach a coordinator from a project
dashboard     CLI dashboard with filters, grouping, and sorting
web           Local web dashboard at http://127.0.0.1:8080
```

Run `innovhub <command> --help` or `man innovhub-<command>` for details on any command.

## Data layout

```
data/
  students/           one JSON per student (student_number.json)
  companies/          one JSON per company (company_id.json)
  projects/           one JSON per project (project_id.json)
  coordinators/       one JSON per coordinator (email_safe.json)
  documents/          ingested source files, named <entity_id>_<doc_type>.<ext>
  embeddings/         .npy vectors, one per entity
  assignments.csv     one row per task per assignment (append-only log)
  programs.csv        known program codes and labels
  audit.log           append-only JSON-lines transaction log
```

## Man pages

```bash
bash install.sh       # generates and installs all man pages
man innovhub          # top-level overview
man innovhub-match    # per-command reference
```

## Configuration

Edit `config.toml` after first run to adjust:
- Similarity and dedup thresholds
- Semester terms, synonyms, and academic year start
- Default coordinator (shown when no coordinator is assigned to a project)
- Server host and port for the web dashboard
