"""
match.py — rank students against projects (or vice versa), with optional
TF-IDF explanation of why a pair scores the way it does.
"""
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np

from src.models import Explanation, TermWeight


# ── Matching ──────────────────────────────────────────────────────────────────

def run(args):
    """
    Main match command dispatcher.
    Full implementation will cover:
      - loading all embeddings of the target type into memory
      - computing cosine_similarity against the query embedding
      - filtering by status == active and hours_available > 0
      - excluding already-assigned pairs (active rows in assignments.csv)
      - returning a ranked list with scores, capacity info, and student hours
      - dispatching to explain() if --explain is passed
    """
    pass


def run_status(args):
    """
    status command — show fill state of a project's tasks, or student overview.
    Full implementation will cover:
      - per-task hours table (total / filled / remaining / assigned students)
      - student summary (hours committed, projects, status)
      - company summary (active projects, fill rate)
    """
    pass


def run_list(args):
    """
    list command — tabulate students, projects, or companies.
    Full implementation will cover:
      - filtering by semester and active/inactive flag
      - rich table output
    """
    pass


# ── Explanation ───────────────────────────────────────────────────────────────

def explain(
    student_number: str,
    project_id: str,
    score: float,
    top_n: int = 10,
) -> Explanation:
    """
    Build a TF-IDF explanation for why student matches project.

    Strategy:
      1. Load the raw text for the student and the project from
         data/documents/.
      2. Fit a TfidfVectorizer across ALL documents in the corpus so that
         IDF weights reflect term rarity globally, not just in this pair.
      3. Extract the top_n terms by shared weight (product of both TF-IDF
         scores), then separate terms present in only one document.

    Returns an Explanation dataclass ready for rendering.
    """
    from src.store import load_json, list_ids
    from src.parse import parse_file

    # ── Collect corpus ────────────────────────────────────────────────────────
    corpus_texts: dict[str, str] = {}

    for kind in ("students", "companies", "projects"):
        for eid in list_ids(kind):
            meta = load_json(kind, eid)
            docs = meta.get("documents", [])
            if not docs:
                continue
            doc_dir = Path(f"data/documents/{kind}")
            texts = []
            for d in docs:
                fpath = doc_dir / d["filename"]
                if fpath.exists():
                    texts.append(parse_file(fpath))
            if texts:
                corpus_texts[eid] = "\n".join(texts)

    if student_number not in corpus_texts:
        raise ValueError(f"No documents found for student {student_number}")
    if project_id not in corpus_texts:
        raise ValueError(f"No documents found for project {project_id}")

    ids = list(corpus_texts.keys())
    texts = [corpus_texts[i] for i in ids]

    # ── Fit TF-IDF across full corpus ─────────────────────────────────────────
    vec = TfidfVectorizer(
        ngram_range=(1, 3),        # unigrams, bigrams, trigrams
        min_df=1,
        max_features=20_000,
        sublinear_tf=True,
        strip_accents=None,        # preserve French accents
    )
    tfidf_matrix = vec.fit_transform(texts)
    feature_names = np.array(vec.get_feature_names_out())

    s_idx = ids.index(student_number)
    p_idx = ids.index(project_id)

    s_vec = tfidf_matrix[s_idx].toarray().flatten()
    p_vec = tfidf_matrix[p_idx].toarray().flatten()

    # ── Shared terms — product of both weights, non-zero in both ─────────────
    both_nonzero = (s_vec > 0) & (p_vec > 0)
    shared_weight = s_vec * p_vec
    shared_weight[~both_nonzero] = 0

    top_indices = np.argsort(shared_weight)[::-1][:top_n]
    shared_terms = [
        TermWeight(
            term=feature_names[i],
            student_weight=round(float(s_vec[i]), 2),
            project_weight=round(float(p_vec[i]), 2),
            shared_weight=round(float(shared_weight[i]), 2),
        )
        for i in top_indices
        if shared_weight[i] > 0
    ]

    # ── Terms present in student only ─────────────────────────────────────────
    student_only_mask = (s_vec > 0) & (p_vec == 0)
    student_only_indices = np.argsort(s_vec * student_only_mask)[::-1][:8]
    student_only = [
        feature_names[i]
        for i in student_only_indices
        if s_vec[i] > 0 and p_vec[i] == 0
    ]

    # ── Terms present in project only ─────────────────────────────────────────
    project_only_mask = (p_vec > 0) & (s_vec == 0)
    project_only_indices = np.argsort(p_vec * project_only_mask)[::-1][:8]
    project_only = [
        feature_names[i]
        for i in project_only_indices
        if p_vec[i] > 0 and s_vec[i] == 0
    ]

    return Explanation(
        student_number=student_number,
        project_id=project_id,
        score=round(score, 2),
        shared_terms=shared_terms,
        student_only_terms=student_only,
        project_only_terms=project_only,
    )


def render_explanation(exp: Explanation) -> None:
    """Print an Explanation to the terminal using rich."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    console.print(
        f"\n── Match explanation: "
        f"[bold]{exp.student_number}[/bold] ↔ "
        f"[bold]{exp.project_id}[/bold] ──",
        style="cyan",
    )
    console.print(f"\n  Score: [bold green]{exp.score:.2f}[/bold green]\n")

    if exp.shared_terms:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Term",           style="white",  min_width=24)
        table.add_column("Student",        style="cyan",   justify="right")
        table.add_column("Project",        style="magenta",justify="right")
        table.add_column("Shared weight",  style="green",  justify="right")

        for t in exp.shared_terms:
            table.add_row(
                t.term,
                f"{t.student_weight:.2f}",
                f"{t.project_weight:.2f}",
                f"{t.shared_weight:.2f}",
            )
        console.print(table)
    else:
        console.print("  [dim]No significant shared terms found.[/dim]\n")

    if exp.student_only_terms:
        console.print(
            "  [cyan]In student only:[/cyan]  "
            + ", ".join(exp.student_only_terms)
        )
    if exp.project_only_terms:
        console.print(
            "  [magenta]In project only:[/magenta]  "
            + ", ".join(exp.project_only_terms)
        )
    console.print()


def run_explain(args):
    """
    explain subcommand entry point.
    Resolves student number and project ID, calls explain(), renders result.
    """
    from src.embed import cosine_similarity, load_embedding
    from src.store import load_json

    student_meta = load_json("students", args.student)
    project_meta = load_json("projects", args.project)

    s_vec = load_embedding(student_meta["embedding_file"])
    p_vec = load_embedding(project_meta["embedding_file"])
    score = cosine_similarity(s_vec, p_vec)

    exp = explain(
        student_number=args.student,
        project_id=args.project,
        score=score,
        top_n=getattr(args, "top_n", 10),
    )
    render_explanation(exp)
