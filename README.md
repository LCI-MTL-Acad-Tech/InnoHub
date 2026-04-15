# Innovation Hub — Student-Project Matching CLI

Fully offline CLI tool for placing students in company internship projects at
Collège LaSalle Montréal. Every document, embedding, and assignment record stays
on your machine. Nothing is sent to any external service after the initial model
download.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Before you start](#before-you-start)
3. [Installation](#installation)
4. [One-time configuration](#one-time-configuration)
5. [MS Forms setup](#ms-forms-setup)
6. [Each-semester workflow](#each-semester-workflow)
7. [Command reference](#command-reference)
8. [Data layout](#data-layout)
9. [Configuration reference](#configuration-reference)
10. [Troubleshooting](#troubleshooting)

---

## What it does

- Ingests student CVs and cover letters (PDF, DOCX, image, or scanned PDF via OCR)
- Ingests company descriptions and project proposals in the same formats
- Embeds all documents locally using a multilingual sentence-transformer model
- Ranks students against projects by semantic similarity
- Suggests competing team (replica) counts based on student supply and task structure
- Tracks assignments, hours, task breakdowns, confirmations, and cancellations
- Supports multiple coordinators, semester management, and full lifecycle operations
- Provides a CLI dashboard and a local web dashboard for visual exploration
- Logs every action to an append-only audit log

---

## Before you start

You will need:

- A Linux machine (Ubuntu 22.04+ recommended) or WSL2 on Windows
- Python 3.11 or newer
- Internet access for the one-time model download (~500 MB); fully offline after that
- Two Microsoft Forms set up as described in [MS Forms setup](#ms-forms-setup)
- The internship schedule for the semester: dates and hours per program code

---

## Installation

### 1. System dependencies

Tesseract OCR is required for image-format CVs (PNG, JPG) and scanned PDFs.

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra
```

### 2. Create a virtual environment and install Python packages

```bash
cd InnoHub/
python3 -m venv .
./bin/pip install -r requirements.txt
```

The first time you run a command that embeds a document, the sentence-transformer
model (~500 MB) is downloaded automatically to ~/.cache/huggingface/. This
requires internet access once and only once. All subsequent runs are fully offline.

### 3. First run — setup wizard

```bash
./bin/python main.py
```

Running with no arguments on a fresh install launches the setup wizard. It will:

- Ask for your name and email (used as the default coordinator)
- Generate config.toml from the template
- Create the full data/ directory structure
- Optionally trigger the model download
- Optionally install man pages system-wide via install.sh
- Optionally add an innovhub shell alias

After the wizard completes, all commands are available.

---

## One-time configuration

These steps are done once when the tool is first deployed. They do not need to
be repeated each semester unless programs or terminology change.

### 1. Review config.toml

Open config.toml and verify or adjust:

```toml
[coordinator]
name  = "Your Name"
email = "your.email@college-lasalle.qc.ca"

[semesters]
terms = ["Winter", "Summer", "Fall"]
academic_year_start = "Fall"
```

The term names in terms must match exactly what you will type when running
commands with --semester. For example, if terms = ["Winter", "Summer", "Fall"],
the semester string is always "Winter 2026", "Fall 2025", etc.

### 2. Review programs.csv

data/programs.csv lists all known program codes. It is pre-populated for
Collège LaSalle Montréal. If a new program is added, append a row:

```
code,label_fr,label_en,active
NEW.XX,Nom du programme,Program name,true
```

Set active to false to hide a program from matching without deleting it.

### 3. Set up your MS Forms

See MS Forms setup below — this is required before any import will work.

---

## MS Forms setup

The import command reads CSV or XLSX exports from two Microsoft Forms: one for
students, one for companies/projects. The question titles in the form must match
the strings below exactly (including capitalisation and spaces).

### Student form

Create a form with the following questions, using these exact titles:

  Question title                                                  | Type
  ----------------------------------------------------------------|-------------
  Votre numéro d'étudiant / Your student ID number               | Short text (required)
  Votre adresse e-mail LCI / Your college email                  | Short text (required)
  Nom de votre programme d'études / Your study program name      | Short text (required; note trailing space — keep it)
  Votre CV d'une page à jour / Your up-to-date one-page CV       | File upload
  Lettre de motivation / Cover letter                            | File upload (optional)
  URL de votre profil LinkedIn / Your LinkedIn profile URL       | Short text (optional)
  URL(s) de portfolio(s) / Your portfolio URL(s)                 | Short text (optional, comma-separated)

When you export responses from MS Forms, choose Download responses → Excel
workbook or CSV. Save the file as students.xlsx or students.csv inside your
raw/ folder.

CV and cover letter files uploaded through the form appear as SharePoint URLs in
the export. Download them manually into raw/CV/ and raw/CL/ respectively.
The filenames do not need to match exactly — the importer uses fuzzy matching on
student names extracted from the filename. Accented characters (e.g. Giguère)
are handled correctly regardless of Unicode normalization form.

### Company / project form

Create a second form with these exact question titles:

  Question title                                                  | Type
  ----------------------------------------------------------------|-------------
  Votre nom / Your name                                          | Short text (contact person)
  Adresse e-mail / Contact email                                 | Short text (required)
  Description du client / Client description                     | Long text
  Titre du projet / Project title                                | Short text (required)
  Description du projet / Project description                    | Long text
  Décomposition des tâches / Task breakdown                      | Long text (see note below)
  Envoyez-vous plus d'informations ? / Are you sending more information? | Choice: Oui/Non or Yes/No
  Veuillez décrire votre mode et fréquence de contact préférés pendant le travail sur le projet.\n\nPlease describe your preferred mode and frequency of contact during the project work.\n | Long text (optional)

Export and save as projects.xlsx or projects.csv in raw/.

Additional project documents (full proposals, briefs, etc.) go in raw/Desc/.
They are matched to projects by title keywords automatically.

#### Task breakdown field

The importer can extract structured tasks from the breakdown field in several
formats. Companies can write in any of these styles:

Bulleted list with hours:
  • Conception UX/UI : 50 h
  • Développement backend : 80 h
  • Tests et débogage : 40 h

Inline with parentheses:
  Conception UX/UI (50 h), développement backend (80 h), tests (40 h)

French prose with "pour N heures":
  …incluant la conception UX/UI pour 50 heures, le développement backend
  pour 80 heures, et les tests pour 40 heures.

Label-only (hours entered interactively during import):
  Develop a platform-specific social media strategy
  Create engaging content tailored to the audience
  Write SEO-optimized content for the website

If the importer cannot extract tasks automatically, it shows the full description
in a pager (less) and prompts for tasks and hours manually.

---

## Each-semester workflow

Follow these steps at the start of each new semester.

### Step 1 — Update semester_programs.csv

Before importing any students, add the internship schedule for the new semester
to data/semester_programs.csv. Each row maps a program code to its internship
course, hour count, and date window.

```
semester,program_code,course_code,hours,date_start,date_end
Winter 2026,420.BP,420-EP6-AS,255,2026-05-11,2026-06-30
Winter 2026,420.BR,420-SG6-AS,255,2026-05-11,2026-06-30
Winter 2026,420.BX,420-JST-AS,495,2026-05-11,2026-07-31
```

- semester must match the term name in your config exactly, e.g. Winter 2026
- program_code must match a code in programs.csv
- hours is the number of placement hours only (not including classroom preparation)
- date_start / date_end are ISO dates (YYYY-MM-DD)

The hours value is used to set each student's hours_available at ingest time
and to calculate how many students fit in a project team.

### Step 2 — Prepare your raw/ folder

Create a raw/ folder (anywhere — you pass its path to the import command):

```
raw/
  students.xlsx      — MS Forms student response export
  projects.xlsx      — MS Forms project response export (optional)
  CV/                — student CV files (PDF, DOCX, PNG, JPG…)
  CL/                — cover letters (optional)
  Desc/              — additional project documents (optional)
```

### Step 3 — Dry run

Always run the import in dry-run mode first to spot errors before writing anything:

```bash
./bin/python main.py import --dir raw/ --semester "Winter 2026" --dry-run
```

Review the output. Look for:
- warnings about unresolved program codes
- warnings about missing CV files
- projects where tasks could not be extracted automatically

### Step 4 — Import

```bash
./bin/python main.py import --dir raw/ --semester "Winter 2026"
```

The importer will:
- Resolve program codes from free-text entries (fuzzy matching, interactive for ambiguous cases)
- Infer IT stream (420.BP / 420.BR / 420.BX) from CV text when the program is
  listed as generic 420.B0
- Parse and embed all CVs and project documents
- Ask you to confirm or correct task breakdowns that could not be parsed automatically
- Skip students and projects already in the system (re-running is safe)

To re-import a single project (e.g. to fix its tasks), delete its JSON and re-run:

```bash
rm data/projects/<project_id>.json
./bin/python main.py import --dir raw/ --semester "Winter 2026"
```

### Step 5 — Verify

```bash
./bin/python main.py list students --semester "Winter 2026"
./bin/python main.py list projects --semester "Winter 2026"
```

### Step 6 — Suggest competing team counts

```bash
./bin/python main.py suggest-teams --semester "Winter 2026" --dry-run
```

The tool computes a max team size for each project from its task structure:
each task contributes floor(task_hours / 40) students to the team size, with a
minimum of 1 per task. For example, four tasks of 90 h each give a max team
size of 8. It then divides the relevant student pool by that team size to suggest
a replica count.

Review the table. When ready to write the counts:

```bash
./bin/python main.py suggest-teams --semester "Winter 2026"
```

You will be prompted to accept or override each suggestion before anything is written.

### Step 7 — Match and assign

Find best project matches for a student:

```bash
./bin/python main.py match --student 2134567
```

Find best student matches for a project:

```bash
./bin/python main.py match --company "Studio Noko"
```

Assign a student to a project:

```bash
./bin/python main.py assign 2134567 studio_noko_refonte_ui_2026W --semester "Winter 2026"
```

The assign command is interactive and generates an email draft at the end.

Confirm an assignment after the company accepts:

```bash
./bin/python main.py confirm 2134567
```

### Step 8 — Monitor

CLI dashboard (static snapshot):

```bash
./bin/python main.py dashboard --semester "Winter 2026"
```

Web dashboard (live, with filters):

```bash
./bin/python main.py web
```

Then open http://127.0.0.1:8080 in your browser.

### Step 9 — End of semester

Mark a student as completed (retains history, removes documents to free space):

```bash
./bin/python main.py complete 2134567
```

Close a project (retains history, removes documents):

```bash
./bin/python main.py close --project studio_noko_refonte_ui_2026W
```

---

## Command reference

Run ./bin/python main.py <command> --help for full options on any command.

  Command         | What it does
  ----------------|---------------------------------------------------------------
  import          | Bulk import from a raw/ folder (MS Forms CSV or XLSX export)
  suggest-teams   | Suggest competing team replica counts for a semester
  ingest          | Manually add a single document (CV, project proposal, etc.)
  match           | Find best-matching projects for a student, or vice versa
  assign          | Assign a student to a project (interactive, generates email draft)
  confirm         | Confirm a proposed assignment
  edit            | Edit hours on a specific task assignment
  remove          | Remove a student from a project (one task or all)
  status          | Show fill status for a project, student, company, or all
  list            | List students, projects, companies, or coordinators
  explain         | Show TF-IDF term breakdown for a student-project match
  activate        | Reactivate a student, project, or company
  deactivate      | Deactivate (suspends matching; prompts to cancel assignments)
  close           | Close a project (purges documents, retains assignment history)
  complete        | Mark a student as completed (purges documents, retains history)
  reassign        | Move a student to a different semester
  coord           | Attach or detach a coordinator from a project
  dashboard       | CLI dashboard with filters, grouping, and sorting
  web             | Local web dashboard at http://127.0.0.1:8080
  reset           | Wipe all data and start fresh (--hard also removes documents)

---

## Data layout

```
data/
  students/              one JSON per student (student_number.json)
  companies/             one JSON per company (company_id.json)
  projects/              one JSON per project (project_id.json)
  coordinators/          one JSON per coordinator (email_underscore.json)
  documents/
    students/            ingested CVs and cover letters
    companies/           company description documents
    projects/            project proposals
  embeddings/
    students/            .npy vector files, one per student
    companies/           .npy vector files, one per company
    projects/            .npy vector files, one per project
  assignments.csv        one row per task per assignment
  programs.csv           known program codes and labels
  semester_programs.csv  internship hours and dates per (semester, program_code)
  program_outcomes.json  learning outcomes and ministry competencies per program
  audit.log              append-only JSON-lines transaction log
```

To fix a typo in recorded hours, open assignments.csv in any text editor and
correct the hours_planned and hours_committed columns for the relevant row.
Find the right row by student_number, project_id, and task_label.

To fix a task definition on a project (label or hours), edit the capacity.tasks
array in data/projects/<project_id>.json directly.

---

## Configuration reference

config.toml is generated by the setup wizard. Key settings:

```toml
[matching]
similarity_threshold = 0.85   # minimum cosine score to surface a match
dedup_threshold      = 0.85   # minimum score to flag a duplicate at ingest
default_results      = 5      # how many results match returns by default

[server]
host = "127.0.0.1"
port = 8080                   # web dashboard port

[semesters]
terms = ["Winter", "Summer", "Fall"]
academic_year_start = "Fall"

[semesters.synonyms]
Winter = ["winter", "hiver", "h", "w"]
Summer = ["summer", "été", "s", "e"]
Fall   = ["fall", "automne", "a", "f"]

[coordinator]
name  = "Default Coordinator Name"
email = "coordinator@college-lasalle.qc.ca"
```

Synonyms let users type "Hiver 2026" or "H2026" and have it resolve to
"Winter 2026" internally. Add your institution's abbreviations as needed.

---

## Troubleshooting

**CUDA / GPU warnings on startup**

  UserWarning: CUDA initialization: The NVIDIA driver on your system is too old…

The embedding model falls back to CPU automatically. Safely ignored. The tool
works correctly on CPU — embedding 60 students takes a few seconds.

**"No active students found" in suggest-teams**

Run the following to diagnose:

```bash
./bin/python -c "
from src.store import list_ids, load_json
from pathlib import Path
for sid in list_ids('students')[:5]:
    m = load_json('students', sid)
    emb = m.get('embedding_file', '')
    print(sid, m.get('semester_start'), m.get('status'),
          'emb=' + str(Path(emb).exists() if emb else 'missing'))
"
```

Common causes:
- semester_start does not match the string passed to --semester (check spelling
  and capitalisation exactly)
- status is not active — use ./bin/python main.py activate --student <id>
- Embedding file is missing — re-run import with the CV present in raw/CV/;
  students with no embedding fall back to their program competency profile for
  team suggestion purposes

**Accented characters in filenames not matched**

Fixed in current version. The importer normalises Unicode on both the filename
from the form export and the actual file on disk.

**"ImportError: cannot import name 'resolve_pending_interior'"**

Update src/bulk_import.py to the latest version from the repository.

**The model download fails or is slow**

Retry with:

```bash
./bin/python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('paraphrase-multilingual-mpnet-base-v2')
"
```

Once downloaded, the model lives in ~/.cache/huggingface/ and is never
downloaded again.

**HuggingFace "unauthenticated requests" warning and XLMRobertaModel LOAD REPORT**

Both are harmless informational messages from the sentence-transformers library.
No action needed.
